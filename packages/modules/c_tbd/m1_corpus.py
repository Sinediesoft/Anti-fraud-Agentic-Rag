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
from dataclasses import dataclass, field
from pathlib import Path

from .facets import derive_from_text

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
    # 正規化後的分類（類型／管道／付款方式／對象），給 M3 做結構化過濾用。
    # 165 的六個欄位裡沒有這些，所以是從內文推斷出來的 —— 推斷的兩種錯誤
    # 與代價寫在 facets.derive_from_text() 的註解裡。
    facets: dict[str, list[str]] = field(default_factory=dict)


def load_local() -> list[Case]:
    """讀自己切好的那一份。沒有就回空的 —— 不要在這裡爆炸。

    facets 若資料檔裡沒有就當場推斷。推斷放在讀取時而不是檢索時，是因為
    檢索是熱路徑：十幾萬筆每次查詢都重算一遍，純規則也扛不住。
    """
    if not LOCAL_CORPUS.exists():
        return []
    cases: list[Case] = []
    # 🔴 一定要用 split("\n")，不能用 splitlines()。
    #
    # json.dumps(ensure_ascii=False) 只跳脫 \n \r \t " \\ 與 0x20 以下的控制字元，
    # **不跳脫** U+2028 LINE SEPARATOR、U+2029 PARAGRAPH SEPARATOR、U+0085 NEL。
    # 但 str.splitlines() 會在這三個上面斷行 —— 於是一筆合法的 JSONL 被切成兩半，
    # 讀回來就是 JSONDecodeError: Unterminated string。
    #
    # 這不是假設：2026-09-20 用 165 的 10,051 筆真實語料實測，裡面有 2 個 U+2028，
    # splitlines() 數出 10,053 行而 split("\n") 數出 10,052 行。受害者的自由敘述
    # 什麼字元都有，用自己造的測試資料永遠碰不到這個。
    for line in LOCAL_CORPUS.read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        raw = json.loads(line)
        text = raw.get("text", "")
        label = raw.get("label", "")
        cases.append(
            Case(
                case_id=str(raw.get("case_id", "")),
                text=text,
                source=raw.get("source", "165"),
                label=label,
                date=raw.get("date", ""),
                county=raw.get("county", ""),
                facets=raw.get("facets") or derive_from_text(text, label),
            )
        )
    return cases


MIN_CHARS = 20  # 比這短的敘述沒有檢索價值，多半是「被詐騙」這類殘缺紀錄


def _norm(s: object) -> str:
    return str(s).strip() if s is not None else ""


def read_shared(path: Path | None = None) -> list[dict]:
    """讀共用語料 parquet，回傳原始 dict 清單。

    pyarrow 延遲 import：跟 tools/fetch_corpus_165.py 同一個做法 —— 讓這個
    模組在沒裝 pyarrow 的環境也 import 得起來（它只是 streamlit 的傳遞相依，
    沒有宣告在 requirements.txt 裡，見 README 的已知待辦）。
    """
    src = path or SHARED_CORPUS
    if not src.exists():
        raise FileNotFoundError(
            f"找不到共用語料 {src}。語料放共用雲端硬碟，路徑用環境變數 "
            f"CORPUS_PARQUET 指定（見 data/README.md）。抓取腳本是 "
            f"tools/fetch_corpus_165.py。"
        )
    import pyarrow.parquet as pq

    return pq.read_table(src).to_pylist()


def _matches_label(label: str, labels: list[str], excludes: list[str] | None = None) -> bool:
    """標籤比對。

    🔴 165 的 label 欄（上游的 CaseTitle）**不是受控分類**，實測 194,355 筆裡
       有 1,003 種值，混了三種東西：

         · 官方分類          假投資詐騙 27,718 筆、假投資 2,509 筆
         · 空白與換行變體    '假投資詐騙 '、'\\n\\n假投資詐騙'、' \\n \\t\\n假投資詐騙'
         · 受害者自己寫的標題「投資夢，成詐騙」「慈惠APP的謊言：⋯百萬騙局」

       所以用子字串比對而不是等值比對 —— 前兩種靠它自然吃得下，第三種只能放棄
       （那些是自由文字，沒有規則抓得完）。

    excludes 是必要的，因為子字串會咬到別人的手法：`假交友(投資詐財)詐騙`
    有 13,945 筆，是投資詐財沒錯，但走的是交友路徑，屬於別人的組合。
    實測加了 `交友` 排除後，命中從 46,554 掉回 30,824 —— 那 15,730 筆本來
    就不該是我的。
    """
    low = label.lower()
    if excludes and any(t and t.lower() in low for t in excludes):
        return False
    return any(t and t.lower() in low for t in labels)


