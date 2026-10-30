"""Registered NIFTY futures paper templates: entry rules, risk in points, exit policy.

Pure definitions and calculations only. Rules describe when a simulated entry may be
attempted; they are not a signal of profitability, and every fill still passes the
quote, session and account risk checks in apps.api.services.futures_paper.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal, cast

from packages.market_data.contracts import Quote
from packages.paper_trading.futures import FuturesSide, money
from packages.paper_trading.rules import (
    EntryCondition,
    EntryConditionResult,
    RuleMetric,
    RuleOperator,
    evaluate_entry_conditions,
)

FUTURES_EXIT_POLICY_VERSION = "nifty-futures-exit-v1"
FUTURES_TEMPLATE_VERSION = "1"
# Calendar days before expiry when a position leaves the contract and new entries use
# the next one; volume migrates ahead of expiry and expiry-day books are their own regime.
ROLLOVER_DAYS_BEFORE_EXPIRY = 2
RESEARCH_NOTE = (
    "Paper template with no validated edge. Rules decide when a simulated entry may be "
    "attempted; they are not a forecast or investment advice."
)

FuturesRuleMetric = Literal[
    "regime_direction", "put_call_ratio_oi", "futures_relative_spread_pct", "futures_basis_pct",
]
_DIRECTION = {"UP": Decimal(1), "NEUTRAL": Decimal(0), "DOWN": Decimal(-1)}


@dataclass(frozen=True)
class FuturesExitPolicy:
    """Immutable exit settings saved with the armed strategy and its filled trade."""

    square_off_minutes_before_close: int
    intraday: bool
    rollover_days_before_expiry: int
    version: str = FUTURES_EXIT_POLICY_VERSION

    def __post_init__(self) -> None:
        if self.version != FUTURES_EXIT_POLICY_VERSION:
            raise ValueError("Unknown NIFTY futures paper exit policy version")
        if (
            type(self.square_off_minutes_before_close) is not int
            or not 5 <= self.square_off_minutes_before_close <= 60
        ):
            raise ValueError("NIFTY futures square-off cutoff must be 5–60 minutes")
        if type(self.intraday) is not bool:
            raise ValueError("NIFTY futures intraday flag must be true or false")
        if (
            type(self.rollover_days_before_expiry) is not int
            or not 1 <= self.rollover_days_before_expiry <= 10
        ):
            raise ValueError("NIFTY futures rollover window must be 1–10 days")

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "square_off_minutes_before_close": self.square_off_minutes_before_close,
            "intraday": self.intraday,
            "rollover_days_before_expiry": self.rollover_days_before_expiry,
        }


def parse_futures_exit_policy(raw: object) -> FuturesExitPolicy:
    if not isinstance(raw, dict) or set(raw) != {
        "version", "square_off_minutes_before_close", "intraday", "rollover_days_before_expiry",
    }:
        raise ValueError("Saved NIFTY futures exit policy is missing or malformed")
    return FuturesExitPolicy(
        square_off_minutes_before_close=raw["square_off_minutes_before_close"],
        intraday=raw["intraday"],
        rollover_days_before_expiry=raw["rollover_days_before_expiry"],
        version=raw["version"],
    )


@dataclass(frozen=True)
class FuturesCondition:
    metric: FuturesRuleMetric
    operator: RuleOperator
    threshold: Decimal

    def as_dict(self) -> dict[str, str]:
        return {
            "metric": self.metric, "operator": self.operator, "threshold": str(self.threshold),
        }


@dataclass(frozen=True)
class FuturesTemplate:
    key: str
    name: str
    description: str
    side: FuturesSide
    intraday: bool
    stop_points: Decimal
    target_points: Decimal
    trailing_points: Decimal
    conditions: tuple[FuturesCondition, ...]
    version: str = FUTURES_TEMPLATE_VERSION

    def __post_init__(self) -> None:
        if min(self.stop_points, self.target_points, self.trailing_points) <= 0:
            raise ValueError("Futures template risk distances must be positive")
        if not self.conditions:
            raise ValueError("Futures template must have entry conditions")

    def amounts(self, lots: int, lot_size: int) -> tuple[Decimal, Decimal, Decimal]:
        """Stop, target and trailing amounts in rupees for the whole position."""
        units = lots * lot_size
        return (
            money(self.stop_points * units),
            money(self.target_points * units),
            money(self.trailing_points * units),
        )

    def definition(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "version": self.version,
            "side": self.side,
            "intraday": self.intraday,
            "stop_points": str(self.stop_points),
            "target_points": str(self.target_points),
            "trailing_points": str(self.trailing_points),
            "conditions": [condition.as_dict() for condition in self.conditions],
        }


def _conditions(side: FuturesSide) -> tuple[FuturesCondition, ...]:
    long = side == "LONG"
    return (
        FuturesCondition("regime_direction", "GTE" if long else "LTE", Decimal(1 if long else -1)),
        FuturesCondition("put_call_ratio_oi", "GTE" if long else "LTE", Decimal("1.00")),
        FuturesCondition("futures_relative_spread_pct", "LTE", Decimal("0.05")),
    )


def _template(key: str, name: str, side: FuturesSide, intraday: bool) -> FuturesTemplate:
    style = "Exits before every close" if intraday else "Held overnight, rolled before expiry"
    direction = "upward" if side == "LONG" else "downward"
    action = "Buys" if side == "LONG" else "Sells"
    return FuturesTemplate(
        key=key, name=name, side=side, intraday=intraday,
        description=(
            f"{action} the front-month future when the completed 5-minute NIFTY regime "
            f"has an {direction} bias and open-interest PCR agrees. {style}."
        ),
        stop_points=Decimal(60 if intraday else 120),
        target_points=Decimal(120 if intraday else 240),
        trailing_points=Decimal(50 if intraday else 120),
        conditions=_conditions(side),
    )


_TEMPLATES: tuple[FuturesTemplate, ...] = (
    _template("nifty_futures_intraday_long", "Intraday long future", "LONG", True),
    _template("nifty_futures_intraday_short", "Intraday short future", "SHORT", True),
    _template("nifty_futures_positional_long", "Positional long future", "LONG", False),
    _template("nifty_futures_positional_short", "Positional short future", "SHORT", False),
)
_BY_KEY = {template.key: template for template in _TEMPLATES}


def list_futures_templates() -> tuple[FuturesTemplate, ...]:
    return _TEMPLATES


def get_futures_template(key: str) -> FuturesTemplate | None:
    return _BY_KEY.get(key)


def in_rollover_window(expiry: date, today: date, days: int) -> bool:
    return expiry - today <= timedelta(days=days)


def front_contract_expiry(expiries: list[date], today: date, days: int) -> date | None:
    """Nearest expiry still outside the rollover window, or None when nothing is listed."""
    return min(
        (expiry for expiry in expiries if not in_rollover_window(expiry, today, days)),
        default=None,
    )


def futures_rule_metrics(
    *,
    quote: Quote,
    spot: Decimal | None,
    regime_bias: str | None,
    put_call_ratio_oi: Decimal | None,
) -> dict[FuturesRuleMetric, Decimal | None]:
    """Decision inputs from one observation; anything missing stays unknown, never zero."""
    bid, ask = quote.bid, quote.ask
    mid = (bid + ask) / 2 if bid is not None and ask is not None and bid > 0 and ask > 0 else None
    return {
        "regime_direction": _DIRECTION.get(regime_bias) if regime_bias is not None else None,
        "put_call_ratio_oi": put_call_ratio_oi,
        "futures_relative_spread_pct": (
            ((cast(Decimal, ask) - cast(Decimal, bid)) / mid * 100).quantize(Decimal("0.0001"))
            if mid is not None else None
        ),
        "futures_basis_pct": (
            ((mid - spot) / spot * 100).quantize(Decimal("0.0001"))
            if mid is not None and spot is not None and spot > 0 else None
        ),
    }


def evaluate_futures_conditions(
    conditions: tuple[FuturesCondition, ...],
    metrics: Mapping[FuturesRuleMetric, Decimal | None],
) -> list[EntryConditionResult]:
    """Same comparison and unknown-data semantics as the option rule engine."""
    return evaluate_entry_conditions(
        [EntryCondition(cast(RuleMetric, row.metric), row.operator, row.threshold)
         for row in conditions],
        cast("Mapping[RuleMetric, Decimal | None]", metrics),
    )
