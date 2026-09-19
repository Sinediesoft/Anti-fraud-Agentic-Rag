"""介面規格：外殼與模組之間唯一的共同語言（說明書 S4）。

外殼不知道你模組裡面怎麼寫，它只知道該怎麼呼叫你。把呼叫方式訂死，
你裡面就可以隨便寫 —— OCR 用哪套、向量庫用哪個、流程怎麼排，完全自由。

欄位的敏感度標記
    【原文】  可能含受害者個資，絕對不准送進雲端模型
    【遮蔽】  已經過 shared.deid 處理，可以送雲端
    【中性】  系統自己產生的，沒有個資

W1 末凍結。要改必須全員同意，而且改完全員重跑分數。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 所有輸出都必須附上這句（說明書 S14 第 5 點）
DISCLAIMER = "本結果僅供參考，正式處理請撥打 165 或向警察機關報案。"

# 165 反詐騙諮詢專線，任何情況下都要顯示（S19 免費版硬規則）
HOTLINE = "165"


class Plan(StrEnum):
    """方案。免費版開放件數最大的那一組，其餘付費解鎖。"""

    FREE = "free"
    PAID = "paid"


class RiskLevel(StrEnum):
    """風險等級。永遠免費顯示，不因為沒解鎖就不給（S19）。"""

    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def is_severe(self) -> bool:
        return self in (RiskLevel.HIGH, RiskLevel.CRITICAL)


class CoverageStatus(StrEnum):
    """這個案子有沒有人接得住。"""

    COVERED = "covered"  # 有模組認領，而且使用者解鎖得到
    COVERED_LOCKED = "covered_locked"  # 有模組認領，但方案沒解鎖 —— 仍要告知使用者
    UNCOVERED = "uncovered"  # 沒有模組夠有把握，只給通用建議與 165 導流


class Confidence(StrEnum):
    """這份判讀有多可信。格式退路啟動時要降到 LOW（S13 第 4 點）。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ──────────────────────────────────────────────────────────────
# 輸入
# ──────────────────────────────────────────────────────────────


class ImageInput(BaseModel):
    """使用者上傳的截圖。【原文】"""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="本機檔案路徑。截圖不離開使用者的電腦")
    filename: str | None = None
    note: str | None = Field(default=None, description="使用者自己對這張圖的說明")


class AnalyzeInput(BaseModel):
    """使用者丟進來的東西。【原文】——模組拿到後第一件事就是呼叫 shared.deid。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(default="", description="【原文】受害者用自己的話講的敘述")
    images: list[ImageInput] = Field(default_factory=list, description="【原文】截圖")
    session_id: str | None = Field(default=None, description="【中性】同一次諮詢的識別碼")
    locale: str = Field(default="zh-TW")

    @property
    def has_images(self) -> bool:
        return len(self.images) > 0

    @property
    def is_empty(self) -> bool:
        return not self.text.strip() and not self.images


class Redaction(BaseModel):
    """一處被遮掉的個資。【中性】——只記類型與位置，不記原值。"""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(description="person / phone / national_id / account / amount / url / ...")
    start: int
    end: int
    placeholder: str


class MaskedText(BaseModel):
    """去識別化過的文字。【遮蔽】

    這個型別本身就是那條界線：shared.models 的雲端呼叫只收 MaskedText，
    收到 str 就報錯。所以「原文不進雲端」是程式強制的，不是註解提醒的。
    只能由 shared.deid.mask() 產生，模組不准自己組一個出來。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    redactions: list[Redaction] = Field(default_factory=list)
    deid_version: str = Field(description="產生它的去識別化規則版本，寫進實驗紀錄表用")

    @property
    def redaction_count(self) -> int:
        return len(self.redactions)


# ──────────────────────────────────────────────────────────────
# 輸出：Verdict 與它的零件
# ──────────────────────────────────────────────────────────────


