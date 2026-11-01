"""Published rules fill conservatively, read no future bars and pass only by clearing every gate."""

from datetime import date, datetime, time
from decimal import Decimal

import pytest

from packages.backtesting.candidate_algorithms import CandidateResult
from packages.backtesting.history_backtest import _IST
from packages.backtesting.published_strategies import (
    PUBLISHED_RULES,
    Bar,
    Book,
    Leg,
    Market,
    advances,
    holdout_failures,
    run_intraday,
    run_opening_range_breakout,
    run_overnight,
    run_weekly,
    simulate,
    spot_from_closed_bars,
    summarize_trades,
)

_DAY = date(2025, 3, 4)
_EXPIRY = date(2025, 3, 6)
_K = Decimal(22000)
_WING = Decimal(22050)
_STRADDLE = (Leg(0, "CE", "SELL"), Leg(0, "PE", "SELL"))


def _at(hour: int, minute: int, day: date = _DAY) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=_IST)


def _flat(price: float) -> Bar:
    return Bar(price, price, price, price)


def _book(*moves: tuple[datetime, float, float], expiry: date = _EXPIRY) -> Book:
    """Call and put bars at one strike, flat at each (moment, call, put)."""
    return Book(expiry, {
        (_K, "CE"): {moment: _flat(call) for moment, call, _ in moves},
        (_K, "PE"): {moment: _flat(put) for moment, _, put in moves},
    })


def test_a_flat_straddle_loses_only_spread_and_charges():
    book = _book((_at(9, 20), 100, 100), (_at(15, 10), 100, 100))
    result = simulate(book, _STRADDLE, [_K, _K], _at(9, 20), _at(15, 10), 1.0)
    # Sold at 99 twice and bought back at 101 twice, on 65 units.
    assert result.gross_pnl == pytest.approx(-4 * 65)
    assert result.charges > 0
    assert result.net_pnl < result.gross_pnl


def test_a_stop_fills_at_the_stop_and_the_other_leg_runs_on():
    book = Book(_EXPIRY, {
        (_K, "CE"): {_at(9, 20): _flat(100), _at(9, 25): Bar(100, 130, 100, 110),
                     _at(15, 10): _flat(300)},
        (_K, "PE"): {_at(9, 20): _flat(100), _at(9, 25): _flat(90), _at(15, 10): _flat(40)},
    })
    result = simulate(book, _STRADDLE, [_K, _K], _at(9, 20), _at(15, 10), 0.0,
                      leg_stop_share=0.25)
    # The call stops at 125 for -25 despite later reaching 300; the put keeps +60.
    assert result.gross_pnl == pytest.approx(35 * 65)


def test_a_stop_gapped_through_fills_at_the_worse_open():
    book = Book(_EXPIRY, {
        (_K, "CE"): {_at(9, 20): _flat(100), _at(9, 25): Bar(160, 170, 150, 165),
                     _at(15, 10): _flat(1)},
        (_K, "PE"): {_at(9, 20): _flat(100), _at(9, 25): _flat(100), _at(15, 10): _flat(100)},
    })
    result = simulate(book, _STRADDLE, [_K, _K], _at(9, 20), _at(15, 10), 0.0,
                      leg_stop_share=0.25)
    assert result.gross_pnl == pytest.approx(-60 * 65)


def test_a_stopped_short_leg_closes_its_wing():
    book = Book(_EXPIRY, {
        (_K, "CE"): {_at(9, 20): _flat(100), _at(9, 25): Bar(100, 130, 100, 120),
                     _at(15, 10): _flat(200)},
        (_WING, "CE"): {_at(9, 20): _flat(70), _at(9, 25): _flat(85), _at(15, 10): _flat(170)},
    })
    legs = (Leg(0, "CE", "SELL", wing=1), Leg(1, "CE", "BUY"))
    result = simulate(book, legs, [_K, _WING], _at(9, 20), _at(15, 10), 0.0, leg_stop_share=0.25)
    assert result.gross_pnl == pytest.approx((-25 + 15) * 65)


def test_a_target_closes_everything_at_that_bar():
    book = _book((_at(9, 20), 100, 100), (_at(9, 25), 40, 40), (_at(15, 10), 500, 500))
    result = simulate(book, _STRADDLE, [_K, _K], _at(9, 20), _at(15, 10), 0.0, target_share=0.5)
    assert result.gross_pnl == pytest.approx(120 * 65)


def test_a_leg_with_no_price_on_the_exit_day_is_not_filled_in():
    book = Book(_EXPIRY, {
        (_K, "CE"): {_at(9, 20): _flat(100), _at(15, 10): _flat(100)},
        (_K, "PE"): {_at(9, 20): _flat(100)},
    })
    later = _at(15, 10, date(2025, 3, 5))
    assert simulate(book, _STRADDLE, [_K, _K], _at(9, 20), later, 0.0) is None


