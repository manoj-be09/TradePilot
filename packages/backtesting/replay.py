import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from packages.backtesting.contracts import (
    BacktestLegResult,
    OptionsBacktestResult,
    ScheduledBacktestTrade,
    ScheduledOptionsBacktestResult,
)
from packages.market_data.contracts import (
    ExpiredOptionContract,
    HistoricalBar,
    MarketDataProvider,
    MarketDataProviderError,
)
from packages.options.models import PayoffLeg, PayoffRequest
from packages.options.payoff import calculate_expiry_payoff
from packages.paper_trading.strategies import StrategyDefinition

_CENT = Decimal("0.01")
_INDIA = ZoneInfo("Asia/Kolkata")
_NIFTY_INDEX_KEY = "NSE_INDEX|Nifty 50"


class BacktestDataError(ValueError):
    """Raised when the requested replay cannot be supported by returned history."""


def estimate_round_trip_cost(
    legs: list[BacktestLegResult],
    fee_per_order: Decimal,
    slippage_per_unit_per_side: Decimal,
) -> Decimal:
    if fee_per_order < 0 or slippage_per_unit_per_side < 0:
        raise BacktestDataError("Cost estimates cannot be negative")
    units = sum(leg.quantity_units for leg in legs)
    return (
        Decimal(2 * len(legs)) * fee_per_order
        + Decimal(2 * units) * slippage_per_unit_per_side
    ).quantize(_CENT, rounding=ROUND_HALF_UP)


def _observed_input_digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _session_date(bar: HistoricalBar) -> date:
    if bar.timestamp.tzinfo is None:
        raise BacktestDataError("Historical daily candles need timezone-aware timestamps")
    return bar.timestamp.astimezone(_INDIA).date()


def _valid_daily_bar(bar: HistoricalBar) -> bool:
    values = (bar.open, bar.high, bar.low, bar.close)
    return (
        bar.timestamp.tzinfo is not None
        and all(value.is_finite() and value > 0 for value in values)
        and bar.low <= min(bar.open, bar.close)
        and max(bar.open, bar.close) <= bar.high
    )


def _require_daily_history(bars: list[HistoricalBar], label: str) -> None:
    if any(not _valid_daily_bar(bar) for bar in bars):
        raise BacktestDataError(f"{label} has an invalid daily candle")
    dates = [_session_date(bar) for bar in bars]
    if len(dates) != len(set(dates)):
        raise BacktestDataError(f"{label} has duplicate daily candles")


def _select_anchor_strike(
    contracts: list[ExpiredOptionContract], spot: Decimal, strategy: StrategyDefinition
) -> Decimal:
    strikes = sorted({contract.strike for contract in contracts})
    contract_keys = {(contract.strike, contract.option_type) for contract in contracts}
    offsets = {leg.strike_offset for leg in strategy.legs}
    candidates = [
        strikes[index]
        for index in range(len(strikes))
        if all(
            0 <= index + offset < len(strikes)
            and all(
                (strikes[index + offset], leg.option_type) in contract_keys
                for leg in strategy.legs
                if leg.strike_offset == offset
            )
            for offset in offsets
        )
    ]
    if not candidates:
        raise BacktestDataError("Historical expiry does not include all required strategy strikes")
    return min(candidates, key=lambda strike: (abs(strike - spot), strike))


