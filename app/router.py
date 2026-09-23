"""路由器 —— 像醫院的分診台（說明書 S7 第 2 點）。

問過每個載入的模組「這個案子像不像你負責的」，收集分數後決定交給誰。

外殼不需要懂任何一種詐騙 —— 每個模組自己判斷「這像不像我」，外殼只負責比大小。
所以新增第六個模組時，外殼一行都不用改。

問之前先做一件事：有截圖就先認一次字（_read_images）。這不是外殼在判讀，
只是讓後面每個模組拿到的都是快取 —— 認字本身跟哪一種詐騙無關。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from contracts import AnalyzeInput, TraceEvent
from shared import models

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


def _read_images(payload: AnalyzeInput) -> list[TraceEvent]:
    """有截圖就在路由之前先認一次字，把結果放進 shared.models.ocr() 的快取。

    為什麼在這裡做：每個模組的 can_handle() 都要看截圖上的字，認領的模組
    analyze() 又要再看一次。這裡先認過，後面那些呼叫全部是快取命中 ——
    同一張圖從「模組數 + 認領數」次降到 1 次。

    認不出來不擋路由：模組自己有退路（只看打字的內容），這裡只記一筆。
    名字用 route: 開頭，app/ui.py 算總耗時時才會把它算進外殼的那一段。
    """
    if not payload.images:
        return []
    started = time.perf_counter()
    failures: list[str] = []
    for image in payload.images:
        try:
            models.ocr(image.path)
        except Exception as exc:  # 認字失敗要降級，不是讓整個路由當掉
            failures.append(f"{type(exc).__name__}: {exc}")
    total = len(payload.images)
    return [
        TraceEvent(
            step="route:ocr",
            duration_ms=(time.perf_counter() - started) * 1000,
            status="degraded" if failures else "ok",
            detail=(
                f"認出 {total - len(failures)}/{total} 張；{failures[0]}"
                if failures
                else f"認出 {total}/{total} 張"
            ),
        )
    ]


def route(registry: Registry, payload: AnalyzeInput, *, manual: str | None = None) -> RouteDecision:
    """決定這題給誰。

    manual 是畫面上的手動切換 —— 那是最後一道保險，路由再怎麼準都要留著（S18）。
    """
    ocr_trace = _read_images(payload)

    if manual:
        forced = registry.get(manual)
        if forced is not None:
            return RouteDecision(
                primary=forced,
                scores={forced.id: 1.0},
                trace=[
                    *ocr_trace,
                    TraceEvent(step="route:manual", duration_ms=0.0, detail=f"手動指定 {manual}"),
                ],
            )

    scores: dict[str, float] = {}
    trace: list[TraceEvent] = list(ocr_trace)
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
