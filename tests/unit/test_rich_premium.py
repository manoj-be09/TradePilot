"""Rich-day condors read only closed bars, respect breaks, and stay inside the per-trade limit."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from math import log, sqrt
from statistics import NormalDist

import pytest

from packages.backtesting.candidate_algorithms import CURRENT_LOT, MAXIMUM_WORST_TRADE
from packages.backtesting.history_backtest import _IST
from packages.backtesting.published_strategies import Bar, Book, Leg, Market, simulate
from packages.backtesting.rich_premium import (
    RICH_RULES,
    WING_POINTS,
    condor_strikes,
    exit_bar_for,
    forecast_variance,
    is_before_break,
    read_richness,
    run_rich_condor,
    session_moves,
    sessions_until_expiry,
)

_NORMAL = NormalDist()
# Monday to Friday, then the Monday to Thursday after it; expiry on the last day.
_SESSIONS = (date(2025, 3, 3), date(2025, 3, 4), date(2025, 3, 5), date(2025, 3, 6),
             date(2025, 3, 7), date(2025, 3, 10), date(2025, 3, 11), date(2025, 3, 12),
             date(2025, 3, 13))
_EXPIRY = _SESSIONS[-1]
_STRIKES = tuple(range(21000, 23050, 50))
_FORWARD = 22000.0
_BARS = 75


def _at(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=_IST)


def _moments(day: date) -> list[datetime]:
    return [_at(day, 9, 15) + timedelta(minutes=5 * at) for at in range(_BARS)]


def _price(kind: str, forward: float, strike: float, years: float, vol: float) -> float:
    root = vol * sqrt(years)
    d1 = log(forward / strike) / root + root / 2
    call = forward * _NORMAL.cdf(d1) - strike * _NORMAL.cdf(d1 - root)
    return call if kind == "CE" else call - forward + strike


def _years(moment: datetime) -> float:
    expires = datetime.combine(_EXPIRY, time(15, 30), tzinfo=_IST)
    return (expires - moment).total_seconds() / (365 * 86400)


def _book(sessions=_SESSIONS, vol: float = 0.15, wiggle: float = 0.0005, strikes=_STRIKES) -> Book:
    """Parity alternates by `wiggle` every bar, so each session's variance is known exactly."""
    bars: dict = {}
    for day in sessions:
        for at, moment in enumerate(_moments(day)):
            forward = _FORWARD * (1 + wiggle if at % 2 else 1)
            for strike in strikes:
                for kind in ("CE", "PE"):
                    price = max(_price(kind, forward, float(strike), _years(moment), vol), 0.05)
                    bars.setdefault((Decimal(strike), kind), {})[moment] = Bar(price, price, price,
                                                                               price)
    return Book(_EXPIRY, bars)


def test_session_variance_is_the_squared_parity_changes_plus_the_gap():
    moves = session_moves(Market([_book()], 0.0))
    assert [move.session for move in moves] == list(_SESSIONS)
    # The parity is read from closes that also carry time value, hence the small tolerance.
    change = log(1.0005)
    assert moves[1].variance == pytest.approx((_BARS - 1) * change * change, rel=0.02)


def test_forecast_blends_last_session_week_and_month():
    moves = session_moves(Market([_book()], 0.0))[:6]
    assert forecast_variance(moves[:4]) is None
    variances = [move.variance for move in moves]
    expected = (variances[-1] + sum(variances[-5:]) / 5 + sum(variances) / 6) / 3
    assert forecast_variance(moves) == pytest.approx(expected)


def test_each_break_before_expiry_counts_as_one_more_session():
    book = _book()
    # Friday's 09:20: Friday and the four sessions after it, one weekend, 5 of 375 minutes gone.
    assert sessions_until_expiry(book, _at(_SESSIONS[4], 9, 20)) == pytest.approx(6 - 5 / 375)
    # Tuesday after the weekend: three sessions left and no break.
    assert sessions_until_expiry(book, _at(_SESSIONS[6], 9, 20)) == pytest.approx(3 - 5 / 375)


def test_richness_is_implied_move_over_forecast_move():
    book = _book()
    market = Market([book], 0.0)
    moves = session_moves(market)
    decided = _at(_SESSIONS[6], 9, 20)
    past = moves[:6]
    reading = read_richness(book, decided, past)
    closed = _at(_SESSIONS[6], 9, 15)
    straddle = (book.bars[(Decimal(22000), "CE")][closed].close
                + book.bars[(Decimal(22000), "PE")][closed].close)
    expected = (straddle / (0.8 * _FORWARD)
                / sqrt(forecast_variance(past) * (3 - 5 / 375)))
    assert reading == pytest.approx(expected, rel=0.001)


def test_richness_ignores_every_bar_after_the_decision():
    book = _book()
    moves = session_moves(Market([book], 0.0))
    decided = _at(_SESSIONS[6], 9, 20)
    before = read_richness(book, decided, moves[:6])
    for series in book.bars.values():
        for moment in series:
            if moment >= decided:
                series[moment] = Bar(9999.0, 9999.0, 9999.0, 9999.0)
    assert read_richness(book, decided, moves[:6]) == before


