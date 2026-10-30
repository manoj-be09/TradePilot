from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from packages.backtesting.history_backtest import BacktestConfig, SkippedExpiry, run_expiry
from packages.backtesting.walk_forward import Candidate, walk_forward
from tests.unit.test_history_backtest import BULL_PUT, _history

EXPIRIES = [date(2026, 6, 2) + timedelta(weeks=week) for week in range(7)]


def _histories():
    # Spot drifts up through each expiry by a different amount, so settings differ.
    histories = []
    for index, expiry in enumerate(EXPIRIES):
        start = expiry - timedelta(days=4)
        rise = Decimal(20 * (index % 3))
        histories.append(_history(
            lambda moment, start=start, rise=rise: Decimal(25010) + (
                rise if moment.date() > start else Decimal(0)),
            days=(start, expiry), expiry=expiry,
        ))
    return histories


CANDIDATES = [
    Candidate("hold", {}),
    Candidate("quick target", {"target_profit_amount": Decimal(1)}),
]


def test_windows_roll_by_the_test_size_and_never_reuse_test_expiries():
    report = walk_forward(_histories(), BacktestConfig(strategy=BULL_PUT), CANDIDATES,
                          train_size=3, test_size=2)
    windows = report.windows
    assert [(w.train_first, w.test_first, w.test_last) for w in windows] == [
        (EXPIRIES[0], EXPIRIES[3], EXPIRIES[4]),
        (EXPIRIES[2], EXPIRIES[5], EXPIRIES[6]),
    ]
    tested = [trade.expiry for trade in report.out_of_sample()]
    assert tested == EXPIRIES[3:]
    assert len(tested) == len(set(tested))
    summary = report.summary()
    assert summary["out_of_sample"]["closed_trades"] == 4
    assert sum(summary["times_chosen"].values()) == 2


def test_each_window_picks_the_best_training_net_pnl():
    histories = _histories()
    base = BacktestConfig(strategy=BULL_PUT)
    report = walk_forward(histories, base, CANDIDATES, train_size=3, test_size=2)
    for window, start in zip(report.windows, (0, 2), strict=True):
        totals = []
        for candidate in CANDIDATES:
            config = replace(base, **candidate.overrides)
            outcomes = [run_expiry(history, config) for history in histories[start:start + 3]]
            totals.append(sum((o.net_pnl for o in outcomes if not isinstance(o, SkippedExpiry)),
                              Decimal(0)))
        best = max(range(len(CANDIDATES)), key=lambda index: (totals[index], -index))
        assert window.chosen == CANDIDATES[best].label
        assert window.train_net_pnl == totals[best]


def test_ties_go_to_the_earlier_candidate_and_bad_inputs_are_refused():
    same = [Candidate("first", {}), Candidate("second", {})]
    report = walk_forward(_histories(), BacktestConfig(strategy=BULL_PUT), same,
                          train_size=2, test_size=3)
    assert {window.chosen for window in report.windows} == {"first"}
    with pytest.raises(ValueError):
        walk_forward(_histories(), BacktestConfig(strategy=BULL_PUT), [], train_size=2, test_size=1)
    with pytest.raises(ValueError):
        walk_forward(_histories(), BacktestConfig(strategy=BULL_PUT), same,
                     train_size=0, test_size=1)
    with pytest.raises(ValueError):
        walk_forward(_histories(), BacktestConfig(strategy=BULL_PUT),
                     [Candidate("x", {}), Candidate("x", {})], train_size=2, test_size=1)
