"""M1 語料處理：從全隊那份 19 萬筆裡，把屬於自己的那幾千筆撈出來（S9）。

五個人的做法可以完全不一樣。這裡是最簡可跑版本：
讀 parquet → 依標籤與平台關鍵詞篩選 → 清理 → 存成自己的資料檔。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
LOCAL_CORPUS = MODULE_DIR / "data" / "cases.jsonl"
# 共用語料放共用雲端硬碟，不進版控（§1.6）
SHARED_CORPUS = Path(os.getenv("CORPUS_PARQUET", "data/cases_165.parquet"))


@dataclass
class Case:
    """一筆 165 案例。六個欄位：編號、日期、縣市、縣市代號、內文、標籤。"""

    case_id: str
    text: str
    label: str = ""
    date: str = ""
    county: str = ""
    tokens: list[str] = field(default_factory=list)


def load_local() -> list[Case]:
    """讀自己切好的那一份。沒有就回空的 —— 不要在這裡爆炸。"""
    if not LOCAL_CORPUS.exists():
        return []
    cases: list[Case] = []
    for line in LOCAL_CORPUS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        cases.append(
            Case(
                case_id=str(raw.get("case_id", "")),
                text=raw.get("text", ""),
                label=raw.get("label", ""),
                date=raw.get("date", ""),
                county=raw.get("county", ""),
            )
        )
    return cases


def build_subset(labels: list[str], platform_terms: list[str]) -> int:
    """從共用語料切出自己的那一份，寫進 data/cases.jsonl。

    TODO(S9)：接上 polars 讀 parquet、接上 CKIP 斷詞、把清理規則寫完整。
    現在只有骨架 —— 這支要能重現產出自己的資料檔才算做完 S9。
    """
    if not SHARED_CORPUS.exists():
        raise FileNotFoundError(
            f"找不到共用語料 {SHARED_CORPUS}。語料放共用雲端硬碟，"
            f"路徑用環境變數 CORPUS_PARQUET 指定（見 data/README.md）。"
        )
    raise NotImplementedError("S9 的工作：寫完篩選、清理、斷詞，並把統計填進 pack.yaml")


def stats(cases: list[Case]) -> dict[str, int]:
    return {
        "total": len(cases),
        "labels": len({c.label for c in cases if c.label}),
        "counties": len({c.county for c in cases if c.county}),
    }
