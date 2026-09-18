"""路由器 —— 像醫院的分診台（說明書 S7 第 2 點）。

問過每個載入的模組「這個案子像不像你負責的」，收集分數後決定交給誰。

外殼不需要懂任何一種詐騙 —— 每個模組自己判斷「這像不像我」，外殼只負責比大小。
所以新增第六個模組時，外殼一行都不用改。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from contracts import AnalyzeInput, TraceEvent

from .registry import LoadedModule, Registry


@dataclass
class RouteDecision:
    """路由結果。三種情況之一。"""

    primary: LoadedModule | None
    also_possible: list[tuple[LoadedModule, float]] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    trace: list[TraceEvent] = field(default_factory=list)

    @property
    def uncovered(self) -> bool:
        """都不夠高 → 尚未涵蓋，只給通用建議跟 165 導流。"""
        return self.primary is None


def _ask(module: LoadedModule, payload: AnalyzeInput) -> tuple[float, TraceEvent]:
    started = time.perf_counter()
    status = "ok"
    try:
        score = float(module.instance.can_handle(payload))
    except Exception as exc:  # 一個模組壞掉不該讓整個路由當掉（降級）
        score, status = 0.0, "failed"
        detail = f"can_handle 失敗：{exc}"
    else:
        score = max(0.0, min(1.0, score))
        detail = f"can_handle={score:.2f}"
    return score, TraceEvent(
        step=f"route:{module.id}",
        duration_ms=(time.perf_counter() - started) * 1000,
        status=status,
        detail=detail,
    )


def route(registry: Registry, payload: AnalyzeInput, *, manual: str | None = None) -> RouteDecision:
    """決定這題給誰。

    manual 是畫面上的手動切換 —— 那是最後一道保險，路由再怎麼準都要留著（S18）。
    """
    if manual:
        forced = registry.get(manual)
        if forced is not None:
            return RouteDecision(
                primary=forced,
                scores={forced.id: 1.0},
                trace=[
                    TraceEvent(step="route:manual", duration_ms=0.0, detail=f"手動指定 {manual}")
                ],
            )

    scores: dict[str, float] = {}
    trace: list[TraceEvent] = []
    claimed: list[tuple[LoadedModule, float]] = []
    hinted: list[tuple[LoadedModule, float]] = []

    for module in registry.usable:
        score, event = _ask(module, payload)
        scores[module.id] = score
        trace.append(event)

        thresholds = module.pack.thresholds
        if score >= thresholds.route_min:
            claimed.append((module, score))
        elif score >= thresholds.route_hint_min:
            hinted.append((module, score))

    if not claimed:
        # 分錯科比查不到更糟。寧可讓它落到「尚未涵蓋」，也不要硬猜。
        return RouteDecision(primary=None, also_possible=hinted, scores=scores, trace=trace)

    # 分數最高的出完整判讀；打平時看 priority，數字小的先
    claimed.sort(key=lambda pair: (-pair[1], pair[0].pack.priority))
    primary, _ = claimed[0]
    others = claimed[1:] + hinted

    return RouteDecision(primary=primary, also_possible=others, scores=scores, trace=trace)
