"""M3 語意檢索：做出「講白話也找得到」的搜尋（S12）。

完成型態是：關鍵字搜尋（BM25）跟語意搜尋同時跑 → RRF 合成排名 → 重排序模型重排前 50 筆。
現在是最簡可跑版本：純關鍵字重疊計分。等 S3 鎖定嵌入模型、S12 建好向量庫再換掉。

硬規則：沒有出處的結果不准回傳 —— 每一筆都要帶案例編號跟日期縣市。
"""

from __future__ import annotations

import json
from pathlib import Path

from contracts import PackSpec, SimilarCase
from shared import deid, models

from .m1_corpus import Case, load_local

MODULE_DIR = Path(__file__).resolve().parent
INDEX_DIR = MODULE_DIR / "index"

# 語料池被關鍵字篩到比這個數還少時就不篩了。
#
# 篩選是為了把不相干的案例擋在向量比對之外，不是為了把召回砍光 —— 問句用的
# 詞剛好在語料裡很罕見時（例如只打了「穩賺不賠」，覆蓋 20.3%），硬篩會讓
# 向量層沒東西可排。留 top_k 的 4 倍當作可排序的餘裕。
_MIN_POOL_AFTER_FILTER_FACTOR = 4


def _pack() -> PackSpec:
    return PackSpec.load(MODULE_DIR / "pack.yaml")


def _keywords() -> tuple[list[str], list[str]]:
    """回傳（平台詞，手法詞）。手法詞含 route_terms 與官方標籤用語。

    分開回傳是因為兩者在第一道門檻的職責不同，見 _gate_keyword()。
    """
    pack = _pack()
    platform = [t for t in pack.platform_terms if t]
    tactic = [t for t in list(pack.route_terms) + list(pack.labels_canon) if t]
    return platform, tactic


def _hits(terms: list[str], text: str) -> list[str]:
    return [t for t in terms if t in text]


def _gate_keyword(query: str, pool: list[Case], top_k: int) -> list[Case] | None:
    """第一道門檻：關鍵字。回 None 代表這題不該有答案。

    ① 問句一個平台詞、一個手法詞都沒命中 -> 回 None。
       擋的是「今天天氣如何」「我家的貓不吃飯」這種 —— 2026-09-21 實測，
       在沒有這道門檻的版本裡它們一樣拿回滿滿 5 筆案例，每一筆都帶著案例
       編號與縣市，看起來跟真的一模一樣。那比查不到更糟。

    ② 用問句命中的**手法詞**篩語料池。
       平台詞刻意不參與篩選：M1 切語料時就是用平台詞篩出來的，這 1,000 筆
       100% 都含平台詞（實測），拿它篩等於沒篩。

    ③ 篩完太少就不篩（見 _MIN_POOL_AFTER_FILTER_FACTOR）。
    """
    platform, tactic = _keywords()
    q_platform = _hits(platform, query)
    q_tactic = _hits(tactic, query)
    if not q_platform and not q_tactic:
        return None

    if not q_tactic:
        return pool  # 只說了平台沒說手法 —— 篩不動，交給第二道門檻排序
    narrowed = [c for c in pool if any(t in c.text for t in q_tactic)]
    if len(narrowed) < top_k * _MIN_POOL_AFTER_FILTER_FACTOR:
        return pool
    return narrowed


def _overlap_score(query: str, text: str) -> float:
    """最笨的相似度：字元 2-gram 重疊。這是 baseline，S12 要贏過它。"""
    if not query or not text:
        return 0.0
    grams_q = {query[i : i + 2] for i in range(len(query) - 1)}
    grams_t = {text[i : i + 2] for i in range(len(text) - 1)}
    if not grams_q:
        return 0.0
    return len(grams_q & grams_t) / len(grams_q)


VECTORS = INDEX_DIR / "vectors.npy"
CASE_IDS = INDEX_DIR / "case_ids.json"
EMBED_BATCH = 32


