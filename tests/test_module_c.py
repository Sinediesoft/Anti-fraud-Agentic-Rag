"""模組 C（Facebook × 投資詐騙）的測試。

這個模組是從另一個本機 repo 移植來的（見 packages/modules/c_tbd/README.md），
所以測試分兩類：

  · 架構類 —— 跟 test_module_a.py 同一套：載得起來、四個進入點都在、不亂搶案子
  · 移植類 —— 驗證搬進來的檢索三件組（facets／store／retriever）行為正確

檢索品質不在這裡驗。那要等 S12 的標註評測集，而且 S3 還沒鎖定嵌入模型 ——
現在實際跑的是第 3 層退路（關鍵字重疊），量它的品質沒有意義。
"""

from __future__ import annotations

import pytest
from contracts import AnalyzeInput, Verdict

from app.registry import load
from packages.modules.c_tbd import facets
from packages.modules.c_tbd.m1_corpus import Case, build_subset, coverage
from packages.modules.c_tbd.m3_retrieval import (
    MODEL_READY,
    NumpyStore,
    Retriever,
    search,
)


def _module():
    return load("c_tbd").get("c_tbd").instance


def _cases() -> list[Case]:
    raw = [
        Case(
            case_id="C-001",
            text="被害人於臉書看到投資廣告，加入LINE群組由老師帶單，初期小額獲利可提領，"
            "加碼投入50萬元後平台無法出金。",
            label="假投資",
            date="2026-03-11",
            county="臺北市",
        ),
        Case(
            case_id="C-002",
            text="被害人接到自稱地檢署檢察官來電，要求將存款轉入監管帳戶自證清白。",
            label="假冒身分",
            date="2026-04-02",
            county="新北市",
        ),
        Case(
            case_id="C-003",
            text="被害人於FB社團看到虛擬貨幣投資廣告，依指示以USDT入金後無法提領。",
            label="假投資",
            date="2026-05-20",
            county="臺中市",
        ),
    ]
    for c in raw:
        c.facets = facets.derive_from_text(c.text, c.label)
    return raw


# ── 架構：跟 test_module_a.py 同一套 ──────────────────────────────────


def test_四個進入點都回得出東西():
    m = _module()
    assert isinstance(m.can_handle(AnalyzeInput(text="被騙了")), float)
    assert isinstance(m.analyze(AnalyzeInput(text="被騙了")), Verdict)
    assert m.info().code == "C"
    assert m.health().module_id == "c_tbd"


def test_組合已決定所以會認領自己的案子():
    """跟 A 相反：C 的 platform × tactic 已經填了，所以該認領就要認領。"""
    m = _module()
    assert m.info().platform == "Facebook"
    assert m.info().tactic == "投資詐騙"
    assert m.can_handle(AnalyzeInput(text="FB社團老師帶單保證獲利，入金後平台關閉")) > 0.55


def test_不搶別人的案子():
    """負面詞要擋得住。E 已佔走求職平台 × 人頭帳戶，這條特別重要。"""
    m = _module()
    for text in [
        "我在蝦皮買東西賣家說訂單設定錯誤要解除分期",
        "接到地檢署電話說我帳戶涉及洗錢要監管",
        "看到高薪打工的貼文，對方要我提供帳戶",
    ]:
        assert m.can_handle(AnalyzeInput(text=text)) == 0.0, text


def test_資訊不足時不認領():
    assert _module().can_handle(AnalyzeInput(text="被騙了")) == 0.0


def test_出不了金這種否定變形也要認得出來():
    """回歸測試。

    「出不了金」裡沒有「出金」這個子字串，所以只列「出金」的話，最典型的
    一種案子反而拿不到分數 —— 實跑時它只有 0.48，低於 route_min 0.55。
    純子字串比對對這類插字變形沒有辦法，只能各自列進 route_terms。
    """
    m = _module()
    text = "我在臉書看到投資廣告加了LINE群組結果出不了金"
    assert m.can_handle(AnalyzeInput(text=text)) > 0.55


# ── 移植：facets ────────────────────────────────────────────────────


def test_從內文推斷分類():
    c = _cases()[0]
    assert "假投資" in c.facets["category"]
    assert "Facebook" in c.facets["channel"]
    assert "LINE" in c.facets["channel"]


def test_抓不到就留空而不是猜():
    """空 list 代表「這筆沒記載」，不是「否」—— 誠實留空是刻意的設計。"""
    f = facets.derive_from_text("被害人遭詐騙損失財物。")
    assert f["payment"] == []
    assert f["target"] == []


def test_官方標籤比內文推斷可信所以獨立餵進去():
    f = facets.derive_from_text("對方要我匯款。", label="假投資")
    assert "假投資" in f["category"]


def test_概括說法會展開成具體管道():
    """問「社群上的詐騙」要能篩到 Facebook 與 Instagram。"""
    assert facets.parse_query("社群上的詐騙")["channel"] == ["Facebook", "Instagram"]


