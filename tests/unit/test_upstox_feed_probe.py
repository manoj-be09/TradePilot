"""The live-feed probe separates observed changes from exchange-time claims."""

import asyncio
from pathlib import Path
from runpy import run_path
from types import SimpleNamespace


def _helpers():
    script = run_path(str(Path("scripts/probe-upstox-v3-feed.py")))
    return script["observation_changes"], script["probe_conclusion"], script["clock_offset_seconds"]


def test_book_change_is_distinct_from_last_price_change():
    compare, _, _ = _helpers()
    first = SimpleNamespace(
        last_price=100, bid=99, ask=101, bid_quantity=50, ask_quantity=50
    )
    second = SimpleNamespace(
        last_price=100, bid=100, ask=101, bid_quantity=50, ask_quantity=50
    )
    _, _, first_book = compare(None, None, first)
    price_changed, book_changed, _ = compare(first.last_price, first_book, second)
    assert price_changed is False
    assert book_changed is True


def test_market_status_and_no_change_prevent_live_update_claim():
    _, conclude, _ = _helpers()
    assert "not established" in conclude("CLOSING_END", "CLOSING_END", 1, 1, 30)
    assert "not established" in conclude("NORMAL_OPEN", "NORMAL_CLOSE", 1, 1, 30)
    assert "not established" in conclude("NORMAL_OPEN", "NORMAL_OPEN", 0, 0, 30)
    assert "not established" in conclude("NORMAL_OPEN", "NORMAL_OPEN", 1, 1, 0.5)
    assert "exchange-origin book time remains unverified" in conclude(
        "NORMAL_OPEN", "NORMAL_OPEN", 0, 1, 30
    )


def test_clock_offset_uses_http_date_midpoint_and_rejects_invalid_input():
    from datetime import datetime, timezone

    _, _, offset = _helpers()
    before = datetime(2026, 10, 9, 7, 0, 0, tzinfo=timezone.utc)
    after = datetime(2026, 10, 9, 7, 0, 2, tzinfo=timezone.utc)
    assert offset("Fri, 09 Oct 2026 07:00:00 GMT", before, after) == 1
    assert offset("not-a-date", before, after) is None


def test_closed_session_never_subscribes(capsys):
    script = run_path(str(Path("scripts/probe-upstox-v3-feed.py")))

    async def clock():
        return None

    class ClosedProvider:
        provider_id = "upstox"

        async def _get(self, path):
            return {"status": "NORMAL_CLOSE"}

        def subscribe(self, instruments):
            raise AssertionError("A closed session must not subscribe")

    script["main"].__globals__["estimate_provider_clock_offset"] = clock
    asyncio.run(script["main"](ClosedProvider()))
    assert "no live-update probe attempted" in capsys.readouterr().out


def test_probe_failure_does_not_expose_credentials(capsys):
    script = run_path(str(Path("scripts/probe-upstox-v3-feed.py")))

    async def clock():
        return None

    class FailedProvider:
        provider_id = "upstox"

        async def _get(self, path):
            raise ValueError("wss://private.example/?token=must-stay-private")

    script["main"].__globals__["estimate_provider_clock_offset"] = clock
    asyncio.run(script["main"](FailedProvider()))
    output = capsys.readouterr().out
    assert "Probe failed (ValueError)" in output
    assert "must-stay-private" not in output
    assert "private.example" not in output


def test_probe_journals_actual_quote_and_disconnect_without_private_url(tmp_path, capsys):
    import json
    from datetime import datetime, timezone

    from packages.market_data.contracts import Quote
    from packages.market_data.feed_journal import FeedJournal

    script = run_path(str(Path("scripts/probe-upstox-v3-feed.py")))

    async def clock():
        return None

    class Provider:
        provider_id = "upstox"

        async def _get(self, path):
            return {"status": "NORMAL_OPEN"}

        async def subscribe(self, instruments):
            yield Quote(
                instrument_token="test", timestamp=datetime.now(timezone.utc),
                last_price=100, bid=99, ask=100, timestamp_basis="provider_tick",
            )
            raise ValueError("wss://private.example/?token=must-stay-private")

    script["main"].__globals__["estimate_provider_clock_offset"] = clock
    path = tmp_path / "probe.jsonl"
    journal = FeedJournal(path)
    try:
        asyncio.run(script["main"](Provider(), journal))
    finally:
        journal.close()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["event"] for row in rows] == ["connected", "quote", "disconnected"]
    assert rows[1]["last"] == "100" and rows[1]["book_basis"] == "unknown"
    assert "private.example" not in path.read_text() + capsys.readouterr().out
