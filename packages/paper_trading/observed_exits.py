"""Replay recorded option exit checks against the deployed paper exit rules.

This verifies an observed decision path, not a complete historical quote path.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal

from packages.database.models import PaperTrade
from packages.market_data.contracts import ExchangeSession
from packages.paper_trading.exits import evaluate_exit
from packages.paper_trading.option_policy import parse_option_exit_policy
from packages.paper_trading.session import PaperSessionError, expiry_square_off_due

ReplayStatus = Literal["MATCHED", "INCOMPLETE", "MISMATCH"]
_RULE_EXITS = frozenset({"STOP_LOSS", "TARGET", "TRAILING_STOP"})
_CENT = Decimal("0.01")


@dataclass(frozen=True)
class ObservedExitReplay:
    status: ReplayStatus
    evaluated_count: int
    skipped_count: int
    last_decision: str | None
    detail: str


def _decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _aware(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _observed_pnl(trade: PaperTrade, quotes: object) -> Decimal | None:
    legs = trade.legs.get("items") if isinstance(trade.legs, dict) else None
    if not isinstance(legs, list) or not isinstance(quotes, list) or len(legs) != len(quotes):
        return None
    close_flow = Decimal(0)
    for leg, quote in zip(legs, quotes, strict=True):
        if not isinstance(leg, dict) or not isinstance(quote, dict):
            return None
        if leg.get("side") not in {"BUY", "SELL"}:
            return None
        exit_side = "SELL" if leg.get("side") == "BUY" else "BUY"
        try:
            units = int(leg["quantity_units"])
        except (KeyError, TypeError, ValueError):
            return None
        side_quote = _decimal(quote.get("selected_side_quote"))
        book_quote = _decimal(quote.get("bid" if exit_side == "SELL" else "ask"))
        if (
            units <= 0
            or quote.get("instrument_key") != leg.get("instrument_key")
            or quote.get("side") != exit_side
            or quote.get("quantity_units") != units
            or quote.get("timestamp_basis") != "exchange"
            or _aware(quote.get("quote_timestamp")) is None
            or side_quote is None or side_quote <= 0 or side_quote != book_quote
        ):
            return None
        amount = (side_quote * units).quantize(_CENT, rounding=ROUND_HALF_UP)
        close_flow += amount if exit_side == "SELL" else -amount
    return (trade.entry_cash_flow + close_flow).quantize(_CENT, rounding=ROUND_HALF_UP)


def _square_off_due(trade: PaperTrade, snapshot: dict[str, Any]) -> bool | None:
    saved_session = snapshot.get("nfo_session")
    evaluated_at = _aware(snapshot.get("evaluated_at"))
    if not isinstance(saved_session, dict) or evaluated_at is None:
        return None
    start = _aware(saved_session.get("start"))
    end = _aware(saved_session.get("end"))
    minutes = snapshot.get("square_off_minutes_before_close")
    if (
        saved_session.get("exchange") != "NFO" or start is None or end is None
        or type(minutes) is not int or not 5 <= minutes <= 60
    ):
        return None
    try:
        return expiry_square_off_due(
            trade.expiry,
            ExchangeSession("NFO", start, end),
            now=evaluated_at,
            minutes_before_close=minutes,
        )
    except PaperSessionError:
        return None


def replay_observed_exits(
    trade: PaperTrade, event_details: list[dict[str, Any]]
) -> ObservedExitReplay:
    """Check saved decisions and high-water mark against the current rule engine.

    An absent or skipped observation leaves the path incomplete. Older records are
    never backfilled or treated as proof of a complete market-data history.
    """
    evaluated = skipped = 0
    peak = Decimal(0)
    last_time: datetime | None = None
    last_decision: str | None = None
    final_pnl: Decimal | None = None

    def result(status: ReplayStatus, detail: str) -> ObservedExitReplay:
        return ObservedExitReplay(status, evaluated, skipped, last_decision, detail)

    entry = trade.entry_rule_snapshot
    try:
        policy = parse_option_exit_policy(
            entry.get("exit_policy") if isinstance(entry, dict) else None
        )
    except ValueError:
        return result("INCOMPLETE", "This trade has no pinned, valid exit policy.")

    for details in event_details:
        snapshot = details.get("snapshot")
        if not isinstance(snapshot, dict):
            return result("MISMATCH", "An exit observation has no valid snapshot.")
        digest = hashlib.sha256(json.dumps(
            snapshot, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        if details.get("snapshot_digest") != digest:
            return result("MISMATCH", "An exit observation no longer matches its saved digest.")
        if snapshot.get("version") != 1 or snapshot.get("strategy_key") != trade.strategy_key:
            return result("MISMATCH", "An exit observation uses a different rule or strategy.")
        decision = snapshot.get("decision")
        if decision == "SKIPPED":
            skipped += 1
            continue
        if (
            snapshot.get("exit_policy_version") != policy.version
            or snapshot.get("square_off_minutes_before_close")
            != policy.square_off_minutes_before_close
        ):
            return result("MISMATCH", "An exit observation used a different saved exit policy.")
        if last_decision in _RULE_EXITS | {"EXPIRY_SQUARE_OFF"}:
            return result("MISMATCH", "An exit check followed a terminal decision.")
        when = _aware(snapshot.get("market_as_of"))
        pnl = _decimal(snapshot.get("unrealized_pnl"))
        saved_peak = _decimal(snapshot.get("peak_unrealized_pnl"))
        if when is None or pnl is None or saved_peak is None:
            return result("MISMATCH", "An eligible exit check has incomplete numeric evidence.")
        if (
            snapshot.get("quality_status") != "PASS"
            or snapshot.get("timestamp_basis") != "exchange"
        ):
            return result("INCOMPLETE", "An exit check lacks verified exchange quote provenance.")
        observed_pnl = _observed_pnl(trade, snapshot.get("leg_quotes"))
        if observed_pnl is None:
            return result("INCOMPLETE", "An exit check lacks complete selected-leg quote evidence.")
        if observed_pnl != pnl:
            return result("MISMATCH", "Selected-leg bid/ask quotes do not reproduce paper P&L.")
        if last_time is not None and when < last_time:
            return result("MISMATCH", "Exit observations are not in market-time order.")
        if any(
            _decimal(snapshot.get(key)) != expected
            for key, expected in (
                ("entry_cash_flow", trade.entry_cash_flow),
                ("stop_loss_amount", trade.stop_loss_amount),
                ("target_profit_amount", trade.target_profit_amount),
                ("trailing_stop_amount", trade.trailing_stop_amount),
            )
        ):
            return result("MISMATCH", "An exit check used different paper risk settings.")
        calculated = evaluate_exit(
            pnl=pnl,
            peak_pnl=peak,
            stop_loss_amount=trade.stop_loss_amount,
            target_profit_amount=trade.target_profit_amount,
            trailing_stop_amount=trade.trailing_stop_amount,
        )
        evaluated += 1
        last_time = when
        last_decision = str(decision)
        final_pnl = pnl
        if saved_peak != calculated.peak_pnl:
            return result("MISMATCH", "The recorded high-water mark differs from rule replay.")
        square_off_due = _square_off_due(trade, snapshot)
        if decision == "EXPIRY_SQUARE_OFF" and square_off_due is None:
            return result("INCOMPLETE", "The square-off cutoff has no verified session evidence.")
        expected = calculated.reason or (
            "EXPIRY_SQUARE_OFF" if square_off_due else "HOLD"
        )
        if decision != expected:
            return result("MISMATCH", "The recorded exit decision differs from rule replay.")
        peak = calculated.peak_pnl

    if not event_details or evaluated == 0:
        return result("INCOMPLETE", "No eligible exit decision was recorded.")
    if skipped:
        return result("INCOMPLETE", "One or more exit checks were skipped; the path has gaps.")
    if trade.status != "CLOSED" or trade.exit_reason not in _RULE_EXITS | {"EXPIRY_SQUARE_OFF"}:
        return result("INCOMPLETE", "This position has no replayable final rule exit.")
    if last_decision != trade.exit_reason or final_pnl != trade.realized_pnl:
        return result("MISMATCH", "The final decision or P&L differs from the saved trade.")
    if peak != trade.peak_unrealized_pnl:
        return result("MISMATCH", "The final high-water mark differs from the saved trade.")
    return result("MATCHED", "Recorded stop, target and trailing decisions match the rule engine.")
