"""A strategy only beats NO_TRADE by passing every gate; anything else means abstain."""

from packages.backtesting.no_trade import compare_with_no_trade


def _overall(**overrides):
    return {
        "closed_trades": 100, "winning_trades": 60, "losing_trades": 40, "average_net_pnl": 400.0,
        "average_win": 1000.0, "average_loss": -500.0, "worst_trade": -2000.0,
        "closed_trade_drawdown": 5000.0,
    } | overrides


FOLDS = [{"net_pnl": 100.0}, {"net_pnl": 200.0}]


def test_passing_every_gate_qualifies():
    result = compare_with_no_trade("a", _overall(), FOLDS)
    assert result.qualifies
    assert result.t_value is not None and result.t_value > 3.03


def test_a_loss_after_charges_never_beats_no_trade():
    result = compare_with_no_trade("a", _overall(average_net_pnl=-1.0), FOLDS)
    assert "beats NO_TRADE after charges" in result.failed_gates


def test_too_few_trades_abstains():
    result = compare_with_no_trade("a", _overall(closed_trades=12, winning_trades=7,
                                                 losing_trades=5), FOLDS)
    assert "at least 30 trades" in result.failed_gates


def test_a_gain_that_chance_could_explain_abstains():
    result = compare_with_no_trade("a", _overall(average_net_pnl=50.0), FOLDS)
    assert "clear of chance across 26 strategies" in result.failed_gates


def test_tail_risk_and_a_losing_fold_each_fail():
    result = compare_with_no_trade(
        "a", _overall(worst_trade=-25000.0, closed_trade_drawdown=70000.0),
        [{"net_pnl": 1.0}, {"net_pnl": -1.0}],
    )
    assert set(result.failed_gates) >= {
        "worst trade within the per-trade limit", "drawdown within the limit",
        "made money in every fold",
    }


def test_no_folds_or_a_single_trade_abstains():
    result = compare_with_no_trade("a", _overall(closed_trades=1, winning_trades=1,
                                                 losing_trades=0), [])
    assert result.t_value is None
    assert "made money in every fold" in result.failed_gates
