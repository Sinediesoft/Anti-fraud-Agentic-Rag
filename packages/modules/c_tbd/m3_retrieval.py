"""M3 語意檢索：做出「講白話也找得到」的搜尋（S12）。

完成型態是：關鍵字搜尋（BM25）跟語意搜尋同時跑 → RRF 合成排名 → 重排序。
這一版移植自另一個本機 repo 的 shared/{retriever,store}.py，已經有的是
語意那一條，加上結構化過濾與增量索引。BM25 與 RRF 的接縫留著（見檔尾）。

硬規則：沒有出處的結果不准回傳 —— 每一筆都要帶案例編號跟日期縣市。

## 三層退路（S13 第 4 點）

    1. 向量檢索        embed() 走 shared.models，模型鎖定後生效
    2. 結構化過濾      facets.py，純規則、零模型，永遠可用
    3. 關鍵字重疊      _overlap_score，最笨但不會失敗的 baseline

    S3 還沒鎖定嵌入模型，所以現在實際跑的是第 3 層。第 1 層的程式碼是完整
    的 —— 鎖定之後把 MODEL_READY 打開就會接上，不是等到那時才開始寫。

## 🔴 移植時改掉的兩件事

    · embed() 原本直接呼叫模型套件，現在一律走 shared.models（規矩一）。
      五個人必須用同一組模型，否則分數不能比。
    · min_score 是對「某個模型 × 某批語料」量出來的，不是通用常數。
      來源那套的門檻**不能沿用** —— 語料從 6 張手寫案例卡換成 165 的
      十幾萬筆原始記錄，尺度完全不同。見 DEFAULT_MIN_SCORE 的註解。
"""

from __future__ import annotations

import json
from pathlib import Path

from contracts import SimilarCase
from shared import deid, models

from .facets import match, parse_query, terms_in
from .m1_corpus import Case, load_local

INDEX_DIR = Path(__file__).resolve().parent / "index"
INDEX_PATH = INDEX_DIR / "vectors.npz"

# ⚠ 在完整索引（10,051 筆）上量出來的。S12 還是要用標註評測集重驗一次。
#
# 2026-09-21 實測（bge-m3、cpu/fp32、完整索引）各問句的分數分布：
#
#                      最高     第10    第100     中位
#     相關（投資詐騙） 0.8200   0.7931   0.7678   0.6767
#     弱相關（點數卡） 0.6727   0.5937   0.5609   0.4778
#     離題（買水果）   0.5504   0.4971   0.4562   0.3886
#     離題（問路）     0.5127   0.4640   0.4357   0.3648
#     離題（天氣）     0.4632   0.4419   0.4229   0.3682
#
# 各門檻下三個離題問句會撈回幾筆：
#
#     0.50 -> 7 / 3 / 0      0.55 -> 1 / 0 / 0      0.60 -> 0 / 0 / 0
#
# 🔴 先前在 300 筆的 demo 索引上量到的是 0.50，放到 10,051 筆就不夠了 ——
#    語料變 33 倍，離題問句「碰巧撞到一筆很像的」的機會也跟著變多（最高分
#    從 0.4653 升到 0.5504）。這正是當時註解裡擔心的那件事，它真的發生了。
#    門檻不能在小索引上量完就當數。
#
# 取 0.60：三個離題問句全部歸零，離最高的離題分數還有 0.05 的餘裕。
#
# 這個語料全是投資詐騙案件，所以相關問句對「幾乎每一筆」都有 0.6 以上的
# 相似度（0.60 之下仍有 9,773 筆）。也就是說這個門檻的工作**不是排序**，
# 是「擋掉根本不該進來的問句」—— 排序交給 k 與去重。
#
# 為什麼不繼續用 0.0：離題問句在 bge-m3 上仍然拿得到 0.46，0.0 等於把那三筆
# 不相關的案例當成答案回給使用者 —— 而它們每一筆都帶著案例編號與縣市，
# 看起來非常像真的。撈回太多在這個專案不是中性的代價。
#
# 有過濾條件（facets）時 retrieve() 會把這個門檻放掉，見該處註解。
DEFAULT_MIN_SCORE = 0.60

