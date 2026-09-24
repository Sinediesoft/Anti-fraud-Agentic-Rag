"""M3 語意檢索：做出「講白話也找得到」的搜尋（S12）。

完成型態是：關鍵字搜尋（BM25）跟語意搜尋同時跑 → RRF 合成排名 → 重排序模型重排前 50 筆。
目前 BM25 與 RRF 已實作，語意那一條在向量庫建好之後自動接上，重排序等 S3 選出模型。

硬規則：沒有出處的結果不准回傳 —— 每一筆都要帶案例編號跟日期縣市。

## 為什麼是 BM25 + 向量的 RRF，而不是只用其中一邊

在 9,167 筆 Threads 購物案例上用 40 題口語查詢實測過（2026-09-22）：

    TF-IDF 字元 n-gram   Top-1 62.5%
    語意向量（bge-small） Top-1 70.0%
    兩者 RRF 合成         Top-1 82.5%

關鍵不是誰比較強，是**兩邊錯的題目不重疊**。詞彙比對抓得住固定話術
（「未完成實名認證」「賣貨便」這些詞在案例裡就是這樣寫），語意向量抓得住
換句話說（「把卡片寄過去」對應到寄提款卡，字面完全不同）。

用 RRF 而不是分數加權，是因為兩邊的分數尺度差很遠且都沒校準
（實測 TF-IDF 落在 0.09–0.14、bge 落在 0.68–0.78）。RRF 只看名次不看分數，
不需要多引入一個沒有依據的正規化參數。

## 為什麼 BM25 自己寫

專案核心依賴只有 pydantic 與 pyyaml，`pyproject.toml` 是凍結的共管路徑，
不能為了檢索去加 scikit-learn。BM25 本來就是幾十行的東西，純標準庫寫得完。

中文沒有空白分詞，所以 term 取**字元 2-gram**：不需要詞典、對新詞
（「賣貨便」「無卡提款」）天然友善，這在中文檢索是標準做法。
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from contracts import SimilarCase
from shared import deid, models

from .m1_corpus import Case, load_local

INDEX_DIR = Path(__file__).resolve().parent / "index"
VECTORS = INDEX_DIR / "vectors.npy"
CASE_IDS = INDEX_DIR / "case_ids.json"

EMBED_BATCH = 32
POOL = 50  # 每一路各取前 N 名進入 RRF

# RRF 的 K 與各路權重。Cormack et al. 2009 的原始建議是 K=60、兩路等權，
# 那個設定假設兩路品質相當 —— 這裡不是。2026-09-23 向量庫建好後實測（20 題）：
#
#     只用 BM25             Top-1 0.450  R@5 0.650  R@10 0.650
#     只用語意向量           Top-1 0.050  R@5 0.300  R@10 0.300
#     K=60 等權（原設定）     Top-1 0.400  R@5 0.600  R@10 0.700
#     K=10 + BM25 權重 2    Top-1 0.450  R@5 0.700  R@10 0.750
#
# 等權融合下來比單用 BM25 還差 —— 弱的那一路把強的擠掉了。K 調小會放大前幾名
# 的差距，權重則直接反映兩路實測的落差；兩者一起用，融合才終於不比單路差。
#
# 向量為什麼這麼弱：這批案例高度同質（71.5% 同一個 label，都在講賣貨便加實名
# 認證），語意上本來就分不開，區辨資訊幾乎都在專有名詞上，那是 BM25 的主場。
#
# ⚠ 只有 20 題，而且那 20 題是從 gold 案例改寫的、保留了專有名詞，本來就對詞彙
# 比對有利。這組參數只能說「融合不再比單路差」，不等於調好了。要有把握得先有
# 一份專門的檢索評估集（口語、避開專有名詞、每題標 gold 案例編號）。
RRF_K = 10
RRF_WEIGHTS = {"bm25": 2.0, "dense": 1.0}

# BM25 參數。k1 控制詞頻飽和、b 控制長度正規化，兩個都是文獻通用值。
BM25_K1 = 1.5
BM25_B = 0.75

_NOISE = re.compile(r"[^一-鿿A-Za-z0-9]+")


def _terms(text: str) -> list[str]:
    """字元 2-gram。英數字連續段落當成單一 term，不切碎。"""
    out: list[str] = []
    for seg in _NOISE.split(text):
        if not seg:
            continue
        if seg.isascii():
            out.append(seg.lower())
            continue
        if len(seg) > 1:
            out.extend(seg[i : i + 2] for i in range(len(seg) - 1))
        else:
            out.append(seg)
    return out


@dataclass
class _Bm25:
    """倒排索引。建一次用很多次，所以放模組層快取。"""

    idf: dict[str, float]
    postings: dict[str, list[tuple[int, int]]]  # term → [(doc 索引, 詞頻)]
    lengths: list[int]
    avg_len: float
    case_ids: list[str]

    @classmethod
    def build(cls, cases: list[Case]) -> _Bm25:
        postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        lengths: list[int] = []
        for i, case in enumerate(cases):
            tf = Counter(_terms(case.text))
            lengths.append(sum(tf.values()) or 1)
            for term, n in tf.items():
                postings[term].append((i, n))

        total = len(cases)
        # 加 0.5 平滑；max(…, 1e-9) 擋掉「幾乎每篇都有」的詞算出負數
        idf = {
            term: max(math.log((total - len(plist) + 0.5) / (len(plist) + 0.5) + 1.0), 1e-9)
            for term, plist in postings.items()
        }
        return cls(
            idf=idf,
            postings=dict(postings),
            lengths=lengths,
            avg_len=sum(lengths) / max(total, 1),
            case_ids=[c.case_id for c in cases],
        )

    def scores(self, query: str) -> dict[str, float]:
        acc: dict[int, float] = defaultdict(float)
        for term in set(_terms(query)):
            plist = self.postings.get(term)
            if not plist:
                continue
            idf = self.idf[term]
            for doc, tf in plist:
                norm = 1 - BM25_B + BM25_B * self.lengths[doc] / self.avg_len
                acc[doc] += idf * (tf * (BM25_K1 + 1)) / (tf + BM25_K1 * norm)
        return {self.case_ids[doc]: score for doc, score in acc.items()}


_INDEX: _Bm25 | None = None
_INDEX_FOR: int = -1


def _bm25(cases: list[Case]) -> _Bm25:
    """語料筆數變了就重建。這比每次 search 都重掃全部案例快兩個數量級。"""
    global _INDEX, _INDEX_FOR
    if _INDEX is None or _INDEX_FOR != len(cases):
        _INDEX = _Bm25.build(cases)
        _INDEX_FOR = len(cases)
    return _INDEX


def _overlap_score(query: str, text: str) -> float:
    """最笨的相似度：字元 2-gram 重疊。留著當退路與對照組。"""
    if not query or not text:
        return 0.0
    grams_q = {query[i : i + 2] for i in range(len(query) - 1)}
    grams_t = {text[i : i + 2] for i in range(len(text) - 1)}
    if not grams_q:
        return 0.0
    return len(grams_q & grams_t) / len(grams_q)


def _vector_scores(query: str, pool: list[Case]) -> dict[str, float] | None:
    """有向量庫就用，沒有就回 None 讓呼叫端只走 BM25。

    向量庫不進版控（見 build_index），所以沒建過索引的人跑起來一樣不會壞，
    只是少了「換句話說也找得到」那一半能力。
    """
    if not (VECTORS.exists() and CASE_IDS.exists()):
        return None
    try:
        import numpy as np

        arr = np.load(VECTORS)
        ids = json.loads(CASE_IDS.read_text(encoding="utf-8"))
        if len(ids) != arr.shape[0]:
            return None
        q = np.asarray(models.embed([query])[0], dtype="float32")
        q /= np.linalg.norm(q)
        sims = arr @ q
    except Exception:
        # 向量庫壞了或模型叫不動都不該讓檢索整個掛掉 —— 退回只用 BM25
        return None
    return dict(zip(ids, (float(x) for x in sims), strict=False))


def build_index(*, batch: int = EMBED_BATCH) -> int:
    """建自己的向量庫（make index MODULE=e_tbd）。

    向量庫是你自己建的，不進版控（幾百 MB）—— 別人要重現時自己跑這行。
    需要 `uv sync --extra ml`（torch + transformers），bge-m3 會在第一次執行時下載。

    案例整筆不切塊：165 的敘述中位數 237 字，本來就在一個切塊的長度內，
    硬切反而會把「先看到貼文、再加 LINE、最後匯款」這種前後關係拆掉。

    存成 numpy 檔而不是向量資料庫：9,167 筆 × 1024 維只有 36 MB，
    而 Chroma/Qdrant 都不在專案相依裡，pyproject.toml 是凍結的共管路徑。

    TODO(S12)：法規文件要切塊（每 300–500 字一段、前後重疊 50 字），案例維持整筆。
    """
    cases = load_local()
    if not cases:
        raise FileNotFoundError("還沒有自己的語料。先跑 M1（S9）切出自己那一份。")

    import numpy as np

    vecs: list[list[float]] = []
    for i in range(0, len(cases), batch):
        chunk = cases[i : i + batch]
        vecs.extend(models.embed([c.text for c in chunk]))
        print(f"  已編碼 {min(i + batch, len(cases)):>6}/{len(cases)}", flush=True)

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(vecs, dtype="float32")
    # 先正規化，之後比對就是單純的內積，省一次除法也少一處出錯的地方
    arr /= np.linalg.norm(arr, axis=1, keepdims=True)
    np.save(VECTORS, arr)
    CASE_IDS.write_text(
        json.dumps([c.case_id for c in cases], ensure_ascii=False), encoding="utf-8"
    )
    return len(cases)


def search(query: str, *, top_k: int = 5, cases: list[Case] | None = None) -> list[SimilarCase]:
    """檢索相似案例。回傳的每一筆都帶案例編號，節錄一律先去識別化。

    BM25 與語意向量各取前 POOL 名，用 RRF 合成。沒有向量庫時只走 BM25。
    """
    pool = cases if cases is not None else load_local()
    if not pool or not query.strip():
        return []

    by_id = {c.case_id: c for c in pool}
    rankings: list[list[str]] = []
    weights: list[float] = []

    bm25 = _bm25(pool).scores(query)
    if bm25:
        rankings.append(sorted(bm25, key=lambda cid: -bm25[cid])[:POOL])
        weights.append(RRF_WEIGHTS["bm25"])

    dense = _vector_scores(query, pool)
    if dense:
        rankings.append(sorted(dense, key=lambda cid: -dense[cid])[:POOL])
        weights.append(RRF_WEIGHTS["dense"])

    if not rankings:
        return []

    fused = reciprocal_rank_fusion(*rankings, weights=weights)
    # 單路時 RRF 分數等同名次倒數，資訊量不如原始分數，所以拿原始分數當 score
    single = bm25 if len(rankings) == 1 else None
    best = max(single.values()) if single else 0.0

    out: list[SimilarCase] = []
    for case_id in fused[:top_k]:
        case = by_id.get(case_id)
        if case is None:
            continue
        score = (single[case_id] / best) if single and best > 0 else _rrf_norm(fused, case_id)
        safe = deid.mask(case.text)
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


def _rrf_norm(fused: list[str], case_id: str) -> float:
    """把 RRF 的名次換算成 0–1 的分數，只為了讓介面有個可讀的數字。

    RRF 的原始值（1/(60+rank) 的和）落在 0.03 附近，直接放進 score 欄位
    會讓使用者以為「相似度只有 3%」。名次才是 RRF 真正的輸出。
    """
    try:
        rank = fused.index(case_id) + 1
    except ValueError:
        return 0.0
    return max(0.0, 1.0 - (rank - 1) / max(len(fused), 1))


def reciprocal_rank_fusion(
    *rankings: list[str], k: int = RRF_K, weights: list[float] | None = None
) -> list[str]:
    """RRF：兩邊都排前面的，最後就排前面。

    用它而不是分數加權，是因為 BM25 與餘弦相似度的尺度差很遠且都沒校準，
    要加權就得先正規化，而正規化的參數本身沒有依據。RRF 只看名次。

    `weights` 加在名次上而不是分數上，所以仍然不需要正規化任何東西 ——
    它表達的是「這一路的名次比較可信」，不是「這一路的分數比較高」。
    不給就是等權（原始論文的設定）。
    """
    if weights is None:
        weights = [1.0] * len(rankings)
    scores: dict[str, float] = {}
    for ranking, weight in zip(rankings, weights, strict=True):
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + weight / (k + rank)
    return [doc for doc, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)]