def _matches_platform(text: str, platform_terms: list[str]) -> bool:
    """平台比對只能看內文 —— 165 的六個欄位裡沒有平台欄。

    ⚠ 這是推斷，漏抓率不明。受害者寫「在網路上看到廣告」而沒寫臉書，
      這筆就撈不到。所以 build_subset() 同時回報兩個數字（手法總數／
      平台交集數），讓漏抓的規模看得見，而不是只給一個漂亮的結果。
    """
    low = text.lower()
    return any(t and t.lower() in low for t in platform_terms)


def build_subset(
    labels: list[str],
    platform_terms: list[str],
    *,
    label_excludes: list[str] | None = None,
    source_path: Path | None = None,
    out_path: Path | None = None,
    platform_only: bool = True,
) -> dict[str, int]:
    """從共用語料切出自己的那一份，寫進 data/cases.jsonl。

    回傳統計 dict，key 對得上 pack.yaml 的 stats 欄位：

        total_cases     這個手法總共幾筆（只看標籤）
        platform_cases  其中在我的平台上的幾筆（標籤 ∩ 內文提到平台）

    🔴 為什麼要回報兩個數字而不是一個：平台只能從內文推斷（165 沒有平台欄），
       漏抓率不明。只給 platform_cases 會讓人以為「我的語料就這麼多」，
       但實際上是「我認得出來的就這麼多」。兩個數字擺在一起，漏抓的規模
       才看得見 —— 如果交集只有手法總數的 3%，那多半是漏抓不是真的稀少。

    platform_only=False 時寫入整個手法的案例（不做平台交集）。語料太少時
    的退路 —— 但那等於放棄「平台 × 手法」這個分法，要寫進報告。
    """
    rows = read_shared(source_path)

    by_label = []
    for r in rows:
        text = _norm(r.get("text"))
        if len(text) < MIN_CHARS:
            continue
        if not _matches_label(_norm(r.get("label")), labels, label_excludes):
            continue
        by_label.append(r)

    on_platform = [r for r in by_label if _matches_platform(_norm(r.get("text")), platform_terms)]
    chosen = on_platform if platform_only else by_label

    # 去重。混來源時 case_id 會撞號，所以鍵是 source + case_id
    seen: set[tuple[str, str]] = set()
    out: list[Case] = []
    for r in chosen:
        key = (_norm(r.get("source")) or "165", _norm(r.get("case_id")))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        text = _norm(r.get("text"))
        label = _norm(r.get("label"))
        out.append(
            Case(
                case_id=key[1],
                text=text,
                source=key[0],
                label=label,
                date=_norm(r.get("date")),
                county=_norm(r.get("county")),
                facets=derive_from_text(text, label),
            )
        )

    dest = out_path or LOCAL_CORPUS
    dest.parent.mkdir(parents=True, exist_ok=True)
    # 結尾要有換行：pre-commit 的 end-of-file-fixer 會補，產生器不補的話
    # 每次重建都會跟 hook 來回打架。
    dest.write_text(
        "".join(
            json.dumps(
                {
                    "case_id": c.case_id,
                    "text": c.text,
                    "source": c.source,
                    "label": c.label,
                    "date": c.date,
                    "county": c.county,
                    "facets": c.facets,
                },
                ensure_ascii=False,
            )
            + "\n"
            for c in out
        ),
        encoding="utf-8",
    )

    return {
        "scanned": len(rows),
        "total_cases": len(by_label),
        "platform_cases": len(on_platform),
        "written": len(out),
    }


