"""M3 語意檢索：做出「講白話也找得到」的搜尋（S12）。

完成型態是：關鍵字搜尋（BM25）跟語意搜尋同時跑 → RRF 合成排名 → 重排序模型重排前 50 筆。
現在是最簡可跑版本：純關鍵字重疊計分。等 S3 鎖定嵌入模型、S12 建好向量庫再換掉。

硬規則：沒有出處的結果不准回傳 —— 每一筆都要帶案例編號跟日期縣市。
"""

from __future__ import annotations

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


def build_index() -> None:
    """建自己的向量庫（make index MODULE=…）。

    向量庫是你自己建的，不進版控（幾百 MB）—— 別人要重現時自己跑這行。

    TODO(S12)：切塊（案例整筆不切；法規每 300–500 字切一段、前後重疊 50 字）
               → shared.models.embed 取向量 → 存進 Chroma 或 Qdrant。
    """
    cases = load_local()
    if not cases:
        raise FileNotFoundError("還沒有自己的語料。先跑 M1（S9）切出自己那一份。")
    models.embed([c.text for c in cases])  # 模型未鎖定時會在這裡明確報錯
    raise NotImplementedError("S12 的工作：把向量寫進向量庫")


def search(query: str, *, top_k: int = 5, cases: list[Case] | None = None) -> list[SimilarCase]:
    """檢索相似案例。回傳的每一筆都帶案例編號，節錄一律先去識別化。"""
    pool = cases if cases is not None else load_local()
    if not pool:
        return []

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
