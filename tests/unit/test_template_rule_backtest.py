"""The history replay can follow a template's real paper entry and exit rules."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from packages.backtesting.history_backtest import (
    BacktestConfig,
    Candle,
    ExpiryHistory,
    SkippedExpiry,
    run_expiry,
)
from packages.paper_trading.strategies import get_strategy

IST = ZoneInfo("Asia/Kolkata")
EXPIRY = date(2026, 6, 30)
STRIKES = [Decimal(value) for value in range(24700, 25350, 50)]


def _moments() -> list[datetime]:
    start = datetime.combine(EXPIRY, time(9, 15), tzinfo=IST)
    return [start + timedelta(minutes=5 * step) for step in range(75)]


def _history(spot_at, oi_at=lambda _moment, _kind: None) -> ExpiryHistory:
    """Every option carries 100 points of time value, so a straddle is worth 200 + the move."""
    bars: dict = {}
    for index, moment in enumerate(_moments()):
        spot = Decimal(spot_at(index))
        for strike in STRIKES:
            for kind, intrinsic in (("CE", spot - strike), ("PE", strike - spot)):
                close = max(intrinsic, Decimal(0)) + 100
                bars.setdefault((strike, kind), []).append(
                    Candle(moment, close, open_interest=oi_at(index, kind))
                )
    return ExpiryHistory(expiry=EXPIRY, lot_size=65, bars=bars)


def _config(key: str, **overrides) -> BacktestConfig:
    strategy = get_strategy(key)
    assert strategy is not None
    return BacktestConfig(
        strategy=strategy, template_rules=True, half_spread_pct=Decimal(0),
        entry_days_before_expiry=0, entry_time=time(9, 20), **overrides,
    )


def test_trailing_stop_closes_after_a_gain_fades():
    # Straddle maximum loss 13,000: stop 5,200, target 7,800, trail 2,730 (42 points).
    path = lambda index: 25000 if index < 5 else 25060 if index < 10 else 25010  # noqa: E731
    trade = run_expiry(_history(path), _config("long_straddle"))
    assert not isinstance(trade, SkippedExpiry)
    assert trade.exit_reason == "TRAILING_STOP"
    assert trade.gross_pnl == Decimal("650.00")


def test_the_target_comes_from_the_entry_quotes():
    path = lambda index: 25000 if index < 5 else 25150  # noqa: E731
    trade = run_expiry(_history(path), _config("long_straddle"))
    assert not isinstance(trade, SkippedExpiry)
    assert trade.exit_reason == "TARGET"
    assert trade.max_loss == Decimal("13000.00")


def test_stop_uses_the_same_sizing():
    path = lambda index: 25000  # noqa: E731
    history = _history(path)
    # With time value collapsing, the straddle's value falls until the stop is hit.
    moments = _moments()
    for (strike, kind), candles in list(history.bars.items()):
        history.bars[(strike, kind)] = [
            Candle(moment, max(candle.close - Decimal(2 * index if index > 3 else 0), Decimal(1)))
            for index, (moment, candle) in enumerate(zip(moments, candles, strict=True))
        ]
    trade = run_expiry(history, _config("long_straddle"))
    assert not isinstance(trade, SkippedExpiry)
    assert trade.exit_reason == "STOP_LOSS"
    assert trade.gross_pnl <= Decimal("-5200")


def test_entry_waits_for_the_ratio_and_keeps_the_armed_strike():
    # Long put needs put/call OI at or below 1. It only passes from bar 8, by which time spot
    # has moved two strikes; the position still uses the strike from when it was armed.
    path = lambda index: 25000 if index < 8 else 25100  # noqa: E731
    oi = lambda index, kind: 100 if kind == "CE" else (300 if index < 8 else 50)  # noqa: E731
    trade = run_expiry(_history(path, oi), _config("long_put"))
    assert not isinstance(trade, SkippedExpiry)
    assert trade.entry_at == _moments()[8]
    assert trade.reference_strike == Decimal(25000)


def test_no_entry_when_the_ratio_never_passes():
    oi = lambda _index, kind: 100 if kind == "CE" else 300  # noqa: E731
    outcome = run_expiry(_history(lambda _: 25000, oi), _config("long_put"))
    assert isinstance(outcome, SkippedExpiry)
    assert "entry rules" in outcome.reason


def test_no_open_interest_is_unknown_and_never_enters():
    outcome = run_expiry(_history(lambda _: 25000), _config("long_put"))
    assert isinstance(outcome, SkippedExpiry)


def test_over_the_per_trade_limit_is_skipped():
    outcome = run_expiry(
        _history(lambda _: 25000), _config("long_straddle", max_trade_risk=Decimal(12999)),
    )
    assert isinstance(outcome, SkippedExpiry)
    assert "per-trade" in outcome.reason
