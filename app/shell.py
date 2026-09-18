"""外殼主流程：把註冊表、路由器、解鎖層、輸出檢核串起來。

這是唯一認識所有模組的那一層。它不懂任何一種詐騙。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from contracts import (
    AnalyzeInput,
    CoverageStatus,
    ModuleHint,
    RiskLevel,
    RoutedResponse,
    TraceEvent,
)

from . import guards
from .entitlements import Entitlements
from .registry import Registry, load
from .router import route

# 尚未涵蓋時給的通用建議。風險等級與 165 導流永遠免費（S19 規則二）
GENERAL_ADVICE = [
    "先停下來，不要再匯任何一筆錢 —— 包含對方說「補稅金」「解凍費」「保證金」才能拿回錢的那一筆。",
    "把對話紀錄、匯款單據、對方帳號完整保留下來，不要刪除也不要封鎖對方。",
    "撥打 165 反詐騙諮詢專線說明情況；已經匯款的話同時聯繫匯款銀行申請圈存。",
]


@dataclass
class Shell:
    registry: Registry
    entitlements: Entitlements

    @classmethod
    def boot(cls, selection: str = "all", *, unlocked: bool = False) -> Shell:
        return cls(registry=load(selection), entitlements=Entitlements(unlocked=unlocked))

    def analyze(self, payload: AnalyzeInput, *, manual: str | None = None) -> RoutedResponse:
        started = time.perf_counter()
        decision = route(self.registry, payload, manual=manual)
        trace: list[TraceEvent] = list(decision.trace)

        hints = [
            ModuleHint(
                module_id=m.id,
                module_name=m.pack.name,
                score=score,
                locked=not self.entitlements.can_use(m),
                message="你可能同時也遇到這個情況",
            )
            for m, score in decision.also_possible
        ]

        if decision.primary is None:
            trace.append(
                TraceEvent(
                    step="coverage",
                    duration_ms=(time.perf_counter() - started) * 1000,
                    status="ok",
                    detail="沒有模組夠有把握，標記為尚未涵蓋",
                )
            )
            return RoutedResponse(
                coverage=CoverageStatus.UNCOVERED,
                risk_level=RiskLevel.UNKNOWN,
                hints=hints,
                general_advice=GENERAL_ADVICE,
                scores=decision.scores,
                trace=trace,
            )

        primary = decision.primary

        if not self.entitlements.can_use(primary):
            # 規則一：不能假裝不知道
            trace.append(
                TraceEvent(step="entitlement", duration_ms=0.0, detail=f"{primary.id} 未解鎖")
            )
            return RoutedResponse(
                coverage=CoverageStatus.COVERED_LOCKED,
                risk_level=RiskLevel.MEDIUM,
                locked_notice=self.entitlements.locked_notice(primary),
                hints=hints,
                general_advice=GENERAL_ADVICE,
                scores=decision.scores,
                trace=trace,
            )

        analyze_started = time.perf_counter()
        try:
            verdict = primary.instance.analyze(payload)
            status = "ok"
            detail = None
        except Exception as exc:  # 模組壞掉要降級，不是整個當掉
            trace.append(
                TraceEvent(
                    step=f"analyze:{primary.id}",
                    duration_ms=(time.perf_counter() - analyze_started) * 1000,
                    status="failed",
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
            return RoutedResponse(
                coverage=CoverageStatus.UNCOVERED,
                risk_level=RiskLevel.UNKNOWN,
                hints=hints,
                general_advice=GENERAL_ADVICE,
                scores=decision.scores,
                trace=trace,
            )

        trace.append(
            TraceEvent(
                step=f"analyze:{primary.id}",
                duration_ms=(time.perf_counter() - analyze_started) * 1000,
                status=status,
                detail=detail,
            )
        )
        trace.extend(verdict.trace)

        checked, problems = guards.enforce(verdict)
        if problems:
            trace.append(
                TraceEvent(
                    step="guards",
                    duration_ms=0.0,
                    status="degraded",
                    detail="；".join(p.detail for p in problems),
                )
            )
        if checked is None:
            return RoutedResponse(
                coverage=CoverageStatus.UNCOVERED,
                risk_level=verdict.risk_level,
                hints=hints,
                general_advice=GENERAL_ADVICE,
                scores=decision.scores,
                trace=trace,
            )

        return RoutedResponse(
            coverage=CoverageStatus.COVERED,
            risk_level=checked.risk_level,
            verdict=checked,
            hints=hints,
            scores=decision.scores,
            trace=trace,
        )
