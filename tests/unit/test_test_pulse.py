from datetime import datetime, timedelta, timezone
from decimal import Decimal

from packages.paper_trading.test_pulse import reference


def test_reference_requires_three_distinct_recent_gate_passed_prior_observations():
    now = datetime(2026, 10, 14, 4, tzinfo=timezone.utc)
    rows = [{"checked_at": (now - timedelta(seconds=i * 15)).isoformat(),
             "quote_at": str(i), "last": str(100 + i), "entry_gate_ok": True}
            for i in (3, 2, 1)]
    current = {"last": "200"}
    assert reference(rows + [current], now) == Decimal("102")
    assert reference(rows[:2] + [current], now) is None
    assert reference([rows[0], rows[0], rows[1], current], now) is None
    assert reference([{**row, "entry_gate_ok": False} for row in rows] + [current], now) is None
    assert reference(rows + [current], now + timedelta(seconds=61)) is None
