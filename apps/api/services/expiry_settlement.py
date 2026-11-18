"""Paper index-option expiry settlement: cash intrinsic at the index close, no broker fill.

The operator route and the nightly job both record through `record_settlement`, holding the
account lock. `settle_expired_trades` is the nightly's automatic path: a NIFTY trade still open
after its expiry settles at NSE's archived official close. Anything it cannot settle (archive not
published yet, a later-expiry leg, a ledger mismatch) stays open for the reviewed route.
"""

from collections.abc import Awaitable, Callable
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from packages.database.models import AuditEvent, PaperAccount, PaperOrder, PaperTrade
from packages.paper_trading.expiry_settlement import (
    ExpirySettlement,
    calculate_expiry_settlement,
)
from packages.paper_trading.official_index_close import (
    OfficialCloseUnavailable,
    OfficialIndexClose,
    fetch_nifty_close,
)
from packages.paper_trading.session import exchange_today
from packages.paper_trading.transactions import (
    PaperLedgerMismatchError,
    assert_consistent_ledger,
    lock_account,
)

_CENT = Decimal("0.01")
AUTO_RECORDED = "AUTO_RECORDED"
AUTO_METHOD = ("Automatic NIFTY index-option expiry cash intrinsic at NSE's archived "
               "official close; no broker fill")


def record_settlement(
    session: Session, account: PaperAccount, trade: PaperTrade,
    calculation: ExpirySettlement, reference: dict[str, str],
    *, quality_status: str, method: str,
) -> None:
    """Close the trade at its settlement cash flow; the caller holds the lock and commits."""
    now = datetime.now(timezone.utc)
    trade.realized_pnl = calculation.realized_pnl
    trade.status = "CLOSED"
    trade.exit_reason = "EXPIRY_SETTLEMENT"
    trade.closed_at = now
    account.cash_balance = (
        account.cash_balance + calculation.settlement_cash_flow
    ).quantize(_CENT, rounding=ROUND_HALF_UP)
    account.updated_at = now
    session.add(PaperOrder(
        account_id=account.id,
        trade_id=trade.id,
        user_id=trade.user_id,
        action="CLOSE",
        status="EXECUTED",
        net_cash_flow=calculation.settlement_cash_flow,
        created_at=now,
        fill_snapshot={
            "paper_only": True,
            "quality_status": quality_status,
            "modeled_costs": {"coverage": "UNAVAILABLE",
                              "reason": "Expiry settlement charges are not modeled"},
            "calculation_version": ("nifty-index-cash-intrinsic-v1" if trade.underlying == "NIFTY"
                                    else "nse-index-cash-intrinsic-v1"),
            "underlying": trade.underlying,
            "settlement_reference": reference,
            "fills": [vars(leg) | {
                "strike": str(leg.strike),
                "intrinsic_value": str(leg.intrinsic_value),
                "cash_flow": str(leg.cash_flow),
            } for leg in calculation.legs],
            "method": method,
        },
    ))
    session.add(AuditEvent(
        actor_user_id=trade.user_id,
        event_type="paper.trade.expiry_settlement_recorded",
        details={"trade_id": str(trade.id), **reference,
                 "realized_pnl": str(calculation.realized_pnl),
                 "quality_status": quality_status},
    ))


async def settle_expired_trades(
    factory: sessionmaker[Session],
    fetch_close: Callable[[date], Awaitable[OfficialIndexClose]] = fetch_nifty_close,
    now: datetime | None = None,
) -> list[str]:
    """Settle NIFTY paper trades still open after their expiry; one line per trade."""
    with factory() as session:
        expired = [(trade.id, trade.user_id, trade.expiry) for trade in session.scalars(
            select(PaperTrade).where(
                PaperTrade.status == "OPEN", PaperTrade.underlying == "NIFTY",
                PaperTrade.expiry < exchange_today(now),
            ))]
    closes: dict[date, OfficialIndexClose | str] = {}
    lines: list[str] = []
    for trade_id, user_id, expiry in expired:
        if expiry not in closes:
            try:
                closes[expiry] = await fetch_close(expiry)
            except OfficialCloseUnavailable as error:
                closes[expiry] = str(error)
        official = closes[expiry]
        if isinstance(official, str):
            lines.append(f"trade {trade_id} ({expiry}) waits for NSE's close: {official}")
            continue
        with factory() as session:
            lines.append(f"trade {trade_id} ({expiry}) "
                         f"{_settle_one(session, trade_id, user_id, official)}")
    return lines


def _settle_one(
    session: Session, trade_id: UUID, user_id: UUID, official: OfficialIndexClose,
) -> str:
    account = lock_account(session, user_id)
    trade = session.scalar(select(PaperTrade).where(
        PaperTrade.id == trade_id, PaperTrade.user_id == user_id,
    ).with_for_update().execution_options(populate_existing=True))
    if account is None or trade is None or trade.status != "OPEN" or trade.account_id != account.id:
        session.rollback()
        return "was no longer open"
    try:
        assert_consistent_ledger(session, account)
        calculation = calculate_expiry_settlement(
            trade.legs.get("items") if isinstance(trade.legs, dict) else None,
            trade.entry_cash_flow, official.close,
        )
    except (PaperLedgerMismatchError, ValueError) as error:
        session.rollback()
        return f"left for review: {error}"
    record_settlement(session, account, trade, calculation, {
        "settlement_date": trade.expiry.isoformat(),
        "final_index_value": str(official.close.quantize(_CENT)),
        "source_reference": official.source_url,
        "archive_sha256": official.sha256,
    }, quality_status=AUTO_RECORDED, method=AUTO_METHOD)
    session.commit()
    return f"settled at NSE close {official.close}: realized {calculation.realized_pnl}"
