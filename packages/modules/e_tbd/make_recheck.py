"""S10 複核批次：把有疑義的案例挑出來，在不帶參考答案的情況下重標。

為什麼需要這一步：第三輪標註時，頁面帶入了第二輪的答案當參考。
結果 v2 vs v3 的 kappa 高達 0.906，但那個數字分不出兩種情況 ——
「判準修好所以答案穩定」還是「看到參考答案就按同一個」。
第二輪是在錯誤判準（取最嚴重）下標的，所以那個高一致性可能只是誤標被延續。

這支腳本產生的頁面**刻意不帶任何參考答案、也不顯示規則的判定**，
只列出需要複核的案例，讓標註者重新判斷。

輸出同樣放 data/（含案例原文，已在 .gitignore）。

執行：
    uv run python packages/modules/e_tbd/make_recheck.py
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent.parent))

from packages.modules.e_tbd.make_gold_sample import HINTS, PAGE, STAGE_NAMES, STAGES  # noqa: E402

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")

SAMPLE = HERE / "data/gold_sample.jsonl"
IDS = HERE / "eval/recheck_ids.json"
OUT = HERE / "data/gold_複核.html"


def main() -> int:
    if not IDS.exists():
        print(f"找不到複核清單：{IDS}")
        return 1
    ids = json.loads(IDS.read_text(encoding="utf-8"))["case_ids"]
    by_id = {
        json.loads(line)["case_id"]: json.loads(line)
        for line in SAMPLE.read_text(encoding="utf-8").splitlines()
        if line
    }

    rows = []
    for cid in ids:
        r = dict(by_id[cid])
        r.pop("prev", None)  # 不帶上一輪答案
        r.pop("rule", None)  # 也不顯示規則怎麼判，避免任何錨定
        r["rule"] = ""
        r["prev"] = ""
        rows.append(r)

    stages_js = [{"id": s, "name": STAGE_NAMES[s], "hint": HINTS[s]} for s in STAGES]
    page = (
        PAGE.replace("__DATA__", json.dumps(rows, ensure_ascii=False).replace("</", "<\/"))
        .replace("__STAGES__", json.dumps(stages_js, ensure_ascii=False))
        .replace("__KEY__", "gold_s10_recheck")
        .replace("__RATER__", "rater1_recheck")
        .replace("S10 標準答案標註", f"S10 複核批次（{len(rows)} 筆）")
        .replace("<code>eval/gold_rater1_v3.json</code>", "<code>eval/gold_recheck.json</code>")
    )
    # 這批不比對規則，把重合度那段拿掉免得誤導
    page = page.replace(
        "<p>與規則判定一致：<b>${agree} / ${DATA.length}</b>\n"
        "      （${(agree/DATA.length*100).toFixed(1)}%）—— 這不是準確率，\n"
        "      是「人工標註和現行規則的重合度」，差距大的地方正是要改規則的地方。</p>",
        "",
    )
    OUT.write_text(page, encoding="utf-8")
    print(f"複核頁 {OUT.relative_to(HERE.parent.parent.parent)}（{len(rows)} 筆，無參考答案）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
