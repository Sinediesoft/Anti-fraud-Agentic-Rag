"""S10 的第二位標註者頁面：同一批 60 筆，空白重標，用來算 Cohen's kappa。

為什麼要另外一支而不是重跑 make_gold_sample.py：那支會在樣本檔不見時
**重新抽樣**。kappa 要兩個人標**同一批**案例，抽到不同的 60 筆就算不出來。
這支不抽樣，直接照 `eval/gold_rater1_v4.json` 裡那 60 個 case_id 取，
所以無論語料或亂數種子怎麼變，兩位標註者看到的都是同一批。

## 兩個刻意的設計

**不帶任何參考答案。** 不顯示 rater1 標了什麼，也不顯示規則判定 ——
v3 那次帶了上一輪答案當參考，kappa 0.906 裡有很大一部分是 anchoring
（拿掉參考重判 14 筆，13 筆改變）。規則判定只在全部標完後的統計頁出現。

**打散順序。** rater1 的樣本是按階段分層抽的，同一階段的案例是連號；
照原順序標，連續好幾筆都是同一答案本身就是一種暗示。用固定種子打散，
兩位標註者的答案仍以 case_id 對齊，不影響 kappa 計算。

執行：
    uv run python packages/modules/e_tbd/make_rater2_page.py
"""

from __future__ import annotations

import io
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent.parent))

from packages.modules.e_tbd import threads_stages as ts  # noqa: E402
from packages.modules.e_tbd.m4_judgement import THREADS_METHOD  # noqa: E402
from packages.modules.e_tbd.make_gold_sample import (  # noqa: E402
    HINTS,
    PAGE,
    STAGE_NAMES,
    STAGES,
)

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")

CASES = HERE / "data/cases.jsonl"
RATER1 = HERE / "eval/gold_rater1_v4.json"
# 含原文，只能放 data/（已在 .gitignore）
OUT_PAGE = HERE / "data/gold_標註_rater2.html"
OUT_SAMPLE = HERE / "data/gold_sample.jsonl"

SHUFFLE_SEED = 20260928


def main() -> int:
    if not CASES.exists():
        print(f"找不到語料子集：{CASES}\n請先跑 m1_corpus.py")
        return 1
    if not RATER1.exists():
        print(f"找不到第一位標註者的結果：{RATER1}")
        return 1

    wanted = [x["case_id"] for x in json.loads(RATER1.read_text(encoding="utf-8"))["labels"]]
    by_id = {
        c["case_id"]: c
        for c in (
            json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line
        )
    }

    missing = [i for i in wanted if i not in by_id]
    if missing:
        print(f"有 {len(missing)} 筆在目前語料裡找不到：{missing[:5]}")
        print("語料若重切過，兩輪標註的對象就不同了，kappa 算出來沒有意義。")
        return 1

    rows = []
    for case_id in wanted:
        c = by_id[case_id]
        a = ts.assess(c["text"], THREADS_METHOD)
        rows.append(
            {
                "case_id": case_id,
                "text": c["text"],
                "label": c.get("label", ""),
                "date": (c.get("date") or "")[:10],
                "county": c.get("county", ""),
                "rule": a.harm.name.lower(),
                "rule_verdict": a.verdict.name,
                # 空字串 = 頁面不顯示「上一輪你標了什麼」
                "prev": "",
            }
        )

    # 樣本檔不見了會讓 make_gold_sample.py 重新抽樣，補回去以免下次重跑時換了一批
    if not OUT_SAMPLE.exists():
        with OUT_SAMPLE.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"順便補回樣本檔 {OUT_SAMPLE.name}（原本不見了，重跑抽樣會換一批）")

    random.Random(SHUFFLE_SEED).shuffle(rows)

    stages_js = [{"id": s, "name": STAGE_NAMES[s], "hint": HINTS[s]} for s in STAGES]
    page = (
        PAGE.replace("__DATA__", json.dumps(rows, ensure_ascii=False).replace("</", "<\\/"))
        .replace("__STAGES__", json.dumps(stages_js, ensure_ascii=False))
        # key 跟 rater1 不同：同一台瀏覽器也能標，不會讀到 rater1 的進度
        .replace("__KEY__", "gold_s10_v4_rater2")
        .replace("__RATER__", "rater2_v4")
        .replace("__OUTFILE__", "gold_rater2_v4.json")
    )
    OUT_PAGE.write_text(page, encoding="utf-8")

    print(f"標註頁 {OUT_PAGE.relative_to(HERE.parent.parent.parent)}（{len(rows)} 筆）")
    print("順序已打散，不顯示 rater1 的答案，也不顯示規則判定。")
    print("標完把頁尾那段 JSON 存成 eval/gold_rater2_v4.json，我再算 kappa。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
