"""Recheck saved, armed option-entry decisions against the pinned paper rules.

This audits observed decisions only. It cannot establish that every market tick was
captured, that a fill was executable, or that the strategy has predictive value.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

from packages.database.models import PaperDeployment, PaperTrade
from packages.paper_trading.rules import EntryCondition, RuleMetric, evaluate_entry_conditions

ReplayStatus = Literal["MATCHED", "INCOMPLETE", "MISMATCH"]
_METRICS: frozenset[str] = frozenset({
    "put_call_ratio_oi", "put_call_ratio_volume",
    "atm_iv_skew_put_minus_call_points", "largest_call_oi_share_pct",
    "largest_put_oi_share_pct", "median_relative_spread_pct", "underlying_spot",
})
_OPERATORS = frozenset({"GT", "GTE", "LT", "LTE"})


@dataclass(frozen=True)
class ObservedEntryReplay:
    status: ReplayStatus
    evaluated_count: int
    last_decision: str | None
    detail: str


def _number(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
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


def _conditions(value: object) -> list[EntryCondition] | None:
    if not isinstance(value, list) or not value:
        return None
    parsed = []
    for row in value:
        if not isinstance(row, dict):
            return None
        metric, operator = row.get("metric"), row.get("operator")
        threshold = _number(row.get("threshold"))
        if metric not in _METRICS or operator not in _OPERATORS or threshold is None:
            return None
        parsed.append(EntryCondition(
            cast("RuleMetric", metric), cast("Literal['GT', 'GTE', 'LT', 'LTE']", operator),
            threshold,
        ))
    return parsed


def replay_observed_entries(
    deployment: PaperDeployment,
    trade: PaperTrade,
    event_details: list[dict[str, Any]],
) -> ObservedEntryReplay:
    """Verify stored checks and the final paper fill against one saved deployment.

    A MATCHED result is a decision-integrity check, not a complete historical
    replay. Missing or unsupported evidence abstains from performance feedback.
    """
    count = 0
    last_decision: str | None = None

    def result(status: ReplayStatus, detail: str) -> ObservedEntryReplay:
        return ObservedEntryReplay(status, count, last_decision, detail)

    if (deployment.trade_id != trade.id or deployment.user_id != trade.user_id
            or deployment.status != "FILLED"):
        return result("MISMATCH", "Deployment is not bound to this filled trade.")
    request = deployment.request_data
    entry = trade.entry_rule_snapshot
    if not isinstance(request, dict) or not isinstance(entry, dict):
        return result("INCOMPLETE", "Saved deployment or entry rules are missing.")
    conditions = _conditions(request.get("entry_conditions"))
    if conditions is None or entry.get("conditions") != request.get("entry_conditions"):
        return result("INCOMPLETE", "The saved entry conditions cannot be reproduced.")
    if (request.get("market_data_provider") != entry.get("market_data_provider")
            or request.get("strategy_key") != trade.strategy_key
            or request.get("expiry") != trade.expiry.isoformat()
            or request.get("exit_policy") != entry.get("exit_policy")):
        return result("MISMATCH", "The filled trade differs from its armed deployment.")

    last_time: datetime | None = None
    last_snapshot: dict[str, Any] | None = None
    for details in event_details:
        snapshot = details.get("snapshot")
        if not isinstance(snapshot, dict):
            return result("MISMATCH", "An entry check has no valid snapshot.")
        digest = hashlib.sha256(json.dumps(
            snapshot, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        if details.get("snapshot_digest") != digest:
            return result("MISMATCH", "An entry check no longer matches its digest.")
        if (snapshot.get("version") != 1
                or snapshot.get("provider") != request["market_data_provider"]
                or snapshot.get("strategy_key") != trade.strategy_key
                or snapshot.get("expiry") != trade.expiry.isoformat()
                or snapshot.get("request_hash") != deployment.request_hash):
            return result("MISMATCH", "An entry check used another deployment or rule.")
        decision = snapshot.get("decision")
        if decision == "QUALITY_FAIL":
            quality = snapshot.get("quality")
            if not isinstance(quality, dict) or quality.get("status") != "FAIL":
                return result("MISMATCH", "Quality-fail decision lacks failing quote quality.")
            when = _aware(snapshot.get("market_as_of"))
            if when is None or (last_time is not None and when < last_time):
                return result("MISMATCH", "Entry checks lack ordered market times.")
            last_time = when
            last_decision = decision
            continue
        if decision not in {"RULES_PASS", "RULES_NOT_MET"}:
            return result("MISMATCH", "An entry check has an unknown decision.")
        when = _aware(snapshot.get("market_as_of"))
        if when is None or (last_time is not None and when < last_time):
            return result("MISMATCH", "Entry checks lack ordered market times.")
        if snapshot.get("timestamp_basis") != "exchange":
            return result("INCOMPLETE", "Entry check lacks exchange timestamp provenance.")
        quality = snapshot.get("quality")
        if not isinstance(quality, dict) or quality.get("status") not in {"PASS", "WARN"}:
            return result("INCOMPLETE", "Entry check lacks evaluable quote quality.")
        rule_snapshot = snapshot.get("entry_rule_snapshot")
        raw_metrics = snapshot.get("rule_metrics")
        if not isinstance(rule_snapshot, dict) or not isinstance(raw_metrics, dict):
            return result("INCOMPLETE", "Entry check lacks rule inputs.")
        if rule_snapshot.get("conditions") != request["entry_conditions"]:
            return result("MISMATCH", "Entry check uses different conditions.")
        metrics: dict[RuleMetric, Decimal | None] = {}
        for condition in conditions:
            raw = raw_metrics.get(condition.metric)
            value = _number(raw) if raw is not None else None
            if raw is not None and value is None:
                return result("MISMATCH", "Entry check has an invalid rule metric.")
            metrics[condition.metric] = value
        expected = [item.as_dict() for item in evaluate_entry_conditions(conditions, metrics)]
        if rule_snapshot.get("results") != expected:
            return result("MISMATCH", "Recorded condition results differ from rule replay.")
        passed = all(item["passed"] is True for item in expected)
        if decision != ("RULES_PASS" if passed else "RULES_NOT_MET"):
            return result("MISMATCH", "Entry decision differs from rule replay.")
        count += 1
        last_time, last_decision, last_snapshot = when, decision, snapshot

    if not event_details or last_decision != "RULES_PASS" or last_snapshot is None:
        return result("INCOMPLETE", "No final passing entry check was recorded.")
    if (last_snapshot.get("entry_rule_snapshot") != entry
            or _aware(entry.get("market_as_of")) != last_time):
        return result("MISMATCH", "Final entry check differs from the filled trade.")
    final_quality = last_snapshot.get("quality")
    if not isinstance(final_quality, dict) or final_quality.get("status") != "PASS":
        return result("INCOMPLETE", "Final fill check lacks passing quote quality.")
    leg_quotes = last_snapshot.get("leg_quotes")
    if not isinstance(leg_quotes, dict) or leg_quotes.get("status") != "RECORDED":
        return result("INCOMPLETE", "Final check lacks selected-leg quote evidence.")
    quoted = leg_quotes.get("legs")
    legs = trade.legs.get("items") if isinstance(trade.legs, dict) else None
    if not isinstance(quoted, list) or not isinstance(legs, list) or len(quoted) != len(legs):
        return result("MISMATCH", "Selected-leg quote count differs from the fill.")
    for quote, leg in zip(quoted, legs, strict=True):
        if not isinstance(quote, dict) or not isinstance(leg, dict):
            return result("MISMATCH", "Selected-leg quote evidence is malformed.")
        side = quote.get("side")
        side_quote = _number(quote.get("selected_side_quote"))
        book_side = _number(quote.get("ask" if side == "BUY" else "bid"))
        book_size = quote.get("ask_quantity" if side == "BUY" else "bid_quantity")
        quantity = quote.get("quantity_units")
        if (quote.get("instrument_key") != leg.get("instrument_key")
                or side not in {"BUY", "SELL"} or side != leg.get("side")
                or type(quantity) is not int or quantity <= 0
                or quantity != leg.get("quantity_units")
                or type(book_size) is not int or book_size < quantity
                or side_quote is None or side_quote <= 0 or side_quote != book_side
                or side_quote != _number(leg.get("entry_price"))
                or quote.get("timestamp_basis") != "exchange"
                or _aware(quote.get("quote_timestamp")) is None):
            return result("MISMATCH", "Selected-leg quotes do not reproduce the fill.")
    return result("MATCHED", "Observed entry checks and final fill match the saved rules.")
