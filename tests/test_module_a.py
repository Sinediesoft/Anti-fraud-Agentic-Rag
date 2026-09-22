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


def _fixture_cases():
    """檢索測試用的小語料池。

    這幾條測試原本直接呼叫 search() 不帶 cases=，等於去讀
    packages/modules/a_tbd/data/cases.jsonl —— 那個路徑被 .gitignore 擋掉
    （`packages/modules/*/data/*`），各自本機跑 M1 才會有。**CI 永遠不會有**，
    所以三條測試在 CI 一律撈回 0 筆而紅，本機卻是綠的。

    改成餵固定的小語料池：不依賴未版控的檔案、不依賴向量庫（沒有 index 時
    search() 會退回字元重疊，不需要 ml 那組套件），而且因為內容是寫死的，
    可以斷言真正的排序，不只是「有回 5 筆」。

    池子刻意小於 top_k × _MIN_POOL_AFTER_FILTER_FACTOR，所以第一道門檻的
    手法詞篩選不會動它（「篩完太少就不篩」），排序完全由第二道門檻決定。

    搭配 _force_overlap_base() 一起用 —— 原因見那支的說明。
    """
    from modules.a_tbd.m1_corpus import Case

    return [
        # 前三筆同時提到「群組」與「出金」—— 手法詞加分該給滿
        Case(case_id="F1", text="我在LINE投資群組裡被老師慫恿入金，要出金時平台說要先繳保證金"),
        Case(case_id="F2", text="加入LINE群組後跟著老師操作，獲利看得到卻出金失敗"),
        Case(case_id="F3", text="LINE群組的分析師叫我加碼，現在出金被拒還要我繳稅金"),
        # 後面這幾筆是同一類案子，但沒有同時講到那兩件事
        Case(case_id="F4", text="在LINE上認識的人推薦我買股票，說穩賺不賠，結果賠光"),
        Case(case_id="F5", text="LINE好友傳假投資網站給我，我匯了三萬元過去就被封鎖"),
        Case(case_id="F6", text="對方在LINE上說虛擬貨幣保證獲利，我面交現金給他之後就聯絡不上"),
        Case(case_id="F7", text="我在LINE被騙去一個投資平台，帳面上有獲利但提領不出來"),
        Case(case_id="F8", text="LINE上的投資顧問要我下載APP，入金之後客服就不回了"),
    ]


@pytest.fixture
def _force_overlap_base(monkeypatch):
    """讓底分一律走字元重疊那條退路。

    不這樣做的話這三條測試在本機與 CI 的行為不同：_vector_scores() 是用
    **真實語料的 case_id** 查表的，本機有 index/ 時它會回一個字典，而上面
    那幾筆 F1–F8 不在裡面 -> 底分全部是 0，只剩手法詞加分；CI 沒有 index，
    回 None，底分才是字元重疊。同一份測試兩種算法，等於沒有基準。

    這三條驗的是兩道門檻與手法詞加分，不是向量層（向量層要 ml 那組套件，
    CI 本來就跑不了）。所以明確釘死退路，兩邊行為一致。
    """
    from modules.a_tbd import m3_retrieval

    monkeypatch.setattr(m3_retrieval, "_vector_scores", lambda *_a, **_k: None)


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


def test_沒提平台不等於平台不符():
    """平台判定是三態，不是兩態。

    2026-09-22 之前只有兩態：有我的平台詞乘 1.3，其餘一律乘 0.6。那把
    「沒提任何平台」跟「提到別人的平台」當成同一件事 —— 前者是資訊不足，
    後者是證據指向別人，倒扣的理由只對後者成立。

    代價量得出來：20 題考題裡 10 題不含平台詞的（受害者真實的打字方式）
    在兩態下全軍覆沒（10/20），三態之後 19/20。本專案的起點例句
    「群組裡的老師叫我先入金才能出金」從 0.30 變成 0.50。

    這條守的是三態各自的係數，以及「中性不等於放寬」—— 手法詞仍然要
    自己掙到 2 個命中才過門檻。
    """
    from modules.a_tbd.module import (
        PLATFORM_HIT,
        PLATFORM_MISS,
        PLATFORM_NEUTRAL,
        _platform_factor,
    )

    terms = ["LINE", "Line", "line", "加賴"]
    assert _platform_factor(terms, "我在 LINE 群組被拉進投資") == PLATFORM_HIT
    assert _platform_factor(terms, "我在臉書看到投資廣告") == PLATFORM_MISS
    assert _platform_factor(terms, "群組裡的老師叫我入金") == PLATFORM_NEUTRAL

    # 兩邊都出現時算自己的。A 的 17,764 筆是「假投資 ∩ 提得到 LINE」切出來的，
    # 而這一類的典型歷程就是別處看廣告、再被導進 LINE 談 —— 若「有別人的
    # 平台」就打折，A 會把自己語料的大半判成不是自己的。
    assert _platform_factor(terms, "臉書看到廣告後加對方LINE進投資群組") == PLATFORM_HIT

    m = _module()
    route_min = _route_min()
    # 中性不是放寬：1 個命中（0.3333）照樣不過門檻
    assert m.can_handle(AnalyzeInput(text="群組裡有人找我")) < route_min
    # 別人的平台 + 我的手法詞，折扣要壓得住 —— 這是三態化最該守住的一邊
    臉書但手法像 = "我在臉書看到投資廣告，對方說保證獲利，現在出金失敗"
    assert m.can_handle(AnalyzeInput(text=臉書但手法像)) < route_min


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
        "我家的貓不吃飯了怎麼辦",  # 受災語彙第一版收了「怎麼辦」，這句就漏過去了
        "我的電腦壞了該怎麼辦才好",
        "對方叫我去超商買遊戲點數然後拍序號",  # 別人的手法，不是我這類
        "我在LINE上跟朋友聊天",  # 有平台詞但沒出事 —— 平台詞單獨不該放行
        "我用LINE傳貼圖給同事",
        "LINE的通知一直響很煩",
    ]:
        assert m3_retrieval.search(query, top_k=5) == [], query


