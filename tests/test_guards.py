"""輸出檢核（S7 第 4 點）。"""

from __future__ import annotations

from contracts import ActionItem, RiskLevel, SimilarCase, Verdict

from app import guards


def test_高風險但行動清單是空的要擋():
    v = Verdict(module_id="x", risk_level=RiskLevel.HIGH, actions=[])
    checked, problems = guards.enforce(v)
    assert checked is None
    assert any(p.fatal for p in problems)


def test_高風險有行動就過():
    v = Verdict(
        module_id="x",
        risk_level=RiskLevel.HIGH,
        actions=[ActionItem(order=1, text="立即撥打 165")],
    )
    checked, problems = guards.enforce(v)
    assert checked is not None and not problems


def test_低風險沒有行動不算致命():
    v = Verdict(module_id="x", risk_level=RiskLevel.LOW, actions=[])
    checked, _ = guards.enforce(v)
    assert checked is not None


def test_免責聲明一定在():
    v = Verdict(module_id="x")
    assert guards.check(v) == []
    assert "165" in v.disclaimer


def test_相似案例沒編號的會被丟掉而不是整份擋掉():
    v = Verdict(
        module_id="x",
        risk_level=RiskLevel.LOW,
        similar_cases=[
            SimilarCase(case_id="C1", excerpt="…"),
            SimilarCase(case_id="C2", excerpt="…"),
        ],
    )
    # 規格本身就擋住空編號，所以這裡驗的是「有編號的都留著」
    checked, _ = guards.enforce(v)
    assert checked is not None
    assert len(checked.similar_cases) == 2
