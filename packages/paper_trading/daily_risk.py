"""Conservative daily paper-entry budget in the NFO trading timezone."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from packages.core.risk import DailyRiskBudget, daily_risk_budget
from packages.database.futures_models import PaperFuturesTrade
from packages.database.models import PaperTrade

__all__ = ["DailyRiskBudget", "daily_realized_pnl", "daily_risk_budget"]

_INDIA = ZoneInfo("Asia/Kolkata")
_CENT = Decimal("0.01")


def daily_realized_pnl(session: Session, account_id: UUID, trading_day: date) -> Decimal:
    start = datetime.combine(trading_day, time.min, tzinfo=_INDIA).astimezone(timezone.utc)
    end = (datetime.combine(trading_day, time.min, tzinfo=_INDIA) + timedelta(days=1)).astimezone(
        timezone.utc
    )
    total = Decimal(0)
    for model in (PaperTrade, PaperFuturesTrade):
        value = session.scalar(
            select(func.coalesce(func.sum(model.realized_pnl), 0)).where(
                model.account_id == account_id,
                model.status == "CLOSED",
                model.closed_at >= start,
                model.closed_at < end,
            )
        )
        total += Decimal(value or 0)
    return total.quantize(_CENT, rounding=ROUND_HALF_UP)
