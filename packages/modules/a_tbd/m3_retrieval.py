"""M3 語意檢索：做出「講白話也找得到」的搜尋（S12）。

完成型態是：關鍵字搜尋（BM25）跟語意搜尋同時跑 → RRF 合成排名 → 重排序模型重排前 50 筆。
現在是最簡可跑版本：純關鍵字重疊計分。等 S3 鎖定嵌入模型、S12 建好向量庫再換掉。

硬規則：沒有出處的結果不准回傳 —— 每一筆都要帶案例編號跟日期縣市。
"""

from __future__ import annotations

import json
from pathlib import Path

from contracts import SimilarCase
from shared import deid, models

from .m1_corpus import Case, load_local

INDEX_DIR = Path(__file__).resolve().parent / "index"


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
