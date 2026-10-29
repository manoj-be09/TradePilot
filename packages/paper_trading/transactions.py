"""Short, fresh account mutations; never hold these locks during provider I/O."""

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from packages.database.futures_models import PaperFuturesOrder, PaperFuturesTrade
from packages.database.models import (
    PaperAccount,
    PaperCapitalAdjustment,
    PaperOrder,
    PaperTrade,
)
from packages.paper_trading.ledger_reconciliation import (
    PaperLedgerReconciliation,
    reconcile_paper_ledger,
)


class PaperLedgerMismatchError(ValueError):
    """Saved F&O paper cash or order lifecycle is internally inconsistent."""


def reconcile_account(session: Session, account: PaperAccount) -> PaperLedgerReconciliation:
    """Reconcile every saved option and futures order against the account cash."""
    trades = list(session.scalars(select(PaperTrade).where(
        PaperTrade.account_id == account.id, PaperTrade.user_id == account.user_id,
    )))
    orders = list(session.scalars(select(PaperOrder).where(
        PaperOrder.account_id == account.id, PaperOrder.user_id == account.user_id,
    )))
    adjustments = list(session.scalars(select(PaperCapitalAdjustment).where(
        PaperCapitalAdjustment.account_id == account.id,
        PaperCapitalAdjustment.user_id == account.user_id,
    )))
    futures_trades = list(session.scalars(select(PaperFuturesTrade).where(
        PaperFuturesTrade.account_id == account.id,
        PaperFuturesTrade.user_id == account.user_id,
    )))
    futures_orders = list(session.scalars(select(PaperFuturesOrder).where(
        PaperFuturesOrder.account_id == account.id,
        PaperFuturesOrder.user_id == account.user_id,
    )))
    return reconcile_paper_ledger(
        account, trades, orders, adjustments, futures_trades, futures_orders
    )


def assert_consistent_ledger(session: Session, account: PaperAccount) -> None:
    """Check the locked account before staging a simulated cash mutation."""
    if reconcile_account(session, account).status != "MATCHED":
        raise PaperLedgerMismatchError(
            "F&O paper ledger mismatch; review the account ledger before changing paper cash"
        )


def open_futures_trades(session: Session, account_id: UUID) -> list[PaperFuturesTrade]:
    """Open futures positions count toward the shared trade-count and risk caps."""
    return list(session.scalars(select(PaperFuturesTrade).where(
        PaperFuturesTrade.account_id == account_id, PaperFuturesTrade.status == "OPEN",
    )))


def lock_account(session: Session, user_id: UUID) -> PaperAccount | None:
    if session.new or session.dirty or session.deleted:
        raise RuntimeError("Account lock must be acquired before staging changes")
    session.rollback()
    if session.get_bind().dialect.name == "sqlite":
        # SQLite has no SELECT FOR UPDATE. Serialize local mutations before reading balances.
        session.execute(text("BEGIN IMMEDIATE"))
    return session.scalar(
        select(PaperAccount)
        .where(PaperAccount.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
