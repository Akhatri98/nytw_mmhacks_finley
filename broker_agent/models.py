from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class TradeInstruction(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_id: str
    clerk_jwt: str
    ticker: str
    direction: Literal["buy", "sell"]
    quantity: Decimal
    trigger_signal_id: str | None = None
    notes: str | None = None


class ComplianceResult(BaseModel):
    approved: bool
    hard_blocks: list[dict]
    warnings: list[dict]


class TradeResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    success: bool
    trade_id: str | None = None
    status: Literal["executed", "failed", "cancelled"]
    price_executed: Decimal | None = None
    screenshot_url: str | None = None
    compliance: ComplianceResult
    error: str | None = None
