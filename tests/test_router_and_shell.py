"""路由與外殼的測試。解鎖那三條硬規則就寫在這裡（S7 / S19）。"""

from __future__ import annotations

import pytest
from contracts import (
    AnalyzeInput,
    CoverageStatus,
    HealthReport,
    ModuleInfo,
    PackSpec,
    Plan,
    RiskLevel,
    Thresholds,
    Verdict,
)

from app.entitlements import Entitlements
from app.registry import LoadedModule, load
from app.router import route
from app.shell import Shell


class _假模組:
    """S4 第 5 點的假模組：什麼都不做，但符合規格。外殼可以拿它先測試。"""

    def __init__(
        self,
        module_id: str,
        score: float,
        *,
        plan: Plan = Plan.PAID,
        risk=RiskLevel.HIGH,
        actions=None,
    ):
        self._id = module_id
        self._score = score
        self._plan = plan
        self._risk = risk
        self._actions = (
            actions
            if actions is not None
            else [{"order": 1, "text": "立即撥打 165 並聯繫匯款銀行申請圈存"}]
        )

    def can_handle(self, payload: AnalyzeInput) -> float:
        return self._score

    def analyze(self, payload: AnalyzeInput) -> Verdict:
        from contracts import ActionItem

        return Verdict(
            module_id=self._id,
            risk_level=self._risk,
            scam_type="測試類型",
            actions=[ActionItem(**a) for a in self._actions],
        )

    def info(self) -> ModuleInfo:
        return ModuleInfo(id=self._id, code="X", name=f"假模組{self._id}", plan=self._plan)

    def health(self) -> HealthReport:
        return HealthReport(module_id=self._id, ready=True)


