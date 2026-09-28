"""S10 驗收：兩位標註者在同一批 60 筆上的一致度（Cohen's kappa，門檻 0.70）。

rater1 = `eval/gold_rater1_v4.json`，rater2 = `eval/gold_rater2_v4.json`
（由 `data/gold_標註_rater2.html` 標完後頁尾那段 JSON 存成）。
兩份以 case_id 對齊，順序不同沒關係；有缺、有多、有 null 都直接報錯，
不默默丟掉 —— 少算幾筆的 kappa 會比真實的好看。

用法：
    uv run python packages/modules/e_tbd/eval_kappa.py
    uv run python packages/modules/e_tbd/eval_kappa.py eval/rater_claude_v4.json
"""

from __future__ import annotations

import io
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent.parent))

from packages.shared import eval as shared_eval  # noqa: E402

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")

R1 = HERE / "eval" / "gold_rater1_v4.json"
R2 = HERE / "eval" / "gold_rater2_v4.json"
OUT = HERE / "eval" / "kappa_v4.json"
THRESHOLD = 0.70


def _labels(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for row in data["labels"]:
        if not row.get("stage"):
            raise SystemExit(f"{path.name}：{row['case_id']} 還沒標")
        out[row["case_id"]] = row["stage"]
    return out


def main() -> int:
    r2 = HERE / sys.argv[1] if len(sys.argv) > 1 else R2
    out = OUT.with_name(f"kappa_{r2.stem}.json") if r2 != R2 else OUT
    if not r2.exists():
        print(f"找不到 {r2.name} —— 先把標註頁頁尾的 JSON 存成這個檔")
        return 1
    a, b = _labels(R1), _labels(r2)
    if a.keys() != b.keys():
        only1, only2 = sorted(a.keys() - b.keys()), sorted(b.keys() - a.keys())
        raise SystemExit(f"兩份的 case_id 對不上：只在 rater1 {only1}；只在 rater2 {only2}")

    ids = sorted(a)
    y1, y2 = [a[i] for i in ids], [b[i] for i in ids]
    kappa = shared_eval.cohen_kappa(y1, y2)
    agree = sum(x == y for x, y in zip(y1, y2, strict=True))
    pairs = Counter((x, y) for x, y in zip(y1, y2, strict=True) if x != y)
    passed = kappa >= THRESHOLD

    print(f"筆數 {len(ids)}　一致 {agree}（{agree / len(ids):.1%}）　kappa {kappa:.3f}")
    print(f"門檻 {THRESHOLD}：{'通過' if passed else '未通過'}")
    if pairs:
        print("\n不一致（rater1 → rater2）：")
        for (x, y), n in pairs.most_common():
            print(f"  {x:>12} → {y:<12} {n}")

    out.write_text(
        json.dumps(
            {
                "n": len(ids),
                "agree": agree,
                "kappa": round(kappa, 4),
                "threshold": THRESHOLD,
                "passed": passed,
                "disagreements": [
                    {"case_id": i, "rater1": a[i], "rater2": b[i]} for i in ids if a[i] != b[i]
                ],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"\n已寫入 {out.relative_to(HERE)}")
    return 0 if passed else 2


if __name__ == "__main__":
    sys.exit(main())
