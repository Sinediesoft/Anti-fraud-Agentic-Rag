"""M5 Agent 與行動劇本：把前面四步串成一條流水線（S14）。

用 Python 標準功能自己寫的狀態機，不裝額外框架。
好處是每一步都看得見，不是黑箱 —— 執行紀錄就是從這裡來的。

順序：收到輸入 →（有圖就先解析）→ 遮個資 → 分類 → 抽歷程 → 檢索案例與法條 → 合成結果
五個人的流程可以完全不一樣。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from contracts import (
    ActionItem,
    AnalyzeInput,
    CaseProfile,
    Confidence,
    LegalRef,
    RiskLevel,
    TraceEvent,
    Verdict,
)
from shared import deid

from . import m2_vision, m3_retrieval, m4_judgement

_RISK_BY_NAME = {
    "low": RiskLevel.LOW,
    "medium": RiskLevel.MEDIUM,
    "high": RiskLevel.HIGH,
    "critical": RiskLevel.CRITICAL,
}


@dataclass
class Context:
    """狀態機在各步驟之間傳遞的東西。"""

    payload: AnalyzeInput
    pack: Any
    playbook: dict
    screens: list[m2_vision.ScreenRead] = field(default_factory=list)
    combined_text: str = ""
    masked_text: str = ""
    judgement: m4_judgement.Judgement | None = None
    similar: list = field(default_factory=list)
    trace: list[TraceEvent] = field(default_factory=list)
    degraded_reasons: list[str] = field(default_factory=list)


def _step(ctx: Context, name: str, fn: Callable[[], None]) -> None:
    """跑一個步驟並記一筆執行紀錄。壞掉就降級，不讓整條流程當掉。"""
    started = time.perf_counter()
    status, detail = "ok", None
    try:
        fn()
    except Exception as exc:
        status = "degraded"
        detail = f"{type(exc).__name__}: {exc}"
        ctx.degraded_reasons.append(f"{name}: {detail}")
    ctx.trace.append(
        TraceEvent(
            step=name,
            duration_ms=(time.perf_counter() - started) * 1000,
            status=status,
            detail=detail,
        )
    )


def run(payload: AnalyzeInput, pack: Any, playbook: dict) -> Verdict:
    ctx = Context(payload=payload, pack=pack, playbook=playbook)

    def read_images() -> None:
        if not payload.images:
            return
        ctx.screens = m2_vision.read_all(payload.images)
        for s in ctx.screens:
            if s.degraded and s.reason:
                ctx.degraded_reasons.append(s.reason)

    def combine() -> None:
        # 多模態：把截圖上的文字跟使用者打的字合起來看（S13 第 6 點）
        parts = [payload.text, *(s.plain_text for s in ctx.screens)]
        ctx.combined_text = "\n".join(p for p in parts if p.strip())

    def apply_deid() -> None:
        # 在所有判讀之前先呼叫 shared.deid。這一步不准自己寫。
        ctx.masked_text = deid.mask(ctx.combined_text).text

    def classify_and_extract() -> None:
        ctx.judgement = m4_judgement.judge(
            ctx.combined_text,
            positive=list(pack.route_terms) + list(pack.labels_canon),
            negative=list(pack.negative_terms),
            platform_terms=list(pack.platform_terms),
            stages=playbook.get("stages", []),
            route_min=pack.thresholds.route_min,
        )

    def retrieve() -> None:
        ctx.similar = m3_retrieval.search(ctx.combined_text, top_k=5)

    _step(ctx, "m2:截圖理解", read_images)
    _step(ctx, "multimodal:合併輸入", combine)
    _step(ctx, "shared.deid:去識別化", apply_deid)
    _step(ctx, "m4:分類與抽取", classify_and_extract)
    _step(ctx, "m3:檢索相似案例", retrieve)

    return _compose(ctx)


def _compose(ctx: Context) -> Verdict:
    """合成結果。這一頁才是使用者真正拿到的東西。"""
    playbook = ctx.playbook
    stages: list[dict] = playbook.get("stages", [])
    judgement = ctx.judgement

    stage_id = (judgement.stage_id if judgement else "") or playbook.get("fallback_stage", "")
    stage = next((s for s in stages if s.get("id") == stage_id), None)
    if stage is None and stages:
        stage = stages[0]

    actions: list[ActionItem] = []
    if stage:
        for i, raw in enumerate(stage.get("actions", []), start=1):
            actions.append(
                ActionItem(
                    order=i,
                    text=raw.get("text", ""),
                    urgency=raw.get("urgency", "normal"),
                    why=raw.get("why"),
                    preventive=bool(raw.get("preventive", False)),
                )
            )

    risk = (
        _RISK_BY_NAME.get(str(stage.get("risk", "")).lower(), RiskLevel.UNKNOWN)
        if stage
        else RiskLevel.UNKNOWN
    )

    legal = [
        LegalRef(
            title=ref.title,
            article=ref.article,
            version_date=ref.version_date,
            excerpt=ref.note or None,
        )
        for ref in ctx.pack.knowledge_refs
    ]

    return Verdict(
        module_id=ctx.pack.id,
        risk_level=risk,
        scam_type=ctx.pack.tactic or ctx.pack.name,
        scam_stage=stage.get("name", "") if stage else "",
        stage_explanation=stage.get("explanation", "") if stage else "",
        similar_cases=ctx.similar,
        legal_refs=legal,
        actions=actions,
        profile=judgement.profile if judgement else CaseProfile(),
        confidence=judgement.confidence if judgement else Confidence.LOW,
        trace=ctx.trace,
        degraded=bool(ctx.degraded_reasons),
        degraded_reasons=ctx.degraded_reasons,
    )
