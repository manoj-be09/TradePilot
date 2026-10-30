"""Proposed entry and exit rules for a paper template. Exploratory, not validated.

Keep in step with paperTemplatePolicy in apps/web/app/page.tsx.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

_CENT = Decimal("0.01")
_STOP_SHARE_OF_MAX_LOSS = Decimal("0.4")
_STOP_SPREAD_MULTIPLE = 3
_TARGET_SHARE_OF_MAX_PROFIT = Decimal("0.5")
_TARGET_MULTIPLE_OF_STOP = Decimal("1.75")
_UNBOUNDED_TARGET_MULTIPLE = Decimal("1.5")
_TRAIL_SHARE_OF_TARGET = Decimal("0.35")
_MINIMUM = Decimal(1)
_BULLISH = {"bull_call_spread", "bull_put_spread", "long_call"}
_BEARISH = {"bear_put_spread", "bear_call_spread", "long_put"}
_NEUTRAL = {"iron_condor"}


@dataclass(frozen=True)
class TemplatePolicy:
    entry_conditions: list[dict[str, Any]]
    stop_loss: Decimal
    target_profit: Decimal
    trailing_stop: Decimal


def _condition(metric: str, operator: str, threshold: str) -> dict[str, Any]:
    return {"metric": metric, "operator": operator, "threshold": threshold}


def entry_conditions(strategy_key: str) -> list[dict[str, Any]]:
    conditions = []
    if strategy_key in _BULLISH:
        conditions.append(_condition("put_call_ratio_oi", "GTE", "1.00"))
    if strategy_key in _BEARISH:
        conditions.append(_condition("put_call_ratio_oi", "LTE", "1.00"))
    if strategy_key in _NEUTRAL:
        conditions.append(_condition("put_call_ratio_oi", "GTE", "0.80"))
        conditions.append(_condition("put_call_ratio_oi", "LTE", "1.20"))
    conditions.append(_condition("median_relative_spread_pct", "LTE", "10.00"))
    return conditions


def template_policy(
    strategy_key: str, maximum_loss: Decimal, maximum_profit: Decimal | None, spread_cost: Decimal,
) -> TemplatePolicy | None:
    """Size exits from the modeled risk. maximum_profit is None when it is unbounded.

    spread_cost is what crossing bid/ask costs on all legs: a position is marked at its exit
    side, so it shows that loss the moment it opens, and a smaller stop would fire on the
    spread alone. The stop never exceeds the maximum loss.
    """
    if maximum_loss <= 0:
        return None
    stop = min(
        maximum_loss,
        max(_MINIMUM, maximum_loss * _STOP_SHARE_OF_MAX_LOSS, spread_cost * _STOP_SPREAD_MULTIPLE),
    )
    if maximum_profit is not None and maximum_profit > 0:
        target = max(_MINIMUM, min(
            maximum_profit * _TARGET_SHARE_OF_MAX_PROFIT, stop * _TARGET_MULTIPLE_OF_STOP,
        ))
    else:
        target = max(_MINIMUM, stop * _UNBOUNDED_TARGET_MULTIPLE)
    trailing = max(_MINIMUM, target * _TRAIL_SHARE_OF_TARGET)
    return TemplatePolicy(
        entry_conditions=entry_conditions(strategy_key),
        stop_loss=stop.quantize(_CENT, rounding=ROUND_HALF_UP),
        target_profit=target.quantize(_CENT, rounding=ROUND_HALF_UP),
        trailing_stop=trailing.quantize(_CENT, rounding=ROUND_HALF_UP),
    )
