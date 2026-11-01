"""Global strategies pick strikes from closed bars and count every hedge and held-to-expiry cost."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from packages.backtesting.global_strategies import (
    GLOBAL_RULES,
    LegSpec,
    choose_strikes,
    hedge_result,
    option_delta,
    run_held,
    run_straddle_momentum,
    run_weekend_straddle,
    run_zero_day_condor,
    strike_at_delta,
)
from packages.backtesting.history_backtest import _IST
from packages.backtesting.published_strategies import Bar, Book, Leg, Market, simulate

_EXPIRY = date(2025, 3, 11)
_ENTRY = date(2025, 3, 4)


def _at(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=_IST)


def _flat(price: float) -> Bar:
    return Bar(price, price, price, price)


def _chain(day: date, clock: tuple[int, int], prices: dict[tuple[int, str], float]) -> dict:
    return {key: {_at(day, *clock): _flat(price)} for key, price in prices.items()}


def _merge(*parts: dict) -> dict:
    merged: dict = {}
    for part in parts:
        for (strike, kind), series in part.items():
            merged.setdefault((Decimal(strike), kind), {}).update(series)
    return merged


_PRICES = {
    (21800, "PE"): 5.0, (21900, "PE"): 30.0, (22000, "PE"): 150.0,
    (22000, "CE"): 150.0, (22100, "CE"): 30.0, (22200, "CE"): 5.0,
}


def _week(open_bar_price: float = 150.0) -> Book:
    return Book(_EXPIRY, _merge(
        _chain(_ENTRY, (9, 15), _PRICES),
        _chain(_ENTRY, (9, 20), {key: open_bar_price for key in _PRICES}),
        _chain(_EXPIRY, (15, 5), {key: 1.0 for key in _PRICES}),
    ))


def test_delta_has_the_right_sign_and_size():
    years = 7 / 365
    call = option_delta("CE", 150.0, 22000.0, 22000.0, years)
    put = option_delta("PE", 150.0, 22000.0, 22000.0, years)
    assert call == pytest.approx(0.5, abs=0.03)
    assert put == pytest.approx(-0.5, abs=0.03)


def test_strike_at_delta_finds_the_far_strike_and_refuses_a_poor_match():
    book = _week()
    decided = _at(_ENTRY, 9, 20)
    assert strike_at_delta(book, "PE", decided, 22000.0, 0.05) == Decimal(21800)
    assert strike_at_delta(book, "PE", decided, 22000.0, 0.9) is None


def test_strikes_use_only_bars_closed_by_the_decision():
    assert choose_strikes(_week(150.0), (LegSpec("PE", "SELL", delta=0.05),), _at(_ENTRY, 9, 20)) \
        == choose_strikes(_week(999.0), (LegSpec("PE", "SELL", delta=0.05),), _at(_ENTRY, 9, 20))


def test_a_wing_sits_beyond_its_sold_leg():
    specs = (LegSpec("CE", "SELL"), LegSpec("CE", "BUY", strikes_beyond=2, beyond_leg=0))
    assert choose_strikes(_week(), specs, _at(_ENTRY, 9, 20)) == [Decimal(22000), Decimal(22200)]
    too_far = (LegSpec("CE", "SELL"), LegSpec("CE", "BUY", strikes_beyond=9, beyond_leg=0))
    assert choose_strikes(_week(), too_far, _at(_ENTRY, 9, 20)) is None


def test_held_put_write_keeps_the_premium_when_it_expires_near_nothing():
    trades = run_held((LegSpec("PE", "SELL"),))(Market([_week()], 0.0))
    assert trades[0].gross_pnl == pytest.approx((150 - 1) * 65)


def test_a_callable_spread_prices_each_fill_by_its_premium():
    book = _week()

    def pct(price: float) -> float:
        return 10.0 if price < 10 else 0.0

    result = simulate(book, [Leg(0, "PE", "SELL")], [Decimal(22000)], _at(_ENTRY, 9, 20),
                      _at(_EXPIRY, 15, 5), pct)
    # Sold at 150 with no spread; bought back at 1.00 plus 10%.
    assert result.gross_pnl == pytest.approx((150 - 1.1) * 65)


def test_hedge_counts_gain_and_cost_and_always_closes():
    gross, charges = hedge_result([
        (_ENTRY, 22000.0, 0.8), (date(2025, 3, 5), 21900.0, 0.1), (_EXPIRY, 21800.0, 0.0),
    ], 0.0)
    # Sold one lot at 22000 and bought it back at 21900.
    assert gross == pytest.approx(100 * 65)
    assert charges > 0


def test_a_hedge_still_open_at_the_last_reading_is_closed():
    gross, _ = hedge_result([(_ENTRY, 22000.0, -0.9), (_EXPIRY, 22100.0, -0.9)], 0.0)
    assert gross == pytest.approx(100 * 65)


def test_weekend_rule_trades_only_across_a_long_gap():
    friday, monday = date(2025, 3, 7), date(2025, 3, 10)
    tuesday = date(2025, 3, 11)
    prices = {(22000, "CE"): 100.0, (22000, "PE"): 100.0}
    book = Book(date(2025, 3, 13), _merge(
        _chain(friday, (15, 10), prices), _chain(friday, (15, 15), prices),
        _chain(monday, (9, 15), {key: 80.0 for key in prices}),
        _chain(monday, (15, 10), prices), _chain(monday, (15, 15), prices),
        _chain(tuesday, (9, 15), prices),
    ))
    trades = run_weekend_straddle(Market([book], 0.0))
    assert len(trades) == 1
    assert trades[0].session == friday


def test_zero_day_condor_needs_an_expiry_session():
    assert run_zero_day_condor(Market([_week()], 0.0)) == []


def test_momentum_waits_for_four_finished_weeks():
    books = []
    for week in range(5):
        entry = date(2025, 3, 3) + timedelta(days=week * 7)
        expiry = entry + timedelta(days=6)
        books.append(Book(expiry, _merge(
            _chain(entry, (9, 15), {(22000, "CE"): 100.0, (22000, "PE"): 100.0}),
            _chain(entry, (9, 20), {(22000, "CE"): 100.0, (22000, "PE"): 100.0}),
            _chain(expiry, (15, 5), {(22000, "CE"): 400.0, (22000, "PE"): 100.0}),
        )))
    trades = run_straddle_momentum(Market(books, 0.0))
    assert [trade.expiry for trade in trades] == [books[4].expiry]


def test_naked_rules_are_labelled_because_paper_trading_cannot_arm_them():
    assert {rule.name for rule in GLOBAL_RULES if not rule.defined_risk} == {
        "weekly_put_write", "far_put_write", "near_strangle", "delta_hedged_straddle",
        "weekend_straddle",
    }