# 第 1 層退路開不開。不是手動翻的布林值 —— 問鎖定表就好，
# 那樣 MODEL_LOCK 一動這裡就跟著動，不必有人記得回來改這一行。
#
# 注意這裡**只問「鎖了沒」，不問「這台裝了套件沒」**。沒裝 ml 那組的人
# 仍然會走進第 1 層，然後被 ModelDependencyError 擋下來退到第 3 層 ——
# 那是刻意的：退路要能被觀察到有啟動，靜悄悄地不走第 1 層反而看不出來。
MODEL_READY = models.MODEL_LOCK["embedding"].is_locked


# ─────────────────────────────────────────────────────────────────────
# 向量儲存層
# ─────────────────────────────────────────────────────────────────────
#
# 為什麼不直接上 Chroma／Qdrant：那是 S12 才要決定的事，而在決定之前需要
# 一個能跑的東西。抽一層介面、先用 numpy 實作，語料真的長到需要 HNSW 時
# 多一個子類就好，上層完全不用動 —— 這個介面就是「不必現在決定」的保險。
#
# ⚠ 真要換過去，順序是「先有評測集，再換」：Chroma 預設的距離度量是 L2 不是
#   餘弦，兩邊量出來的 min_score 會全部失效。


def _import_numpy():
    """numpy 走延遲 import，跟 shared.models 的 _import_ml() 同一個做法。

    numpy 不在 pyproject 的 dependencies、也不在 dev 那組 —— 它是 ml 那組的
    torch 順便帶進來的。所以「沒裝 ml」等於「沒有 numpy」，而照 README 跑
    make install（只裝 dev + ui）的人正是這種機器，CI 也是（uv sync --extra dev）。

    🔴 這個 import 放在模組層的話，整包測試會在**收集階段**就掛掉：
       collection 期間 import 失敗，pytest 直接 Interrupted，連完全沒用到向量
       的測試都跑不了。而且模組層的 import 會繞過 tests 裡 importorskip 那層
       保護 —— 保護寫在測試裡，但炸在 import 時，根本輪不到它。

    丟 ModelDependencyError 而不是讓 ModuleNotFoundError 往上冒，是為了讓
    search() 現成的那道 except 接得住、照常退到第 3 層。語意上也正好對得上這個
    例外的定義：模型鎖了、程式也接上了，就是這台少裝套件，下一步是自己裝 extra。
    """
    try:
        import numpy
    except ImportError as exc:
        raise models.ModelDependencyError(
            "向量儲存層要用 numpy，這台沒裝。它跟著 ml 那組進來："
            "uv sync --extra ml（torch 的 wheel 依平台而異，見 pyproject.toml 的註解）。"
            "只是要跑測試或用關鍵字檢索的話不必裝 —— 查詢會自動退到第 3 層。"
        ) from exc
    return numpy


