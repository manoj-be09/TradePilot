from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from packages.paper_trading.rules import RuleMetric, RuleOperator


class StrategyLegDefinitionResponse(BaseModel):
    option_type: Literal["CE", "PE"]
    side: Literal["BUY", "SELL"]
    strike_offset: int
    quantity_ratio: int = Field(default=1, ge=1, le=10)


class OptionMarketConditionEvidenceResponse(BaseModel):
    condition: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    trade_count: int
    winning_trade_count: int
    average_net_pnl: Decimal | None = None
    average_r: Decimal | None = None


class StrategyDefinitionResponse(BaseModel):
    key: str
    name: str
    description: str
    version: str
    risk_profile: Literal["DEFINED_RISK"]
    lifecycle_status: Literal["PAPER_TEMPLATE"]
    expiry_scope: Literal["SAME_EXPIRY"] = "SAME_EXPIRY"
    legs: list[StrategyLegDefinitionResponse]
    closed_trade_count: int = 0
    winning_trade_count: int = 0
    qualified_automated_trade_count: int = 0
    qualified_automated_wins: int = 0
    qualified_policy_variant_count: int = 0
    market_condition_evidence: list[OptionMarketConditionEvidenceResponse] = Field(
        default_factory=list
    )
    observed_win_rate_pct: Decimal | None = None
    estimated_win_probability_pct: Decimal | None = None
    average_net_pnl: Decimal | None = None
    expected_net_pnl: Decimal | None = None


class PaperAccountResponse(BaseModel):
    initial_cash: Decimal
    cash_balance: Decimal
    kill_switch_enabled: bool
    open_trade_count: int
    open_max_loss: Decimal
    daily_realized_pnl: Decimal
    daily_loss_limit: Decimal
    daily_risk_remaining: Decimal
    live_trading_enabled: Literal[False] = False


class PaperLedgerReconciliationResponse(BaseModel):
    status: Literal["MATCHED", "MISMATCH"]
    initial_cash: Decimal
    recorded_cash_balance: Decimal
    expected_cash_balance: Decimal
    cash_difference: Decimal
    order_count: int = Field(ge=0)
    open_trade_count: int = Field(ge=0)
    closed_trade_count: int = Field(ge=0)
    issue_count: int = Field(ge=0)
    issues: list[str]


class PaperExitReconciliationResponse(BaseModel):
    trade_id: UUID
    status: Literal["MATCHED", "INCOMPLETE", "MISMATCH"]
    evaluated_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    last_decision: str | None
    detail: str


class PaperKillSwitchRequest(BaseModel):
    enabled: bool


class PaperTradeCustomLegRequest(BaseModel):
    option_type: Literal["CE", "PE"]
    side: Literal["BUY", "SELL"]
    strike: Decimal = Field(gt=0)
    ratio: int = Field(default=1, ge=1, le=10)


class PaperEntryConditionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: RuleMetric
    operator: RuleOperator
    threshold: Decimal = Field(ge=Decimal("-1000000000"), le=Decimal("1000000000"))


class PaperStrategyPresetCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    strategy_key: str = Field(min_length=1, max_length=64)
    entry_conditions: list[PaperEntryConditionRequest] = Field(default_factory=list, max_length=5)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Preset name cannot be blank")
        return value


class PaperStrategyPresetResponse(BaseModel):
    id: UUID
    name: str
    strategy_key: str
    entry_conditions: list[PaperEntryConditionRequest]
    created_at: datetime

    @classmethod
    def from_model(cls, model: Any) -> "PaperStrategyPresetResponse":
        created_at = model.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return cls(
            id=model.id,
            name=model.name,
            strategy_key=model.strategy_key,
            entry_conditions=[
                PaperEntryConditionRequest.model_validate(condition)
                for condition in model.entry_conditions or []
            ],
            created_at=created_at,
        )


