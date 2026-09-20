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


def _route_min() -> float:
    """門檻從 pack.yaml 讀，不要寫死 —— S18 調校時那個值會動。"""
    return load("a_tbd").get("a_tbd").pack.thresholds.route_min


def test_四個進入點都回得出東西():
    m = _module()
    assert isinstance(m.can_handle(AnalyzeInput(text="被騙了")), float)
    assert isinstance(m.analyze(AnalyzeInput(text="被騙了")), Verdict)
    assert m.info().code == "A"
    assert m.health().module_id == "a_tbd"


def test_不是自己那類的案子不要亂搶():
    """這條原本叫「組合未決定時不認領任何案子」，斷言 can_handle 一律回 0
    —— 那是 platform / tactic 還空著的時代。2026-09-20 填上 LINE × 假投資
    之後，真正要守的是「不亂搶」，不是「永遠回 0」。

    第三句在 2026-09-21 補了手法詞「群組」（區辨力 +0.39）之後會拿到
    0.20：它本來就是一句假投資的敘述，給非零分數是對的。重點是它沒說
    平台，乘法把它壓在 route_min 之下 —— 停在那裡，讓外殼去問別人。
    """
    m = _module()
    assert m.can_handle(AnalyzeInput(text="我在網路上買東西被騙")) == 0.0
    assert m.can_handle(AnalyzeInput(text="有人說我中獎了")) == 0.0
    assert m.can_handle(AnalyzeInput(text="群組裡的老師叫我先入金")) < _route_min()


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


def test_平台詞要卡字界不然online也算LINE():
    """「line」是子字串，online / Online / ONLINE 都含有它。

    2026-09-21 掃全部 194,355 筆共用語料：子字串比對命中 81,597 筆，
    卡字界之後 81,327 筆 —— 那 270 筆是 online 之類的英文字誤判。
    中文詞不受影響：「加賴」沒有 a-z 的字界概念，照樣比子字串。
    """
    from modules.a_tbd.module import _platform_hit

    terms = ["LINE", "Line", "line", "加賴"]
    for text in [
        "我在LINE上被騙",
        "line上有人找我",
        "Line群組",
        "LINE@官方帳號",
        "加賴之後被拉進群組",
    ]:
        assert _platform_hit(terms, text) is True, text
    for text in ["我在online購物網站被騙", "Online投資平台", "ONLINE遊戲點數", "deadline快到了"]:
        assert _platform_hit(terms, text) is False, text


def test_平台不對就不認領別人的案子():
    """「平台 × 手法」是乘法不是加法。

    加法那版（score + 0.15）讓手法詞夠多就能蓋過平台不符 —— 實測一個
    臉書的案子在 A 這裡拿到 0.50，剛好等於 route_min，A 照樣認領。
    這條守的就是那個：手法再像，平台不對就要掉到門檻下。
    """
    m = _module()
    route_min = _route_min()
    臉書案 = "我在臉書看到投資廣告，加了粉專客服，後來叫我入金，現在說要繳稅金才能出金"
    line案 = "LINE 群組裡的老師叫我先入金才能出金，現在平台說要繳保證金才能提領"
    assert m.can_handle(AnalyzeInput(text=臉書案)) < route_min
    assert m.can_handle(AnalyzeInput(text=line案)) >= route_min
    # 同一段手法敘述，只差平台 —— 分數必須拉得開
    assert m.can_handle(AnalyzeInput(text=line案)) > m.can_handle(AnalyzeInput(text=臉書案))


def test_prompt沒寫好時根本不呼叫模型(monkeypatch):
    """S13 的 prompt 還沒寫，那就不該去打擾模型。

    這條守的是 2026-09-21 量到的那個浪費：M4 原本送一個空字串給 Ollama，
    模型回空字串，但每次判讀要多等一次冷載入（實測 788 ms，UI 上 1546 ms）。
    呼叫次數直接數 —— 「有沒有變慢」測不出來，「有沒有呼叫」測得出來。
    """
    called = []
    monkeypatch.setattr(models, "call_slm", lambda *a, **k: called.append(a) or "")
    _module().analyze(AnalyzeInput(text="群組裡的老師叫我先入金才能出金"))
    assert called == []


def test_走規則抽取時信心一定標低():
    """空 prompt 那版的第二個問題：呼叫沒拋例外就 break，confidence 留在
    MEDIUM —— 但 profile 是規則硬抽的。信心值對使用者說謊比慢更嚴重。

    這裡刻意不 monkeypatch 任何東西：Ollama 有沒有在跑都該是 low，因為
    現在根本沒有走模型那條路。
    """
    verdict = _module().analyze(AnalyzeInput(text="我已經匯了三萬元出去"))
    assert verdict.confidence.value == "low"


def test_判讀過程一定先經過去識別化():
    verdict = _module().analyze(AnalyzeInput(text="我手機0912345678，匯了五萬"))
    steps = [e.step for e in verdict.trace]
    assert "shared.deid:去識別化" in steps
    assert steps.index("shared.deid:去識別化") < steps.index("m4:分類與抽取")
