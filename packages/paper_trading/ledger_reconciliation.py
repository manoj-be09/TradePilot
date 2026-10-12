"""Read-only consistency checks for one account's simulated options ledger."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from packages.database.models import PaperAccount, PaperOrder, PaperTrade
from packages.paper_trading.expiry_settlement import calculate_expiry_settlement

_CENT = Decimal("0.01")


@dataclass(frozen=True)
class PaperLedgerReconciliation:
    status: Literal["MATCHED", "MISMATCH"]
    initial_cash: Decimal
    recorded_cash_balance: Decimal
    expected_cash_balance: Decimal
    cash_difference: Decimal
    order_count: int
    open_trade_count: int
    closed_trade_count: int
    issue_count: int
    issues: list[str]


def reconcile_paper_ledger(
    account: PaperAccount,
    trades: list[PaperTrade],
    orders: list[PaperOrder],
) -> PaperLedgerReconciliation:
    """Compare all saved option orders with the account cash and trade lifecycle.

    This proves internal bookkeeping consistency only; it does not verify a market
    quote, broker execution, official settlement value, or unrecorded trades.
    """
    issues: list[str] = []
    issue_count = 0

    def flag(message: str) -> None:
        nonlocal issue_count
        issue_count += 1
        if len(issues) < 20:
            issues.append(message)

    by_trade: dict[object, list[PaperOrder]] = {}
    trades_by_id = {trade.id: trade for trade in trades}
    for order in orders:
        if order.account_id != account.id or order.user_id != account.user_id:
            flag(f"Order {order.id} has a different account or owner.")
        if order.trade_id not in trades_by_id:
            flag(f"Order {order.id} has no trade in this account.")
        by_trade.setdefault(order.trade_id, []).append(order)

    for trade in trades:
        if trade.account_id != account.id or trade.user_id != account.user_id:
            flag(f"Trade {trade.id} has a different account or owner.")
        found = by_trade.get(trade.id, [])
        opened = [order for order in found if order.action == "OPEN"]
        closed = [order for order in found if order.action == "CLOSE"]
        if len(opened) != 1 or opened[0].net_cash_flow != trade.entry_cash_flow:
            flag(f"Trade {trade.id} has no matching opening cash flow.")
        if len(found) != len(opened) + len(closed):
            flag(f"Trade {trade.id} has an unsupported order action.")
        if trade.status == "OPEN":
            if closed or trade.realized_pnl is not None:
                flag(f"Open trade {trade.id} has a close or realized P&L.")
        elif trade.status == "CLOSED":
            if len(closed) != 1 or trade.realized_pnl is None:
                flag(f"Closed trade {trade.id} lacks one close or realized P&L.")
            elif len(opened) == 1 and (
                opened[0].net_cash_flow + closed[0].net_cash_flow
            ).quantize(_CENT) != trade.realized_pnl:
                flag(f"Closed trade {trade.id} P&L differs from its two cash flows.")
            if trade.exit_reason == "EXPIRY_SETTLEMENT" and len(closed) == 1:
                snapshot = closed[0].fill_snapshot
                reference = (
                    snapshot.get("settlement_reference")
                    if isinstance(snapshot, dict) else None
                )
                if not isinstance(reference, dict):
                    flag(f"Expiry settlement {trade.id} has no recorded reference.")
                else:
                    try:
                        if (
                            reference.get("settlement_date") != trade.expiry.isoformat()
                            or not isinstance(reference.get("source_reference"), str)
                            or not reference["source_reference"].strip()
                        ):
                            raise ValueError("Invalid settlement reference")
                        final_value = Decimal(str(reference["final_index_value"]))
                        saved_legs = (
                            trade.legs.get("items")
                            if isinstance(trade.legs, dict) else None
                        )
                        settlement = calculate_expiry_settlement(
                            saved_legs, trade.entry_cash_flow, final_value
                        )
                    except (KeyError, ValueError, TypeError, InvalidOperation):
                        flag(f"Expiry settlement {trade.id} cannot be recomputed.")
                    else:
                        if (
                            settlement.settlement_cash_flow != closed[0].net_cash_flow
                            or settlement.realized_pnl != trade.realized_pnl
                        ):
                            flag(
                                f"Expiry settlement {trade.id} differs from "
                                "saved legs and index close."
                            )
        else:
            flag(f"Trade {trade.id} has an unknown lifecycle status.")

    expected = (account.initial_cash + sum(
        (order.net_cash_flow for order in orders), Decimal(0)
    )).quantize(_CENT)
    difference = (account.cash_balance - expected).quantize(_CENT)
    if difference != 0:
        flag("Account cash differs from initial cash plus all saved paper orders.")
    return PaperLedgerReconciliation(
        status="MATCHED" if issue_count == 0 else "MISMATCH",
        initial_cash=account.initial_cash,
        recorded_cash_balance=account.cash_balance,
        expected_cash_balance=expected,
        cash_difference=difference,
        order_count=len(orders),
        open_trade_count=sum(trade.status == "OPEN" for trade in trades),
        closed_trade_count=sum(trade.status == "CLOSED" for trade in trades),
        issue_count=issue_count,
        issues=issues,
    )