def _wrap(instance, *, plan=Plan.PAID, priority=100) -> LoadedModule:
    pack = PackSpec(
        id=instance.info().id,
        code="X",
        name=instance.info().name,
        plan=plan,
        platform="測試平台",
        tactic="測試手法",
        labels_canon=["測試"],
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


# ── 路由 ────────────────────────────────────────────────────


def test_都不夠高時標記尚未涵蓋():
    reg = _假註冊表([_wrap(_假模組("m1", 0.1))])
    assert route(reg, AnalyzeInput(text="…")).uncovered


def test_分數最高的出完整判讀_其他的出提醒():
    reg = _假註冊表([_wrap(_假模組("低", 0.6)), _wrap(_假模組("高", 0.9))])
    decision = route(reg, AnalyzeInput(text="…"))
    assert decision.primary.id == "高"
    assert [m.id for m, _ in decision.also_possible] == ["低"]


def test_打平時看_priority():
    reg = _假註冊表(
        [_wrap(_假模組("後", 0.8), priority=200), _wrap(_假模組("先", 0.8), priority=10)]
    )
    assert route(reg, AnalyzeInput(text="…")).primary.id == "先"


def test_一個模組壞掉不會讓路由當掉():
    class _會爆炸的(_假模組):
        def can_handle(self, payload):
            raise RuntimeError("壞了")

    reg = _假註冊表([_wrap(_會爆炸的("壞", 0.0)), _wrap(_假模組("好", 0.9))])
    assert route(reg, AnalyzeInput(text="…")).primary.id == "好"


def test_手動切換是最後一道保險():
    reg = _假註冊表([_wrap(_假模組("a", 0.0)), _wrap(_假模組("b", 0.0))])
    assert route(reg, AnalyzeInput(text="…"), manual="b").primary.id == "b"


# ── 解鎖三條硬規則（S7 第 3 點 / S19）──────────────────────


def test_規則一_未解鎖時仍要告訴使用者屬於哪一類():
    shell = Shell(
        _假註冊表([_wrap(_假模組("c", 0.9), plan=Plan.PAID)]), Entitlements(unlocked=False)
    )
    res = shell.analyze(AnalyzeInput(text="…"))
    assert res.coverage is CoverageStatus.COVERED_LOCKED
    assert "需要解鎖" in res.locked_notice
    assert res.verdict is None  # 完整判讀要解鎖
    assert "假模組c" in res.locked_notice  # 但不能假裝不知道是哪一類


def test_規則二_風險等級與_165_導流永遠免費():
    shell = Shell(
        _假註冊表([_wrap(_假模組("c", 0.9), plan=Plan.PAID)]), Entitlements(unlocked=False)
    )
    res = shell.analyze(AnalyzeInput(text="…"))
    assert res.hotline == "165"
    assert res.risk_level is not None
    assert res.general_advice


def test_規則三_解鎖設定壞掉時往多給的方向失敗():
    class _壞掉的方案:
        plan = property(lambda self: (_ for _ in ()).throw(RuntimeError("設定壞了")))

    broken = _wrap(_假模組("c", 0.9))
    object.__setattr__(broken, "pack", _壞掉的方案())
    assert Entitlements(unlocked=False).can_use(broken) is True


def test_免費模組不需要解鎖就能用():
    shell = Shell(
        _假註冊表([_wrap(_假模組("a", 0.9), plan=Plan.FREE)]), Entitlements(unlocked=False)
    )
    res = shell.analyze(AnalyzeInput(text="…"))
    assert res.coverage is CoverageStatus.COVERED
    assert res.verdict is not None


def test_尚未涵蓋時給通用建議與_165():
    shell = Shell(_假註冊表([_wrap(_假模組("a", 0.1))]), Entitlements(unlocked=True))
    res = shell.analyze(AnalyzeInput(text="…"))
    assert res.coverage is CoverageStatus.UNCOVERED
    assert res.general_advice and res.hotline == "165"


def test_高風險卻沒有行動清單的判讀不准吐出去():
    shell = Shell(
        _假註冊表([_wrap(_假模組("a", 0.9, plan=Plan.FREE, actions=[]), plan=Plan.FREE)]),
        Entitlements(unlocked=True),
    )
    res = shell.analyze(AnalyzeInput(text="…"))
    assert res.coverage is CoverageStatus.UNCOVERED
    assert res.general_advice


def test_模組_analyze_爆炸時降級而不是整個當掉():
    class _會爆炸的(_假模組):
        def analyze(self, payload):
            raise RuntimeError("模型掛了")

    shell = Shell(
        _假註冊表([_wrap(_會爆炸的("a", 0.9), plan=Plan.FREE)]), Entitlements(unlocked=True)
    )
    res = shell.analyze(AnalyzeInput(text="…"))
    assert res.coverage is CoverageStatus.UNCOVERED
    assert any(e.status == "failed" for e in res.trace)


def test_執行紀錄有東西可以展示():
    shell = Shell(
        _假註冊表([_wrap(_假模組("a", 0.9), plan=Plan.FREE)]), Entitlements(unlocked=True)
    )
    assert shell.analyze(AnalyzeInput(text="…")).trace


@pytest.mark.parametrize("selection", ["a_tbd", "_template"])
def test_真模組掛上外殼跑得完(selection):
    shell = Shell(load(selection), Entitlements(unlocked=True))
    res = shell.analyze(AnalyzeInput(text="有人說我中獎了要我先繳手續費"))
    assert res.coverage in tuple(CoverageStatus)
    assert res.disclaimer


# ── 多模組喚起：analyze_all（跨平台的案子兩邊都要出判讀）────────


def _t(route_min: float = 0.55, hint: float = 0.35) -> Thresholds:
    return Thresholds(route_min=route_min, route_hint_min=hint)


def _shell(modules, *, unlocked: bool = True) -> Shell:
    return Shell(_假註冊表(modules), Entitlements(unlocked=unlocked))


def test_兩個模組都認領時各出一份判讀():
    """實測「fb 點連結加 line，老師說保證獲利」a=0.867 / c=0.817 兩個都過門檻 ——
    analyze() 只跑分數高的那個，analyze_all() 兩份都給。"""
    modules = [_wrap(_假模組("a", 0.87), priority=10), _wrap(_假模組("c", 0.82), priority=900)]
    responses = _shell(modules).analyze_all(AnalyzeInput(text="…"))
    assert [r.verdict.module_id for r in responses] == ["a", "c"], "第一份要是分數最高的"
    assert all(r.coverage is CoverageStatus.COVERED for r in responses)


def test_只到提示門檻的不會被喚起():
    modules = [_wrap(_假模組("a", 0.9)), _wrap(_假模組("c", 0.4))]
    responses = _shell(modules).analyze_all(AnalyzeInput(text="…"))
    assert len(responses) == 1
    # 沒被喚起的仍然是提示 —— 還沒出事的句子拿到兩份「你被詐騙了」會誤導
    assert [h.module_id for h in responses[0].hints] == ["c"]


def test_被喚起的模組不會同時出現在提示裡():
    modules = [_wrap(_假模組("a", 0.87), priority=10), _wrap(_假模組("c", 0.82), priority=900)]
    responses = _shell(modules).analyze_all(AnalyzeInput(text="…"))
    assert all(not r.hints for r in responses)


def test_都不夠高時回一份尚未涵蓋():
    responses = _shell([_wrap(_假模組("a", 0.1))]).analyze_all(AnalyzeInput(text="…"))
    assert len(responses) == 1
    assert responses[0].coverage is CoverageStatus.UNCOVERED
    assert responses[0].general_advice


def test_多模組時解鎖規則一樣生效():
    """免費那個出完整判讀，付費那個仍要告訴使用者屬於哪一類（規則一）。"""
    modules = [
        _wrap(_假模組("免費", 0.9), plan=Plan.FREE, priority=10),
        _wrap(_假模組("付費", 0.8), plan=Plan.PAID, priority=900),
    ]
    responses = _shell(modules, unlocked=False).analyze_all(AnalyzeInput(text="…"))
    assert responses[0].coverage is CoverageStatus.COVERED
    assert responses[1].coverage is CoverageStatus.COVERED_LOCKED
    assert responses[1].verdict is None
    assert "假模組付費" in responses[1].locked_notice


def test_多模組時輸出檢核一樣生效():
    """高風險卻沒行動清單的那一份不准吐出去，另一份不受影響。"""
    modules = [
        _wrap(_假模組("好", 0.9), priority=10),
        _wrap(_假模組("壞", 0.8, actions=[]), priority=900),
    ]
    responses = _shell(modules).analyze_all(AnalyzeInput(text="…"))
    assert responses[0].coverage is CoverageStatus.COVERED
    assert responses[1].coverage is CoverageStatus.UNCOVERED


def test_一個模組爆炸不影響另一個():
    class _會爆炸的(_假模組):
        def analyze(self, payload):
            raise RuntimeError("模型掛了")

    modules = [_wrap(_會爆炸的("壞", 0.9), priority=10), _wrap(_假模組("好", 0.8), priority=900)]
    responses = _shell(modules).analyze_all(AnalyzeInput(text="…"))
    assert responses[0].coverage is CoverageStatus.UNCOVERED
    assert responses[1].coverage is CoverageStatus.COVERED


def test_各模組用自己的門檻不是同一條():
    """c 的 route_min 0.55、a 的 0.50 —— 0.52 這個分數只有 a 算認領。"""
    a = _wrap(_假模組("a", 0.52), priority=10)
    c = _wrap(_假模組("c", 0.52), priority=900)
    object.__setattr__(a.pack, "thresholds", _t(0.50))
    object.__setattr__(c.pack, "thresholds", _t(0.55))
    responses = _shell([a, c]).analyze_all(AnalyzeInput(text="…"))
    assert len(responses) == 1
    assert responses[0].verdict.module_id == "a"


def test_手動指定時只跑那一個():
    modules = [_wrap(_假模組("a", 0.9)), _wrap(_假模組("c", 0.9))]
    responses = _shell(modules).analyze_all(AnalyzeInput(text="…"), manual="c")
    assert len(responses) == 1
    assert responses[0].verdict.module_id == "c"


def test_analyze_的行為沒有被_analyze_all_改到():
    """W2 凍結的那條路要一字不變 —— evaluate.py 與 S16 入場檢查走的是它。"""
    modules = [_wrap(_假模組("a", 0.87), priority=10), _wrap(_假模組("c", 0.82), priority=900)]
    res = _shell(modules).analyze(AnalyzeInput(text="…"))
    assert res.verdict.module_id == "a"
    assert [h.module_id for h in res.hints] == ["c"], "單模組那條路仍然把其他人當提示"
