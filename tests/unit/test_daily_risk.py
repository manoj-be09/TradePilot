from decimal import Decimal

from packages.paper_trading.daily_risk import daily_risk_budget


def test_daily_budget_counts_realized_losses_and_open_worst_case_without_profit_credit():
    budget = daily_risk_budget(
        initial_cash=Decimal("1000000"), limit_pct=5,
        realized_pnl=Decimal("-4700"), open_max_loss=Decimal("45000"),
    )
    assert budget.loss_limit == Decimal("50000.00")
    assert budget.remaining_for_new_risk == Decimal("300.00")

    winning_day = daily_risk_budget(
        initial_cash=Decimal("1000000"), limit_pct=5,
        realized_pnl=Decimal("10000"), open_max_loss=Decimal("45000"),
    )
    assert winning_day.remaining_for_new_risk == Decimal("5000.00")
