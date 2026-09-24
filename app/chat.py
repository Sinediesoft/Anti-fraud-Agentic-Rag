"""對話層 —— 使用者輸入 → 固定追問三輪 → 交給分數最高的那一個模組 → 輸出。

為什麼要追問：真實使用者的第一句話幾乎都不夠。實測「我在 fb 上點了一個連結，
連結讓我加 line」這句 —— 兩個平台名都有，但沒有任何手法詞 —— 所有模組都拿 0 分，
結果是「尚未涵蓋」。而這正是最該介入的時刻：他還沒匯錢。

同一句話逐輪補上去（2026-09-24 重測）：

    turn0  原句                          a=0.000       c=0.150       尚未涵蓋
    turn1  ＋「帶我操作股票」              a=0.433 提示   c=0.483 提示   尚未涵蓋
    turn2  ＋「老師說保證獲利」            a=0.780 認領   c=0.817 認領   交給 c，a 只提醒
    turn3  ＋「入金五萬、出金拿不回」       a=0.867 認領   c=0.900 認領   交給 c，a 只提醒

**只交給一個模組**（說明書 S7 路由的第二種結果）：好幾個都認領時，分數最高的
出完整判讀，其他的只出「你可能同時也遇到」的提醒。判讀走 shell.analyze()，
跟「單次查詢」分頁是同一條路。

**不早停**：就算第一輪就有模組認領，三輪照問完。追問問的是「怎麼付的」「卡在
哪一步」，那正是模組判斷階段要的線索 —— 各模組的 m4 在整段文字裡找階段線索詞、
取最後出現的那一階（detect_stage()）。一認領就停，判讀會在使用者講出「已經匯了」
之前就定案，階段會判得比實際前面。

三條界線，寫死在設計裡：

  一、這一層不懂詐騙。追問要問什麼，詞彙全部從各模組的 pack.yaml 讀
      （route_terms／platform_terms），不寫死在外殼。加第六個模組不用改這裡。
  二、這一層不生成內容。輸出是把 RoutedResponse 攤平成訊息的樣板，
      所有內容都已經過 app.guards 檢核。判讀完的追問走白名單，不自由發揮。
  三、風險等級與 165 專線不等追問完。偵測到受災訊號當下就先給停損三條，
      然後照樣把三輪問完（S19 規則二：那些永遠免費）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from contracts import (
    DISCLAIMER,
    HOTLINE,
    AnalyzeInput,
    CoverageStatus,
    ImageInput,
    RiskLevel,
    RoutedResponse,
    Verdict,
)

from .router import route
from .shell import GENERAL_ADVICE, Shell

# 追問幾題。數的是「機器人問出去的問題」，不是「使用者講了幾次」——
# 三題問完之後的那一次輸入才送去判讀，所以使用者總共會打四次字。
MAX_QUESTIONS = 3

# 對話框裡最多列幾筆相似案例、每筆節錄多長。
#
# 2026-09-22 拿真模組跑你的例句，c_tbd 回了 16 筆、每筆四五行 —— 攤成 16 則
# 對話泡泡之後，前面的判讀跟行動清單全被推到看不見的地方。表單那邊可以捲，
# 對話框不行。這是呈現上的取捨，不是丟資料：完整清單在「單次查詢」分頁，
# 而且下面會明講還有幾筆。
MAX_CASES_SHOWN = 3
MAX_EXCERPT_CHARS = 80

RISK_LABEL: dict[RiskLevel, str] = {
    RiskLevel.UNKNOWN: "尚無法判斷",
    RiskLevel.LOW: "低",
    RiskLevel.MEDIUM: "中",
    RiskLevel.HIGH: "高",
    RiskLevel.CRITICAL: "非常高",
}

# 「錢出事了」的訊號。跟 packages/modules/a_tbd/m3_retrieval.py 的那份重複是
# 刻意的 —— 外殼不准 import 任何一個模組，否則加第六個模組就要改這裡。
#
# 🔴 這份清單第一版不要收「怎麼辦」「求助」這類泛用詞：那是句型不是訊號，
#    「我家的貓不吃飯了怎麼辦」會被誤判。只收錢真的出事的詞。
# TODO(S18)：20 題考題出來後拿它們校一次。
DISTRESS_TERMS = (
    "被騙",
    "受騙",
    "詐騙",
    "詐欺",
    "上當",
    "匯款",
    "匯了",
    "轉帳",
    "入金",
    "儲值",
    "拿不回",
    "要不回",
    "領不出",
    "提不出",
    "凍結",
    "報案",
    "165",
)

# 付款方式與進度的詞。這兩份是通用語彙不是詐騙知識 —— 外殼知道「匯款是一種
# 付款方式」不等於它懂假投資。界線在這裡：任何跟特定手法綁定的詞都不准放進來，
# 那種詞屬於各模組的 route_terms。
# TODO(S18)：同上，用 20 題考題校。
PAYMENT_TERMS = (
    "匯款",
    "匯了",
    "轉帳",
    "轉了",
    "ATM",
    "atm",
    "提款機",
    "超商",
    "面交",
    "網銀",
    "刷卡",
    "信用卡",
    "點數",
    "儲值",
    "現金",
    "支付",
    "付了",
    "付款",
)
STAGE_TERMS = (
    "還沒",
    "正在考慮",
    "已經",
    "剛剛",
    "昨天",
    "前天",
    "上週",
    "上個月",
    "拿不回",
    "要不回",
    "領不出",
    "提不出",
    "封鎖",
    "失聯",
    "聯絡不上",
    "報案",
    "凍結",
    "第一次",
    "第二次",
    "第三次",
    "好幾次",
)


class Phase(StrEnum):
    COLLECTING = "collecting"  # 追問中
    DONE = "done"  # 判讀已出，只接受白名單追問


@dataclass(frozen=True)
class ChatMessage:
    """對話框裡的一則。kind 讓介面決定怎麼畫，不影響內容。"""

    role: str  # user / bot
    text: str
    kind: str = "say"  # say / ask / advice / risk / verdict / locked / hint / note


@dataclass(frozen=True)
class Slot:
    """要問到的一格。key 對得上 CaseProfile 的欄位（tactic_said 除外）。"""

    key: str
    question: str


# 問題順序 = 對分數的貢獻順序。tactic_said 擺第一是因為 can_handle 的分數
# 幾乎全部來自手法詞 —— 那是最快把使用者推過門檻的一題。
# amount_range 與 days_elapsed 刻意不問：它們不在任何模組的 route_terms 裡，
# 問了對路由沒有幫助，只會拖長對話。真的需要時它們在 FOLLOW_UPS 裡。
SLOTS = (
    Slot(
        "tactic_said",
        "對方後來叫你做什麼？例如投資、下單、儲值、面交、提供帳號或證件都算 —— "
        "把他講過的話照你記得的說一遍就好。",
    ),
    Slot(
        "payment_method",
        "你有付過錢或提供過個人資料嗎？如果有，是怎麼付的 —— 匯款、ATM、超商、面交，還是買點數？",
    ),
    Slot(
        "scam_stage",
        "這件事從開始到現在大概多久了？你現在卡在哪一步 —— "
        "還在考慮、已經付了，還是想拿回來卻拿不回？",
    ),
    Slot(
        "moved_to",
        "你們最早是在哪裡認識或看到對方的？後來有換到別的地方聊嗎？",
    ),
)

# 四格都填滿了、但三輪還沒問完時用這些。填不進 CaseProfile，
# 但答案一樣會進文字池、一樣會推分數。
FOLLOW_UPS = (
    "大概牽涉到多少錢？講個範圍就好，不用精確數字。",
    "對方有給過你什麼嗎 —— 網站、App、收據截圖，或是所謂的「憑證」？",
    "群組或對話裡除了你還有別人嗎？他們說了什麼？",
    "對話紀錄跟匯款單據你還留著嗎？有沒有已經刪掉、或是把對方封鎖了？",
)

# 判讀出來之後的追問白名單。問到白名單外的東西一律請他重新描述 ——
# 這一層不生成內容，所以能回答的只有 Verdict 裡本來就有的欄位。
_FOLLOW_UP_FIELDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("階段", "第幾步", "哪一步", "進度", "走到"), "stage"),
    (("案例", "類似", "別人", "其他人"), "cases"),
    (("怎麼辦", "做什麼", "行動", "接下來", "該做"), "actions"),
    (("法條", "法律", "告他", "刑法", "報警"), "legal"),
    (("類型", "哪一種", "什麼詐騙", "是不是詐騙"), "type"),
    (("風險", "嚴重"), "risk"),
)


def _hits(terms: tuple[str, ...] | list[str], text: str) -> bool:
    return any(t and t in text for t in terms)


@dataclass
class ChatSession:
    """一次諮詢。純邏輯，不 import streamlit —— 所以測得動。"""

    shell: Shell
    max_questions: int = MAX_QUESTIONS
    session_id: str | None = None

    turns: int = 0
    phase: Phase = Phase.COLLECTING
    utterances: list[str] = field(default_factory=list)
    images: list[ImageInput] = field(default_factory=list)
    history: list[ChatMessage] = field(default_factory=list)
    asked: list[str] = field(default_factory=list)
    response: RoutedResponse | None = None
    # 每一輪的 can_handle 分數。執行紀錄是展示重點（S7 注意事項）——
    # 「問一題分數跳多少」是這個產品最值得看的一張圖。
    score_history: list[dict[str, float]] = field(default_factory=list)
    advice_given: bool = False

    # ── 公開 ────────────────────────────────────────────────

    def greet(self) -> list[ChatMessage]:
        out = [
            ChatMessage(
                "bot",
                "用你自己的話講就好，發生什麼事了？可以直接貼對話截圖。",
                kind="ask",
            ),
            ChatMessage("bot", f"☎️ 反詐騙諮詢專線 {HOTLINE} 任何時候都可以直接打。", kind="note"),
        ]
        self.history.extend(out)
        return out

    def add_images(self, images: list[ImageInput]) -> None:
        """同一個檔案只加一次。

        介面的上傳框會一直留著使用者放過的檔案，每次送出都把它們整批再交一次 ——
        原本照單全收，送四次同一張圖就疊成四張，每個模組的路由與判讀都要把它們
        各讀一遍。用路徑判斷就夠：介面存檔時用內容的雜湊當檔名（app/uploads.py），
        路徑一樣就是同一張圖。
        """
        seen = {image.path for image in self.images}
        for image in images:
            if image.path not in seen:
                self.images.append(image)
                seen.add(image.path)

    def send(self, text: str) -> list[ChatMessage]:
        text = text.strip()
        if not text and not self.images:
            return [ChatMessage("bot", "打幾個字都好，我才知道從哪裡問起。", kind="ask")]

        self.history.append(ChatMessage("user", text))
        if text:
            self.utterances.append(text)

        out = self._follow_up(text) if self.phase is Phase.DONE else self._advance()
        self.history.extend(out)
        return out

    def finish(self) -> list[ChatMessage]:
        """「直接看結果」—— 使用者不想再回答了，用手上的資料送出。

        跟三輪的自動流程是兩件事：那條講的是系統什麼時候停止追問，
        這顆按鈕是使用者的自主權。不想講還被卡住比問不夠糟。
        """
        if self.phase is Phase.DONE:
            return []
        out = self._decide()
        self.history.extend(out)
        return out

    def restart(self) -> None:
        self.turns = 0
        self.phase = Phase.COLLECTING
        self.utterances.clear()
        self.images.clear()
        self.history.clear()
        self.asked.clear()
        self.response = None
        self.score_history.clear()
        self.advice_given = False

    @property
    def text(self) -> str:
        return "\n".join(self.utterances)

    @property
    def questions_left(self) -> int:
        return max(0, self.max_questions - len(self.asked))

    # ── 追問 ────────────────────────────────────────────────

    def _advance(self) -> list[ChatMessage]:
        self.turns += 1
        self.score_history.append(self._scores())

        # 不早停：就算已經有模組認領了，三題照問完。後面幾題的答案（怎麼付的、
        # 卡在哪一步）是模組判斷階段的線索，早停的話階段會判得比實際前面。
        if len(self.asked) >= self.max_questions:
            # 最後一輪才偵測到受災訊號：判讀就接在後面，不能再說「我再問你幾個
            # 問題」；尚未涵蓋的判讀本身就附了同樣的停損三條，不必同一則講兩遍。
            advice = self._distress_advice(more_questions=False)
            decided = self._decide()
            assert self.response is not None
            if self.response.coverage is CoverageStatus.UNCOVERED:
                advice = []
            return [*advice, *decided]

        # 規矩二：風險與 165 不等追問完。偵測到錢出事了就先給停損三條，
        # 然後照樣把剩下的輪數問完。
        out = self._distress_advice(more_questions=True)
        out.append(ChatMessage("bot", self._next_question(), kind="ask"))
        return out

    def _scores(self) -> dict[str, float]:
        payload = AnalyzeInput(text=self.text, images=self.images, session_id=self.session_id)
        return route(self.shell.registry, payload).scores

    def _distress_advice(self, *, more_questions: bool) -> list[ChatMessage]:
        if self.advice_given or not _hits(DISTRESS_TERMS, self.text):
            return []
        self.advice_given = True
        out = [
            ChatMessage(
                "bot",
                "先不管是哪一類 —— 聽起來錢已經出去了，下面三件事現在就可以做：",
                kind="advice",
            ),
            *[ChatMessage("bot", f"・{a}", kind="advice") for a in GENERAL_ADVICE],
        ]
        if more_questions:
            # 最後一輪不加這句：判讀緊接在後，而且判讀自己會附 165
            out.append(
                ChatMessage(
                    "bot",
                    f"☎️ 反詐騙諮詢專線 {HOTLINE}。我再問你幾個問題，才能告訴你這是哪一類、下一步會發生什麼。",
                    kind="note",
                )
            )
        return out

    def _filled(self) -> set[str]:
        """哪幾格已經知道了。詞彙從各模組的 pack.yaml 讀，不寫死在外殼。"""
        text = self.text
        filled: set[str] = set()

        route_terms: list[str] = []
        platforms_hit = 0
        for module in self.shell.registry.usable:
            route_terms.extend(module.pack.route_terms)
            if _hits(module.pack.platform_terms, text):
                platforms_hit += 1

        if _hits(route_terms, text):
            filled.add("tactic_said")
        if _hits(PAYMENT_TERMS, text):
            filled.add("payment_method")
        if _hits(STAGE_TERMS, text):
            filled.add("scam_stage")
        # 兩個以上不同模組的平台詞都命中 —— 那就是跨平台引流，
        # 「在哪認識、後來換去哪」已經講了，不用再問。
        if platforms_hit >= 2:
            filled.add("moved_to")
        return filled

    def _next_question(self) -> str:
        """挑還沒填、也還沒問過的那一格。

        照稿唸會問到使用者剛講過的事 —— 他第一句就說「老師叫我入金」，
        再問一次「對方叫你做什麼」很蠢，而且浪費掉三輪裡的一輪。
        """
        filled = self._filled()
        for slot in SLOTS:
            if slot.key not in filled and slot.key not in self.asked:
                self.asked.append(slot.key)
                return slot.question
        for question in FOLLOW_UPS:
            if question not in self.asked:
                self.asked.append(question)
                return question
        return FOLLOW_UPS[-1]

    # ── 判讀 ────────────────────────────────────────────────

    def _decide(self) -> list[ChatMessage]:
        self.phase = Phase.DONE
        payload = AnalyzeInput(text=self.text, images=self.images, session_id=self.session_id)
        self.response = self.shell.analyze(payload)
        if not self.score_history:
            self.score_history.append(self._scores())
        return self._render(self.response)

    def _render(self, response: RoutedResponse) -> list[ChatMessage]:
        """把 RoutedResponse 攤平成訊息。純樣板 —— 這裡不生成任何內容，
        所有文字都來自已經過 app.guards 檢核的 Verdict。"""
        out: list[ChatMessage] = []

        out.append(ChatMessage("bot", f"風險等級：{RISK_LABEL[response.risk_level]}", kind="risk"))
        out.append(
            ChatMessage("bot", f"☎️ 反詐騙諮詢專線 {HOTLINE} —— 任何情況都可以直接打。", kind="note")
        )

        if response.verdict is not None:
            out.extend(self._render_verdict(response.verdict))
        elif response.coverage is CoverageStatus.COVERED_LOCKED:
            # 規則一：不能假裝不知道
            out.append(ChatMessage("bot", response.locked_notice, kind="locked"))
        else:
            out.append(
                ChatMessage(
                    "bot",
                    "你的情況目前還沒有對應的判讀模組，我不想硬猜 —— 但下面這些事現在就可以做：",
                    kind="say",
                )
            )
            out.extend(ChatMessage("bot", f"・{a}", kind="advice") for a in response.general_advice)

        for hint in response.hints:
            if response.coverage is CoverageStatus.UNCOVERED:
                text = f"（另外你的描述有一點像「{hint.module_name}」，但我沒有把握到可以下判讀的程度。）"
            else:
                # 有主判讀時，提醒裡可能有也過了門檻、只是分數沒排第一的模組 ——
                # 不能說成「沒有把握」。用說明書 S7 的原話，跟「單次查詢」分頁一致。
                locked = "，這個類型需要解鎖才有完整判讀" if hint.locked else ""
                text = f"（你可能同時也遇到「{hint.module_name}」{locked}。）"
            out.append(ChatMessage("bot", text, kind="hint"))

        out.append(ChatMessage("bot", DISCLAIMER, kind="note"))
        return out

    def _render_verdict(self, verdict: Verdict) -> list[ChatMessage]:
        out: list[ChatMessage] = []

        head = f"這看起來是**{verdict.scam_type}**。"
        if verdict.scam_stage:
            head += f"你目前走到的是：{verdict.scam_stage}。"
        out.append(ChatMessage("bot", head, kind="verdict"))

        if verdict.stage_explanation:
            out.append(ChatMessage("bot", verdict.stage_explanation, kind="verdict"))

        if verdict.actions:
            out.append(ChatMessage("bot", "接下來該做的事：", kind="say"))
            for action in sorted(verdict.actions, key=lambda a: a.order):
                mark = "🕒 " if action.preventive else ""
                line = f"{action.order}. {mark}{action.text}"
                if action.why:
                    line += f"（{action.why}）"
                out.append(ChatMessage("bot", line, kind="verdict"))

        if verdict.similar_cases:
            out.append(ChatMessage("bot", "跟你情況相近的案例：", kind="say"))
            for case in verdict.similar_cases[:MAX_CASES_SHOWN]:
                # 出處要靠 source + case_id 才唯一（見 SimilarCase 的欄位說明）
                excerpt = case.excerpt
                if len(excerpt) > MAX_EXCERPT_CHARS:
                    excerpt = excerpt[:MAX_EXCERPT_CHARS].rstrip() + "…"
                out.append(
                    ChatMessage(
                        "bot", f"・[{case.source} {case.case_id}] {excerpt}", kind="verdict"
                    )
                )
            extra = len(verdict.similar_cases) - MAX_CASES_SHOWN
            if extra > 0:
                out.append(
                    ChatMessage(
                        "bot",
                        f"（另外還有 {extra} 筆相近的案例，在「單次查詢」分頁看得到完整清單。）",
                        kind="note",
                    )
                )

        if verdict.degraded:
            out.append(
                ChatMessage(
                    "bot",
                    "（這份判讀有環節降級了：" + "；".join(verdict.degraded_reasons) + "）",
                    kind="note",
                )
            )
        return out

    # ── 判讀之後的追問（白名單）──────────────────────────────

    def _follow_up(self, text: str) -> list[ChatMessage]:
        """只回答 Verdict 裡本來就有的欄位。

        這一層不生成內容，所以「使用者追問 → LLM 自由發揮」那個洞不存在。
        未解鎖模組的 verdict 本來就是 None，結構上就沒有東西可以外洩。
        """
        field_key = next(
            (key for words, key in _FOLLOW_UP_FIELDS if any(w in text for w in words)), None
        )
        if field_key is None:
            return [
                ChatMessage(
                    "bot",
                    "我只能就這次判讀回答 —— 你可以問風險、類型、你走到哪一步、"
                    "接下來該做什麼、相似案例或適用法條。要重講一次請按「重新開始」。",
                    kind="say",
                )
            ]

        response = self.response
        assert response is not None  # 走到 DONE 一定判讀過了
        answers = self._answer(response, field_key) if response.verdict is not None else []
        if answers:
            return answers

        if response.coverage is CoverageStatus.COVERED_LOCKED:
            return [ChatMessage("bot", response.locked_notice, kind="locked")]
        return [ChatMessage("bot", "這次的判讀裡沒有這一項。", kind="say")]

    def _answer(self, response: RoutedResponse, field_key: str) -> list[ChatMessage]:
        verdict = response.verdict
        assert verdict is not None
        if field_key == "risk":
            return [ChatMessage("bot", f"風險等級：{RISK_LABEL[response.risk_level]}", kind="risk")]
        if field_key == "type" and verdict.scam_type:
            return [ChatMessage("bot", verdict.scam_type, kind="verdict")]
        if field_key == "stage" and verdict.scam_stage:
            text = verdict.scam_stage
            if verdict.stage_explanation:
                text += f"\n\n{verdict.stage_explanation}"
            return [ChatMessage("bot", text, kind="verdict")]
        if field_key == "actions" and verdict.actions:
            return [
                ChatMessage("bot", f"{a.order}. {a.text}", kind="verdict")
                for a in sorted(verdict.actions, key=lambda a: a.order)
            ]
        if field_key == "cases" and verdict.similar_cases:
            return [
                ChatMessage("bot", f"・[{c.source} {c.case_id}] {c.excerpt}", kind="verdict")
                for c in verdict.similar_cases
            ]
        if field_key == "legal" and verdict.legal_refs:
            return [
                ChatMessage("bot", f"・{r.title} {r.article}（{r.version_date}）", kind="verdict")
                for r in verdict.legal_refs
            ]
        return []