class SimilarCase(BaseModel):
    """檢索到的相似案例。【遮蔽】

    沒有出處的結果不准回傳 —— 每一筆都要帶案例編號（S12 第 7 點）。
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, description="案例編號。空字串會被外殼擋下來")
    source: str = Field(
        default="165",
        description=(
            "這筆案例來自哪個語料來源。多來源時 case_id 會撞號 —— "
            "兩份資料各自從 1 開始編都很正常，所以出處要靠 source + case_id "
            "才唯一。預設 165 是因為它是主語料，加別的來源時務必明寫。"
        ),
    )
    excerpt: str = Field(description="【遮蔽】節錄或改寫，絕不投影真實受害者原文")
    date: str | None = Field(default=None, description="案件日期")
    county: str | None = Field(default=None, description="縣市")
    score: float = Field(default=0.0, ge=0.0, le=1.0, description="相似度")
    label: str | None = Field(default=None, description="這筆案例的手法分類")


class LegalRef(BaseModel):
    """法條或話術庫出處。條號要標版本日期，法規會修（S12 第 5 點）。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="例：中華民國刑法第 339 條")
    article: str = Field(description="條號")
    version_date: str = Field(description="版本日期。沒寫日期等於沒有出處")
    excerpt: str | None = None
    url: str | None = None


class ActionItem(BaseModel):
    """行動清單的一條。要具體到能執行。

    可以做：「立即撥打 165 並聯繫匯款銀行申請圈存」
    不能做：「提高警覺」
    """

    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=1, description="第幾條。第 1 條是這一類模組最有辨識度的地方")
    text: str = Field(min_length=1, description="要使用者做的事，動詞開頭")
    urgency: str = Field(default="normal", description="now / today / normal")
    why: str | None = Field(default=None, description="為什麼要做這件事")
    preventive: bool = Field(
        default=False,
        description="預防性提示：提醒接下來可能發生什麼，而不是現在要做什麼",
    )


class CaseProfile(BaseModel):
    """從一段自由敘述抽出來的歷程（說明書 S13 第 3 點）。

    六個共用欄位全隊一致，特有欄位由各模組自己定義，放進 module_specific。
    特有欄位要能真的影響行動清單，否則就是裝飾。
    """

    model_config = ConfigDict(extra="forbid")

    contact_platform: str | None = Field(default=None, description="接觸平台")
    moved_to: str | None = Field(default=None, description="轉去哪裡聊")
    payment_method: str | None = Field(default=None, description="怎麼付款")
    amount_range: str | None = Field(default=None, description="金額範圍，不放精確金額")
    scam_stage: str | None = Field(default=None, description="目前走到詐騙的哪一步")
    days_elapsed: int | None = Field(default=None, ge=0, description="拖了幾天")

    module_specific: dict[str, Any] = Field(default_factory=dict, description="這一類特有的欄位")
    field_confidence: dict[str, float] = Field(
        default_factory=dict, description="每個欄位的信心值，退路抽取時要標低"
    )


class TraceEvent(BaseModel):
    """執行紀錄的一筆。

    這不是除錯工具，是展示重點 —— 放在看得見的地方（S7 注意事項）。
    """

    model_config = ConfigDict(extra="forbid")

    step: str = Field(description="例：deid / classify / retrieve / compose")
    duration_ms: float = Field(ge=0)
    status: str = Field(default="ok", description="ok / degraded / failed / skipped")
    detail: str | None = None
    started_at: datetime | None = None


