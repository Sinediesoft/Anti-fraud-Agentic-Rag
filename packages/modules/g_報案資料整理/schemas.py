"""使用者自己填的報案資料。【原文】——含真名、精確金額、完整帳號。

跟系統其他地方刻意相反：Verdict 為了隱私只留金額範圍（CaseProfile.amount_range）、
帳號也被 deid 遮掉，但報案需要的正是這些精確值。所以這份資料由使用者直接提供，
只在本機處理，不進任何模型。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PaymentMethod = Literal["轉帳", "ATM", "網路銀行", "超商代碼", "虛擬貨幣", "現金面交", "其他"]


class Reporter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    phone: str


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: datetime
    description: str


class Transfer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: datetime | None = None
    amount: int = Field(gt=0, description="新台幣，精確金額")
    method: PaymentMethod
    payee: str | None = Field(default=None, description="收款帳號、超商代碼或錢包地址")
    bank: str | None = None
    note: str | None = None


class Counterparty(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(description="電話 / LINE / 網址 / 帳號 / 其他")
    value: str


class ReportFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reporter: Reporter
    summary: str = Field(description="用自己的話講經過")
    events: list[Event] = Field(default_factory=list)
    transfers: list[Transfer] = Field(default_factory=list)
    counterparties: list[Counterparty] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    actions_taken: list[str] = Field(default_factory=list)