def build_index(*, batch: int = EMBED_BATCH) -> int:
    """建自己的向量庫（make index MODULE=…）。

    向量庫是你自己建的，不進版控（幾百 MB）—— 別人要重現時自己跑這行。

    案例整筆不切塊：165 的敘述中位數 236 字，本來就在一個切塊的長度內，
    硬切反而會把「先加 LINE、再匯款」這種前後關係拆掉。

    存成 numpy 檔而不是 Chroma / Qdrant：那兩個都不在專案相依裡，而
    pyproject.toml 是凍結的共管路徑。1000 筆 × 1024 維只有 4 MB，
    用不著向量資料庫。真的要換是 S12 的決定。

    TODO(S12)：法規文件要切塊（每 300–500 字一段、前後重疊 50 字），
               案例維持整筆。向量庫選型也在那時決定。
    """
    cases = load_local()
    if not cases:
        raise FileNotFoundError("還沒有自己的語料。先跑 M1（S9）切出自己那一份。")

    import numpy as np

    vecs: list[list[float]] = []
    for i in range(0, len(cases), batch):
        chunk = cases[i : i + batch]
        vecs.extend(models.embed([c.text for c in chunk]))
        print(f"  已編碼 {min(i + batch, len(cases)):>5}/{len(cases)}", flush=True)

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(vecs, dtype="float32")
    # 先正規化，之後比對就是單純的內積，省一次除法也少一處出錯的地方
    arr /= np.linalg.norm(arr, axis=1, keepdims=True)
    np.save(VECTORS, arr)
    CASE_IDS.write_text(
        json.dumps([c.case_id for c in cases], ensure_ascii=False), encoding="utf-8"
    )
    return len(cases)


def _vector_scores(query: str, pool: list[Case]) -> dict[str, float] | None:
    """有向量庫就用，沒有就回 None 讓呼叫端退回字元重疊。"""
    if not (VECTORS.exists() and CASE_IDS.exists()):
        return None
    try:
        import numpy as np

        arr = np.load(VECTORS)
        ids = json.loads(CASE_IDS.read_text(encoding="utf-8"))
        q = np.asarray(models.embed([query])[0], dtype="float32")
        q /= np.linalg.norm(q)
        sims = arr @ q
    except Exception:
        # 向量庫壞了或模型叫不動都不該讓檢索整個掛掉 —— 退回字元重疊
        return None
    return dict(zip(ids, (float(x) for x in sims), strict=False))


def search(query: str, *, top_k: int = 5, cases: list[Case] | None = None) -> list[SimilarCase]:
    """檢索相似案例。回傳的每一筆都帶案例編號，節錄一律先去識別化。"""
    pool = cases if cases is not None else load_local()
    if not pool:
        return []

    # ── 第一道門檻：關鍵字 ──────────────────────────────────────
    # 問句跟這個模組的平台 × 手法完全沾不上邊時，正確答案是「沒有」，
    # 不是「最接近的五筆」。
    gated = _gate_keyword(query, pool, top_k)
    if gated is None:
        return []
    pool = gated

    # ── 第二道門檻：現有的評分機制，原封不動 ────────────────────
    # 第一層：向量檢索。沒有向量庫（還沒建、或模型叫不動）就退回字元重疊，
    # 檢索不會因此整個失效 —— 只是變笨。
    by_id = _vector_scores(query, pool)
    if by_id is not None:
        scored = sorted(
            ((by_id.get(c.case_id, 0.0), c) for c in pool),
            key=lambda pair: pair[0],
            reverse=True,
        )
    else:
        scored = sorted(
            ((_overlap_score(query, c.text), c) for c in pool),
            key=lambda pair: pair[0],
            reverse=True,
        )

    out: list[SimilarCase] = []
    for score, case in scored[:top_k]:
        if score <= 0:
            continue
        safe = deid.mask(case.text)
        out.append(
            SimilarCase(
                case_id=case.case_id,
                source=case.source,
                excerpt=safe.text[:120],
                date=case.date or None,
                county=case.county or None,
                score=round(min(1.0, score), 3),
                label=case.label or None,
            )
        )
    return out


def reciprocal_rank_fusion(*rankings: list[str], k: int = 60) -> list[str]:
    """RRF：兩邊都排前面的，最後就排前面。S12 要用它合成 BM25 與向量搜尋。"""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return [doc for doc, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)]
