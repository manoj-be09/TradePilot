from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.backtesting.history_backtest import (
    BacktestConfig,
    Candle,
    ExpiryHistory,
    SkippedExpiry,
    fill_price,
    run_backtest,
    run_expiry,
    synthetic_spot,
)
from packages.database import history_models  # noqa: F401
from packages.database.base import Base
from packages.database.history_models import HistoricalImportContract, HistoricalOptionBar
from packages.history.loader import load_histories
from packages.paper_trading.strategies import get_strategy

IST = ZoneInfo("Asia/Kolkata")
EXPIRY = date(2026, 6, 30)
STRIKES = [Decimal(value) for value in range(24800, 25250, 50)]
BULL_PUT = get_strategy("bull_put_spread")
assert BULL_PUT is not None


def _moments(day: date) -> list[datetime]:
    start = datetime.combine(day, time(9, 15), tzinfo=IST)
    return [start + timedelta(minutes=5 * step) for step in range(75)]  # to 15:25


def _history(
    spot_at, days=(date(2026, 6, 26), EXPIRY), expiry=EXPIRY, time_value=lambda _: 10
) -> ExpiryHistory:
    """Calls and puts carry equal time value over intrinsic, so K + C - P == spot."""
    bars: dict = {}
    for day in days:
        for moment in _moments(day):
            spot = spot_at(moment)
            extrinsic = Decimal(time_value(moment))
            for strike in STRIKES:
                call = max(spot - strike, Decimal(0)) + extrinsic
                put = max(strike - spot, Decimal(0)) + extrinsic
                bars.setdefault((strike, "CE"), []).append(Candle(moment, call))
                bars.setdefault((strike, "PE"), []).append(Candle(moment, put))
    return ExpiryHistory(expiry=expiry, lot_size=75, bars=bars)


def test_fill_model_and_parity_spot():
    assert fill_price(Decimal(100), "BUY", Decimal(1)) == Decimal("101.00")
    assert fill_price(Decimal(100), "SELL", Decimal(1)) == Decimal("99.00")
    assert fill_price(Decimal("0.02"), "SELL", Decimal(1)) == Decimal("0.05")
    calls = {Decimal(25000): Decimal(40), Decimal(25050): Decimal(15)}
    puts = {Decimal(25000): Decimal(20), Decimal(25050): Decimal(45)}
    assert synthetic_spot(calls, puts) == Decimal(25020)
    assert synthetic_spot(calls, {}) is None


def test_flat_market_holds_to_expiry_square_off_and_charges_costs():
    trade = run_expiry(_history(lambda _: Decimal(25010)), BacktestConfig(strategy=BULL_PUT))
    assert not isinstance(trade, SkippedExpiry)
    assert trade.reference_strike == Decimal(25000)
    assert trade.synthetic_spot == Decimal(25010)
    assert trade.entry_at == datetime(2026, 6, 26, 9, 30, tzinfo=IST)
    assert trade.exit_reason == "EXPIRY_SQUARE_OFF"
    assert trade.exit_at == datetime(2026, 6, 30, 15, 10, tzinfo=IST)
    assert [(leg.strike, leg.side) for leg in trade.legs] == [
        (Decimal(25000), "SELL"), (Decimal(24950), "BUY"),
    ]
    # Same closes at entry and exit: the only loss is the modeled spread, plus charges.
    spread_cost = sum(
        (abs(leg.entry_price - leg.exit_price) * leg.quantity_units for leg in trade.legs),
        Decimal(0),
    )
    assert trade.gross_pnl == -spread_cost
    assert trade.charges > 0
    assert trade.net_pnl < trade.gross_pnl


def test_stop_is_checked_on_bar_closes_and_can_realize_more_than_the_stop():
    crash = datetime(2026, 6, 29, 11, 0, tzinfo=IST)

    def spot(moment):
        return Decimal(24700) if moment >= crash else Decimal(25010)

    history = _history(spot, days=(date(2026, 6, 26), date(2026, 6, 29), EXPIRY))
    trade = run_expiry(history, BacktestConfig(strategy=BULL_PUT, stop_loss_amount=Decimal(500)))
    assert not isinstance(trade, SkippedExpiry)
    assert trade.exit_reason == "STOP_LOSS"
    assert trade.exit_at == crash
    assert trade.gross_pnl < Decimal(-500)
    # Beyond maximum loss only by the modeled exit spread on deep in-the-money legs.
    exit_spread = sum((leg.exit_price * leg.quantity_units for leg in trade.legs), Decimal(0))
    assert trade.gross_pnl >= -trade.max_loss - exit_spread / 100


def test_target_exit_and_missing_expiry_day_is_skipped_not_filled():
    rally = datetime(2026, 6, 26, 12, 0, tzinfo=IST)
    history = _history(
        lambda moment: Decimal(25300) if moment >= rally else Decimal(24990),
        time_value=lambda moment: 2 if moment >= rally else 10,
    )
    trade = run_expiry(history, BacktestConfig(strategy=BULL_PUT, target_profit_amount=Decimal(1)))
    assert not isinstance(trade, SkippedExpiry)
    assert trade.exit_reason == "TARGET"

    no_expiry_day = _history(lambda _: Decimal(25010), days=(date(2026, 6, 26),))
    skipped = run_expiry(no_expiry_day, BacktestConfig(strategy=BULL_PUT))
    assert isinstance(skipped, SkippedExpiry)
    assert "expiry day" in skipped.reason


def test_report_folds_and_skips():
    histories = [
        _history(lambda _: Decimal(25010), expiry=EXPIRY),
        _history(lambda _: Decimal(25010), days=(date(2026, 6, 26),), expiry=date(2026, 7, 1)),
    ]
    report = run_backtest(histories, BacktestConfig(strategy=BULL_PUT))
    summary = report.summary(folds=2)
    assert summary["expiries_traded"] == 1
    assert summary["expiries_skipped"] == 1
    assert len(summary["folds"]) == 1
    assert summary["overall"]["closed_trades"] == 1
    assert summary["overall"]["exit_reasons"]["EXPIRY_SQUARE_OFF"] == 1
    assert summary["overall"]["cvar_5pct"] == summary["overall"]["net_pnl"]


def test_loader_uses_complete_contracts_only():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[
        HistoricalOptionBar.__table__, HistoricalImportContract.__table__,
    ])
    factory = sessionmaker(engine)
    now = datetime(2026, 6, 30, tzinfo=timezone.utc)
    with factory() as session:
        for key, status in (("K1", "COMPLETE"), ("K2", "FAILED")):
            session.add(HistoricalImportContract(
                instrument_key=key, interval="5m", source="test", trading_symbol=key,
                expiry=EXPIRY, strike=Decimal(25000), option_type="CE", lot_size=75,
                requested_from=EXPIRY, requested_to=EXPIRY, status=status, bar_count=1,
                first_bar=now, last_bar=now, detail="", imported_at=now,
            ))
            session.add(HistoricalOptionBar(
                instrument_key=key, interval="5m", bar_start=now, expiry=EXPIRY,
                strike=Decimal(25000), option_type="CE", open=Decimal(1), high=Decimal(1),
                low=Decimal(1), close=Decimal(1), volume=1, open_interest=None,
            ))
        session.commit()
        histories, rejected = load_histories(
            session, interval="5m", start=EXPIRY, end=EXPIRY
        )
    assert rejected == []
    assert len(histories) == 1
    assert histories[0].lot_size == 75
    assert [len(candles) for candles in histories[0].bars.values()] == [1]
