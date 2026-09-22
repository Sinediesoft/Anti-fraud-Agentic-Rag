"""S10 標註的抽樣：從 10,051 筆裡抽出要人工標的那 60 筆。

    python -m packages.modules.c_tbd.annotate

產出 `data/annotations.todo.jsonl`（不進版控，裡面是真實受害者的敘述），
每筆已經按 `eval/annotation_schema.yaml` 把欄位開好、值留空，直接填就行。

## 四個決定，都寫在這裡免得日後沒人記得為什麼

**一、主軸用付款方式，不用手法或平台。**
手法篩完只剩一種（91% 都是「假投資詐騙」），平台篩完 99.8% 都是 Facebook ——
這兩個維度在這份語料裡已經沒有鑑別力。付款方式才是真正改變行動清單的東西：
虛擬貨幣幾乎追不回、面交牽涉車手、轉帳還有圈存的機會。

**二、副軸用敘述長度。**
長度是「這個人講了多少細節」的代理指標。只標長的會讓抽取欄位的分數虛高
（細節都在，當然抽得到），只標短的又量不出上限。三分位各取一份。

**三、等量配置（每格 5 筆），不是比例配置。**
母體其實蠻平均的（虛擬貨幣 2,651／面交 2,877／轉帳 2,852／其他 1,462），
比例配置會給 16/17/17/9。等量是刻意把「其他／無」那格從 9 拉到 15 ——
那格最雜（超商代碼、信用卡、遊戲點數、完全沒提），最需要樣本。

🔴 代價：這 60 筆**不能拿來讀母體比例**。想知道「虛擬貨幣佔多少」要回去查
   全量語料，不能從標註集數。這件事要寫進報告。

**四、排除「只靠弱證據被收進來」的 209 筆。**
那些是內文只出現「社團」或「一頁式廣告」、從沒提過 Facebook／臉書／FB 的案例。
它們是不是 FB 案件本身就有爭議（見 REPORT.md 限制的平台推斷那段），而 S6 的
交叉表還沒正式決議 —— 萬一邊界改了，這批最可能被判出去。標註是最貴的人工，
不要把它花在最可能作廢的樣本上。

## 還沒做的

TODO(S10)：標註規則書（判準、邊界案例怎麼判、不一致的裁決規則）還沒寫。
           這份只決定「要填什麼」，不決定「怎麼判」—— 後者要讀過案例才訂得出來。
TODO(S10)：複核者還沒問到。kappa 要兩個人標同一批才算得出來，門檻 ≥ 0.70。
TODO(S10)：這支沒有測試。該測的三件事 —— 分層格子數對不對、同一個 SALT 抽兩次
           結果一樣、弱平台證據真的被排掉。

## 為什麼不用 random.seed

`random.sample` 的結果在不同 Python 版本之間不保證一樣，而這 60 筆是要寫進
報告、要讓複核者拿到同一批、要在幾週後重現的東西。改用 case_id 的 sha1 排序：
純函式、跨版本穩定、換機器也一樣。想換一批就改 SALT。
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from .m1_corpus import LOCAL_CORPUS, Case, load_local

MODULE_DIR = Path(__file__).resolve().parent
TODO_PATH = MODULE_DIR / "data" / "annotations.todo.jsonl"

SAMPLE_SIZE = 60
PER_CELL = 5

# 換一批樣本就改這個字串。改了要在 REPORT.md 記一筆，否則複核者拿到的會是別批。
SALT = "c_tbd-s10-2026-09-23"

# 強平台證據：內文真的講了 Facebook。沒有這些字的案例是靠「社團」「一頁式廣告」
# 這類弱詞被收進語料的，抽樣時避開（理由見檔頭第四點）。
STRONG_PLATFORM = ("facebook", "臉書", "fb", "粉專", "粉絲專頁")

# 一筆案例可能同時命中好幾種付款方式，所以要指定唯一歸屬。
# 順序照「行動差異」排，不是照筆數：能不能追回錢差最多的排前面。
PAYMENT_PRIORITY = (
    ("虛擬貨幣", ("虛擬貨幣",)),
    ("面交", ("面交",)),
    ("轉帳", ("網銀匯款", "ATM轉帳")),
)
PAYMENT_OTHER = "其他/無"
LENGTH_BUCKETS = ("短", "中", "長")


def _has_strong_platform(text: str) -> bool:
    low = text.lower()
    return any(term in low for term in STRONG_PLATFORM)


def _bucket_payment(case: Case) -> str:
    found = set((case.facets or {}).get("payment") or [])
    for name, members in PAYMENT_PRIORITY:
        if found & set(members):
            return name
    return PAYMENT_OTHER


def _length_cuts(cases: list[Case]) -> tuple[int, int]:
    """長度三分位的兩個切點。跟著語料走，不寫死 —— 語料換了切點就該跟著換。"""
    lens = sorted(len(c.text) for c in cases)
    if not lens:
        return (0, 0)
    return (lens[len(lens) // 3], lens[len(lens) * 2 // 3])


def _bucket_length(case: Case, cuts: tuple[int, int]) -> str:
    n = len(case.text)
    if n < cuts[0]:
        return LENGTH_BUCKETS[0]
    if n < cuts[1]:
        return LENGTH_BUCKETS[1]
    return LENGTH_BUCKETS[2]


def _rank(case: Case) -> str:
    """穩定的抽樣序。同一個 case_id 永遠排在同一個位置。"""
    return hashlib.sha1(f"{SALT}:{case.source}:{case.case_id}".encode()).hexdigest()


def eligible(cases: list[Case]) -> list[Case]:
    return [c for c in cases if _has_strong_platform(c.text)]


def stratify(cases: list[Case]) -> dict[tuple[str, str], list[Case]]:
    cuts = _length_cuts(cases)
    cells: dict[tuple[str, str], list[Case]] = {}
    for c in cases:
        cells.setdefault((_bucket_payment(c), _bucket_length(c, cuts)), []).append(c)
    return cells


def pick(cases: list[Case], per_cell: int = PER_CELL) -> list[Case]:
    """每格取前 per_cell 筆（照 _rank 排序）。

    某一格不足時**不補到別格** —— 補了就不是分層抽樣，而且會讓「哪一格樣本
    不夠」這件事消失在結果裡。目前 12 格最小的也有 471 筆，不會發生。
    """
    cells = stratify(cases)
    out: list[Case] = []
    for key in sorted(cells):
        out.extend(sorted(cells[key], key=_rank)[:per_cell])
    return out


def blank_record(case: Case) -> dict:
    """照 eval/annotation_schema.yaml 把欄位開好、值留空。

    值一律留 null 或空陣列，不預填規則抽出來的結果 —— 預填會讓標註者變成
    「確認機器的答案」而不是獨立判斷，kappa 就失去意義了。
    """
    return {
        "case_id": case.case_id,
        "source": case.source,
        "text": case.text,
        "date": case.date,
        "county": case.county,
        "stage": None,
        "profile": {
            "contact_platform": None,
            "moved_to": None,
            "payment_method": None,
            "amount_range": None,
            "days_elapsed": None,
        },
        "module_specific": {
            "fake_platform_name": None,
            "withdrawal_blocked_reason": None,
            "transfer_count": None,
            "group_type": None,
            "recruited_others": None,
        },
        "facets_true": {"category": [], "channel": [], "payment": [], "target": []},
        "question": {"usable": None, "text": None, "plain": None},
        "similar_case_ids": [],
        "meta": {"annotator": None, "annotated_on": None, "confidence": None, "notes": None},
    }


def write_todo(picked: list[Case], out_path: Path | None = None) -> Path:
    dest = out_path or TODO_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    # 結尾要有換行，理由同 m1_corpus：不然每次重跑都跟 end-of-file-fixer 打架
    dest.write_text(
        "".join(
            json.dumps(blank_record(c), ensure_ascii=False) + "\n"
            for c in sorted(picked, key=_rank)
        ),
        encoding="utf-8",
    )
    return dest


def _report(all_cases: list[Case], pool: list[Case], picked: list[Case]) -> None:
    cuts = _length_cuts(pool)
    print(f"全量語料        {len(all_cases):>6,} 筆")
    print(f"排除弱平台證據  {len(all_cases) - len(pool):>6,} 筆（只提社團／一頁式廣告）")
    print(f"抽樣母體        {len(pool):>6,} 筆")
    print(f"長度三分位切點  {cuts[0]} / {cuts[1]} 字\n")

    cells = stratify(pool)
    print("分層格子（母體筆數 → 抽出）")
    print(f"  {'':<10}" + "".join(f"{b:>12}" for b in LENGTH_BUCKETS))
    got = Counter((_bucket_payment(c), _bucket_length(c, cuts)) for c in picked)
    for pay in [name for name, _ in PAYMENT_PRIORITY] + [PAYMENT_OTHER]:
        row = "".join(
            f"{len(cells.get((pay, b), [])):>8,} →{got[(pay, b)]:>2}" for b in LENGTH_BUCKETS
        )
        print(f"  {pay:<10}{row}")
    print(f"\n共抽出 {len(picked)} 筆（目標 {SAMPLE_SIZE}）")

    print("\n抽出來這批的其他分布（只供檢查偏斜，不代表母體）：")
    for label, counts in [
        ("縣市", Counter(c.county for c in picked)),
        ("年月", Counter((c.date or "")[:7] for c in picked)),
    ]:
        top = "、".join(f"{k or '未填'} {v}" for k, v in counts.most_common(6))
        print(f"  {label:<4} {len(counts):>2} 種 —— {top}")
    lens = sorted(len(c.text) for c in picked)
    print(f"  長度   最短 {lens[0]}／中位 {lens[len(lens) // 2]}／最長 {lens[-1]} 字")


def _main() -> None:
    cases = load_local()
    if not cases:
        raise FileNotFoundError(f"還沒有自己的語料（{LOCAL_CORPUS}）。先跑 M1（S9）。")
    pool = eligible(cases)
    picked = pick(pool)
    _report(cases, pool, picked)
    dest = write_todo(picked)
    print(f"\n待標檔已寫出：{dest}")
    print("欄位定義見 eval/annotation_schema.yaml。這個檔不進版控（含真實敘述）。")


if __name__ == "__main__":
    _main()
