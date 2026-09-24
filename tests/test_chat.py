"""對話層的測試。

追問三輪、不早停、不生成內容、未解鎖不外洩 —— 設計上的每一條都寫成測試。
"""

from __future__ import annotations

from contracts import (
    ActionItem,
    AnalyzeInput,
    HealthReport,
    ImageInput,
    ModuleInfo,
    PackSpec,
    Plan,
    RiskLevel,
    SimilarCase,
    Verdict,
)

from app.chat import MAX_CASES_SHOWN, MAX_QUESTIONS, ChatSession, Phase
from app.entitlements import Entitlements
from app.registry import LoadedModule
from app.shell import GENERAL_ADVICE, Shell


class _假模組:
    def __init__(
        self,
        module_id: str,
        score: float,
        *,
        risk: RiskLevel = RiskLevel.HIGH,
        actions: list[str] | None = None,
        scam_type: str = "測試類型",
        cases: list[tuple[str, str]] | None = None,
    ):
        self._id = module_id
        self._score = score
        self._risk = risk
        self._actions = actions if actions is not None else ["立即撥打 165 並聯繫匯款銀行申請圈存"]
        self._scam_type = scam_type
        self._cases = cases or []
        self.analyze_calls = 0

    def can_handle(self, payload: AnalyzeInput) -> float:
        return self._score

    def analyze(self, payload: AnalyzeInput) -> Verdict:
        self.analyze_calls += 1
        return Verdict(
            module_id=self._id,
            risk_level=self._risk,
            scam_type=self._scam_type,
            scam_stage="第三步：對方開始拖延出金",
            stage_explanation="這一步通常表示對方準備收網了。",
            actions=[ActionItem(order=i + 1, text=t) for i, t in enumerate(self._actions)],
            similar_cases=[
                SimilarCase(case_id=cid, source=src, excerpt=f"案例 {cid} 的節錄")
                for src, cid in self._cases
            ],
        )

    def info(self) -> ModuleInfo:
        return ModuleInfo(id=self._id, code="X", name=f"假模組{self._id}", plan=Plan.FREE)

    def health(self) -> HealthReport:
        return HealthReport(module_id=self._id, ready=True)


def _wrap(
    instance,
    *,
    plan: Plan = Plan.FREE,
    priority: int = 100,
    route_terms: list[str] | None = None,
    platform_terms: list[str] | None = None,
) -> LoadedModule:
    pack = PackSpec(
        id=instance.info().id,
        code="X",
        name=instance.info().name,
        plan=plan,
        platform="測試平台",
        tactic="測試手法",
        labels_canon=["測試"],
        route_terms=route_terms or [],
        platform_terms=platform_terms or [],
        priority=priority,
    )
    return LoadedModule(
        pack=pack,
        instance=instance,
        health=instance.health(),
        path=None,  # type: ignore[arg-type]
    )


class _假註冊表:
    def __init__(self, modules):
        self.loaded = modules

    @property
    def usable(self):
        return self.loaded

    def get(self, module_id):
        return next((m for m in self.loaded if m.id == module_id), None)


def _session(modules, *, unlocked: bool = True, **kwargs) -> ChatSession:
    shell = Shell(_假註冊表(modules), Entitlements(unlocked=unlocked))
    return ChatSession(shell=shell, **kwargs)


def _texts(messages) -> str:
    return "\n".join(m.text for m in messages)


def _kinds(messages) -> set[str]:
    return {m.kind for m in messages}


# ── 追問：固定三輪，不早停 ──────────────────────────────────


def test_第一輪就有模組認領也不早停():
    # 分數 0.9 從頭到尾都認領得了，但三題照問完
    chat = _session([_wrap(_假模組("a", 0.9))])
    for _ in range(MAX_QUESTIONS):
        out = chat.send("群組裡的老師叫我先入金才能出金")
        assert "ask" in _kinds(out), "還沒問滿三題就下判讀了"
        assert chat.phase is Phase.COLLECTING
    assert len(chat.asked) == MAX_QUESTIONS


def test_問滿三題之後的下一句才送去判讀():
    chat = _session([_wrap(_假模組("a", 0.9))])
    for _ in range(MAX_QUESTIONS):
        chat.send("我被騙了")
    out = chat.send("大概五萬")
    assert chat.phase is Phase.DONE
    assert "verdict" in _kinds(out)


def test_沒有模組認領時也照樣問滿三輪():
    chat = _session([_wrap(_假模組("a", 0.0))])
    for _ in range(MAX_QUESTIONS):
        assert "ask" in _kinds(chat.send("我在fb上點了一個連結，連結讓我加line"))
    out = chat.send("沒有了")
    assert chat.phase is Phase.DONE
    # 三輪問完還是沒人認領 —— 給通用建議，不硬猜
    assert "尚未有對應" in _texts(out) or "還沒有對應" in _texts(out)