class NumpyStore:
    """全部放在記憶體、存成單一個 .npz。

    向量是正規化過的，所以 scores 就是矩陣乘法（餘弦相似度）。
    cases 與向量永遠等長、位置對齊。
    """

    def __init__(self, model: str, dim: int, path: Path | None = None) -> None:
        # 這一行是「沒裝 numpy」唯一的守門處 —— 其餘方法都要先有實例才到得了。
        np = _import_numpy()
        self.model = model  # 哪個嵌入模型算的
        self.dim = dim
        self.path = path
        self._cases: list[Case] = []
        self._vecs = np.zeros((0, dim), dtype="float32")

    @property
    def cases(self) -> list[Case]:
        return self._cases

    @property
    def vectors(self):
        """整個索引的向量。給門檻量測用。"""
        return self._vecs

    def __len__(self) -> int:
        return len(self._cases)

    def add(self, cases: list[Case], vecs) -> int:
        np = _import_numpy()
        vecs = np.asarray(vecs, dtype="float32")
        if len(cases) != len(vecs):
            raise ValueError("案例與向量數量對不起來")
        if len(cases) == 0:
            return 0
        if vecs.shape[1] != self.dim:
            # 設定裡的維度跟模型實際吐出來的對不上。當場報錯，不要默默存進去
            # —— 維度不合的向量算出來的相似度是沒有意義的數字。
            raise ValueError(
                f"維度對不上：設定說 {self.dim}，{self.model} 實際吐出 {vecs.shape[1]}"
            )
        self._cases = self._cases + list(cases)
        self._vecs = np.vstack([self._vecs, vecs])
        return len(cases)

    def remove_source(self, source: str) -> int:
        """刪掉某個來源的所有案例，回傳刪了幾筆。

        混來源時（165 ＋ 其他）需要這個：某一份來源要重抓時只重算它那部分。
        """
        if not source:
            raise ValueError("不能刪 source 為空的案例")
        keep = [i for i, c in enumerate(self._cases) if c.source != source]
        removed = len(self._cases) - len(keep)
        if removed:
            self._cases = [self._cases[i] for i in keep]
            self._vecs = self._vecs[keep]
        return removed

    def scores(self, qv):
        np = _import_numpy()
        if len(self._cases) == 0:
            return np.zeros(0, dtype="float32")
        return self._vecs @ qv

    def sources(self) -> set[str]:
        return {c.source for c in self._cases if c.source}

    # ── 持久化 ──────────────────────────────────────────────────────
    def save(self) -> None:
        if self.path is None:
            return
        np = _import_numpy()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            self.path,
            vecs=self._vecs,
            cases=np.array(json.dumps([_case_as_dict(c) for c in self._cases], ensure_ascii=False)),
            meta=np.array(json.dumps({"model": self.model, "dim": self.dim})),
        )

    def load(self) -> bool:
        """讀回上次存的。回傳有沒有讀成功。

        ⚠ 換了嵌入模型或維度就**必須**整個重算 —— 不同模型的向量空間不相通，
          混在一起算出來的相似度是沒有意義的數字，而且不會報錯。所以這裡拿
          meta 比對，對不上就當作沒有快取。

          這件事在本專案格外重要：S3 鎖定嵌入模型時填的 revision 一旦變動，
          五個人各自的索引都要重建，否則分數不能互相比較。
        """
        if self.path is None or not self.path.exists():
            return False
        np = _import_numpy()
        z = np.load(self.path, allow_pickle=False)
        meta = json.loads(str(z["meta"]))
        if meta.get("model") != self.model or meta.get("dim") != self.dim:
            return False
        self._cases = [Case(**d) for d in json.loads(str(z["cases"]))]
        self._vecs = z["vecs"]
        return True


def _case_as_dict(c: Case) -> dict:
    return {
        "case_id": c.case_id,
        "text": c.text,
        "source": c.source,
        "label": c.label,
        "date": c.date,
        "county": c.county,
        "facets": c.facets,
    }


# ─────────────────────────────────────────────────────────────────────
# 檢索器
# ─────────────────────────────────────────────────────────────────────


