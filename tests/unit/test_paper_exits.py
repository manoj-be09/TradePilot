from decimal import Decimal

from packages.paper_trading.exits import evaluate_exit


def test_stop_loss_and_target_are_evaluated_on_total_position_pnl():
    stopped = evaluate_exit(
        pnl=Decimal("-100"),
        peak_pnl=Decimal("20"),
        stop_loss_amount=Decimal("100"),
        target_profit_amount=Decimal("250"),
        trailing_stop_amount=None,
    )
    assert stopped.reason == "STOP_LOSS"
    assert stopped.peak_pnl == Decimal("20")

    targeted = evaluate_exit(
        pnl=Decimal("250"),
        peak_pnl=Decimal("100"),
        stop_loss_amount=None,
        target_profit_amount=Decimal("250"),
        trailing_stop_amount=None,
    )
    assert targeted.reason == "TARGET"
    assert targeted.peak_pnl == Decimal("250")


def test_trailing_stop_uses_and_returns_persisted_high_water_mark():
    decision = evaluate_exit(
        pnl=Decimal("225"),
        peak_pnl=Decimal("475"),
        stop_loss_amount=None,
        target_profit_amount=None,
        trailing_stop_amount=Decimal("200"),
    )
    assert decision.reason == "TRAILING_STOP"
    assert decision.peak_pnl == Decimal("475")


def test_trailing_stop_does_not_trigger_before_position_has_been_profitable():
    decision = evaluate_exit(
        pnl=Decimal("-225"),
        peak_pnl=Decimal("-25"),
        stop_loss_amount=None,
        target_profit_amount=None,
        trailing_stop_amount=Decimal("100"),
    )
    assert decision.reason is None
    assert decision.peak_pnl == Decimal("-25")
