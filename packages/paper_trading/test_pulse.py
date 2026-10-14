"""One-shot paper diagnostic rule; deliberately absent from research STRATEGIES."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

KEY = "test_pulse"
VERSION = "equity-test-pulse-v2"
MAX_SAMPLE_AGE_SECONDS = 120


def reference(
    samples: list[dict[str, Any]], now: datetime,
    *, max_age_seconds: int = MAX_SAMPLE_AGE_SECONDS,
) -> Decimal | None:
    prices: dict[str, Decimal] = {}
    for sample in samples[:-1]:
        try:
            checked = datetime.fromisoformat(sample["checked_at"])
            price = Decimal(sample["last"])
            if (sample.get("entry_gate_ok") and checked.tzinfo is not None
                    and 0 <= (now - checked).total_seconds() <= max_age_seconds
                    and price.is_finite() and price > 0):
                prices[sample["quote_at"]] = price
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue
    values = list(prices.values())[-3:]
    return sum(values, Decimal(0)) / 3 if len(values) == 3 else None
