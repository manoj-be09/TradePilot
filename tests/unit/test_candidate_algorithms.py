"""Candidate rules read only what was known at arming, and pass only by clearing every gate."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from packages.backtesting.candidate_algorithms import (
    CANDIDATE_ALGORITHMS,
    CandidateResult,
    WeeklyResult,
    advances_from_development,
    holdout_failures,
    summarize_results,
)
from packages.backtesting.entry_signals import (
    EntrySignal,
    read_signal,
    read_signals,
    session_closes,
    trailing_rank,
)
from packages.backtesting.history_backtest import _IST, Candle, ExpiryHistory
from packages.paper_trading.strategies import STRATEGIES

_EXPIRY = date(2025, 3, 11)


def _bar(day: date, hour: int, minute: int, close: str) -> Candle:
    moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=_IST)
    return Candle(start=moment.astimezone(timezone.utc), close=Decimal(close))


def _history(open_bar_close: str = "100") -> ExpiryHistory:
    day = date(2025, 3, 4)
    return ExpiryHistory(expiry=_EXPIRY, lot_size=75, bars={
        (Decimal(22000), "CE"): [_bar(day, 9, 15, "100"), _bar(day, 9, 20, open_bar_close)],
        (Decimal(22000), "PE"): [_bar(day, 9, 15, "100"), _bar(day, 9, 20, open_bar_close)],
    })


def _closes() -> dict[date, float]:
    start = date(2025, 2, 1)
    return {start + timedelta(days=index): 22000.0 + (index % 3) * 50 for index in range(30)}


def _signal(**changes) -> EntrySignal:
    return EntrySignal(**{
        "expiry": _EXPIRY, "decided_at": datetime(2025, 3, 4, 9, 20, tzinfo=_IST),
        "spot": 22000.0, "implied_volatility": 0.14, "realized_volatility": 0.12,
        "volatility_premium": 1.2, "momentum": 0.01, "move_size": 0.5, "above_trend": True,
        "put_skew": 0.1, "premium_rank": 0.5, "skew_rank": 0.5,
    } | changes)


def _rule(name: str):
    return next(item for item in CANDIDATE_ALGORITHMS if item.name == name)


def test_a_bar_still_open_at_the_decision_cannot_change_the_signal():
    calm = read_signal(_history("100"), _closes(), 7)
    wild = read_signal(_history("900"), _closes(), 7)
    assert calm is not None
    assert calm == wild


def test_closes_from_the_decision_day_onward_are_ignored():
    closes = _closes()
    calm = read_signal(_history(), closes, 7)
    moved = read_signal(_history(), closes | {date(2025, 3, 4): 40000.0}, 7)
    assert calm == moved


def test_too_little_earlier_history_gives_no_signal():
    assert read_signal(_history(), {date(2025, 3, 3): 22000.0}, 7) is None


def test_session_close_comes_from_parity_at_the_last_bar():
    day = date(2025, 3, 4)
    history = ExpiryHistory(expiry=_EXPIRY, lot_size=75, bars={
        (Decimal(22000), "CE"): [_bar(day, 15, 25, "150")],
        (Decimal(22000), "PE"): [_bar(day, 15, 25, "100")],
    })
    assert session_closes([history]) == {day: 22050.0}


def test_rank_waits_for_enough_readings_and_uses_only_the_earlier_ones():
    assert trailing_rank(5.0, [1.0] * 7) is None
    assert trailing_rank(5.0, [1.0] * 4 + [9.0] * 4) == 0.5


def test_a_later_expiry_cannot_change_an_earlier_signal():
    start = date(2025, 1, 1)
    weekly = []
    for week in range(14):
        expiry = start + timedelta(days=7 * week + 7)
        day = expiry - timedelta(days=7)
        premium = str(90 + week * 3)
        weekly.append(ExpiryHistory(expiry=expiry, lot_size=75, bars={
            (Decimal(22000), "CE"): [
                _bar(day, 9, 15, premium),
                _bar(day + timedelta(days=week % 3 + 1), 15, 25, str(100 + week)),
            ],
            (Decimal(22000), "PE"): [
                _bar(day, 9, 15, premium),
                _bar(day + timedelta(days=week % 3 + 1), 15, 25, "100"),
            ],
        }))
    closes_only_earlier = read_signals(weekly[:-1], 7)
    with_later = read_signals(weekly, 7)
    for expiry, signal in closes_only_earlier.items():
        assert with_later[expiry].premium_rank == signal.premium_rank


def test_every_rule_names_only_real_templates():
    keys = {strategy.key for strategy in STRATEGIES}
    for rule in CANDIDATE_ALGORITHMS:
        assert set(rule.templates) <= keys


def test_rules_sit_out_until_ranks_exist_and_in_the_middle():
    for name in ("rich_vol_condor", "cheap_vol_straddle", "fade_skew", "volatility_switch"):
        assert _rule(name).pick(_signal(premium_rank=None, skew_rank=None)) is None
        assert _rule(name).pick(_signal()) is None


def test_rules_pick_their_named_side():
    assert _rule("volatility_switch").pick(_signal(premium_rank=0.9)) == "iron_butterfly"
    assert _rule("volatility_switch").pick(_signal(premium_rank=0.1)) == "long_straddle"
    assert _rule("trend_debit_spread").pick(_signal()) == "bull_call_spread"
    assert _rule("trend_debit_spread").pick(
        _signal(above_trend=False, momentum=-0.01)) == "bear_put_spread"
    assert _rule("trend_debit_spread").pick(_signal(above_trend=False)) is None
    assert _rule("fade_large_move").pick(_signal(move_size=-1.5)) == "bull_put_spread"
    assert _rule("fade_large_move").pick(_signal(move_size=1.5)) == "bear_call_spread"
    assert _rule("fade_skew").pick(_signal(skew_rank=0.9)) == "bull_put_spread"


def test_summary_counts_drawdown_in_expiry_order():
    results = [WeeklyResult(date(2025, 1, 2), 100.0), WeeklyResult(date(2025, 1, 9), -300.0),
               WeeklyResult(date(2025, 1, 16), 50.0)]
    summary = summarize_results(list(reversed(results)))
    assert summary.drawdown == 300.0
    assert summary.worst_trade == -300.0
    assert summary.total_net_pnl == -150.0
    assert summarize_results([]).trades == 0


def _result(**changes) -> CandidateResult:
    return CandidateResult(**{
        "trades": 30, "average_net_pnl": 500.0, "win_rate": 0.6, "t_value": 3.0,
        "worst_trade": -5000.0, "drawdown": 10000.0, "total_net_pnl": 15000.0,
    } | changes)


_EVERY_WEEK = _result(average_net_pnl=100.0, win_rate=0.5, t_value=0.5)


def test_development_needs_trades_edge_and_more_than_trading_every_week():
    assert advances_from_development(_result(), _EVERY_WEEK)
    assert not advances_from_development(_result(trades=14), _EVERY_WEEK)
    assert not advances_from_development(_result(t_value=1.4), _EVERY_WEEK)
    assert not advances_from_development(_result(average_net_pnl=50.0), _EVERY_WEEK)
    assert not advances_from_development(_result(win_rate=0.4), _EVERY_WEEK)


def test_holdout_names_each_failed_gate():
    assert holdout_failures(_result(), _EVERY_WEEK) == ()
    failed = holdout_failures(
        _result(average_net_pnl=-1.0, trades=5, t_value=None, win_rate=0.4,
                worst_trade=-25000.0, drawdown=70000.0), _EVERY_WEEK)
    assert len(failed) == 7
