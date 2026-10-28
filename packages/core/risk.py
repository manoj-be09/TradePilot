"""Deterministic account-level entry risk checks.

Mirrors the checks in the paper entry handler (`apps/api/main.py`, after the account
lock) in the same order and with the same limits and messages, so the handler, the
backtester and a future live runtime can share one implementation. A rejection is a
final answer for that intent; callers must not retry with altered inputs to pass.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

_CENT = Decimal("0.01")


@dataclass(frozen=True)
class DailyRiskBudget:
    realized_pnl: Decimal
    loss_limit: Decimal
    open_max_loss: Decimal
    remaining_for_new_risk: Decimal


def daily_risk_budget(
    *,
    initial_cash: Decimal,
    limit_pct: int,
    realized_pnl: Decimal,
    open_max_loss: Decimal,
) -> DailyRiskBudget:
    loss_limit = (initial_cash * Decimal(limit_pct) / Decimal(100)).quantize(
        _CENT, rounding=ROUND_HALF_UP
    )
    realized_loss = max(Decimal(0), -realized_pnl)
    remaining = max(Decimal(0), loss_limit - realized_loss - open_max_loss)
    return DailyRiskBudget(
        realized_pnl=realized_pnl,
        loss_limit=loss_limit,
        open_max_loss=open_max_loss,
        remaining_for_new_risk=remaining.quantize(_CENT, rounding=ROUND_HALF_UP),
    )


@dataclass(frozen=True)
class RiskLimits:
    max_open_trades: int
    max_trade_risk_pct: Decimal | int | float
    max_portfolio_risk_pct: Decimal | int | float
    daily_loss_limit_pct: int


@dataclass(frozen=True)
class AccountRiskState:
    initial_cash: Decimal
    cash_balance: Decimal
    kill_switch_enabled: bool
    open_trade_max_losses: Sequence[Decimal]
    realized_pnl_today: Decimal


@dataclass(frozen=True)
class EntryIntent:
    """A proposed defined-risk entry: worst-case expiry loss and entry cash flow."""

    max_loss: Decimal
    entry_cash_flow: Decimal


@dataclass(frozen=True)
class RiskRejection:
    code: str
    status_code: int
    message: str


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    rejections: tuple[RiskRejection, ...]
    per_trade_limit: Decimal
    portfolio_limit: Decimal
    open_max_loss: Decimal
    remaining_daily_budget: Decimal

    @property
    def first_rejection(self) -> RiskRejection | None:
        return self.rejections[0] if self.rejections else None


def evaluate_entry(
    intent: EntryIntent, account: AccountRiskState, limits: RiskLimits
) -> RiskDecision:
    """Evaluate every check; `rejections` keeps the handler's order (first = reported)."""
    rejections: list[RiskRejection] = []
    if intent.max_loss <= 0:
        rejections.append(RiskRejection(
            "INVALID_MAX_LOSS", 409, "Calculated maximum loss is invalid; trade rejected",
        ))
    if account.kill_switch_enabled:
        rejections.append(RiskRejection(
            "KILL_SWITCH", 423, "Paper kill switch is active; new trades are paused",
        ))
    if len(account.open_trade_max_losses) >= limits.max_open_trades:
        rejections.append(RiskRejection(
            "MAX_OPEN_TRADES", 409, "Maximum number of simultaneous paper trades reached",
        ))
    per_trade_limit = _pct(account.initial_cash, limits.max_trade_risk_pct)
    portfolio_limit = _pct(account.initial_cash, limits.max_portfolio_risk_pct)
    current_risk = sum(account.open_trade_max_losses, Decimal(0))
    if intent.max_loss > per_trade_limit:
        rejections.append(RiskRejection(
            "PER_TRADE_LIMIT", 409,
            f"Maximum loss exceeds the per-trade risk cap of ₹{per_trade_limit}",
        ))
    if current_risk + intent.max_loss > portfolio_limit:
        rejections.append(RiskRejection(
            "PORTFOLIO_LIMIT", 409,
            f"Open-trade risk would exceed the portfolio cap of ₹{portfolio_limit}",
        ))
    budget = daily_risk_budget(
        initial_cash=account.initial_cash, limit_pct=limits.daily_loss_limit_pct,
        realized_pnl=account.realized_pnl_today, open_max_loss=current_risk,
    )
    if intent.max_loss > budget.remaining_for_new_risk:
        rejections.append(RiskRejection(
            "DAILY_LOSS_BUDGET", 409,
            "Daily paper loss budget would be exceeded; "
            f"₹{budget.remaining_for_new_risk} remains for new risk",
        ))
    projected_cash = (account.cash_balance + intent.entry_cash_flow).quantize(_CENT)
    if current_risk + intent.max_loss > projected_cash:
        rejections.append(RiskRejection(
            "CASH_COVERAGE", 409,
            "Available paper cash cannot cover maximum loss across open positions",
        ))
    return RiskDecision(
        approved=not rejections,
        rejections=tuple(rejections),
        per_trade_limit=per_trade_limit,
        portfolio_limit=portfolio_limit,
        open_max_loss=current_risk,
        remaining_daily_budget=budget.remaining_for_new_risk,
    )


def _pct(base: Decimal, pct: Decimal | int | float) -> Decimal:
    # Same expression and default rounding as the existing handler.
    return (base * Decimal(pct) / Decimal(100)).quantize(_CENT)
