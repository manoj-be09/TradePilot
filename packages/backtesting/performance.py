"""Closed-trade statistics. Sources are summarized separately, never pooled."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class PerformanceTrade:
    net_pnl: Decimal
    initial_risk: Decimal | None = None
    exit_at: datetime | None = None


def _drawdown(values: list[Decimal]) -> Decimal:
    total = peak = largest = Decimal(0)
    for value in values:
        total += value
        peak = max(peak, total)
        largest = max(largest, peak - total)
    return largest


def summarize(trades: list[PerformanceTrade]) -> dict[str, Any]:
    """Input must be ordered by exit time. Zero-P&L trades count as breakeven."""
    pnl = [trade.net_pnl for trade in trades]
    if any(not value.is_finite() for value in pnl):
        raise ValueError("Performance requires finite net P&L")
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    win_total = sum(wins, Decimal(0))
    loss_total = -sum(losses, Decimal(0))
    normalized = [
        trade.net_pnl / trade.initial_risk for trade in trades
        if trade.initial_risk is not None
        and trade.initial_risk.is_finite() and trade.initial_risk > 0
    ]
    complete_risk = bool(trades) and len(normalized) == len(trades)
    dates = [trade.exit_at for trade in trades if trade.exit_at is not None]
    return {
        "closed_trades": len(pnl),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "breakeven_trades": len(pnl) - len(wins) - len(losses),
        "win_rate": float(Decimal(len(wins)) / len(pnl) * 100) if pnl else None,
        "net_pnl": float(sum(pnl, Decimal(0))),
        "average_net_pnl": float(sum(pnl, Decimal(0)) / len(pnl)) if pnl else None,
        "average_win": float(win_total / len(wins)) if wins else None,
        "average_loss": float(-loss_total / len(losses)) if losses else None,
        "profit_factor": float(win_total / loss_total) if loss_total else None,
        "profit_factor_status": (
            "available" if loss_total else "no_losses" if wins else "no_decisive_trades"
        ),
        "best_trade": float(max(pnl)) if pnl else None,
        "worst_trade": float(min(pnl)) if pnl else None,
        "closed_trade_drawdown": float(_drawdown(pnl)),
        "risk_recorded_trades": len(normalized),
        # Do not silently report a biased subset when legacy records lack initial risk.
        "average_r": float(sum(normalized) / len(normalized)) if complete_risk else None,
        "closed_trade_drawdown_r": float(_drawdown(normalized)) if complete_risk else None,
        "first_exit_at": min(dates).isoformat() if dates else None,
        "last_exit_at": max(dates).isoformat() if dates else None,
    }
