"""M1 語料處理：從全隊那份共用語料裡，把屬於自己的那幾千筆撈出來（S9）。

模組 E 的做法：平台 × 手法兩層篩選。

  平台  起點在 Threads —— 12,743 筆（6.56%，平台排名第四）
  手法  網路購物類 —— 其中 9,191 筆（72.1%），壓倒性第一

平台用「起點」定義而不是「只出現這個平台」：Threads 關鍵字位置中位數 0.02、
99.4% 落在案例前 20%，兩平台都出現時 99.5% 是 Threads 先出現。所以導流到
LINE（57.2%）是後續環節，整條算這個模組，跟做 LINE 的組員不衝突。

「脆」只收精確樣態。實測明確指 Threads 的只有 73 筆，而誤中（脆弱／乾脆／
酥脆／清脆）有 1,167 筆 —— 直接收會引入 16 倍雜訊。

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

# 平台推導規則：起點在 Threads。
# 中文字也算 \w，所以  邊界斷言在中文語境會失效；Rust regex 又不支援
# lookaround，(?<!) 也不能用。因此改成直接列舉「脆」的精確樣態——
# 明確指 Threads 的只有 73 筆，誤中（脆弱／乾脆／酥脆／清脆）有 1,167 筆。
THREADS_PATTERN = r"(?i)threads|脆(上|裡|裏|的貼文)|(在|滑|用|玩|逛)脆|脆友"

# 模組界線：超商物流平台歸模組 D（2026-09-23 與 D 談定）。
#
# D 宣告的平台軸是「7-11 賣貨便／交貨便、全家好賣+、超商店到店」，
# 那是平台，讓給他名正言順。但「假客服」「實名認證」是**話術**不是平台——
# 那些話術在非超商的情境一樣會出現（切完之後剩餘語料仍有 51.5% 與 34.6%），
# 而且假客服是整個購物詐騙族的共同環節（切之前佔 70%），不是超商物流的專屬特徵。
#
# 所以切法是照 D 自己定義的平台軸切，不照話術切。
# 詞表是掃語料掃出來的，不是猜的——第一版只寫「賣貨便|交貨便|好賣+|店到店」，
# 抽樣時發現案例用的是「全家好賣」（沒有 +）、「收貨通」、「fun 心取」、
# 「famiport 假網址」、「7-11 的網址」等變體，界線等於沒切乾淨。
#
# 「全家」直接整個擋：實測剩餘語料 134 筆提到它，全部是便利商店
# （全家便利商店 42、全家Fami 20、全家好買家、全家寄貨、全家Fun心取…），
# 沒有一筆是「全家人」。變體太多，列舉不完，擋詞根比較可靠。
#
# 只擋超商，不擋一般物流：黑貓宅急便（76 筆）、嘉里大榮（64）、郵局（237）
# 不在 D 宣告的平台軸內，那些留在本模組。
EXCLUDE_PATTERN = (
    r"(?i)賣貨便|交貨便|好賣\+|好賣加|全家|famiport|famip|fun\s*心取|收貨通"
    r"|店到店|超商|7-?11|統一超商|小七|ibon|萊爾富|OK超商|OK便利"
)


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
    本模組實際遇到的記在 pack.yaml 的 label_aliases —— 例如「假網拍詐騙」
    有前面帶空白、開頭帶換行與定位字元兩種寫法，「網路購物詐騙」前後各有一種。
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

    為什麼兩份都要傳：label_aliases 收的是正規化之後**仍然**對不上 canon 的寫法 ——
    承辦人打錯字（「假買家騙賣騙家詐騙」「假買家片賣家詐騙」）、全形括號
    （「假消費異常詐騙（騙買家）」）。這些正規化修不掉，只比對 labels_canon 會漏。
    PR #24 把這種情形歸為「語意重複，要人判斷不能自動合併」—— 判斷的結果
    就記在 pack.yaml 的 label_aliases 裡，這裡照著用。

    平台篩選用 THREADS_PATTERN（模組層級常數），platform_terms 只用來統計，
    不參與篩選 —— 兩者故意分開：規則要能被人工複核，詞表要能被調。

    回傳切出來的筆數。
    """
    import polars as pl  # 只有這支要用，不讓整個模組扛這個相依

    wanted = sorted({normalize_label(x) for x in labels if x and x.strip()})
    corpus = _resolve_corpus()
    df = pl.read_parquet(corpus)

    df = df.with_columns(
        pl.col("label").map_elements(normalize_label, return_dtype=pl.String).alias("_label_norm")
    )
    # 三層篩選：平台（起點在 Threads）→ 手法（網路購物類）→ 排除模組 D 的地盤
    on_platform = df.filter(pl.col("text").str.contains(THREADS_PATTERN))
    by_tactic = on_platform.filter(pl.col("_label_norm").is_in(wanted))
    subset = by_tactic.filter(~pl.col("text").str.contains(EXCLUDE_PATTERN))

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
    print(f"全量          {df.height:,} 筆")
    print(f"起點在 Threads {on_platform.height:,} 筆")
    print(
        f"其中網購類     {by_tactic.height:,} 筆（{by_tactic.height / on_platform.height * 100:.1f}%）"
    )
    print(f"排除超商物流   {by_tactic.height - subset.height:,} 筆 → 模組 D")
    print(f"本模組範圍     {subset.height:,} 筆")
    print(f"去重後        {written:,} 筆（重複 {subset.height - written:,}）")
    print(f"平台詞命中     {platform_hits:,} 筆（{pct:.0f}%）")
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
