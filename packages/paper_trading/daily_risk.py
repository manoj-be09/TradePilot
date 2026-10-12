"""Conservative daily paper-entry budget in the NFO trading timezone."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from packages.database.models import PaperTrade

_INDIA = ZoneInfo("Asia/Kolkata")
_CENT = Decimal("0.01")


@dataclass(frozen=True)
class DailyRiskBudget:
    realized_pnl: Decimal
    loss_limit: Decimal
    open_max_loss: Decimal
    remaining_for_new_risk: Decimal


def daily_realized_pnl(session: Session, account_id: UUID, trading_day: date) -> Decimal:
    start = datetime.combine(trading_day, time.min, tzinfo=_INDIA).astimezone(timezone.utc)
    end = (datetime.combine(trading_day, time.min, tzinfo=_INDIA) + timedelta(days=1)).astimezone(
        timezone.utc
    )
    value = session.scalar(
        select(func.coalesce(func.sum(PaperTrade.realized_pnl), 0)).where(
            PaperTrade.account_id == account_id,
            PaperTrade.status == "CLOSED",
            PaperTrade.closed_at >= start,
            PaperTrade.closed_at < end,
        )
    )
    return Decimal(value or 0).quantize(_CENT, rounding=ROUND_HALF_UP)


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