class PaperTradeCreateRequest(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def reject_unsupported_index_selectors(cls, value: Any) -> Any:
        # Saved requests also contain provider/policy metadata; retain legacy parsing.
        if isinstance(value, dict) and any(
            key in value for key in ("underlying_key", "index")
        ):
            raise ValueError("Index-specific paper execution is not enabled yet")
        return value

    strategy_version_id: UUID | None = None
    underlying: str = Field(default="NIFTY", min_length=1, max_length=128)
    expiry_test_only: bool = False

    @model_serializer(mode="wrap")
    def serialize_index(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.underlying == "NIFTY":
            data.pop("underlying", None)
        if not self.expiry_test_only:
            data.pop("expiry_test_only", None)
        return data
    strategy_key: str = Field(min_length=1, max_length=64)
    expiry: date
    reference_strike: Decimal | None = Field(default=None, gt=0)
    lots: int = Field(ge=1, le=10)
    stop_loss_amount: Decimal | None = Field(default=None, gt=0)
    target_profit_amount: Decimal | None = Field(default=None, gt=0)
    trailing_stop_amount: Decimal | None = Field(default=None, gt=0)
    custom_legs: list[PaperTradeCustomLegRequest] | None = Field(
        default=None, min_length=1, max_length=4
    )
    entry_conditions: list[PaperEntryConditionRequest] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_strategy_legs(self) -> "PaperTradeCreateRequest":
        if self.expiry_test_only and (
            self.underlying != "NIFTY" or self.strategy_key not in {"long_call", "long_put"}
            or self.lots != 1 or self.strategy_version_id is not None or self.entry_conditions
            or any(value is not None for value in (
                self.stop_loss_amount, self.target_profit_amount, self.trailing_stop_amount,
            ))
        ):
            raise ValueError("Expiry test requires one NIFTY long call/put lot without saved rules")
        if self.strategy_key == "custom":
            if self.custom_legs is None:
                raise ValueError("Custom strategies must include between one and four option legs")
            identities = [(leg.option_type, leg.strike) for leg in self.custom_legs]
            if len(set(identities)) != len(identities):
                raise ValueError("A custom strategy cannot repeat the same option contract")
            if self.reference_strike is None:
                self.reference_strike = self.custom_legs[0].strike
        elif self.custom_legs is not None:
            raise ValueError("custom_legs can only be supplied for the custom strategy")
        elif self.reference_strike is None:
            raise ValueError("A reference strike is required for a strategy template")
        return self


class PaperDeploymentResponse(BaseModel):
    id: UUID
    strategy_key: str
    underlying: str = "NIFTY"
    expiry: date
    reference_strike: Decimal
    lots: int
    entry_conditions: list[PaperEntryConditionRequest]
    stop_loss_amount: Decimal | None
    target_profit_amount: Decimal | None
    trailing_stop_amount: Decimal | None
    status: Literal["WAITING_FOR_ENTRY", "FILLED", "EXPIRED", "CANCELLED", "REJECTED"]
    detail: str
    trade_id: UUID | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, model: Any) -> "PaperDeploymentResponse":
        body = PaperTradeCreateRequest.model_validate(model.request_data)
        assert body.reference_strike is not None
        return cls(
            id=model.id,
            strategy_key=body.strategy_key,
            underlying=body.underlying,
            expiry=body.expiry,
            reference_strike=body.reference_strike,
            lots=body.lots,
            entry_conditions=body.entry_conditions,
            stop_loss_amount=body.stop_loss_amount,
            target_profit_amount=body.target_profit_amount,
            trailing_stop_amount=body.trailing_stop_amount,
            status=model.status,
            detail=model.detail,
            trade_id=model.trade_id,
            created_at=model.created_at.replace(tzinfo=timezone.utc)
            if model.created_at.tzinfo is None else model.created_at,
            updated_at=model.updated_at.replace(tzinfo=timezone.utc)
            if model.updated_at.tzinfo is None else model.updated_at,
        )


class PaperEntryReconciliationResponse(BaseModel):
    trade_id: UUID
    status: Literal["MATCHED", "INCOMPLETE", "MISMATCH"]
    evaluated_count: int
    last_decision: str | None
    detail: str


class PaperExpirySettlementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settlement_date: date
    final_index_value: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    source_reference: str = Field(min_length=8, max_length=500)
    archive_sha256: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
    confirmed_final: bool = False

    @field_validator("source_reference")
    @classmethod
    def trim_source_reference(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 8:
            raise ValueError("Settlement source reference is too short")
        return value


class PaperExpiryIndexCloseReference(BaseModel):
    status: Literal["available", "unavailable"]
    trade_id: UUID
    expiry: date
    reason: str | None = None
    provider: str | None = None
    instrument_key: str | None = None
    candle_date: date | None = None
    daily_close: Decimal | None = None
    warning: str = (
        "Provider daily candle close is a cross-check only. It is not verified as the "
        "official NSE final settlement value and cannot settle this paper position."
    )


class PaperOfficialIndexCloseReference(BaseModel):
    status: Literal["available", "unavailable"]
    underlying: str = "NIFTY"
    index_name: str | None = None
    trade_id: UUID
    expiry: date
    reason: str | None = None
    daily_close: Decimal | None = None
    source_url: str | None = None
    archive_sha256: str | None = None
    retrieved_at: datetime | None = None
    warning: str = (
        "This is the NSE daily Nifty 50 index close, not an automatic settlement. "
        "Review any exchange corrections and the settlement preview before confirming."
    )


class PaperExpirySettlementLegResponse(BaseModel):
    instrument_key: str
    option_type: Literal["CE", "PE"]
    side: Literal["BUY", "SELL"]
    strike: Decimal
    quantity_units: int
    intrinsic_value: Decimal
    cash_flow: Decimal


class PaperExpirySettlementPreviewResponse(BaseModel):
    trade_id: UUID
    settlement_date: date
    final_index_value: Decimal
    source_reference: str
    settlement_cash_flow: Decimal
    realized_pnl: Decimal
    legs: list[PaperExpirySettlementLegResponse]
    warning: str = (
        "Operator-supplied final index value is not independently verified by this app. "
        "This is a paper cash-intrinsic settlement without charges, not a broker settlement record."
    )


class PaperOfficialExpirySettlementPreviewResponse(BaseModel):
    status: Literal["available", "unavailable"]
    trade_id: UUID
    expiry: date
    reason: str | None = None
    source_url: str | None = None
    archive_sha256: str | None = None
    retrieved_at: datetime | None = None
    preview: PaperExpirySettlementPreviewResponse | None = None
    warning: str = (
        "Read-only cash-intrinsic estimate from the NSE daily Nifty 50 close. "
        "Review exchange corrections and final settlement terms before recording anything."
    )


class PaperLegResponse(BaseModel):
    instrument_key: str
    trading_symbol: str | None
    option_type: Literal["CE", "PE"]
    side: Literal["BUY", "SELL"]
    strike: Decimal
    quantity_units: int
    entry_price: Decimal
    entry_bid: Decimal
    entry_ask: Decimal


class PaperTradeResponse(BaseModel):
    id: UUID
    strategy_key: str
    strategy_name: str
    underlying: str
    expiry: date
    reference_strike: Decimal
    lots: int
    lot_size: int
    max_loss: Decimal
    entry_cash_flow: Decimal
    entry_rule_snapshot: dict[str, Any]
    stop_loss_amount: Decimal | None
    target_profit_amount: Decimal | None
    trailing_stop_amount: Decimal | None
    peak_unrealized_pnl: Decimal
    exit_reason: Literal[
        "STOP_LOSS", "TARGET", "TRAILING_STOP", "EXPIRY_SQUARE_OFF",
        "MANUAL", "EXPIRY_SETTLEMENT"
    ] | None
    realized_pnl: Decimal | None
    unrealized_pnl: Decimal | None = None
    status: Literal["OPEN", "CLOSED"]
    legs: list[PaperLegResponse]
    opened_at: datetime
    closed_at: datetime | None
    valuation_warning: str | None = None
    execution_warnings: list[str] = Field(default_factory=list)
    modeled_costs: dict[str, Any] | None = None

    @classmethod
    def from_model(cls, model: Any) -> "PaperTradeResponse":
        opened_at = model.opened_at
        closed_at = model.closed_at
        if opened_at.tzinfo is None:
            opened_at = opened_at.replace(tzinfo=timezone.utc)
        if closed_at is not None and closed_at.tzinfo is None:
            closed_at = closed_at.replace(tzinfo=timezone.utc)
        return cls(
            id=model.id,
            strategy_key=model.strategy_key,
            strategy_name=model.strategy_name,
            underlying=model.underlying,
            expiry=model.expiry,
            reference_strike=model.reference_strike,
            lots=model.lots,
            lot_size=model.lot_size,
            max_loss=model.max_loss,
            entry_cash_flow=model.entry_cash_flow,
            entry_rule_snapshot=model.entry_rule_snapshot or {},
            stop_loss_amount=model.stop_loss_amount,
            target_profit_amount=model.target_profit_amount,
            trailing_stop_amount=model.trailing_stop_amount,
            peak_unrealized_pnl=model.peak_unrealized_pnl or Decimal(0),
            exit_reason=model.exit_reason,
            realized_pnl=model.realized_pnl,
            status=model.status,
            legs=[PaperLegResponse.model_validate(leg) for leg in model.legs["items"]],
            opened_at=opened_at,
            closed_at=closed_at,
        )


class PaperOrderResponse(BaseModel):
    id: UUID
    trade_id: UUID
    action: Literal["OPEN", "CLOSE"]
    status: Literal["EXECUTED"]
    net_cash_flow: Decimal
    fill_snapshot: dict[str, Any]
    created_at: datetime


class PaperTradeListResponse(BaseModel):
    trades: list[PaperTradeResponse]
    warnings: list[str]


class PaperKillSwitchResponse(BaseModel):
    enabled: bool
    updated_at: datetime


class PaperMonitorResponse(BaseModel):
    checked_at: datetime
    evaluated_count: int
    closed_trade_ids: list[UUID]
    unrealized_pnl: dict[UUID, Decimal]
    warnings: list[str]


class UserNotificationResponse(BaseModel):
    id: UUID
    event_type: str
    title: str
    message: str
    details: dict[str, Any]
    created_at: datetime
    read_at: datetime | None

    @classmethod
    def from_model(cls, model: Any) -> "UserNotificationResponse":
        created_at = model.created_at
        read_at = model.read_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if read_at is not None and read_at.tzinfo is None:
            read_at = read_at.replace(tzinfo=timezone.utc)
        return cls(
            id=model.id,
            event_type=model.event_type,
            title=model.title,
            message=model.message,
            details=model.details or {},
            created_at=created_at,
            read_at=read_at,
        )


class NotificationListResponse(BaseModel):
    notifications: list[UserNotificationResponse]
    unread_count: int
