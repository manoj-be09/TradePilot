from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OptionGreeksInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option_type: Literal["CE", "PE"]
    underlying_price: Decimal = Field(gt=0)
    strike: Decimal = Field(gt=0)
    valuation_time: datetime
    expiration_time: datetime
    volatility: Decimal = Field(
        gt=0, le=5, description="Annualized volatility as a decimal; 0.20 means 20%."
    )
    risk_free_rate: Decimal = Field(
        ge=-1, le=1, description="Annualized continuously compounded rate as a decimal."
    )
    dividend_yield: Decimal = Field(
        ge=-1, le=1, description="Annualized continuous yield as a decimal."
    )
    market_price: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_timezone_aware_times(self) -> "OptionGreeksInput":
        if self.valuation_time.tzinfo is None or self.expiration_time.tzinfo is None:
            raise ValueError("Valuation and expiration times must include a timezone")
        return self


class OptionGreeksResult(BaseModel):
    option_type: Literal["CE", "PE"]
    model_price: Decimal
    delta: Decimal | None
    gamma: Decimal | None
    theta_per_calendar_day: Decimal | None
    vega_per_volatility_point: Decimal | None
    rho_per_rate_point: Decimal | None
    implied_volatility: Decimal | None
    implied_volatility_status: str
    assumptions: list[str]


class PayoffLeg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option_type: Literal["CE", "PE"]
    strike: Decimal = Field(gt=0)
    quantity: int = Field(description="Positive quantity buys; negative quantity sells.")
    entry_premium: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def reject_zero_quantity(self) -> "PayoffLeg":
        if self.quantity == 0:
            raise ValueError("Leg quantity cannot be zero")
        return self


class PayoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    underlying: str = Field(min_length=1, max_length=40)
    multiplier: Decimal = Field(gt=0, description="Contract lot multiplier supplied by the caller.")
    price_min: Decimal = Field(ge=0)
    price_max: Decimal = Field(gt=0)
    grid_points: int = Field(default=101, ge=2, le=501)
    legs: list[PayoffLeg] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_range(self) -> "PayoffRequest":
        if self.price_min >= self.price_max:
            raise ValueError("price_min must be less than price_max")
        return self


class PayoffPoint(BaseModel):
    underlying_price: Decimal
    profit_loss: Decimal


class PayoffResult(BaseModel):
    underlying: str
    multiplier: Decimal
    breakeven_prices: list[Decimal]
    maximum_profit: Decimal | None
    maximum_profit_unbounded: bool
    maximum_loss: Decimal | None
    maximum_loss_unbounded: bool
    points: list[PayoffPoint]
    assumptions: list[str]
    transaction_cost_scenario: dict[str, object] | None = None
