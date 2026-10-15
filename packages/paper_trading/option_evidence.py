"""Conservative eligibility checks for automated options paper outcomes.

This describes observed simulated trades; it is not out-of-sample probability calibration.
"""

import json
from datetime import datetime
from decimal import Decimal

from packages.database.models import PaperOrder, PaperTrade
from packages.paper_trading.option_policy import parse_option_exit_policy

AUTOMATED_EXIT_REASONS = frozenset({
    "STOP_LOSS", "TARGET", "TRAILING_STOP", "EXPIRY_SQUARE_OFF",
})


def observed_pcr_condition(value: Decimal | None) -> str:
    """Describe observed OI balance; this is not a directional forecast."""
    if value is None or not value.is_finite() or value < 0:
        return "UNKNOWN"
    if value > Decimal("1.05"):
        return "BULLISH"
    if value < Decimal("0.95"):
        return "BEARISH"
    return "NEUTRAL"


def trade_entry_condition(trade: PaperTrade) -> str:
    context = trade.entry_rule_snapshot.get("entry_market_context")
    if not isinstance(context, dict):
        return "UNKNOWN"
    raw = context.get("put_call_ratio_oi")
    try:
        value = Decimal(str(raw)) if raw is not None else None
    except (ValueError, ArithmeticError):
        return "UNKNOWN"
    return observed_pcr_condition(value)


def _aware_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def qualifies_automated_option_outcome(
    trade: PaperTrade, orders: list[PaperOrder]
) -> bool:
    """Accept only a complete, quote-backed, rule-driven paper round trip."""
    if (
        trade.entry_rule_snapshot.get("testing_only") is True
        or trade.status != "CLOSED"
        or trade.realized_pnl is None
        or trade.closed_at is None
        or trade.max_loss <= 0
        or trade.stop_loss_amount is None
        or trade.trailing_stop_amount is None
        or trade.exit_reason not in AUTOMATED_EXIT_REASONS
        or len(orders) != 2
    ):
        return False
    entry = trade.entry_rule_snapshot
    if not isinstance(entry, dict):
        return False
    try:
        parse_option_exit_policy(entry.get("exit_policy"))
    except ValueError:
        return False
    by_action = {order.action: order for order in orders}
    if set(by_action) != {"OPEN", "CLOSE"}:
        return False
    opened, closed = by_action["OPEN"], by_action["CLOSE"]
    if any(
        order.status != "EXECUTED" or order.user_id != trade.user_id
        or order.account_id != trade.account_id
        for order in orders
    ):
        return False
    if (opened.net_cash_flow + closed.net_cash_flow).quantize(Decimal("0.01")) != (
        trade.realized_pnl
    ):
        return False
    if opened.net_cash_flow != trade.entry_cash_flow:
        return False
    if closed.fill_snapshot.get("exit_reason") != trade.exit_reason:
        return False
    conditions = entry.get("conditions") if isinstance(entry, dict) else None
    results = entry.get("results") if isinstance(entry, dict) else None
    if (
        not isinstance(conditions, list)
        or not conditions
        or not isinstance(results, list)
        or len(results) != len(conditions)
        or any(not isinstance(result, dict) or result.get("passed") is not True
               for result in results)
        or not isinstance(entry.get("template_version"), str)
    ):
        return False
    snapshots = [opened.fill_snapshot, closed.fill_snapshot]
    if any(
        not isinstance(snapshot, dict)
        or snapshot.get("paper_only") is not True
        or snapshot.get("quality_status") != "PASS"
        or not isinstance(snapshot.get("fills"), list)
        or not snapshot["fills"]
        for snapshot in snapshots
    ):
        return False
    entry_at = _aware_time(snapshots[0].get("market_as_of"))
    exit_at = _aware_time(snapshots[1].get("market_as_of"))
    if entry_at is None or exit_at is None or exit_at <= entry_at:
        return False
    if _aware_time(entry.get("market_as_of")) != entry_at:
        return False
    for snapshot, market_at in zip(snapshots, (entry_at, exit_at), strict=True):
        fills = snapshot["fills"]
        for fill in fills:
            if not isinstance(fill, dict):
                return False
            book_at = _aware_time(fill.get("book_as_of"))
            if (
                _aware_time(fill.get("quote_timestamp")) is None
                or fill.get("book_timestamp_basis") != "exchange"
                or book_at is None
                or not -5 <= (market_at - book_at).total_seconds() <= 60
            ):
                return False
    return True


def option_policy_signature(trade: PaperTrade) -> str:
    """Keep distinct rule and risk settings out of the same rankable sample."""
    entry = trade.entry_rule_snapshot
    exit_policy = parse_option_exit_policy(entry.get("exit_policy"))
    return json.dumps({
        "strategy_key": trade.strategy_key,
        "template_version": entry["template_version"],
        "strategy_definition_hash": entry.get("strategy_definition_hash"),
        "conditions": entry["conditions"],
        "lots": trade.lots,
        "stop_loss_amount": str(trade.stop_loss_amount),
        "target_profit_amount": str(trade.target_profit_amount),
        "trailing_stop_amount": str(trade.trailing_stop_amount),
        "exit_policy": exit_policy.as_dict(),
    }, sort_keys=True, separators=(",", ":"))
