"""逐條比對判斷。

對每條條款 + m3 檢索到的法規出處，呼叫 shared.models 判斷「這條是否
疑似牴觸」與原因。語氣要保守：不確定就標低信心、建議諮詢律師，不武斷
定罪——跟 ScamModule.can_handle() 「不確定就給低分」是同一種脾氣。
"""

from __future__ import annotations

from contracts import LegalRef

from .m2_segment import Clause
from .schemas import ClauseFinding


def judge(clause: Clause, refs: list[LegalRef]) -> ClauseFinding:
    """對一條條款下判斷。

    TODO：m3 語料庫建好之後才能接上 shared.models.call_slm()。
    在那之前先回傳一個低信心、未判定的 finding，讓報告結構跑得通。
    """
    return ClauseFinding(
        clause_no=clause.clause_no,
        clause_text=clause.text,
        suspected_illegal=False,
        matched_refs=refs,
        reason="法規語料庫與判斷邏輯尚未實作，見 m3_retrieval.py 與 README.md",
        confidence="low",
    )
