"""四個進入點（說明書 S4 / S14 第 2 點）。

這是你唯一要跟外界對齊的地方。外殼只透過這四個函式認識你，
裡面的 M1–M5 怎麼寫完全自由。
"""

from __future__ import annotations

from pathlib import Path

import yaml
from contracts import (
    AnalyzeInput,
    HealthCheck,
    HealthReport,
    ModuleInfo,
    PackSpec,
    Verdict,
)
from shared import models

from . import m1_corpus, m2_vision, m4_judgement, m5_agent

MODULE_DIR = Path(__file__).resolve().parent


class ConvenienceLogisticsModule:
    """模組 D —— 超商店到店物流 × 假冒物流平台客服（實名認證話術）。

    判準不是「對方像不像壞人」，是「官方流程有沒有這一步」。
    刑事警察局 2026-05-17 公告：賣貨便無需實名認證、金流簽署。
    """

    def __init__(self) -> None:
        self.pack = PackSpec.load(MODULE_DIR / "pack.yaml")
        self.playbook = (
            yaml.safe_load((MODULE_DIR / "playbook.yaml").read_text(encoding="utf-8")) or {}
        )

    # ── 進入點 1 ────────────────────────────────────────────
    def can_handle(self, payload: AnalyzeInput) -> float:
        """這個案子有多像我負責的類型，回 0 到 1。

        要保守：不確定就給低分，讓外殼判定「尚未涵蓋」。
        分錯科比查不到更糟。
        """
        if not self.pack.is_configured:
            # 平台 × 手法還沒決定的模組不搶案子
            return 0.0

        text = payload.text
        for image in payload.images:
            text += "\n" + m2_vision.read_screenshot(image).plain_text

        score = m4_judgement.keyword_score(
            text,
            positive=list(self.pack.route_terms) + list(self.pack.labels_canon),
            negative=list(self.pack.negative_terms),
        )
        platform_hit = any(t and t in text for t in self.pack.platform_terms)
        # 平台對得上才加分 —— 這是「平台 × 手法」這個分法在路由上的具體表現
        return min(1.0, score + (0.15 if platform_hit else 0.0))

    # ── 進入點 2 ────────────────────────────────────────────
    def analyze(self, payload: AnalyzeInput) -> Verdict:
        return m5_agent.run(payload, self.pack, self.playbook)

    # ── 進入點 3 ────────────────────────────────────────────
    def info(self) -> ModuleInfo:
        return ModuleInfo(
            id=self.pack.id,
            code=self.pack.code,
            name=self.pack.name,
            plan=self.pack.plan,
            platform=self.pack.platform,
            tactic=self.pack.tactic,
            owner=self.pack.owner,
            version=self.pack.version,
        )

    # ── 進入點 4 ────────────────────────────────────────────
    def health(self) -> HealthReport:
        """我準備好了沒。required=False 的項目壞了只會降級，不會擋啟動。"""
        checks = [
            HealthCheck(
                name="pack",
                ok=self.pack.is_configured,
                detail="平台 × 手法與標籤都填了"
                if self.pack.is_configured
                else "平台 × 手法尚未決定",
                required=False,
            ),
            HealthCheck(
                name="playbook",
                ok=bool(self.playbook.get("stages")),
                detail=f"{len(self.playbook.get('stages', []))} 個階段",
                required=True,
            ),
            HealthCheck(
                name="corpus",
                ok=bool(m1_corpus.load_local()),
                detail="自己的語料檔還沒切出來（S9）" if not m1_corpus.load_local() else "",
                required=False,
            ),
            HealthCheck(
                name="ocr",
                ok=m2_vision.OCR_ENGINE is not None,
                detail="OCR 引擎尚未選定（S11），有圖時會降級成純文字",
                required=False,
            ),
            HealthCheck(
                name="models",
                ok=models.all_locked(),
                detail="模型尚未鎖定（S3），M4 會走規則退路",
                required=False,
            ),
        ]
        return HealthReport(
            module_id=self.pack.id,
            ready=not [c for c in checks if not c.ok and c.required],
            checks=checks,
        )


def build_module() -> ConvenienceLogisticsModule:
    """外殼靠這個工廠函式拿到實例。函式名稱不能改（contracts.ENTRYPOINT_FACTORY）。"""
    return ConvenienceLogisticsModule()