def test_出事了但講不出手法的人要找得到案例(_force_overlap_base):
    """「我在LINE上被騙了三萬元」—— 沒有任何手法詞，但他是真的受害者。

    這種人最需要看到相似案例，卻最講不出關鍵字。所以第一道門檻除了手法詞
    之外另收一組受災語彙（DISTRESS_TERMS），兩者有一個就放行。

    底分擋不住這件事：2026-09-21 實測，純聊天的底分最高 0.6674，而一個
    真正相關的查詢最高才 0.6829 —— A 的語料整池都是 LINE 假投資，任何
    提到 LINE 的中文句子跟整池的距離都差不多，底分沒有鑑別力。
    """
    from modules.a_tbd import m3_retrieval

    hits = m3_retrieval.search("我在LINE上被騙了三萬元", top_k=5, cases=_fixture_cases())
    assert len(hits) == 5
    assert all(h.case_id for h in hits)


def test_該撈到的還是要撈得到(_force_overlap_base):
    """第一道門檻的反面 —— 擋掉離題很容易順手把相關的也擋掉。

    第二句刻意不說平台：那是這個專案的起點題（「群組裡的老師叫我先入金
    才能出金」在官方站台 0 筆命中），無論如何都要找得到。
    """
    from modules.a_tbd import m3_retrieval

    pool = _fixture_cases()
    for query in [
        "LINE 群組裡的老師叫我先入金才能出金",
        "群組裡的老師叫我先入金才能出金",
        "line上有人找我投資，說保證獲利",
    ]:
        hits = m3_retrieval.search(query, top_k=5, cases=pool)
        # 斷言是「撈得到」，不是「剛好 5 筆」。原本寫 == 5 是照 17,764 筆的
        # 語料校準的，放到 8 筆的固定池就變成在測這個池子有多大 ——
        # 第二句沒有平台詞，F4 跟它一個 2-gram 都不共用也沒有手法詞，
        # 分數是 0 被濾掉，回 4 筆才是對的。
        assert hits, query
        assert len(hits) <= 5, query
        assert all(h.case_id for h in hits), "每一筆都要帶案例編號"
        assert all(h.score > 0 for h in hits), "分數 0 的不該回傳"


def test_提到手法詞的案例要排在前面(_force_overlap_base):
    """第二道門檻的加分。

    向量相似度只看「整段話像不像」，分不出「像是因為都在講投資」還是
    「像是因為都在講出不了金」—— 而後者才是使用者問的那件事。

    2026-09-21 實測「LINE 群組裡的老師叫我先入金才能出金」：只看相似度時
    前 5 名沒有一筆同時提到「群組」與「出金」；加分之後前 5 名全部都有。
    """
    from modules.a_tbd import m3_retrieval

    q = "LINE 群組裡的老師叫我先入金才能出金"
    hits = m3_retrieval.search(q, top_k=5, cases=_fixture_cases())
    assert len(hits) == 5
    # 固定語料池讓這件事斷言得出來 —— 原本只驗「有回 5 筆」，
    # 並沒有測到這個測試名稱承諾的「排在前面」。
    # F1/F2/F3 同時提到「群組」與「出金」，加分給滿，必須排在前三。
    assert {h.case_id for h in hits[:3]} == {"F1", "F2", "F3"}
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


def test_讀得回帶有U2028的語料(tmp_path, monkeypatch):
    """回歸測試：165 的真實敘述裡有 U+2028 LINE SEPARATOR。

    json.dumps(ensure_ascii=False) **不跳脫** U+2028／U+2029／U+0085，
    但 str.splitlines() 會在它們上面斷行 —— 一筆合法的 JSONL 被切成兩半，
    讀回來就是 JSONDecodeError: Unterminated string。

    2026-09-21 切到全量 17,764 筆時當場踩到（裡面有 2 個 U+2028），
    make index 掛在 health()。抽樣的 1,000 筆裡沒有，所以之前看不到 ——
    自己造的測試資料也永遠碰不到，所以這條要明寫。

    模組 C 在 b6387a8 就踩過同一個坑（10,051 筆裡有 2 個）。
    """
    import json

    from modules.a_tbd import m1_corpus

    path = tmp_path / "cases.jsonl"
    monkeypatch.setattr(m1_corpus, "LOCAL_CORPUS", path)
    row = {
        "case_id": "A-1",
        "text": f"前半段{chr(0x2028)}後半段，中間那個是 LINE SEPARATOR。",
        "source": "165",
        "label": "假投資詐騙",
        "date": "",
        "county": "",
    }
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    cases = m1_corpus.load_local()
    assert len(cases) == 1, "U+2028 把一筆語料切成兩半了"
    assert chr(0x2028) in cases[0].text


def test_判讀過程一定先經過去識別化():
    verdict = _module().analyze(AnalyzeInput(text="我手機0912345678，匯了五萬"))
    steps = [e.step for e in verdict.trace]
    assert "shared.deid:去識別化" in steps
    assert steps.index("shared.deid:去識別化") < steps.index("m4:分類與抽取")
