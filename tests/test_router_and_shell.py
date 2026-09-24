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


# ── 好幾個都夠高：只交給一個（S7 路由第二種結果、S18 同時中多個）──────


def test_各模組用自己的門檻不是同一條():
    """c 的 route_min 0.55、a 的 0.50 —— 0.52 這個分數只有 a 算認領。

    c 的 priority 比較前面：要是門檻被改成全隊同一條，兩個打平就會換 c 出線。"""
    a = _wrap(_假模組("a", 0.52), priority=900)
    c = _wrap(_假模組("c", 0.52), priority=10)
    object.__setattr__(a.pack, "thresholds", Thresholds(route_min=0.50, route_hint_min=0.35))
    object.__setattr__(c.pack, "thresholds", Thresholds(route_min=0.55, route_hint_min=0.35))
    decision = route(_假註冊表([a, c]), AnalyzeInput(text="…"))
    assert decision.primary.id == "a"
    assert [m.id for m, _ in decision.also_possible] == ["c"], "只到提示門檻的仍然是提醒"


def test_好幾個都認領時只有分數最高的出判讀_其他的出提醒():
    """S18：「FB 網購後又接到假客服電話」A 跟 D 都要認出來，A 出完整判讀、D 出提醒。"""
    modules = [_wrap(_假模組("a", 0.87), priority=10), _wrap(_假模組("d", 0.82), priority=900)]
    res = Shell(_假註冊表(modules), Entitlements(unlocked=True)).analyze(AnalyzeInput(text="…"))
    assert res.verdict.module_id == "a"
    assert [h.module_id for h in res.hints] == ["d"]
