"""輸出檢核（說明書 S7 第 4 點）。

要擋的三件事：
  風險高但行動清單是空的、相似案例沒附編號、免責聲明不見了。

擋的方式分兩種：能修的就地修好並記一筆降級；不能修的（高風險卻沒行動）
不准當成完整判讀吐出去 —— 那比說不知道糟得多。
"""

from __future__ import annotations

from dataclasses import dataclass

from contracts import DISCLAIMER, Verdict


@dataclass(frozen=True)
class Violation:
    rule: str
    detail: str
    fatal: bool


def check(verdict: Verdict) -> list[Violation]:
    """只看不改，測試與 selfcheck 都用這支。"""
    problems: list[Violation] = []

    if verdict.risk_level.is_severe and not verdict.actions:
        problems.append(
            Violation(
                rule="high_risk_needs_action",
                detail=f"風險 {verdict.risk_level.value} 但行動清單是空的",
                fatal=True,
            )
        )

    for case in verdict.similar_cases:
        if not case.case_id.strip():
            problems.append(
                Violation(rule="case_needs_id", detail="相似案例沒有案例編號", fatal=False)
            )

    if not verdict.disclaimer.strip():
        problems.append(Violation(rule="disclaimer_required", detail="免責聲明不見了", fatal=False))

    for action in verdict.actions:
        if not action.text.strip():
            problems.append(
                Violation(rule="action_not_empty", detail="行動清單有空白項目", fatal=False)
            )

    return problems


def enforce(verdict: Verdict) -> tuple[Verdict | None, list[Violation]]:
    """能修的修掉，不能修的回 None，讓外殼改走通用建議那條路。"""
    problems = check(verdict)
    if any(p.fatal for p in problems):
        return None, problems
    if not problems:
        return verdict, []

    repaired = verdict.model_copy(
        update={
            "similar_cases": [c for c in verdict.similar_cases if c.case_id.strip()],
            "actions": [a for a in verdict.actions if a.text.strip()],
            "disclaimer": verdict.disclaimer.strip() or DISCLAIMER,
            "degraded": True,
            "degraded_reasons": [*verdict.degraded_reasons, *(p.detail for p in problems)],
        }
    )
    return repaired, problems