def test_richness_refuses_a_centre_far_from_the_market():
    # Every listed strike sits above 22100, like a ladder left behind by a fast fall.
    book = _book(strikes=tuple(range(22150, 23050, 50)))
    moves = [move for move in session_moves(Market([_book()], 0.0))]
    assert read_richness(book, _at(_SESSIONS[6], 9, 20), moves[:6]) is None


def test_the_session_before_a_break_is_skipped_unless_it_is_expiry_day():
    book = _book()
    assert is_before_break(book, _SESSIONS[4], _SESSIONS[5])
    assert not is_before_break(book, _SESSIONS[5], _SESSIONS[6])
    assert not is_before_break(book, _EXPIRY, date(2025, 3, 17))
    assert not is_before_break(book, _SESSIONS[5], None)


def test_exit_is_the_same_day_or_the_next_close_but_never_overnight_into_expiry():
    book = _book()
    monday, tuesday, wednesday = _SESSIONS[5], _SESSIONS[6], _SESSIONS[7]
    assert exit_bar_for(book, monday, tuesday, False) == _at(monday, 15, 10)
    assert exit_bar_for(book, monday, tuesday, True) == _at(tuesday, 15, 10)
    # Wednesday's next session is expiry day, so even the held form is out on Wednesday.
    assert exit_bar_for(book, wednesday, _EXPIRY, True) == _at(wednesday, 15, 10)
    assert exit_bar_for(book, _EXPIRY, None, True) == _at(_EXPIRY, 15, 5)
    assert exit_bar_for(book, monday, None, True) is None


def test_wings_sit_a_fixed_distance_beyond_the_sold_strikes():
    book = _book()
    strikes = condor_strikes(book, _at(_SESSIONS[6], 9, 20), 0.2)
    assert strikes is not None
    sold_call, sold_put, far_call, far_put = strikes
    assert far_call - sold_call == WING_POINTS
    assert sold_put - far_put == WING_POINTS
    assert sold_put < Decimal(22000) < sold_call


def test_a_missing_wing_means_no_trade():
    book = _book()
    strikes = condor_strikes(book, _at(_SESSIONS[6], 9, 20), 0.2)
    assert strikes is not None
    del book.bars[(strikes[2], "CE")]
    assert condor_strikes(book, _at(_SESSIONS[6], 9, 20), 0.2) is None


def test_one_lot_fits_the_per_trade_limit_whatever_the_credit():
    assert WING_POINTS * CURRENT_LOT < MAXIMUM_WORST_TRADE


def test_rule_trades_only_at_or_above_its_threshold():
    market = Market([_book()], 0.0)
    moves = session_moves(market)
    readings = [read_richness(move.book, _at(move.session, 9, 20), moves[max(0, at - 22):at])
                for at, move in enumerate(moves)]
    known = [reading for reading in readings if reading is not None]
    assert known
    assert run_rich_condor(0.3, max(known) + 1.0, False)(market) == []
    traded = run_rich_condor(0.3, min(known) - 0.01, False)(market)
    # Every reading passes, so the only sessions left out are the Friday before the weekend.
    assert {trade.session for trade in traded} == set(_SESSIONS[5:])


def test_rule_enters_later_when_asked_and_keeps_the_same_sessions():
    market = Market([_book()], 0.0)
    at_once = run_rich_condor(0.3, 0.0, False)(market)
    delayed = run_rich_condor(0.3, 0.0, False, entry_delay_bars=1)(market)
    assert [trade.session for trade in delayed] == [trade.session for trade in at_once]


def _quiet_book() -> tuple[Book, list[Decimal]]:
    first, second = _SESSIONS[5], _SESSIONS[6]
    bars = {
        (Decimal(22000), "CE"): {_at(first, 9, 20): Bar(100, 100, 100, 100)},
        (Decimal(22300), "CE"): {_at(first, 9, 20): Bar(20, 20, 20, 20)},
    }
    # Another contract prints on the exit day, so that session exists.
    bars[(Decimal(21000), "PE")] = {_at(second, 15, 10): Bar(5, 5, 5, 5)}
    return Book(_EXPIRY, bars), [Decimal(22000), Decimal(22300)]


def test_quiet_legs_drop_the_trade_by_default():
    book, strikes = _quiet_book()
    legs = [Leg(0, "CE", "SELL"), Leg(0, "CE", "BUY")]
    assert simulate(book, legs, strikes, _at(_SESSIONS[5], 9, 20), _at(_SESSIONS[6], 15, 10),
                    0.0) is None


def test_quiet_exit_counts_a_bought_leg_as_worthless_and_a_sold_leg_at_its_last_price():
    book, strikes = _quiet_book()
    legs = [Leg(0, "CE", "SELL"), Leg(0, "CE", "BUY")]
    result = simulate(book, legs, strikes, _at(_SESSIONS[5], 9, 20), _at(_SESSIONS[6], 15, 10),
                      0.0, quiet_exit=True)
    assert result is not None
    # The sold call never moved from 100; the bought wing bought at 20 counts as 0.05.
    assert result.gross_pnl == pytest.approx((100 - 100 + 0.05 - 20) * CURRENT_LOT)


def test_rules_are_defined_risk_and_no_more_than_the_holdout_allows():
    assert len(RICH_RULES) <= 3
    assert all(rule.defined_risk for rule in RICH_RULES)