def test_spot_uses_only_bars_closed_by_the_decision():
    book = _book((_at(9, 15), 150, 100), (_at(9, 20), 900, 100))
    assert spot_from_closed_bars(book, _at(9, 20)) == 22050.0
    assert spot_from_closed_bars(book, _at(9, 15)) is None


def test_intraday_rule_can_skip_expiry_day():
    moves = [(_at(9, 15, _EXPIRY), 100, 100), (_at(9, 20, _EXPIRY), 100, 100),
             (_at(15, 10, _EXPIRY), 50, 50)]
    market = Market([_book(*moves)], 0.0)
    assert len(run_intraday(_STRADDLE, None)(market)) == 1
    assert run_intraday(_STRADDLE, None, skip_expiry_day=True)(market) == []


def test_overnight_rule_sells_late_and_buys_after_the_next_open():
    next_day = date(2025, 3, 5)
    book = _book((_at(15, 10), 100, 100), (_at(15, 15), 100, 100),
                 (_at(9, 15, next_day), 80, 80))
    trades = run_overnight(_STRADDLE)(Market([book], 0.0))
    assert len(trades) == 1
    assert trades[0].gross_pnl == pytest.approx(40 * 65)


def test_overnight_rule_never_holds_an_expired_contract():
    book = _book((_at(15, 10), 100, 100), (_at(15, 15), 100, 100), expiry=_DAY)
    assert run_overnight(_STRADDLE)(Market([book], 0.0)) == []


def test_weekly_rule_exits_by_the_expiry_square_off():
    moves = [(_at(9, 15, date(2025, 3, 3)), 100, 100), (_at(9, 20, date(2025, 3, 3)), 100, 100),
             (_at(15, 5, _EXPIRY), 10, 10)]
    trades = run_weekly(_STRADDLE, None)(Market([_book(*moves)], 0.0))
    assert trades[0].gross_pnl == pytest.approx(180 * 65)


def test_weekly_rich_rule_sits_out_without_a_ranked_signal():
    moves = [(_at(9, 15, date(2025, 3, 3)), 100, 100), (_at(9, 20, date(2025, 3, 3)), 100, 100),
             (_at(15, 5, _EXPIRY), 10, 10)]
    assert run_weekly(_STRADDLE, None, only_when_rich=True)(Market([_book(*moves)], 0.0)) == []


def _breakout_book(stopped: bool) -> Book:
    calls = {(9, 15): 100, (9, 20): 102, (9, 25): 98, (9, 30): 130, (9, 35): 140,
             (9, 40): 60 if stopped else 150, (15, 10): 200}
    return Book(_EXPIRY, {
        (_K, "CE"): {_at(*clock): _flat(call) for clock, call in calls.items()},
        (_K, "PE"): {_at(*clock): _flat(100) for clock in calls},
    })


def test_breakout_buys_after_the_range_and_holds_to_the_exit():
    trades = run_opening_range_breakout(Market([_breakout_book(False)], 0.0))
    # The 09:30 bar breaks the range, so entry is the 09:35 open at 140.
    assert trades[0].gross_pnl == pytest.approx((200 - 140) * 65)


def test_breakout_stops_on_a_close_beyond_the_far_side_of_the_range():
    trades = run_opening_range_breakout(Market([_breakout_book(True)], 0.0))
    assert trades[0].gross_pnl == pytest.approx((60 - 140) * 65)


def test_naked_rules_are_labelled_because_paper_trading_cannot_arm_them():
    naked = {rule.name for rule in PUBLISHED_RULES if not rule.defined_risk}
    assert naked == {"straddle_920_leg_stop", "straddle_920_no_stop", "short_strangle_weekly",
                     "overnight_straddle"}


def test_summary_of_no_trades_is_empty():
    assert summarize_trades([]).trades == 0


def _result(**changes) -> CandidateResult:
    return CandidateResult(**{
        "trades": 30, "average_net_pnl": 500.0, "win_rate": 0.6, "t_value": 3.0,
        "worst_trade": -5000.0, "drawdown": 10000.0, "total_net_pnl": 15000.0,
    } | changes)


def test_advancing_needs_trades_profit_and_a_clear_reading():
    assert advances(_result())
    assert not advances(_result(trades=14))
    assert not advances(_result(average_net_pnl=-1.0))
    assert not advances(_result(t_value=1.4))


def test_holdout_names_each_failed_gate():
    assert holdout_failures(_result()) == ()
    failed = holdout_failures(_result(average_net_pnl=-1.0, trades=5, t_value=None,
                                      worst_trade=-25000.0, drawdown=70000.0))
    assert len(failed) == 5
