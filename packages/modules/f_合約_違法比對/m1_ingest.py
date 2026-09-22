"""輸入 + 去識別化。

合約文字必有當事人姓名/身分證/金額，一律先過 shared.deid.mask() 拿到
MaskedText，跟 A-E 五個模組同一條硬規則——原文不進雲端是程式強制的，
call_cloud() 只收 MaskedText。

v1 只吃貼上的純文字；掃描件 OCR 留到 v2。
"""

from __future__ import annotations

from pathlib import Path

from contracts import MaskedText
from shared.deid import mask


def load_text(path: Path) -> str:
    """讀一份合約檔案。v1 只支援純文字檔。"""
    return path.read_text(encoding="utf-8")


def ingest(raw_text: str) -> MaskedText:
    """讀入合約原文，回傳去識別化過的版本。"""
    return mask(raw_text)