class Retriever:
    """檢索器。狀態全部掛在實例上，不是模組層全域 ——

    所以同一個 process 裡可以同時存在好幾個，測試也能各建各的、互不污染。
    """

    # 🔴 這一行不能寫死字串，要從 MODEL_LOCK 取。
    #
    # 它的用途是 NumpyStore 的快取鍵：換了模型或換了版本，磁碟上那份索引就
    # 必須整個重算，因為不同模型的向量空間不相通 —— 混著算出來的相似度是
    # 沒有意義的數字，而且不會報錯。
    #
    # 原本這裡是空字串，那讓整個比對形同虛設：任何索引檔的 meta 都會是 ""，
    # 跟任何模型都「對得上」，於是換模型之後會安靜地沿用舊向量。
    # 從鎖定表取就不會有這個問題 —— #33 之後 revision 一變，快取自動失效。
    embed_model = f"{models.MODEL_LOCK['embedding'].name}@{models.MODEL_LOCK['embedding'].revision}"
    dim = 1024  # bge-m3 是 1024；換模型要跟著改，store.add() 會擋下不一致

    def __init__(
        self,
        cases: list[Case] | None = None,
        min_score: float | None = None,
        store_path: Path | None = None,
    ) -> None:
        """cases 不傳就讀自己切好的那一份（M1 的產出）。

        store_path 不傳就不落地 —— 測試預設如此，不碰磁碟。
        """
        self._seed = cases
        self._store: NumpyStore | None = None
        self.store_path = store_path
        self.min_score = DEFAULT_MIN_SCORE if min_score is None else min_score

    # ── 延遲載入：建構不做重活，第一次用到才算 ──────────────────────
    @property
    def store(self) -> NumpyStore:
        if self._store is None:
            self._store = self._build_store()
        return self._store

    @property
    def cases(self) -> list[Case]:
        return self.store.cases

    @property
    def indexed(self) -> bool:
        """索引算好了沒。給 health() 與預熱用 —— 建構出 Retriever 不代表
        索引就算好了，這兩件事的成本差好幾個數量級。"""
        return self._store is not None

    def filters_for(self, question: str) -> dict[str, list[str]]:
        """問句抽得出哪些精確過濾條件。

        獨立成一個方法是因為 M5 也需要知道：篩選過的結果代表「這就是全部
        符合的」，而那件事得講給模型聽 —— 它自己判斷不出來（見 facets.describe）。
        """
        return parse_query(question)

    def terms_for(self, question: str) -> list[str]:
        """問句裡出現的那幾個詞（使用者原本打的字）。給重問時補齊省略式問句。"""
        return terms_in(question)

    # ── 第 1 層退路：向量 ──────────────────────────────────────────
    def embed(self, texts: list[str]) -> list[list[float]]:
        """一律走 shared.models（規矩一）—— 五個人必須用同一組模型。

        模型未鎖定時 shared.models.embed() 會丟 ModelNotSelectedError，
        呼叫端要接住並落到第 3 層，不要讓它炸到使用者面前。
        """
        return models.embed(texts)

    def _build_store(self) -> NumpyStore:
        store = NumpyStore(self.embed_model, self.dim, self.store_path)
        if not store.load():  # 沒有索引檔，或模型／維度對不上
            seed = self._seed if self._seed is not None else load_local()
            if seed:
                store.add(seed, self.embed([c.text for c in seed]))
        store.save()
        return store

    def retrieve(
        self,
        question: str,
        k: int = 5,
        min_score: float | None = None,
        filters: dict[str, list[str]] | None = None,
    ) -> list[tuple[Case, float]]:
        """回傳 k 筆，同一個標籤只留最高分那筆。

        先看問句有沒有可以精確過濾的條件（類型、管道、付款方式、對象），有的話
        就先篩再排序。這是為了「篩選型」問題 —— 問「Facebook 上有哪些投資詐騙」
        時，純語意相似度只會給你最像的前 k 個，沒辦法保證「全部」。

        有過濾條件時會放掉 min_score 和 k：篩選已經保證相關，分數門檻反而會
        誤殺；k 也要放開，不然問「有哪些」只給 5 個。

        ⚠ 「放掉 min_score」有個副作用，來源那套踩過：離題問句只要碰巧含有一個
          詞彙表裡的詞，就會被當成篩選型問句而整條繞過門檻。這個語料的詞彙表詞
          更常見（「投資」「廣告」「轉帳」），所以**更容易踩到**。S12 要處理。
        """
        if min_score is None:
            min_score = self.min_score
        if filters is None:
            filters = self.filters_for(question)

        cases = self.cases
        if not cases:
            return []

        qv = self.embed([question])[0]
        np = _import_numpy()
        scores = self.store.scores(np.asarray(qv, dtype="float32"))

        if filters:
            cand = [i for i in range(len(cases)) if match(cases[i].facets, filters)]
            min_score = 0.0  # 篩過了，不再用分數擋
            k = max(k, len({cases[i].label for i in cand}))  # 要給全部，不是前 k 個
            order = sorted(cand, key=lambda i: -scores[i])
        else:
            order = np.argsort(-scores)

        out: list[tuple[Case, float]] = []
        seen: set[str] = set()
        for i in order:
            if scores[i] < min_score:
                break  # 已排序，低於門檻後面不用看了
            c = cases[i]
            key = c.label or c.case_id
            if key in seen:
                continue
            seen.add(key)
            out.append((c, float(scores[i])))
            if len(out) == k:
                break
        return out


# ─────────────────────────────────────────────────────────────────────
# 第 3 層退路：關鍵字重疊
# ─────────────────────────────────────────────────────────────────────


def _overlap_score(query: str, text: str) -> float:
    """最笨的相似度：字元 2-gram 重疊。這是 baseline，S12 的向量檢索要贏過它。"""
    if not query or not text:
        return 0.0
    grams_q = {query[i : i + 2] for i in range(len(query) - 1)}
    grams_t = {text[i : i + 2] for i in range(len(text) - 1)}
    if not grams_q:
        return 0.0
    return len(grams_q & grams_t) / len(grams_q)