def coverage(cases: list[Case]) -> dict[str, tuple[list[str], list[str]]]:
    """每個 facet 維度用到了詞彙表裡的哪些值、還有哪些沒用到。

    移植自來源那套 tools/build_corpus.py，而且這是那支腳本最有價值的一段。

    為什麼要印出來：詞彙表裡有「遊戲點數」但語料裡一筆都沒有，代表使用者問
    「遊戲點數詐騙」會**抽得出過濾條件、然後篩出 0 筆** —— 那比抽不出條件更糟，
    因為它會**保證**回答「沒有」，而使用者會把那句話讀成「沒有這種詐騙」。

    這個語料是 19 萬筆真實案件，所以缺口多半不是語料的問題，而是
    facets.derive_from_text() 的推斷漏抓 —— 看得見才修得了。
    """
    from .facets import TABLES

    out: dict[str, tuple[list[str], list[str]]] = {}
    for key, table in TABLES.items():
        used = {v for c in cases for v in (c.facets or {}).get(key, [])}
        out[key] = (sorted(used), [k for k in table if k not in used])
    return out


def stats(cases: list[Case]) -> dict[str, int]:
    return {
        "total": len(cases),
        "labels": len({c.label for c in cases if c.label}),
        "counties": len({c.county for c in cases if c.county}),
    }


def _main() -> None:
    """切語料並印出報告。

        python -m packages.modules.c_tbd.m1_corpus

    篩選條件從 pack.yaml 讀，不寫死在程式裡 —— 那份檔案才是模組的身分宣告，
    兩邊各寫一份一定會走散。
    """
    import argparse

    import yaml

    ap = argparse.ArgumentParser(description="從共用語料切出模組 C 的那一份（S9）")
    ap.add_argument("--corpus", type=Path, default=None, help="覆寫 CORPUS_PARQUET")
    ap.add_argument("--out", type=Path, default=None, help="覆寫輸出路徑")
    ap.add_argument(
        "--tactic-only",
        action="store_true",
        help="不做平台交集，整個手法都收。語料太少時的退路，要寫進報告",
    )
    args = ap.parse_args()

    pack = yaml.safe_load((MODULE_DIR / "pack.yaml").read_text(encoding="utf-8"))
    labels = list(pack.get("labels_canon") or []) + list(pack.get("label_aliases") or [])
    platform_terms = list(pack.get("platform_terms") or [])
    # 標籤排除沿用 negative_terms，不另開欄位 —— PackSpec 設了 extra="forbid"，
    # 而它在凍結的 packages/contracts/ 裡。語意本來就對得上。
    excludes = list(pack.get("negative_terms") or [])

    print(f"手法標籤：{'、'.join(labels)}")
    print(f"排除    ：{'、'.join(excludes) or '（無）'}")
    print(f"平台詞  ：{'、'.join(platform_terms)}")
    print()

    got = build_subset(
        labels,
        platform_terms,
        label_excludes=excludes,
        source_path=args.corpus,
        out_path=args.out,
        platform_only=not args.tactic_only,
    )

    print(f"掃了           {got['scanned']:>8,} 筆")
    print(f"手法命中       {got['total_cases']:>8,} 筆   <- pack.yaml 的 stats.total_cases")
    print(f"平台交集       {got['platform_cases']:>8,} 筆   <- pack.yaml 的 stats.platform_cases")
    print(f"實際寫出       {got['written']:>8,} 筆")

    if got["total_cases"]:
        ratio = got["platform_cases"] / got["total_cases"]
        print(f"\n平台交集佔手法總數 {ratio:.1%}")
        if ratio < 0.10:
            print(
                "[!]  比例偏低。165 沒有平台欄，平台只能從內文推斷 ——\n"
                "     受害者寫「在網路上看到廣告」而沒寫臉書，這筆就撈不到。\n"
                "     這多半是漏抓而不是真的稀少，要補 pack.yaml 的 platform_terms，\n"
                "     或用 --tactic-only 收整個手法（但那等於放棄平台這一維，要寫進報告）。"
            )

    cases = load_local() if args.out is None else []
    if cases:
        print("\n分類覆蓋率：")
        gaps = False
        for key, (used, unused) in coverage(cases).items():
            print(f"  {key:<9} 用到 {len(used):>2} 個：{'、'.join(used) or '（無）'}")
            if unused:
                gaps = True
                print(f"  {'':<9} [!] 詞彙表有但語料沒有：{'、'.join(unused)}")
        if gaps:
            print(
                "  [!] 那幾個「有詞彙表沒語料」的值，使用者問得出條件但會篩到 0 筆 ——\n"
                "      那比抽不出條件更糟，因為它會保證回答「沒有」。"
            )
        else:
            print("  [OK] 四個維度都有語料，不會出現「問得出條件卻篩到 0 筆」的情況。")


if __name__ == "__main__":
    _main()
