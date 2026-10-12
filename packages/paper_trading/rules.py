from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

RuleMetric = Literal[
    "put_call_ratio_oi",
    "put_call_ratio_volume",
    "atm_iv_skew_put_minus_call_points",
    "largest_call_oi_share_pct",
    "largest_put_oi_share_pct",
    "median_relative_spread_pct",
    "underlying_spot",
]
RuleOperator = Literal["GT", "GTE", "LT", "LTE"]


@dataclass(frozen=True)
class EntryCondition:
    metric: RuleMetric
    operator: RuleOperator
    threshold: Decimal


@dataclass(frozen=True)
class EntryConditionResult:
    metric: RuleMetric
    operator: RuleOperator
    threshold: Decimal
    actual: Decimal | None
    passed: bool | None

    def as_dict(self) -> dict[str, str | bool | None]:
        return {
            "metric": self.metric,
            "operator": self.operator,
            "threshold": str(self.threshold),
            "actual": str(self.actual) if self.actual is not None else None,
            "passed": self.passed,
        }


def evaluate_entry_conditions(
    conditions: Sequence[EntryCondition], metrics: Mapping[RuleMetric, Decimal | None]
) -> list[EntryConditionResult]:
    """Evaluate all entry filters; unavailable market data stays explicitly unknown."""
    results: list[EntryConditionResult] = []
    for condition in conditions:
        actual = metrics.get(condition.metric)
        if actual is None:
            passed = None
        elif condition.operator == "GT":
            passed = actual > condition.threshold
        elif condition.operator == "GTE":
            passed = actual >= condition.threshold
        elif condition.operator == "LT":
            passed = actual < condition.threshold
        else:
            passed = actual <= condition.threshold
        results.append(
            EntryConditionResult(
                metric=condition.metric,
                operator=condition.operator,
                threshold=condition.threshold,
                actual=actual,
                passed=passed,
            )
        )
    return results
