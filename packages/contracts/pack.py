"""pack.yaml 的規格（說明書 S4 第 3 點）。

每個模組資料夾裡都要有一份 pack.yaml。外殼開機時掃描 packages/modules/，
讀這份檔案決定要不要載入、歸誰、免費還是付費、路由關鍵字是什麼。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import Plan


class Thresholds(BaseModel):
    """這個模組自己的門檻。can_handle 的分數超過 route_min 才算認領。"""

    model_config = ConfigDict(extra="forbid")

    route_min: float = Field(default=0.55, ge=0.0, le=1.0, description="認領門檻")
    route_hint_min: float = Field(
        default=0.35, ge=0.0, le=1.0, description="低於認領門檻但值得提醒的分數"
    )

    @model_validator(mode="after")
    def _hint_below_route(self) -> Thresholds:
        if self.route_hint_min > self.route_min:
            raise ValueError("route_hint_min 不能高於 route_min")
        return self


class KnowledgeRef(BaseModel):
    """適用法規與話術庫（S12 第 5 點）。條號要標版本日期。"""

    model_config = ConfigDict(extra="forbid")

    title: str
    article: str = ""
    version_date: str = ""
    note: str = ""


class PackSpec(BaseModel):
    """pack.yaml 的內容。欄位不符合會直接報錯，不會讓外殼載入壞掉的模組。"""

    model_config = ConfigDict(extra="forbid")

    # 身分
    id: str = Field(description="模組代號，要跟資料夾名一致")
    code: str = Field(description="A / B / C / D / E")
    name: str
    owner: str = ""
    version: str = "0.1.0"
    plan: Plan = Plan.PAID

    # 負責的組合。尚未決定時留空字串，外殼會把它當成「未設定」而不是錯誤
    platform: str = Field(default="", description="平台，例：Facebook。未決定留空")
    tactic: str = Field(default="", description="詐騙手法，例：網購詐騙。未決定留空")

    # 涵蓋範圍（S9）
    labels_canon: list[str] = Field(
        default_factory=list, description="整理後的分類名，從 S6 的對照表挑"
    )
    label_aliases: list[str] = Field(default_factory=list, description="官方原始的各種寫法")
    platform_terms: list[str] = Field(
        default_factory=list, description="你的平台在受害者口中會怎麼被講"
    )
    route_terms: list[str] = Field(
        default_factory=list, description="路由關鍵字：這一類特有的話術詞彙"
    )
    negative_terms: list[str] = Field(
        default_factory=list, description="出現就要扣分的詞，用來跟隔壁模組分開"
    )

    knowledge_refs: list[KnowledgeRef] = Field(default_factory=list)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    priority: int = Field(default=100, description="分數打平時誰先，數字小的先")

    # 交付物清單（S15 第 6 點的七項）
    deliverables: dict[str, Any] = Field(default_factory=dict)
    # 語料統計（S9 第 4 點）
    stats: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_configured(self) -> bool:
        """平台 × 手法決定了沒。沒決定的模組只會被載入，不會被路由認領。"""
        return bool(self.platform and self.tactic and self.labels_canon)

    @classmethod
    def load(cls, path: str | Path) -> PackSpec:
        path = Path(path)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)