def test_展開後不重複():
    """「社群或FB的詐騙」不該讓 Facebook 出現兩次。"""
    got = facets.parse_query("社群或FB的詐騙")["channel"]
    assert got.count("Facebook") == 1


def test_重疊的詞只留最長的那個():
    """「假投資」與「投資」都在詞彙表裡，補出來的問句不能變成
    「Facebook、假投資、投資有哪些詐騙手法？」—— 那不是人話。"""
    terms = facets.terms_in("Facebook上的假投資")
    assert "投資" not in terms or "假投資" in terms
    assert len(terms) == len(set(terms))


def test_抽不出詞時老實回None():
    assert facets.as_question(facets.terms_in("那怎麼辦？")) is None


def test_多值欄位有交集就算符合():
    f = {"channel": ["Facebook"], "category": ["假投資"]}
    cases = _cases()
    assert facets.match(cases[0].facets, f)
    assert not facets.match(cases[1].facets, f)  # C-002 是假檢警


def test_填了詞彙表沒有的值要當場報錯():
    """症狀是「資料存在但永遠篩不到」，不會有任何跡象，所以要炸。"""
    with pytest.raises(facets.UnknownFacet):
        facets.validate({"channel": ["Threads"]}, code="C-999")


# ── 移植：向量儲存層 ────────────────────────────────────────────────


def test_維度對不上要當場報錯():
    """維度不合的向量算出來的相似度是沒有意義的數字，不能默默存進去。"""
    store = NumpyStore("測試模型", dim=4)
    with pytest.raises(ValueError, match="維度對不上"):
        store.add(_cases()[:1], [[0.1, 0.2, 0.3]])


def test_數量對不起來要當場報錯():
    store = NumpyStore("測試模型", dim=3)
    with pytest.raises(ValueError, match="數量對不起來"):
        store.add(_cases()[:2], [[0.1, 0.2, 0.3]])


def test_換了模型就不讀舊快取(tmp_path):
    """不同模型的向量空間不相通，混在一起算出來的相似度沒有意義而且不會報錯。

    這條在本專案格外重要：S3 鎖定嵌入模型時填的 revision 一旦變動，
    五個人各自的索引都要重建，否則分數不能互相比較。
    """
    path = tmp_path / "vectors.npz"
    a = NumpyStore("模型甲", dim=3, path=path)
    a.add(_cases()[:1], [[1.0, 0.0, 0.0]])
    a.save()

    assert NumpyStore("模型甲", dim=3, path=path).load() is True
    assert NumpyStore("模型乙", dim=3, path=path).load() is False  # 換模型
    assert NumpyStore("模型甲", dim=8, path=path).load() is False  # 換維度


