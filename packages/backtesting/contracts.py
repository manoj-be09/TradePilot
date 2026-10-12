from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class BacktestLegResult(BaseModel):
    instrument_key: str
    trading_symbol: str
    option_type: Literal["CE", "PE"]
    side: Literal["BUY", "SELL"]
    strike: Decimal
    quantity_units: int
    entry_timestamp: datetime
    entry_price: Decimal
    exit_timestamp: datetime
    exit_price: Decimal
    gross_pnl: Decimal


class OptionsBacktestResult(BaseModel):
    strategy_version_id: UUID | None = None
    strategy_definition_hash: str | None = None
    run_id: UUID
    provider: str
    mode: Literal["historical_single_expiry_replay"]
    strategy_key: str
    strategy_name: str
    expiry: date
    entry_date: date
    exit_date: date
    entry_reference_spot: Decimal
    reference_strike: Decimal
    lot_size: int
    lots: int
    gross_pnl: Decimal
    fee_per_order: Decimal | None = None
    slippage_per_unit_per_side: Decimal | None = None
    estimated_cost: Decimal | None = None
    net_pnl: Decimal | None = None
    return_on_risk_net_pct: Decimal | None = None
    observed_input_digest: str | None = None
    maximum_loss_at_entry: Decimal
    return_on_risk_pct: Decimal
    legs: list[BacktestLegResult]
    assumptions: list[str]
    warnings: list[str]


class OptionsBacktestRunRecord(BaseModel):
    created_at: datetime
    result: OptionsBacktestResult


class ScheduledBacktestTrade(BaseModel):
    expiry: date
    entry_date: date
    exit_date: date
    gross_pnl: Decimal
    estimated_cost: Decimal
    net_pnl: Decimal
    cumulative_net_pnl: Decimal
    maximum_loss_at_entry: Decimal


class ScheduledOptionsBacktestResult(BaseModel):
    strategy_version_id: UUID | None = None
    strategy_definition_hash: str | None = None
    run_id: UUID
    provider: str
    mode: Literal["historical_scheduled_expiry_replay"]
    strategy_key: str
    strategy_name: str
    strategy_version: str
    start_date: date
    end_date: date
    entry_sessions_before_expiry: int
    lots: int
    fee_per_order: Decimal
    slippage_per_unit_per_side: Decimal
    trade_count: int
    gross_pnl: Decimal
    estimated_cost: Decimal
    net_pnl: Decimal
    max_closed_trade_drawdown: Decimal
    winning_trades: int
    observed_input_digest: str
    trades: list[ScheduledBacktestTrade]
    assumptions: list[str]
    warnings: list[str]


class ScheduledOptionsBacktestRunRecord(BaseModel):
    created_at: datetime
    result: ScheduledOptionsBacktestResult
