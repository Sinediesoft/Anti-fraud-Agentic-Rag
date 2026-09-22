"""進入點：python -m packages.modules.f_合約_違法比對.cli <合約檔案>

獨立 CLI，不經過 app/ 外殼——這個工具沒有 pack.yaml，registry 不會
載入它，不會跟 A-E 搶 can_handle()。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .m1_ingest import ingest, load_text
from .m2_segment import segment
from .m3_retrieval import retrieve
from .m4_judgement import judge
from .m5_report import compose


def run(contract_path: Path) -> int:
    raw = load_text(contract_path)
    masked = ingest(raw)
    clauses = segment(masked.text)
    findings = [judge(clause, retrieve(clause.text)) for clause in clauses]
    report = compose(contract_path.name, findings)

    print(report.model_dump_json(indent=2, exclude_none=True))
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("用法：python -m packages.modules.f_合約_違法比對.cli <合約檔案>", file=sys.stderr)
        return 2
    return run(Path(argv[0]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