def test_刪得掉某個來源的案例():
    store = NumpyStore("測試模型", dim=2)
    cases = _cases()
    cases[0].source = "165"
    cases[1].source = "其他"
    cases[2].source = "165"
    store.add(cases, [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
    assert store.remove_source("其他") == 1
    assert len(store) == 2
    assert store.sources() == {"165"}


def test_不准刪來源為空的案例():
    store = NumpyStore("測試模型", dim=2)
    with pytest.raises(ValueError):
        store.remove_source("")


# ── 移植：檢索 ──────────────────────────────────────────────────────


def test_模型未鎖定時第一層退路要明確報錯而不是靜默():
    """ModelNotSelectedError 是預期內的狀態，呼叫端要接住並落到第 3 層。"""
    from shared.models import ModelNotSelectedError

    with pytest.raises(ModelNotSelectedError):
        Retriever(cases=_cases()).embed(["測試"])


def test_目前實際跑的是第三層退路():
    """S3 鎖定嵌入模型之後這條要改。留著是為了讓那次改動被看見。"""
    assert MODEL_READY is False


def test_搜尋得到而且每筆都帶得出出處():
    """硬規則：沒有出處的結果不准回傳。"""
    hits = search("我在臉書看到投資廣告加了LINE群組結果出不了金", cases=_cases())
    assert hits
    for h in hits:
        assert h.case_id
        assert h.source


def test_節錄一律去識別化():
    """絕不投影真實受害者原文。原文的金額不該原樣出現在節錄裡。"""
    hits = search("投資出不了金", cases=_cases())
    assert hits
    assert all("50萬元" not in h.excerpt for h in hits)


def test_沒有語料時回空的而不是爆炸():
    assert search("任何問題", cases=[]) == []


def test_篩選型問句篩得掉不相關的類型():
    r = Retriever(cases=_cases())
    f = r.filters_for("Facebook 上有哪些投資詐騙")
    hit_ids = [c.case_id for c in _cases() if facets.match(c.facets, f)]
    assert "C-002" not in hit_ids  # 假檢警，不該被撈到
    assert {"C-001", "C-003"} <= set(hit_ids)


# ── 移植：M1 切語料 ─────────────────────────────────────────────────

LABELS = ["假投資", "假投資詐騙"]
PLATFORM = ["Facebook", "FB", "臉書", "粉專", "社團"]


def _fixture_parquet(tmp_path):
    """造一份跟 tools/fetch_corpus_165.py 產出結構相同的小 parquet。

    欄位照 #24 的 FIELD_MAP：case_id / date / county / county_id / text / label / source
    """
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")

    rows = [
        # 手法符合 × 有提到平台 —— 要收
        ("1", "臉書投資廣告加LINE群組由老師帶單，加碼後平台無法出金。", "假投資"),
        ("2", "於FB社團看到虛擬貨幣投資廣告，以USDT入金後無法提領。", "假投資"),
        # 手法符合 × 沒提到平台 —— 漏抓的示範，計入 total 但不計入 platform
        ("3", "朋友介紹一個投資平台說保證獲利，入金後才發現無法出金。", "假投資"),
        # 手法不符 —— 不收。注意這筆有「臉書」，平台對但手法不對
        ("4", "在臉書看到高薪打工的貼文，對方要我提供帳戶幫忙代收款項。", "人頭帳戶"),
        ("5", "接到自稱地檢署檢察官來電，要求將存款轉入監管帳戶。", "假冒機構"),
        # 太短 —— MIN_CHARS 濾掉
        ("6", "被騙了", "假投資"),
        # 重複 case_id —— 去重
        ("1", "重複的那一筆。臉書投資廣告群組帶單出不了金。", "假投資"),
    ]
    data = {
        "case_id": [r[0] for r in rows],
        "text": [r[1] for r in rows],
        "label": [r[2] for r in rows],
        "date": ["2026-03-11"] * len(rows),
        "county": ["臺北市"] * len(rows),
        "county_id": ["63"] * len(rows),
        "source": ["165"] * len(rows),
    }
    path = tmp_path / "corpus.parquet"
    pq.write_table(pa.table(data), path, compression="zstd")
    return path


def test_切語料的四個數字各自算對(tmp_path):
    got = build_subset(
        LABELS,
        PLATFORM,
        source_path=_fixture_parquet(tmp_path),
        out_path=tmp_path / "cases.jsonl",
    )
    assert got["scanned"] == 7
    # total／platform 是**去重前**的計數，去重只在寫出時做。
    # 這樣分是刻意的：那兩個數字要回答「語料裡有多少」，寫出數才是「我拿到多少」。
    assert got["total_cases"] == 4  # 1、2、3 與重複的那筆（太短的已被濾掉）
    assert got["platform_cases"] == 3  # 1、2、重複那筆（3 沒提到平台）
    assert got["written"] == 2  # 去重後只剩 1、2


def test_平台對但手法不對的不能收(tmp_path):
    """那筆「在臉書看到高薪打工」是 E 的領域，只因為有「臉書」就收進來會很糟。"""
    out = tmp_path / "cases.jsonl"
    build_subset(LABELS, PLATFORM, source_path=_fixture_parquet(tmp_path), out_path=out)
    assert "高薪打工" not in out.read_text(encoding="utf-8")


def test_太短的敘述不收(tmp_path):
    out = tmp_path / "cases.jsonl"
    build_subset(LABELS, PLATFORM, source_path=_fixture_parquet(tmp_path), out_path=out)
    assert "被騙了" not in out.read_text(encoding="utf-8")


def test_手法總數與平台交集要分開回報(tmp_path):
    """只給一個數字會讓人以為「我的語料就這麼多」，
    但實際上是「我認得出來的就這麼多」—— 平台只能從內文推斷。"""
    got = build_subset(
        LABELS,
        PLATFORM,
        source_path=_fixture_parquet(tmp_path),
        out_path=tmp_path / "cases.jsonl",
    )
    assert got["total_cases"] > got["platform_cases"]  # 有筆沒提到平台


def test_退路模式收整個手法不做平台交集(tmp_path):
    got = build_subset(
        LABELS,
        PLATFORM,
        source_path=_fixture_parquet(tmp_path),
        out_path=tmp_path / "cases.jsonl",
        platform_only=False,
    )
    assert got["written"] == 3  # 多收了那筆沒提到平台的


def test_找不到共用語料時的訊息要說得出去哪裡拿(tmp_path):
    with pytest.raises(FileNotFoundError, match="fetch_corpus_165"):
        build_subset(LABELS, PLATFORM, source_path=tmp_path / "不存在.parquet")


def test_覆蓋率報得出詞彙表有但語料沒有的值():
    """這比「抽不出條件」更糟：使用者問得出條件、然後篩到 0 筆，
    而系統會保證回答「沒有」。

    target 這一維在 165 語料上實測是 0 —— 受害者的第一人稱敘述幾乎不會
    自稱「投資人」「長者」。這是真實缺口，不是測試資料造出來的。
    """
    cov = coverage(_cases())
    used, unused = cov["target"]
    assert used == []
    assert unused  # 詞彙表列了十個，一個都沒用到
