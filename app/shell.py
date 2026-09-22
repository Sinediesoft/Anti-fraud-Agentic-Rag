"""外殼主流程：把註冊表、路由器、解鎖層、輸出檢核串起來。

這是唯一認識所有模組的那一層。它不懂任何一種詐騙。

兩個進入點：

    analyze()      主模組出一份判讀。行為與 W2 凍結時完全一致，
                   evaluate.py 與既有測試走的都是這條。
    analyze_all()  所有「認領」的模組各出一份判讀。

為什麼需要 analyze_all：真實的詐騙是跨平台的 —— 在 FB 看到廣告、被帶到
LINE 收割，兩個平台各有一個模組負責。route() 本來就會把兩個都認出來
（實測「fb 點連結加 line，老師說保證獲利」a=0.867 / c=0.817，兩個都過門檻），
但 analyze() 只跑分數最高的那個，另一個被降級成一句「你可能同時也遇到這個
情況」的提示 —— 沒有案例、沒有行動清單。使用者真正需要的是兩份都給。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
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
from .registry import LoadedModule, Registry, load
from .router import RouteDecision, route

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

    # ── 進入點 ──────────────────────────────────────────────

    def analyze(self, payload: AnalyzeInput, *, manual: str | None = None) -> RoutedResponse:
        """主模組出一份判讀。W2 凍結時的行為，一字不動。"""
        started = time.perf_counter()
        decision = route(self.registry, payload, manual=manual)
        trace: list[TraceEvent] = list(decision.trace)
        hints = [self._hint(m, score) for m, score in decision.also_possible]

        if decision.primary is None:
            trace.append(self._coverage_event(started))
            return self._uncovered(decision, trace, hints=hints)

        return self._run_one(decision.primary, payload, decision, hints, trace)

    def analyze_all(
        self, payload: AnalyzeInput, *, manual: str | None = None
    ) -> list[RoutedResponse]:
        """所有認領的模組各出一份判讀。第一份是主判讀（分數最高的那個）。

        「認領」的定義沒有新發明 —— 就是各模組 pack.yaml 裡自己寫的
        route_min。只到 route_hint_min 的仍然只給提示，不出完整判讀：
        「我在 fb 點了連結加 line」這種還沒出事的句子拿到兩份「你被詐騙了」
        會誤導人，那比說不知道糟。

        回傳 list 而不是改 RoutedResponse 的形狀 —— packages/contracts/ 是
        W1 凍結的，能不動就不動。
        """
        started = time.perf_counter()
        decision = route(self.registry, payload, manual=manual)
        base_trace: list[TraceEvent] = list(decision.trace)

        if decision.primary is None:
            hints = [self._hint(m, score) for m, score in decision.also_possible]
            base_trace.append(self._coverage_event(started))
            return [self._uncovered(decision, base_trace, hints=hints)]

        # also_possible 裡混了「也認領的」與「只到 hint 的」（見 router.py 的
        # others = claimed[1:] + hinted）。每個模組自己帶著門檻，這裡重新分一次
        # 就好 —— 不必為了這件事去動 router。
        claimed = [decision.primary, *(m for m, s in decision.also_possible if self._claims(m, s))]
        # 會出完整判讀的不該同時又出現在「你可能同時也遇到」裡
        hints = [self._hint(m, s) for m, s in decision.also_possible if not self._claims(m, s)]

        if len(claimed) == 1:
            return [self._run_one(claimed[0], payload, decision, hints, base_trace)]

        # 並行跑。收益取決於 Ollama 的 OLLAMA_NUM_PARALLEL（預設 1，SLM 呼叫
        # 仍會排隊），但嵌入那段是本地 torch，確實會同時跑。
        # TODO(S18)：合併週用展示機量一次，決定要不要調 Ollama。
        with ThreadPoolExecutor(max_workers=len(claimed)) as pool:
            futures = [
                pool.submit(self._run_one, m, payload, decision, hints, base_trace) for m in claimed
            ]
            return [f.result() for f in futures]

    # ── 內部 ────────────────────────────────────────────────

    def _claims(self, module: LoadedModule, score: float) -> bool:
        return score >= module.pack.thresholds.route_min

    def _hint(self, module: LoadedModule, score: float) -> ModuleHint:
        return ModuleHint(
            module_id=module.id,
            module_name=module.pack.name,
            score=score,
            locked=not self.entitlements.can_use(module),
            message="你可能同時也遇到這個情況",
        )

    def _coverage_event(self, started: float) -> TraceEvent:
        return TraceEvent(
            step="coverage",
            duration_ms=(time.perf_counter() - started) * 1000,
            status="ok",
            detail="沒有模組夠有把握，標記為尚未涵蓋",
        )

    def _uncovered(
        self,
        decision: RouteDecision,
        trace: list[TraceEvent],
        *,
        hints: list[ModuleHint],
        risk_level: RiskLevel = RiskLevel.UNKNOWN,
    ) -> RoutedResponse:
        return RoutedResponse(
            coverage=CoverageStatus.UNCOVERED,
            risk_level=risk_level,
            hints=hints,
            general_advice=GENERAL_ADVICE,
            scores=decision.scores,
            trace=trace,
        )

    def _run_one(
        self,
        module: LoadedModule,
        payload: AnalyzeInput,
        decision: RouteDecision,
        hints: list[ModuleHint],
        base_trace: list[TraceEvent],
    ) -> RoutedResponse:
        """一個模組跑出一份 RoutedResponse。解鎖檢查與輸出檢核都在這裡。

        analyze() 與 analyze_all() 共用 —— 多模組那條路不能繞過 entitlements
        或 guards，所以只有這一個地方呼叫 module.instance.analyze()。
        """
        trace = list(base_trace)

        if not self.entitlements.can_use(module):
            # 規則一：不能假裝不知道
            trace.append(
                TraceEvent(step="entitlement", duration_ms=0.0, detail=f"{module.id} 未解鎖")
            )
            return RoutedResponse(
                coverage=CoverageStatus.COVERED_LOCKED,
                risk_level=RiskLevel.MEDIUM,
                locked_notice=self.entitlements.locked_notice(module),
                hints=hints,
                general_advice=GENERAL_ADVICE,
                scores=decision.scores,
                trace=trace,
            )

        analyze_started = time.perf_counter()
        try:
            verdict = module.instance.analyze(payload)
        except Exception as exc:  # 模組壞掉要降級，不是整個當掉
            trace.append(
                TraceEvent(
                    step=f"analyze:{module.id}",
                    duration_ms=(time.perf_counter() - analyze_started) * 1000,
                    status="failed",
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
            return self._uncovered(decision, trace, hints=hints)

        trace.append(
            TraceEvent(
                step=f"analyze:{module.id}",
                duration_ms=(time.perf_counter() - analyze_started) * 1000,
                status="ok",
                detail=None,
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
            return self._uncovered(decision, trace, hints=hints, risk_level=verdict.risk_level)

        return RoutedResponse(
            coverage=CoverageStatus.COVERED,
            risk_level=checked.risk_level,
            verdict=checked,
            hints=hints,
            scores=decision.scores,
            trace=trace,
        )