async def run_single_expiry_replay(
    provider: MarketDataProvider,
    strategy: StrategyDefinition,
    *,
    expiry: date,
    entry_date: date,
    exit_date: date,
    lots: int,
    fee_per_order: Decimal = Decimal(0),
    slippage_per_unit_per_side: Decimal = Decimal(0),
) -> OptionsBacktestResult:
    if expiry >= date.today():
        raise BacktestDataError("Select an option expiry that has already passed")
    if entry_date >= exit_date or exit_date > expiry:
        raise BacktestDataError("Replay dates must satisfy entry < exit <= expiry")
    if lots < 1:
        raise BacktestDataError("Lots must be at least one")
    if fee_per_order < 0 or slippage_per_unit_per_side < 0:
        raise BacktestDataError("Cost estimates cannot be negative")

    previous_session_start = datetime.combine(
        entry_date - timedelta(days=15), time.min, tzinfo=timezone.utc
    )
    previous_session_end = datetime.combine(
        entry_date - timedelta(days=1), time.max, tzinfo=timezone.utc
    )
    spot_bars = await provider.get_historical(
        _NIFTY_INDEX_KEY, previous_session_start, previous_session_end, "1d"
    )
    _require_daily_history(spot_bars, "Prior NIFTY index history")
    previous_spot_bars = sorted(
        (bar for bar in spot_bars if _session_date(bar) < entry_date),
        key=lambda bar: bar.timestamp,
    )
    if not previous_spot_bars:
        raise BacktestDataError("No prior NIFTY daily candle is available to select the ATM strike")
    if (entry_date - _session_date(previous_spot_bars[-1])).days > 10:
        raise BacktestDataError("Prior NIFTY daily candle is too old for ATM strike selection")
    entry_reference_spot = previous_spot_bars[-1].close

    contracts = await provider.get_expired_option_contracts("NIFTY", expiry)
    contracts = [contract for contract in contracts if contract.expiry == expiry]
    if not contracts:
        raise BacktestDataError("No expired NIFTY option contracts were returned for this expiry")
    contract_identities = {(contract.strike, contract.option_type) for contract in contracts}
    if len(contract_identities) != len(contracts):
        raise MarketDataProviderError(
            "Expired option contract master contained duplicate contracts"
        )
    lot_sizes = {contract.lot_size for contract in contracts}
    if len(lot_sizes) != 1:
        raise BacktestDataError("Expired option contracts disagree on lot size")
    lot_size = next(iter(lot_sizes))
    reference_strike = _select_anchor_strike(contracts, entry_reference_spot, strategy)
    strikes = sorted({contract.strike for contract in contracts})
    reference_index = strikes.index(reference_strike)
    by_identity = {(contract.strike, contract.option_type): contract for contract in contracts}

    results: list[BacktestLegResult] = []
    payoff_legs: list[PayoffLeg] = []
    for strategy_leg in strategy.legs:
        strike = strikes[reference_index + strategy_leg.strike_offset]
        contract = by_identity.get((strike, strategy_leg.option_type))
        if contract is None:
            raise BacktestDataError("A required expired option contract is unavailable")
        candles = await provider.get_expired_historical(
            contract.instrument_key, entry_date, exit_date, "1d"
        )
        _require_daily_history(candles, f"Historical candles for {contract.trading_symbol}")
        session_bars = sorted(
            (bar for bar in candles if entry_date <= _session_date(bar) <= exit_date),
            key=lambda bar: bar.timestamp,
        )
        entry_bars = [bar for bar in session_bars if _session_date(bar) == entry_date]
        exit_bars = [bar for bar in session_bars if _session_date(bar) == exit_date]
        if len(entry_bars) != 1 or len(exit_bars) != 1:
            raise BacktestDataError(
                f"Historical candles for {contract.trading_symbol} must include both selected dates"
            )
        entry_bar = entry_bars[0]
        exit_bar = exit_bars[-1]
        entry_price = entry_bar.open
        exit_price = exit_bar.close
        if entry_price <= 0 or exit_price <= 0:
            raise BacktestDataError(
                f"Historical entry or exit price is unusable for {contract.trading_symbol}"
            )
        signed_lots = lots * strategy_leg.quantity_ratio
        if strategy_leg.side == "SELL":
            signed_lots = -signed_lots
        gross_pnl = (exit_price - entry_price) * signed_lots * lot_size
        results.append(
            BacktestLegResult(
                instrument_key=contract.instrument_key,
                trading_symbol=contract.trading_symbol,
                option_type=strategy_leg.option_type,
                side=strategy_leg.side,
                strike=strike,
                quantity_units=lots * strategy_leg.quantity_ratio * lot_size,
                entry_timestamp=entry_bar.timestamp,
                entry_price=entry_price,
                exit_timestamp=exit_bar.timestamp,
                exit_price=exit_price,
                gross_pnl=gross_pnl.quantize(_CENT, rounding=ROUND_HALF_UP),
            )
        )
        payoff_legs.append(
            PayoffLeg(
                option_type=strategy_leg.option_type,
                strike=strike,
                quantity=signed_lots,
                entry_premium=entry_price,
            )
        )

    payoff = calculate_expiry_payoff(
        PayoffRequest(
            underlying="NIFTY",
            multiplier=Decimal(lot_size),
            price_min=Decimal(0),
            price_max=max(strike for strike, _ in by_identity) * Decimal(2),
            grid_points=2,
            legs=payoff_legs,
        )
    )
    if payoff.maximum_loss is None or payoff.maximum_loss_unbounded:
        raise BacktestDataError("This strategy does not have a finite expiry loss")
    maximum_loss = payoff.maximum_loss.quantize(_CENT, rounding=ROUND_HALF_UP)
    if maximum_loss <= 0:
        raise BacktestDataError("The selected structure has no measurable maximum loss")
    gross_pnl = sum((leg.gross_pnl for leg in results), Decimal(0)).quantize(
        _CENT, rounding=ROUND_HALF_UP
    )
    estimated_cost = estimate_round_trip_cost(
        results, fee_per_order, slippage_per_unit_per_side
    )
    net_pnl = (gross_pnl - estimated_cost).quantize(_CENT, rounding=ROUND_HALF_UP)
    fingerprint_payload: dict[str, object] = {
        "provider": provider.provider_id,
        "strategy_key": strategy.key,
        "strategy_version": strategy.version,
        "expiry": expiry.isoformat(),
        "entry_date": entry_date.isoformat(),
        "exit_date": exit_date.isoformat(),
        "lots": lots,
        "fee_per_order": str(fee_per_order),
        "slippage_per_unit_per_side": str(slippage_per_unit_per_side),
        "prior_spot_timestamp": previous_spot_bars[-1].timestamp.isoformat(),
        "prior_spot_close": str(entry_reference_spot),
        "legs": [
            {
                "instrument_key": leg.instrument_key,
                "side": leg.side,
                "strike": str(leg.strike),
                "quantity_units": leg.quantity_units,
                "entry_timestamp": leg.entry_timestamp.isoformat(),
                "entry_price": str(leg.entry_price),
                "exit_timestamp": leg.exit_timestamp.isoformat(),
                "exit_price": str(leg.exit_price),
            }
            for leg in results
        ],
    }
    return OptionsBacktestResult(
        run_id=uuid4(),
        provider=provider.provider_id,
        mode="historical_single_expiry_replay",
        strategy_key=strategy.key,
        strategy_name=strategy.name,
        expiry=expiry,
        entry_date=entry_date,
        exit_date=exit_date,
        entry_reference_spot=entry_reference_spot,
        reference_strike=reference_strike,
        lot_size=lot_size,
        lots=lots,
        gross_pnl=gross_pnl,
        fee_per_order=fee_per_order,
        slippage_per_unit_per_side=slippage_per_unit_per_side,
        estimated_cost=estimated_cost,
        net_pnl=net_pnl,
        observed_input_digest=_observed_input_digest(fingerprint_payload),
        return_on_risk_net_pct=(
            net_pnl / (maximum_loss + estimated_cost) * Decimal(100)
        ).quantize(_CENT, rounding=ROUND_HALF_UP),
        maximum_loss_at_entry=maximum_loss,
        return_on_risk_pct=(gross_pnl / maximum_loss * Decimal(100)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ),
        legs=results,
        assumptions=[
            "Reference strike uses the prior NIFTY trading session close; no future spot is used.",
            "Entry uses each expired option's open on entry_date; "
            "exit uses its close on exit_date.",
            "Maximum loss is theoretical expiry payoff based on the observed entry premiums.",
            "This is one manually selected position, not repeated signal-based strategy testing.",
            "Estimated costs apply a declared flat fee per leg order and slippage "
            "per unit on entry and exit; taxes, margin, spread dynamics, and market "
            "impact remain unmodeled.",
        ],
        warnings=[
            "Historical option OHLC does not prove an executable fill at the "
            "displayed candle price."
        ],
    )


