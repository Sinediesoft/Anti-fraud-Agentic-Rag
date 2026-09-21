"""M1 語料處理：從全隊那份共用語料裡，把屬於自己的那幾千筆撈出來（S9）。

模組 E 的做法：以標籤為主軸切，不靠平台篩。

理由是 2026-09-20 的實測：E 的三個候選標籤裡，只有「假求職」跟求職平台
對得上（65%），「騙取金融帳戶」只有 4%、「假借銀行貸款」是 0%。
硬套平台軸會切掉大量真實案例，所以這個模組收斂成「假求職詐騙」單一手法，
不限平台 —— 跨平台是這個手法的本質（社群 61%、通訊軟體 62%）。

語料來源不綁死單一來源。165 是主語料，但這支只認下面 Case 那個正規化後的
形狀，任何來源只要能映射成它就能接進來。

TODO(S9)：斷詞還沒做。說明書指定 CKIP Transformers，但它要拉 torch，
          而 pyproject 的 ml extra 目前是空的（S3 只鎖了 SLM 與嵌入模型）。
          那是共用檔案，要動得先跟全隊講 —— 先把 tokens 留空，
          M3 建索引時再決定是走斷詞還是直接用嵌入模型。
"""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
LOCAL_CORPUS = MODULE_DIR / "data" / "cases.jsonl"
# 共用語料放共用雲端硬碟，不進版控（§1.6）
# 預設檔名不帶來源名 —— 混來源時這份是正規化後的合併檔。只用 165 的話，
# 把 CORPUS_PARQUET 指到 cases_165.parquet 即可。
SHARED_CORPUS = Path(os.getenv("CORPUS_PARQUET", "data/corpus.parquet"))
# 只有 165 一個來源時的慣用檔名，找不到預設檔時退而求其次
FALLBACK_CORPUS = Path("data/cases_165.parquet")

# 控制字元（不含 \t \n \r，那些交給下面的空白正規化）
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MULTI_SPACE = re.compile(r"\s+")


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


def normalize_label(label: str) -> str:
    """標籤的規則層清理。

    官方標籤有 1,003 種寫法，多數是格式變體：前後多餘空白、全半形不一。
    假求職這一組實測有 6 種寫法，其中 4 種只差在空白與換行，合計 8 筆 ——
    不清理就會漏掉。
    """
    return unicodedata.normalize("NFKC", label).replace("\n", "").replace("\t", "").strip()


def clean_text(text: str) -> str:
    """內文清理：移除控制字元、統一全半形、收斂連續空白。"""
    out = unicodedata.normalize("NFKC", text)
    out = CONTROL_CHARS.sub("", out)
    return MULTI_SPACE.sub(" ", out).strip()


def _resolve_corpus() -> Path:
    if SHARED_CORPUS.exists():
        return SHARED_CORPUS
    if FALLBACK_CORPUS.exists():
        return FALLBACK_CORPUS
    raise FileNotFoundError(
        f"找不到共用語料（試過 {SHARED_CORPUS} 與 {FALLBACK_CORPUS}）。"
        f"語料放共用雲端硬碟，路徑用環境變數 CORPUS_PARQUET 指定（見 data/README.md）。"
    )


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
                source=raw.get("source", "165"),
                label=raw.get("label", ""),
                date=raw.get("date", ""),
                county=raw.get("county", ""),
            )
        )
    return cases


def build_subset(labels: list[str], platform_terms: list[str]) -> int:
    """從共用語料切出自己的那一份，寫進 data/cases.jsonl。

    labels 傳 labels_canon + label_aliases 兩份合起來，內部會正規化後去重。

    為什麼兩份都要傳：「假求職詐騙」與「假求職」正規化之後仍是兩個不同的字串
    （差的不是格式而是字面），只比對 labels_canon 會漏掉後者的 446 筆。
    PR #24 把這種情形歸為「語意重複，要人判斷不能自動合併」—— 判斷的結果
    就記在 pack.yaml 的 label_aliases 裡，這裡照著用。

    platform_terms 不參與篩選，只用來統計有多少筆提到求職管道 ——
    那個數字填進 pack.yaml 的 stats.platform_cases。

    回傳切出來的筆數。
    """
    import polars as pl  # 只有這支要用，不讓整個模組扛這個相依

    wanted = sorted({normalize_label(x) for x in labels if x and x.strip()})
    corpus = _resolve_corpus()
    df = pl.read_parquet(corpus)

    df = df.with_columns(
        pl.col("label").map_elements(normalize_label, return_dtype=pl.String).alias("_label_norm")
    )
    subset = df.filter(pl.col("_label_norm").is_in(wanted))

    LOCAL_CORPUS.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    written = 0
    platform_hits = 0

    with LOCAL_CORPUS.open("w", encoding="utf-8", newline="\n") as fh:
        for row in subset.iter_rows(named=True):
            text = clean_text(row["text"])
            if not text:
                continue
            # 去重：同一件事被重複通報過。用內文開頭比對，因為 case_id 一定不同
            fingerprint = text[:120]
            if fingerprint in seen:
                continue
            seen.add(fingerprint)

            if any(term and term in text for term in platform_terms):
                platform_hits += 1

            fh.write(
                json.dumps(
                    {
                        "case_id": str(row["case_id"]),
                        "text": text,
                        "source": row.get("source", "165"),
                        "label": row["_label_norm"],
                        "date": str(row.get("date", "")),
                        "county": row.get("county", ""),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            written += 1

    pct = platform_hits / written * 100 if written else 0
    print(f"來源          {corpus}")
    print(f"比對標籤      {wanted}")
    print(f"標籤命中      {subset.height:,} 筆")
    print(f"去重後        {written:,} 筆（重複 {subset.height - written:,}）")
    print(f"提到求職管道  {platform_hits:,} 筆（{pct:.0f}%）")
    print(f"寫入          {LOCAL_CORPUS}")
    return written


def stats(cases: list[Case]) -> dict[str, int]:
    return {
        "total": len(cases),
        "labels": len({c.label for c in cases if c.label}),
        "counties": len({c.county for c in cases if c.county}),
    }


def main() -> int:
    """重建自己的語料檔：uv run python packages/modules/e_tbd/m1_corpus.py"""
    import yaml

    if isinstance(sys.stdout, __import__("io").TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    pack = yaml.safe_load((MODULE_DIR / "pack.yaml").read_text(encoding="utf-8"))
    labels = list(pack["labels_canon"]) + list(pack.get("label_aliases") or [])
    build_subset(labels, pack["platform_terms"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
