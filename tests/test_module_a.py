"""模組 A 的測試。

現在平台 × 手法還沒決定，所以這裡驗的是「空置狀態下架構是對的」：
載得起來、跑得完、而且不會亂搶案子。組合填進 pack.yaml 之後再補真正的品質測試。
"""

from __future__ import annotations

import pytest
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


def test_離題的問句不該拿回任何案例():
    """第一道門檻（關鍵字）守的就是這個。

    2026-09-21 實測，沒有這道門檻時「請問今天天氣如何」會拿回滿滿 5 筆
    假投資案例（分數 0.43～0.50），每一筆都帶著案例編號、日期與縣市 ——
    看起來跟真的一模一樣。那比查不到更糟：使用者沒辦法分辨。

    向量相似度本身擋不住，因為 bge-m3 算中文幾乎不可能給出 <= 0 的分數，
    而原本第二道門檻只有 `score <= 0 就丟掉`。
    """
    from modules.a_tbd import m3_retrieval

    for query in [
        "我明天要去菜市場買水果",
        "請問今天天氣如何",
        "請問台北車站怎麼走",
        "我家的貓不吃飯了怎麼辦",
        "對方叫我去超商買遊戲點數然後拍序號",  # 別人的手法，不是我這類
    ]:
        assert m3_retrieval.search(query, top_k=5) == [], query


def test_該撈到的還是要撈得到():
    """第一道門檻的反面 —— 擋掉離題很容易順手把相關的也擋掉。

    第二句刻意不說平台：那是這個專案的起點題（「群組裡的老師叫我先入金
    才能出金」在官方站台 0 筆命中），無論如何都要找得到。
    """
    from modules.a_tbd import m3_retrieval

    for query in [
        "LINE 群組裡的老師叫我先入金才能出金",
        "群組裡的老師叫我先入金才能出金",
        "line上有人找我投資，說保證獲利",
    ]:
        hits = m3_retrieval.search(query, top_k=5)
        assert len(hits) == 5, query
        assert all(h.case_id for h in hits), "每一筆都要帶案例編號"


def test_提到手法詞的案例要排在前面():
    """第二道門檻的加分。

    向量相似度只看「整段話像不像」，分不出「像是因為都在講投資」還是
    「像是因為都在講出不了金」—— 而後者才是使用者問的那件事。

    2026-09-21 實測「LINE 群組裡的老師叫我先入金才能出金」：只看相似度時
    前 5 名沒有一筆同時提到「群組」與「出金」；加分之後前 5 名全部都有。
    """
    from modules.a_tbd import m3_retrieval

    q = "LINE 群組裡的老師叫我先入金才能出金"
    hits = m3_retrieval.search(q, top_k=5)
    assert len(hits) == 5
    # 加分是依比例給的：全中才加滿
    assert m3_retrieval._tactic_bonus("提到群組也提到出金", ["群組", "出金"]) == pytest.approx(
        m3_retrieval.TACTIC_BONUS
    )
    assert m3_retrieval._tactic_bonus("只提到群組", ["群組", "出金"]) == pytest.approx(
        m3_retrieval.TACTIC_BONUS / 2
    )
    assert m3_retrieval._tactic_bonus("都沒提到", ["群組", "出金"]) == 0.0
    # 問句沒有手法詞時不加分，否則長度不同的問句分數不能比
    assert m3_retrieval._tactic_bonus("隨便什麼字", []) == 0.0


def test_判讀過程一定先經過去識別化():
    verdict = _module().analyze(AnalyzeInput(text="我手機0912345678，匯了五萬"))
    steps = [e.step for e in verdict.trace]
    assert "shared.deid:去識別化" in steps
    assert steps.index("shared.deid:去識別化") < steps.index("m4:分類與抽取")