def test_直接看結果會跳過剩下的輪數():
    chat = _session([_wrap(_假模組("a", 0.9))])
    chat.send("群組裡的老師叫我入金")
    out = chat.finish()
    assert chat.phase is Phase.DONE
    assert "verdict" in _kinds(out)
    assert chat.finish() == [], "已經判讀完了不該再跑一次"


# ── 追問：題目要動態選 ──────────────────────────────────────


def test_不會問已經講過的事():
    """第一句就說了手法跟付款方式，那兩題就不該再問。"""
    modules = [_wrap(_假模組("a", 0.9), route_terms=["投資", "入金"], platform_terms=["LINE"])]
    chat = _session(modules)
    out = chat.send("LINE 群組裡的老師叫我投資，我已經匯款三次了")
    assert "tactic_said" not in chat.asked
    assert "payment_method" not in chat.asked
    assert "ask" in _kinds(out)


def test_什麼都沒講時從手法那題開始問():
    modules = [_wrap(_假模組("a", 0.0), route_terms=["投資"], platform_terms=["LINE", "fb"])]
    chat = _session(modules)
    chat.send("我在fb上點了一個連結，連結讓我加line")
    assert chat.asked[0] == "tactic_said"


def test_同一題不會問兩次():
    chat = _session([_wrap(_假模組("a", 0.0))])
    for _ in range(MAX_QUESTIONS):
        chat.send("嗯")
    assert len(set(chat.asked)) == len(chat.asked)


# ── 規矩二：風險與 165 不等追問完 ──────────────────────────


def test_偵測到受災訊號就先給停損三條():
    chat = _session([_wrap(_假模組("a", 0.0))])
    out = chat.send("我已經匯款五萬給對方了")
    assert "advice" in _kinds(out), "錢都出去了還要等三輪才給建議"
    assert "165" in _texts(out)
    # 但追問照樣繼續
    assert "ask" in _kinds(out)
    assert chat.phase is Phase.COLLECTING


def test_停損三條只給一次():
    chat = _session([_wrap(_假模組("a", 0.0))])
    chat.send("我已經匯款了")
    out = chat.send("又匯了一次")
    assert "advice" not in _kinds(out)


def test_沒出事的句子不會被當成受災():
    chat = _session([_wrap(_假模組("a", 0.0))])
    out = chat.send("我在fb上點了一個連結，連結讓我加line")
    assert "advice" not in _kinds(out)


def test_最後一輪才出現受災訊號時不會說還要再問():
    """問滿三題之後那句才講「匯款」—— 判讀就接在後面，不能再說「我再問你幾個問題」。"""
    chat = _session([_wrap(_假模組("a", 0.9))])
    for _ in range(MAX_QUESTIONS):
        chat.send("嗯")
    out = chat.send("我已經匯款五萬了")
    assert chat.phase is Phase.DONE
    assert "advice" in _kinds(out), "停損三條還是要給"
    assert "再問你" not in _texts(out)


def test_最後一輪才出現受災訊號_尚未涵蓋時停損三條只講一次():
    """尚未涵蓋的判讀本身就附了同樣的三條，同一則回覆不該列兩遍。"""
    chat = _session([_wrap(_假模組("a", 0.0))])
    for _ in range(MAX_QUESTIONS):
        chat.send("嗯")
    out = _texts(chat.send("我已經匯款五萬了"))
    assert out.count(GENERAL_ADVICE[0]) == 1


# ── 輸出 ────────────────────────────────────────────────────


def test_判讀輸出一定帶_165_與免責聲明():
    chat = _session([_wrap(_假模組("a", 0.9))])
    out = chat.finish()
    assert "165" in _texts(out)
    assert "僅供參考" in _texts(out)


def test_兩個模組都認領時只交給分數最高的那一個():
    """說明書 S7 路由第二種結果：好幾個都夠高 → 分數最高的出完整判讀。"""
    a = _假模組("a", 0.9, scam_type="LINE 投資詐騙")
    c = _假模組("c", 0.8, scam_type="Facebook 投資詐騙")
    out = _texts(_session([_wrap(a, priority=10), _wrap(c, priority=900)]).finish())
    assert "LINE 投資詐騙" in out
    assert "Facebook 投資詐騙" not in out
    assert c.analyze_calls == 0, "沒被選中的模組不該跑判讀"


