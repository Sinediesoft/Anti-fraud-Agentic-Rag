"""M1 語料處理：從全隊那份共用語料裡，把屬於自己的那幾千筆撈出來（S9）。

五個人的做法可以完全不一樣。這裡是最簡可跑版本：
讀 parquet → 依標籤與平台關鍵詞篩選 → 清理 → 存成自己的資料檔。

語料來源不綁死單一來源。165 是主語料（量最大、標籤是官方標的），但這支
只認下面 Case 那個正規化後的形狀 —— 任何來源只要能映射成它就能接進來。
新增來源要做的事寫在 data/README.md，重點是兩個難題：標籤體系要對齊，
以及平台欄位多半得從內文推斷。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
LOCAL_CORPUS = MODULE_DIR / "data" / "cases.jsonl"
# 共用語料放共用雲端硬碟，不進版控（§1.6）
# 預設檔名不帶來源名 —— 混來源時這份是正規化後的合併檔。只用 165 的話，
# 把 CORPUS_PARQUET 指到 cases_165.parquet 即可。
SHARED_CORPUS = Path(os.getenv("CORPUS_PARQUET", "data/corpus.parquet"))


@dataclass
class Case:
    """一筆案例 —— 各來源正規化之後的共同形狀。

    165 提供的六個欄位（編號、日期、縣市、縣市代號、內文、標籤）剛好對得上
    前五個。別的來源缺哪個就留空，但 case_id、text、source 三個一定要有：
    沒有 case_id 與 source 就給不出出處，外殼的 guards 會擋下來。
    """

    case_id: str
    text: str
    # 混來源時 case_id 會撞號（兩份資料各自從 1 開始編很正常），
    # 所以出處是 source + case_id，不是 case_id 自己。
    source: str = "165"
    label: str = ""
    date: str = ""
    county: str = ""
    tokens: list[str] = field(default_factory=list)


def load_local() -> list[Case]:
    """讀自己切好的那一份。沒有就回空的 —— 不要在這裡爆炸。"""
    if not LOCAL_CORPUS.exists():
        return []
    cases: list[Case] = []
    # 🔴 一定要用 split("\n")，不能用 splitlines()。
    #
    # json.dumps(ensure_ascii=False) 只跳脫 \n \r \t " \\ 與 0x20 以下的控制字元，
    # **不跳脫** U+2028 LINE SEPARATOR、U+2029 PARAGRAPH SEPARATOR、U+0085 NEL。
    # 但 str.splitlines() 會在這三個上面斷行 —— 一筆合法的 JSONL 被切成兩半，
    # 讀回來就是 JSONDecodeError: Unterminated string。
    #
    # 2026-09-22 用自己這 17,764 筆實測：裡面有 2 個 U+2028，splitlines() 數出
    # 17,766 行、split("\n") 數出 17,764 行。而且它不是只讓那兩筆讀不到 ——
    # load_local() 直接拋例外，模組整個載不起來：test_module_a 從 3 紅變成 12 紅，
    # 連 can_handle 都進不去。空 data/ 的時代碰不到，一放真語料就爆。
    #
    # C 在 2026-09-20 踩過同一個坑（10,051 筆裡也是 2 個），見 c_tbd/m1_corpus.py。
    # _template 與 e_tbd 還是 splitlines()，那是別人的資料夾，沒有一起改。
    for line in LOCAL_CORPUS.read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        raw = json.loads(line)
        cases.append(
            Case(
                case_id=str(raw.get("case_id", "")),
                text=raw.get("text", ""),
                source=raw.get("source", "165"),
                label=raw.get("label", ""),
                date=raw.get("date", ""),
                county=raw.get("county", ""),
            )
        )
    return cases


def norm_label(s: str) -> str:
    """標籤正規化。165 的官方標籤有前後空白與全形括號的變體 ——
    實測 50 組只差這些字元就會被當成不同類，涉及 191 種原始寫法。"""
    return re.sub(r"[\s\u3000]+", "", s).replace("（", "(").replace("）", ")")


def matches_platform(text: str, platform_terms: list[str]) -> bool:
    """平台是從內文推斷的 —— 165 沒有平台欄位。

    關鍵詞一律不分大小寫比對：實測 LINE / Line / line 三種寫法都有人用，
    只認大寫會漏掉 7,182 筆。
    """
    lo = text.lower()
    return any(term.lower() in lo for term in platform_terms)


def build_subset(labels: list[str], platform_terms: list[str], *, limit: int = 0) -> int:
    """從共用語料切出自己的那一份，寫進 data/cases.jsonl。

    兩道篩選：標籤要在 labels 裡（正規化後比對），內文要命中平台關鍵詞。
    limit > 0 時只取前幾筆 —— 建索引很貴，展示用抽樣就夠。

    TODO(S9)：接上 CKIP 斷詞、把清理規則寫完整。目前沒有做斷詞，
    tokens 留空，檢索靠嵌入模型自己處理。
    """
    if not SHARED_CORPUS.exists():
        raise FileNotFoundError(
            f"找不到共用語料 {SHARED_CORPUS}。語料放共用雲端硬碟，"
            f"路徑用環境變數 CORPUS_PARQUET 指定（見 data/README.md）。"
        )

    import pyarrow.parquet as pq

    wanted = {norm_label(x) for x in labels}
    rows = pq.read_table(SHARED_CORPUS).to_pylist()

    kept: list[dict] = []
    for r in rows:
        if norm_label(r.get("label", "")) not in wanted:
            continue
        text = r.get("text", "") or ""
        if not matches_platform(text, platform_terms):
            continue
        kept.append(
            {
                "case_id": str(r.get("case_id", "")),
                "source": r.get("source", "165"),
                "text": text,
                "label": r.get("label", ""),
                "date": r.get("date", ""),
                "county": r.get("county", ""),
            }
        )
        if limit and len(kept) >= limit:
            break

    LOCAL_CORPUS.parent.mkdir(parents=True, exist_ok=True)
    with LOCAL_CORPUS.open("w", encoding="utf-8") as f:
        for rec in kept:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(kept)


def stats(cases: list[Case]) -> dict[str, int]:
    return {
        "total": len(cases),
        "labels": len({c.label for c in cases if c.label}),
        "counties": len({c.county for c in cases if c.county}),
    }
