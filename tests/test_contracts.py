"""介面規格的測試。規格是五個人唯一的共同語言，它壞了五個人一起壞。"""

from __future__ import annotations

import pytest
from contracts import (
    ActionItem,
    AnalyzeInput,
    PackSpec,
    RiskLevel,
    SimilarCase,
    Thresholds,
    Verdict,
)
from pydantic import ValidationError


def test_verdict_預設帶免責聲明():
    v = Verdict(module_id="x")
    assert "165" in v.disclaimer


def test_verdict_免責聲明不得為空():
    with pytest.raises(ValidationError):
        Verdict(module_id="x", disclaimer="   ")


def test_相似案例一定要有案例編號():
    with pytest.raises(ValidationError):
        SimilarCase(case_id="", excerpt="…")


def test_高風險等級判定():
    assert RiskLevel.CRITICAL.is_severe
    assert RiskLevel.HIGH.is_severe
    assert not RiskLevel.MEDIUM.is_severe


def test_行動項目不接受空字串():
    with pytest.raises(ValidationError):
        ActionItem(order=1, text="")


def test_提醒門檻不能高於認領門檻():
    with pytest.raises(ValidationError):
        Thresholds(route_min=0.4, route_hint_min=0.9)


def test_輸入為空判定():
    assert AnalyzeInput().is_empty
    assert not AnalyzeInput(text="被騙了").is_empty


def test_規格不接受多餘欄位():
    # 凍結的規格要能擋住「偷偷多塞一個欄位」
    with pytest.raises(ValidationError):
        Verdict(module_id="x", 我自己加的欄位=1)


def test_pack_未設定組合時_is_configured_為假(tmp_path):
    p = tmp_path / "pack.yaml"
    p.write_text('id: x\ncode: "X"\nname: 測試\nplatform: ""\ntactic: ""\n', encoding="utf-8")
    assert PackSpec.load(p).is_configured is False
