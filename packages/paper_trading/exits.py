from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ExitDecision:
    peak_pnl: Decimal
    reason: str | None


def evaluate_exit(
    *,
    pnl: Decimal,
    peak_pnl: Decimal,
    stop_loss_amount: Decimal | None,
    target_profit_amount: Decimal | None,
    trailing_stop_amount: Decimal | None,
) -> ExitDecision:
    """Evaluate configured P&L exits using a persisted high-water mark."""
    updated_peak = max(peak_pnl, pnl)
    if stop_loss_amount is not None and pnl <= -stop_loss_amount:
        return ExitDecision(updated_peak, "STOP_LOSS")
    if target_profit_amount is not None and pnl >= target_profit_amount:
        return ExitDecision(updated_peak, "TARGET")
    if (
        trailing_stop_amount is not None
        and updated_peak > 0
        and updated_peak - pnl >= trailing_stop_amount
    ):
        return ExitDecision(updated_peak, "TRAILING_STOP")
    return ExitDecision(updated_peak, None)
