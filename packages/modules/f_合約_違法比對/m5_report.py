"""組成逐條報告。"""

from __future__ import annotations

from .schemas import ClauseFinding, ContractReviewReport


def compose(source_filename: str, findings: list[ClauseFinding]) -> ContractReviewReport:
    return ContractReviewReport(source_filename=source_filename, findings=findings)
