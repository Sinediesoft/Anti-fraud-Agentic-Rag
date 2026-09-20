"""模組 A 的測試。

現在平台 × 手法還沒決定，所以這裡驗的是「空置狀態下架構是對的」：
載得起來、跑得完、而且不會亂搶案子。組合填進 pack.yaml 之後再補真正的品質測試。
"""

from __future__ import annotations

from contracts import AnalyzeInput, Verdict
from shared import models

from app.registry import load


def _module():
    return load("a_tbd").get("a_tbd").instance


def test_四個進入點都回得出東西():
    m = _module()
    assert isinstance(m.can_handle(AnalyzeInput(text="被騙了")), float)
    assert isinstance(m.analyze(AnalyzeInput(text="被騙了")), Verdict)
    assert m.info().code == "A"
    assert m.health().module_id == "a_tbd"


def test_組合未決定時不認領任何案子():
    m = _module()
    for text in ["我在網路上買東西被騙", "群組裡的老師叫我先入金", "有人說我中獎了"]:
        assert m.can_handle(AnalyzeInput(text=text)) == 0.0


def test_health_誠實說出還缺什麼():
    report = _module().health()
    names = {c.name: c for c in report.checks}
    assert names["pack"].ok is True  # 2026-09-20 填了 LINE x 假投資
    assert names["models"].ok is False  # S3 只鎖了 4 個裡的 2 個
    assert report.ready is True  # 這些都不擋啟動 —— 這才是這個測試守的規矩


def test_沒有模型也跑得完一次判讀(monkeypatch):
    # 三層退路的第三層：改用規則硬抽並標記信心低。
    #
    # 原本靠「模型還沒鎖定所以必定拋例外」來觸發，但 slm 在 2026-09-20 接上
    # Ollama 之後就不一定拋了 —— 那樣這個測試會變成「Ollama 有沒有在跑」的
    # 測試，而不是退路的測試。改成自己製造模型不可用。
    def 模型不可用(*_a, **_k):
        raise models.ModelNotSelectedError("測試刻意製造的失敗")

    monkeypatch.setattr(models, "call_slm", 模型不可用)
    verdict = _module().analyze(AnalyzeInput(text="我已經匯了三萬元出去"))
    assert verdict.disclaimer
    assert verdict.trace
    assert verdict.confidence.value == "low"


def test_判讀過程一定先經過去識別化():
    verdict = _module().analyze(AnalyzeInput(text="我手機0912345678，匯了五萬"))
    steps = [e.step for e in verdict.trace]
    assert "shared.deid:去識別化" in steps
    assert steps.index("shared.deid:去識別化") < steps.index("m4:分類與抽取")
