"""這個工具自己的報告格式。

不進 packages/contracts/——那層是 A-E 共用、W1 凍結的介面，服務的是
「受害者現在該怎麼辦」（Verdict）。合約違法比對是逐條法規比對，語境不同，
但零件借用 contracts 裡已經有的：LegalRef（法條出處）、ActionItem（建議行動）、
DISCLAIMER（免責聲明）。這裡只補一個「條款 x 法規」的關聯，Verdict 沒有這個欄位。
"""

from __future__ import annotations

from contracts import ActionItem, LegalRef
from pydantic import BaseModel, ConfigDict, Field

# 法律版免責聲明。跟 contracts.DISCLAIMER（撥打 165）語境不同——
# 這裡是合規檢查，不是詐騙判讀，不能沿用同一句。
LEGAL_DISCLAIMER = "本結果為 AI 初步比對，非正式法律意見，正式問題請洽律師或法律扶助基金會。"


class ClauseFinding(BaseModel):
    """一條條款的比對結果。"""

    model_config = ConfigDict(extra="forbid")

    clause_no: str = Field(description="合約裡的第幾條/項，例：第 7 條第 2 項")
    clause_text: str = Field(description="條款原文節錄")
    suspected_illegal: bool = Field(description="是否疑似牴觸。不確定一律 False，寫進 note")
    matched_refs: list[LegalRef] = Field(default_factory=list, description="命中的法規/公告出處")
    reason: str = Field(default="", description="為什麼疑似牴觸，白話說明")
    confidence: str = Field(
        default="low", description="low / medium / high，比照 contracts.Confidence 的精神"
    )
    suggested_actions: list[ActionItem] = Field(default_factory=list)


class ContractReviewReport(BaseModel):
    """cli.py 的完整輸出。"""

    model_config = ConfigDict(extra="forbid")

    source_filename: str = ""
    findings: list[ClauseFinding] = Field(default_factory=list)
    disclaimer: str = Field(default=LEGAL_DISCLAIMER)

    @property
    def suspected_count(self) -> int:
        return sum(1 for f in self.findings if f.suspected_illegal)
