"""進入點：python -m packages.modules.g_報案資料整理.cli <facts.yaml> --out <報案資料整理.md>

讀寫一律明確指定 utf-8：繁中 Windows 的 open() 預設是 cp950，使用者的
敘述裡只要有一個 emoji 就會炸。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from contracts import Verdict
from pydantic import ValidationError

from .draft import missing_items, render
from .schemas import ReportFacts


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m packages.modules.g_報案資料整理.cli",
        description="整理報案前要準備的資料，報案時帶去派出所。",
    )
    ap.add_argument("facts", type=Path, help="報案資料 YAML，格式見 examples/facts.example.yaml")
    ap.add_argument("--verdict", type=Path, help="系統判讀的 Verdict JSON（可省略）")
    ap.add_argument(
        "--out", type=Path, required=True, help="整理好的檔案寫到哪裡。不要放在 repo 裡"
    )
    args = ap.parse_args(argv)

    try:
        facts = ReportFacts.model_validate(yaml.safe_load(args.facts.read_text(encoding="utf-8")))
        verdict = (
            Verdict.model_validate_json(args.verdict.read_text(encoding="utf-8"))
            if args.verdict
            else None
        )
    except ValidationError as exc:
        print(f"[X] 資料格式不對：\n{exc}", file=sys.stderr)
        return 2

    args.out.write_text(render(facts, verdict), encoding="utf-8")
    todo = missing_items(facts)
    print(f"[OK] 已寫到 {args.out}" + (f"，還缺 {len(todo)} 項資料" if todo else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