async def run_scheduled_expiry_replay(
    provider: MarketDataProvider,
    strategy: StrategyDefinition,
    *,
    start_date: date,
    end_date: date,
    entry_sessions_before_expiry: int,
    lots: int,
    fee_per_order: Decimal,
    slippage_per_unit_per_side: Decimal,
) -> ScheduledOptionsBacktestResult:
    """Replay one template at a fixed offset before each past expiry, with declared costs."""
    if start_date > end_date or (end_date - start_date).days > 120:
        raise BacktestDataError("Select a date range of at most 120 calendar days")
    if not 1 <= entry_sessions_before_expiry <= 5:
        raise BacktestDataError("Entry offset must be one to five trading sessions")
    if lots < 1 or fee_per_order < 0 or slippage_per_unit_per_side < 0:
        raise BacktestDataError("Lots must be positive and cost estimates cannot be negative")

    expiries = sorted(
        expiry
        for expiry in set(await provider.get_expired_option_expiries("NIFTY"))
        if start_date <= expiry <= end_date and expiry < datetime.now(_INDIA).date()
    )
    if not expiries:
        raise BacktestDataError("No past NIFTY option expiries are available in this range")
    if len(expiries) > 4:
        raise BacktestDataError("Select at most four expiries by narrowing the date range")

    spot_bars = await provider.get_historical(
        _NIFTY_INDEX_KEY,
        datetime.combine(start_date - timedelta(days=30), time.min, tzinfo=timezone.utc),
        datetime.combine(end_date, time.max, tzinfo=timezone.utc),
        "1d",
    )
    _require_daily_history(spot_bars, "NIFTY index session history")
    sessions = sorted({_session_date(bar) for bar in spot_bars})
    trades: list[ScheduledBacktestTrade] = []
    observed_inputs: list[dict[str, object]] = []
    cumulative = Decimal(0)
    peak = Decimal(0)
    max_drawdown = Decimal(0)
    for expiry in expiries:
        previous_sessions = [day for day in sessions if day < expiry]
        if expiry not in sessions or len(previous_sessions) < entry_sessions_before_expiry:
            raise BacktestDataError(
                f"NIFTY daily sessions are incomplete for expiry {expiry.isoformat()}"
            )
        entry_date = previous_sessions[-entry_sessions_before_expiry]
        if (expiry - entry_date).days > 14:
            raise BacktestDataError(
                f"NIFTY daily sessions have a gap before expiry {expiry.isoformat()}"
            )
        try:
            replay = await run_single_expiry_replay(
                provider, strategy, expiry=expiry, entry_date=entry_date,
                exit_date=expiry, lots=lots,
            )
        except BacktestDataError as error:
            raise BacktestDataError(f"Expiry {expiry.isoformat()}: {error}") from error
        cost = estimate_round_trip_cost(
            replay.legs, fee_per_order, slippage_per_unit_per_side
        )
        net_pnl = (replay.gross_pnl - cost).quantize(_CENT, rounding=ROUND_HALF_UP)
        cumulative += net_pnl
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
        trades.append(
            ScheduledBacktestTrade(
                expiry=expiry, entry_date=entry_date, exit_date=expiry,
                gross_pnl=replay.gross_pnl, estimated_cost=cost, net_pnl=net_pnl,
                cumulative_net_pnl=cumulative,
                maximum_loss_at_entry=replay.maximum_loss_at_entry,
            )
        )
        observed_inputs.append(
            {
                "expiry": expiry.isoformat(),
                "entry_reference_spot": str(replay.entry_reference_spot),
                "legs": [
                    {
                        "instrument_key": leg.instrument_key,
                        "quantity_units": leg.quantity_units,
                        "entry_timestamp": leg.entry_timestamp.isoformat(),
                        "entry_price": str(leg.entry_price),
                        "exit_timestamp": leg.exit_timestamp.isoformat(),
                        "exit_price": str(leg.exit_price),
                    }
                    for leg in replay.legs
                ],
            }
        )

    fingerprint_payload = {
        "provider": provider.provider_id,
        "strategy_key": strategy.key, "strategy_version": strategy.version,
        "entry_sessions_before_expiry": entry_sessions_before_expiry, "lots": lots,
        "fee_per_order": str(fee_per_order),
        "slippage_per_unit_per_side": str(slippage_per_unit_per_side),
        "observed_inputs": observed_inputs,
    }
    digest = _observed_input_digest(fingerprint_payload)
    return ScheduledOptionsBacktestResult(
        run_id=uuid4(), provider=provider.provider_id,
        mode="historical_scheduled_expiry_replay",
        strategy_key=strategy.key, strategy_name=strategy.name,
        strategy_version=strategy.version, start_date=start_date, end_date=end_date,
        entry_sessions_before_expiry=entry_sessions_before_expiry, lots=lots,
        fee_per_order=fee_per_order,
        slippage_per_unit_per_side=slippage_per_unit_per_side,
        trade_count=len(trades),
        gross_pnl=sum((trade.gross_pnl for trade in trades), Decimal(0)),
        estimated_cost=sum((trade.estimated_cost for trade in trades), Decimal(0)),
        net_pnl=cumulative, max_closed_trade_drawdown=max_drawdown,
        winning_trades=sum(trade.net_pnl > 0 for trade in trades),
        observed_input_digest=digest, trades=trades,
        assumptions=[
            "Entry is a fixed number of NIFTY trading sessions before each expiry; "
            "exit is on expiry.",
            "Each position uses the prior NIFTY close to select strikes and daily option "
            "open/close prices.",
            "Estimated costs are flat per leg order plus slippage per unit on both sides.",
            "Drawdown uses only closed-trade net P&L; intratrade exposure is not captured.",
            "The SHA-256 digest fingerprints observed selected inputs, not an immutable "
            "provider dataset.",
        ],
        warnings=[
            "This is a scheduled replay, not signal-driven testing or proof of executable fills.",
            "Fees omit taxes, exchange charges, margin, spread and market impact "
            "unless represented by the entered estimates.",
            "Missing historical data aborts the whole run; no expiry is silently skipped.",
        ],
    )
