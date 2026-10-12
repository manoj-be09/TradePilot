"""Hand-calculated closed-trade reports, including size and missing-data boundaries."""

from decimal import Decimal as D

import pytest

from packages.backtesting.performance import PerformanceTrade, summarize


def test_net_statistics_with_breakeven_and_recovery_drawdown():
    # Closed equity: 100, 50, 50, 250, 100. Peak-to-trough = 150.
    rows = [PerformanceTrade(D(p), D(r)) for p, r in
            [(100, 50), (-50, 50), (0, 100), (200, 100), (-150, 100)]]
    result = summarize(rows)
    assert result["closed_trades"] == 5
    assert result["winning_trades"] == result["losing_trades"] == 2
    assert result["breakeven_trades"] == 1
    assert result["win_rate"] == 40
    assert result["net_pnl"] == 100
    assert result["average_net_pnl"] == 20
    assert result["average_win"] == 150
    assert result["average_loss"] == -100
    assert result["profit_factor"] == 1.5
    assert result["closed_trade_drawdown"] == 150
    # R returns: 2, -1, 0, 2, -1.5 => mean .3R, drawdown 1.5R.
    assert result["average_r"] == .3
    assert result["closed_trade_drawdown_r"] == 1.5


def test_variable_sizing_uses_each_original_risk_not_current_budget():
    rows = [PerformanceTrade(D(100), D(50)), PerformanceTrade(D(-500), D(500))]
    result = summarize(rows)
    assert result["average_net_pnl"] == -200
    assert result["average_r"] == .5
    scaled = summarize([PerformanceTrade(t.net_pnl * 10, t.initial_risk * 10) for t in rows])
    assert scaled["average_r"] == result["average_r"]
    assert scaled["closed_trade_drawdown_r"] == result["closed_trade_drawdown_r"]


@pytest.mark.parametrize("risk", [None, D(0), D(-2), D("NaN"), D("Infinity")])
def test_incomplete_original_risk_never_silently_uses_a_subset(risk):
    result = summarize([PerformanceTrade(D(50), D(25)), PerformanceTrade(D(-10), risk)])
    assert result["net_pnl"] == 40
    assert result["risk_recorded_trades"] == 1
    assert result["average_r"] is None
    assert result["closed_trade_drawdown_r"] is None


def test_empty_all_winning_all_losing_and_breakeven_samples():
    empty = summarize([])
    assert empty["win_rate"] is None and empty["average_net_pnl"] is None
    assert empty["profit_factor"] is None and empty["average_r"] is None
    win = summarize([PerformanceTrade(D(10), D(10))])
    assert win["profit_factor"] is None and win["profit_factor_status"] == "no_losses"
    loss = summarize([PerformanceTrade(D(-10), D(10))])
    assert loss["profit_factor"] == 0 and loss["win_rate"] == 0
    assert loss["closed_trade_drawdown"] == 10
    flat = summarize([PerformanceTrade(D(0), D(10))])
    assert flat["profit_factor"] is None and flat["win_rate"] == 0
    with pytest.raises(ValueError):
        summarize([PerformanceTrade(D("NaN"))])