def test_其他認領的模組只出提醒():
    """同一條的後半句：其他的出「你可能同時也遇到這個」的提醒。"""
    modules = [
        _wrap(_假模組("a", 0.9), priority=10),
        _wrap(_假模組("c", 0.8), priority=900),
    ]
    out = _session(modules).finish()
    assert any(m.kind == "hint" and "假模組c" in m.text for m in out)


def test_未解鎖的模組當提醒時也講明要解鎖():
    """規則一：付費模組沒排第一、只拿到提醒，也要讓人知道解鎖後有完整判讀。"""
    modules = [
        _wrap(_假模組("a", 0.9), plan=Plan.FREE, priority=10),
        _wrap(_假模組("c", 0.8), plan=Plan.PAID, priority=900),
    ]
    out = _session(modules, unlocked=False).finish()
    hint = next(m for m in out if m.kind == "hint" and "假模組c" in m.text)
    assert "需要解鎖" in hint.text


def test_相似案例要帶出處():
    modules = [_wrap(_假模組("a", 0.9, cases=[("165", "A-001")]))]
    assert "[165 A-001]" in _texts(_session(modules).finish())


# ── 解鎖：規則一「不能假裝不知道」────────────────────────────


def test_未解鎖的模組仍然告訴使用者屬於哪一類():
    modules = [_wrap(_假模組("c", 0.9), plan=Plan.PAID)]
    out = _texts(_session(modules, unlocked=False).finish())
    assert "假模組c" in out
    assert "需要解鎖" in out
    assert "165" in out  # 規則二：專線永遠免費


def test_未解鎖模組的判讀內容不會外洩():
    modules = [_wrap(_假模組("c", 0.9, scam_type="不該出現的類型"), plan=Plan.PAID)]
    chat = _session(modules, unlocked=False)
    out = _texts(chat.finish())
    assert "不該出現的類型" not in out
    # 追問也問不出來
    assert "不該出現的類型" not in _texts(chat.send("這是什麼詐騙類型？"))


# ── 判讀後的追問走白名單 ────────────────────────────────────


def test_白名單內的追問回得出來():
    modules = [_wrap(_假模組("a", 0.9, actions=["立即撥打 165", "保留匯款單據"]))]
    chat = _session(modules)
    chat.finish()
    out = _texts(chat.send("那我接下來該做什麼？"))
    assert "保留匯款單據" in out


def test_白名單外的追問不自由發揮():
    chat = _session([_wrap(_假模組("a", 0.9))])
    chat.finish()
    out = _texts(chat.send("順便幫我查一下明天天氣"))
    assert "只能就這次判讀回答" in out


def test_同一張截圖每次送出都再加一次也只算一張():
    # 介面的上傳框會一直留著使用者放過的檔案，每次送出都把它們整批再交一次
    chat = _session([_wrap(_假模組("a", 0.9))])
    shot = ImageInput(path="/tmp/anti-fraud-uploads/aaa.png", filename="screenshot.png")

    for text in ("我被騙了", "他叫我入金", "已經匯了三次"):
        chat.add_images([shot])
        chat.send(text)

    assert len(chat.images) == 1


def test_同檔名的不同截圖都要留著():
    chat = _session([_wrap(_假模組("a", 0.9))])

    chat.add_images([ImageInput(path="/tmp/anti-fraud-uploads/aaa.png", filename="image.png")])
    chat.add_images([ImageInput(path="/tmp/anti-fraud-uploads/bbb.png", filename="image.png")])

    assert len(chat.images) == 2


def test_重新開始會清乾淨():
    chat = _session([_wrap(_假模組("a", 0.9))])
    chat.send("我被騙了")
    chat.finish()
    chat.restart()
    assert chat.phase is Phase.COLLECTING
    assert chat.asked == [] and chat.utterances == [] and chat.response is None
    assert chat.advice_given is False


# ── 執行紀錄是展示重點 ──────────────────────────────────────


def test_每一輪的分數都記下來():
    chat = _session([_wrap(_假模組("a", 0.9))])
    chat.send("第一句")
    chat.send("第二句")
    assert len(chat.score_history) == 2
    assert chat.score_history[0]["a"] == 0.9


def test_相似案例在對話框裡會截斷_並講明還有幾筆():
    """16 筆全部攤成對話泡泡會把判讀推到看不見的地方（2026-09-22 實測 c_tbd）。"""
    cases = [("165", f"C-{i:03d}") for i in range(8)]
    out = _texts(_session([_wrap(_假模組("a", 0.9, cases=cases))]).finish())
    assert out.count("[165 C-") == MAX_CASES_SHOWN
    assert f"還有 {8 - MAX_CASES_SHOWN} 筆" in out
