from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OptionsBacktestRequest(BaseModel):
    strategy_version_id: UUID | None = None
    model_config = ConfigDict(extra="forbid")

    strategy_key: str = Field(min_length=1, max_length=64)
    expiry: date
    entry_date: date
    exit_date: date
    lots: int = Field(default=1, ge=1, le=100)
    fee_per_order: Decimal = Field(default=Decimal("20"), ge=0, le=1000, decimal_places=2)
    slippage_per_unit_per_side: Decimal = Field(
        default=Decimal("0.50"), ge=0, le=100, decimal_places=2
    )

    @model_validator(mode="after")
    def validate_dates(self) -> "OptionsBacktestRequest":
        if self.entry_date >= self.exit_date:
            raise ValueError("Entry date must be before exit date")
        if self.exit_date > self.expiry:
            raise ValueError("Exit date must be on or before option expiry")
        if (self.exit_date - self.entry_date).days > 45:
            raise ValueError("A single-expiry replay cannot span more than 45 calendar days")
        return self


class ScheduledOptionsBacktestRequest(BaseModel):
    strategy_version_id: UUID | None = None
    model_config = ConfigDict(extra="forbid")

    strategy_key: str = Field(min_length=1, max_length=64)
    start_date: date
    end_date: date
    entry_sessions_before_expiry: int = Field(default=2, ge=1, le=5)
    lots: int = Field(default=1, ge=1, le=100)
    fee_per_order: Decimal = Field(default=Decimal("20"), ge=0, le=1000, decimal_places=2)
    slippage_per_unit_per_side: Decimal = Field(
        default=Decimal("0.50"), ge=0, le=100, decimal_places=2
    )

    @model_validator(mode="after")
    def validate_range(self) -> "ScheduledOptionsBacktestRequest":
        if self.start_date > self.end_date:
            raise ValueError("Start date must be on or before end date")
        if (self.end_date - self.start_date).days > 120:
            raise ValueError("Scheduled replay cannot span more than 120 calendar days")
        return self
