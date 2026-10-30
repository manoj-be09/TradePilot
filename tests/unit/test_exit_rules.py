"""The earlier half picks a rule set and only the later half can confirm it."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from packages.backtesting.exit_rules import EXIT_RULE_SETS, compare_half, judge, split_at_middle
from packages.backtesting.history_backtest import BacktestTrade


def _trade(index: int, current: float, others: dict[str, float]) -> BacktestTrade:
    def build(net: float, alternatives: dict) -> BacktestTrade:
        moment = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=7 * index)
        return BacktestTrade(
            expiry=date(2026, 1, 1) + timedelta(days=7 * index), entry_at=moment, exit_at=moment,
            exit_reason="TARGET", synthetic_spot=Decimal(25000), reference_strike=Decimal(25000),
            max_loss=Decimal(1000), gross_pnl=Decimal(str(net)), charges=Decimal(0), legs=[],
            entry_margin=Decimal(1000), alternatives=alternatives,
        )

    return build(current, {
        rules.name: build(others.get(rules.name, current), {}) for rules in EXIT_RULE_SETS
    })


def _gain(index: int, size: float) -> float:
    return size + (index % 3 - 1) * 5


def test_halves_are_split_by_expiry_and_the_gain_is_paired():
    trades = [_trade(index, 10, {"hold": 10 + _gain(index, 20)}) for index in reversed(range(4))]
    earlier, later = split_at_middle(trades)
    assert [trade.expiry for trade in earlier] == sorted(trade.expiry for trade in earlier)
    assert earlier[-1].expiry < later[0].expiry
    result = compare_half(trades, "hold")
    assert result.average_current == 10
    assert result.average_gain > 0


def test_a_rule_that_helps_in_both_halves_holds_up():
    trades = [_trade(index, -10, {"no_target": -10 + _gain(index, 50)}) for index in range(40)]
    verdict = judge(trades)
    assert verdict is not None
    assert verdict.best_rules == "no_target"
    assert verdict.holds_up is True


def test_a_rule_that_only_helped_the_earlier_half_does_not():
    trades = [
        _trade(index, -10, {"no_target": -10 + _gain(index, 50 if index < 20 else -50)})
        for index in range(40)
    ]
    verdict = judge(trades)
    assert verdict is not None
    assert verdict.best_rules == "no_target"
    assert verdict.holds_up is False


def test_a_better_rule_that_still_loses_money_does_not_hold_up():
    trades = [_trade(index, -100, {"hold": -100 + _gain(index, 50)}) for index in range(40)]
    verdict = judge(trades)
    assert verdict is not None
    assert verdict.later.average_gain > 0
    assert verdict.holds_up is False


def test_too_few_trades_gives_no_verdict():
    assert judge([_trade(index, 1, {}) for index in range(20)]) is None