def _keyword_search(query: str, pool: list[Case], top_k: int) -> list[tuple[Case, float]]:
    scored = sorted(
        ((_overlap_score(query, c.text), c) for c in pool),
        key=lambda pair: pair[0],
        reverse=True,
    )
    return [(c, s) for s, c in scored[:top_k] if s > 0]


# ─────────────────────────────────────────────────────────────────────
# 對外入口
# ─────────────────────────────────────────────────────────────────────


def build_index() -> None:
    """建自己的向量庫（make index MODULE=c_tbd）。

    向量庫是你自己建的，不進版控（幾百 MB）—— 別人要重現時自己跑這行。
    """
    cases = load_local()
    if not cases:
        raise FileNotFoundError("還沒有自己的語料。先跑 M1（S9）切出自己那一份。")
    r = Retriever(cases=cases, store_path=INDEX_PATH)
    r.store  # noqa: B018 —— 取用屬性就會觸發建索引並落地


def search(query: str, *, top_k: int = 5, cases: list[Case] | None = None) -> list[SimilarCase]:
    """檢索相似案例。回傳的每一筆都帶案例編號，節錄一律先去識別化。

    這是外殼與 M4／M5 唯一該呼叫的入口。三層退路在這裡收斂：
    向量檢索失敗就自動落到關鍵字，不讓使用者看到例外。

    「失敗」有兩種，兩種都要接住：

      · ModelNotSelectedError   五個人還沒決議（現在嵌入已經鎖了，所以不會）
      · ModelDependencyError    這台沒裝 ml 那組套件（現在最常見的就是這個）

    漏接第二種的後果是：沒裝 torch 的組員一查詢就看到例外，而不是安靜地
    用關鍵字檢索 —— 而那正是三層退路存在的意義。

    反過來，第 1 層成功但沒有任何一筆過 min_score **不是失敗**，會回空清單，
    不落到關鍵字。細節見下面哨兵那段註解。
    """
    pool = cases if cases is not None else load_local()
    if not pool:
        return []

    # 🔴 哨兵用 None，不能用空清單。
    #
    # 「第 1 層失敗」與「第 1 層成功但沒東西過 min_score」是兩件事，混為一談
    # 會讓 min_score 永遠沒機會生效：它把離題問句擋下來、回了空清單，反而正好
    # 觸發退路，交給完全沒有門檻的第 3 層 —— 而第 3 層保證回得出東西。
    #
    # 實測（10,051 筆索引、min_score=0.6）：「今天天氣如何」沒有任何一筆過門檻，
    # 修掉之前 search() 會回三筆投資詐騙案例給使用者。
    #
    # 退路是為了接住失敗，不是為了保證一定有結果。查不到就是查不到 ——
    # 外殼對「尚未涵蓋」本來就有處理（通用建議＋165 導流）。
    hits: list[tuple[Case, float]] | None = None
    if MODEL_READY:
        try:
            hits = Retriever(cases=pool, store_path=INDEX_PATH).retrieve(query, k=top_k)
        except (models.ModelNotSelectedError, models.ModelDependencyError):
            hits = None  # 落到第 3 層。這是預期內的狀態，不是錯誤
        except FileNotFoundError:
            # 索引還沒建（make index MODULE=c_tbd 還沒跑過）。也是預期內的。
            hits = None
    if hits is None:
        hits = _keyword_search(query, pool, top_k)

    out: list[SimilarCase] = []
    for case, score in hits:
        safe = deid.mask(case.text)  # 絕不投影真實受害者原文
        out.append(
            SimilarCase(
                case_id=case.case_id,
                source=case.source,
                excerpt=safe.text[:120],
                date=case.date or None,
                county=case.county or None,
                score=round(min(1.0, max(0.0, score)), 3),
                label=case.label or None,
            )
        )
    return out


def reciprocal_rank_fusion(*rankings: list[str], k: int = 60) -> list[str]:
    """RRF：兩邊都排前面的，最後就排前面。S12 要用它合成 BM25 與向量搜尋。

    接縫留在這裡 —— 來源那套只有語意那一條，BM25 是本專案要補的部分。
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return [doc for doc, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)]
