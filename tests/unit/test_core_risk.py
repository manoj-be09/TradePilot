from decimal import Decimal

import pytest

from packages.core.risk import (
    AccountRiskState,
    EntryIntent,
    RiskLimits,
    evaluate_entry,
)
from packages.paper_trading import daily_risk

LIMITS = RiskLimits(
    max_open_trades=3, max_trade_risk_pct=2, max_portfolio_risk_pct=10, daily_loss_limit_pct=3,
)


def _account(**overrides: object) -> AccountRiskState:
    values: dict[str, object] = {
        "initial_cash": Decimal("500000"), "cash_balance": Decimal("500000"),
        "kill_switch_enabled": False, "open_trade_max_losses": (),
        "realized_pnl_today": Decimal("0"),
    }
    values.update(overrides)
    return AccountRiskState(**values)  # type: ignore[arg-type]


def _codes(max_loss: str, cash_flow: str = "0", **account: object) -> list[str]:
    decision = evaluate_entry(
        EntryIntent(Decimal(max_loss), Decimal(cash_flow)), _account(**account), LIMITS
    )
    assert decision.approved is (not decision.rejections)
    return [rejection.code for rejection in decision.rejections]


def test_within_all_limits_is_approved_with_reported_limits() -> None:
    decision = evaluate_entry(
        EntryIntent(Decimal("9000"), Decimal("-1200")), _account(), LIMITS
    )
    assert decision.approved
    assert decision.per_trade_limit == Decimal("10000.00")
    assert decision.portfolio_limit == Decimal("50000.00")
    assert decision.remaining_daily_budget == Decimal("15000.00")


@pytest.mark.parametrize(("max_loss", "expected"), [
    ("10000.00", []),  # exactly at the 2% cap is allowed
    ("10000.01", ["PER_TRADE_LIMIT"]),
])
def test_per_trade_boundary(max_loss: str, expected: list[str]) -> None:
    assert _codes(max_loss) == expected


def test_portfolio_boundary_counts_open_risk() -> None:
    wide = RiskLimits(max_open_trades=10, max_trade_risk_pct=2, max_portfolio_risk_pct=10,
                      daily_loss_limit_pct=100)
    full = tuple(Decimal("10000") for _ in range(4))

    def codes(max_loss: str, open_risk: tuple[Decimal, ...]) -> list[str]:
        decision = evaluate_entry(
            EntryIntent(Decimal(max_loss), Decimal(0)),
            _account(open_trade_max_losses=open_risk), wide,
        )
        return [r.code for r in decision.rejections]

    assert codes("10000", full) == []  # exactly 10% in total
    assert codes("10000", (*full[:3], Decimal("10000.01"))) == ["PORTFOLIO_LIMIT"]
    # With the default 3% daily limit, 20,000 already open exhausts the 15,000 budget.
    assert _codes("10000", open_trade_max_losses=full[:2]) == ["DAILY_LOSS_BUDGET"]


def test_max_open_trades() -> None:
    assert "MAX_OPEN_TRADES" in _codes("1", open_trade_max_losses=(Decimal(1),) * 3)


def test_daily_budget_uses_realized_losses_and_open_risk() -> None:
    # 3% of 5 lakh = 15,000; 6,000 lost today and 5,000 open leaves 4,000.
    codes = _codes(
        "4000.01", realized_pnl_today=Decimal("-6000"),
        open_trade_max_losses=(Decimal("5000"),),
    )
    assert codes == ["DAILY_LOSS_BUDGET"]
    assert _codes("4000", realized_pnl_today=Decimal("-6000"),
                  open_trade_max_losses=(Decimal("5000"),)) == []
    # Realized profit does not raise the loss budget.
    assert _codes("10000", realized_pnl_today=Decimal("99999")) == []


def test_kill_switch_reports_423_first_and_keeps_order() -> None:
    decision = evaluate_entry(
        EntryIntent(Decimal("20000"), Decimal("0")), _account(kill_switch_enabled=True), LIMITS
    )
    assert decision.first_rejection is not None
    assert (decision.first_rejection.code, decision.first_rejection.status_code) == (
        "KILL_SWITCH", 423
    )
    assert [r.code for r in decision.rejections] == [
        "KILL_SWITCH", "PER_TRADE_LIMIT", "DAILY_LOSS_BUDGET",
    ]


def test_invalid_max_loss_and_cash_coverage() -> None:
    assert _codes("0") == ["INVALID_MAX_LOSS"]
    assert _codes("9000", "-495000", cash_balance=Decimal("500000")) == ["CASH_COVERAGE"]


def test_messages_match_the_existing_handler() -> None:
    decision = evaluate_entry(EntryIntent(Decimal("10000.01"), Decimal("0")), _account(), LIMITS)
    assert decision.first_rejection is not None
    assert decision.first_rejection.message == (
        "Maximum loss exceeds the per-trade risk cap of ₹10000.00"
    )


def test_paper_trading_module_reexports_the_core_budget() -> None:
    assert daily_risk.daily_risk_budget is __import__(
        "packages.core.risk", fromlist=["daily_risk_budget"]
    ).daily_risk_budget