class Verdict(BaseModel):
    """analyze() 的完整回傳（說明書 S4 第 2 點）。

    注意 scam_stage 的命名：指「詐騙進行到哪一步」，不是「我們處理到哪一步」。
    說明書特別警告這個欄位兩個人理解不同會到第五週才發現，所以名字裡直接寫死 scam。
    """

    model_config = ConfigDict(extra="forbid")

    module_id: str = Field(description="哪個模組出的判讀")
    risk_level: RiskLevel = RiskLevel.UNKNOWN
    scam_type: str = Field(default="", description="詐騙類型，用整理過的分類名")
    scam_stage: str = Field(default="", description="受害者目前走到詐騙的哪一步")
    stage_explanation: str = Field(default="", description="用白話解釋這一步發生什麼事")

    similar_cases: list[SimilarCase] = Field(default_factory=list)
    legal_refs: list[LegalRef] = Field(default_factory=list)
    actions: list[ActionItem] = Field(default_factory=list)
    profile: CaseProfile = Field(default_factory=CaseProfile)

    confidence: Confidence = Confidence.MEDIUM
    trace: list[TraceEvent] = Field(default_factory=list)
    degraded: bool = Field(default=False, description="有環節壞掉但沒整個當掉")
    degraded_reasons: list[str] = Field(default_factory=list)

    disclaimer: str = Field(default=DISCLAIMER)

    @field_validator("disclaimer")
    @classmethod
    def _disclaimer_must_exist(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("免責聲明不得為空")
        return v

    @property
    def elapsed_ms(self) -> float:
        return sum(e.duration_ms for e in self.trace)


# ──────────────────────────────────────────────────────────────
# 模組的身分與健康狀態
# ──────────────────────────────────────────────────────────────


class ModuleInfo(BaseModel):
    """info() 的回傳（說明書 S4 第 1 點）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="模組代號，例：a_tbd")
    code: str = Field(description="A / B / C / D / E")
    name: str = Field(description="顯示用名稱")
    plan: Plan = Plan.PAID
    platform: str = Field(default="", description="負責的平台。未決定時留空")
    tactic: str = Field(default="", description="負責的詐騙手法。未決定時留空")
    owner: str = Field(default="", description="負責人")
    version: str = Field(default="0.1.0")


class HealthCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="例：corpus / index / ocr / slm")
    ok: bool
    detail: str = ""
    required: bool = Field(default=True, description="False 表示壞了只會降級，不會擋啟動")


class HealthReport(BaseModel):
    """health() 的回傳：我準備好了沒。外殼開機掃描時會呼叫。"""

    model_config = ConfigDict(extra="forbid")

    module_id: str
    ready: bool
    checks: list[HealthCheck] = Field(default_factory=list)

    @property
    def failures(self) -> list[HealthCheck]:
        return [c for c in self.checks if not c.ok]

    @property
    def blocking_failures(self) -> list[HealthCheck]:
        return [c for c in self.checks if not c.ok and c.required]


# ──────────────────────────────────────────────────────────────
# 外殼給使用者的最終回應
# ──────────────────────────────────────────────────────────────


class ModuleHint(BaseModel):
    """「你可能同時也遇到這個」的提醒（S7 路由第二種結果）。"""

    model_config = ConfigDict(extra="forbid")

    module_id: str
    module_name: str
    score: float = Field(ge=0.0, le=1.0)
    locked: bool = False
    message: str = ""


class RoutedResponse(BaseModel):
    """外殼吐給介面的東西。模組只負責 Verdict，涵蓋與解鎖是外殼的事。"""

    model_config = ConfigDict(extra="forbid")

    coverage: CoverageStatus
    risk_level: RiskLevel = Field(
        default=RiskLevel.UNKNOWN, description="永遠顯示，不因未解鎖而隱藏"
    )
    verdict: Verdict | None = Field(default=None, description="未解鎖或未涵蓋時為 None")
    locked_notice: str = Field(default="", description="未解鎖時要對使用者說的話")
    hints: list[ModuleHint] = Field(default_factory=list)
    general_advice: list[str] = Field(default_factory=list, description="未涵蓋時的通用建議")
    hotline: str = Field(default=HOTLINE, description="永遠免費顯示")
    scores: dict[str, float] = Field(
        default_factory=dict, description="各模組的 can_handle 分數，展示用"
    )
    trace: list[TraceEvent] = Field(default_factory=list)
    disclaimer: str = Field(default=DISCLAIMER)
