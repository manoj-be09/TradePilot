"""Exploratory strategy results conditioned on earlier NIFTY daily conditions."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from packages.market_data.contracts import HistoricalBar
from packages.paper_trading.equity import STRATEGIES, money, replay_events
from packages.regime.outlook import MarketOutlook, describe_market

IST = ZoneInfo("Asia/Kolkata")
MIN_MATCHED_TRADES = 20


class ShadowFitRow(BaseModel):
    strategy: str
    name: str
    total_closed_trades: int
    classified_trades: int
    matching_trades: int
    minimum_matching_trades: int = MIN_MATCHED_TRADES
    status: Literal["insufficient", "exploratory"]
    matching_average_net_pnl: Decimal | None = None
    all_average_net_pnl: Decimal | None = None


class ShadowFitResult(BaseModel):
    status: Literal["ready", "unavailable"]
    reason: str | None = None
    provider: str
    symbol: str
    benchmark: str = "NIFTY 50"
    benchmark_session: date | None = None
    recent_direction: Literal["Rising", "Falling", "Mixed"] | None = None
    relative_volatility_label: str | None = None
    stock_history_from: date | None = None
    stock_history_to: date | None = None
    stock_bars: int = 0
    benchmark_bars: int = 0
    rows: list[ShadowFitRow] = Field(default_factory=list)
    ranking_changed: bool = False
    out_of_sample_validated: bool = False
    method: str = (
        "A replay trade is matched to NIFTY conditions known at its signal close. "
        "Matched-trade performance appears only after 20 matching closed trades. "
        "This same-sample comparison has not passed out-of-sample validation."
    )


def unavailable_fit(provider: str, symbol: str, reason: str) -> ShadowFitResult:
    return ShadowFitResult(status="unavailable", provider=provider, symbol=symbol, reason=reason)


def compare_shadow_fit(
    stock_bars: list[HistoricalBar], nifty_bars: list[HistoricalBar],
    current: MarketOutlook, *, provider: str, symbol: str, nifty_key: str,
    budget: Decimal, risk_budget: Decimal,
) -> ShadowFitResult:
    if (
        current.status != "ready" or current.session_date is None
        or current.recent_direction is None or current.relative_volatility_label is None
    ):
        return unavailable_fit(provider, symbol, "Current NIFTY conditions are unavailable.")
    if len(stock_bars) < 400 or len(nifty_bars) < 400:
        return unavailable_fit(provider, symbol, "At least two years of daily history are needed.")
    stock_dates = [bar.timestamp.astimezone(IST).date() for bar in stock_bars]
    nifty_dates = [bar.timestamp.astimezone(IST).date() for bar in nifty_bars]
    if (
        (stock_dates[-1] - stock_dates[0]).days < 730
        or (nifty_dates[-1] - nifty_dates[0]).days < 730
    ):
        return unavailable_fit(provider, symbol, "Daily history covers less than two years.")
    if any(left >= right for left, right in zip(nifty_dates, nifty_dates[1:], strict=False)):
        return unavailable_fit(provider, symbol, "NIFTY daily history is duplicated or unordered.")
    if any(left >= right for left, right in zip(stock_dates, stock_dates[1:], strict=False)):
        return unavailable_fit(provider, symbol, "Stock daily history is duplicated or unordered.")

    nifty_index = {day: index for index, day in enumerate(nifty_dates)}
    regime_cache: dict[date, tuple[str, str] | None] = {}

    def regime_at(signal_at: datetime) -> tuple[str, str] | None:
        day = signal_at.astimezone(IST).date()
        if day not in regime_cache:
            index = nifty_index.get(day)
            if index is None or index < 120:
                regime_cache[day] = None
            else:
                observed = describe_market(
                    nifty_bars[:index + 1], provider=provider,
                    instrument_key=nifty_key, expected_session=day,
                )
                regime_cache[day] = (
                    (observed.recent_direction, observed.relative_volatility_label)
                    if observed.status == "ready"
                    and observed.recent_direction is not None
                    and observed.relative_volatility_label is not None
                    else None
                )
        return regime_cache[day]

    current_pair = (current.recent_direction, current.relative_volatility_label)
    rows: list[ShadowFitRow] = []
    for key, (name, _) in STRATEGIES.items():
        trades, _ = replay_events(key, stock_bars, budget, risk_budget)
        classified = [(trade, regime_at(trade.signal_at)) for trade in trades]
        matching = [trade for trade, regime in classified if regime == current_pair]
        classified_count = sum(regime is not None for _, regime in classified)
        enough = len(matching) >= MIN_MATCHED_TRADES
        rows.append(ShadowFitRow(
            strategy=key, name=name, total_closed_trades=len(trades),
            classified_trades=classified_count, matching_trades=len(matching),
            status="exploratory" if enough else "insufficient",
            matching_average_net_pnl=(
                money(sum((trade.net_pnl for trade in matching), Decimal(0)) / len(matching))
                if enough else None
            ),
            all_average_net_pnl=(
                money(sum((trade.net_pnl for trade in trades), Decimal(0)) / len(trades))
                if trades else None
            ),
        ))
    return ShadowFitResult(
        status="ready", provider=provider, symbol=symbol,
        benchmark_session=current.session_date,
        recent_direction=current.recent_direction,
        relative_volatility_label=current.relative_volatility_label,
        stock_history_from=stock_dates[0], stock_history_to=stock_dates[-1],
        stock_bars=len(stock_bars), benchmark_bars=len(nifty_bars), rows=rows,
    )
