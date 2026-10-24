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


def test_missing_provider_tick_is_not_reported_as_zero_latency():
    from datetime import datetime, timedelta, timezone

    labels = run_path(str(Path("scripts/probe-upstox-v3-feed.py")))["tick_time_labels"]
    observed = datetime(2026, 10, 24, 10, 0, tzinfo=timezone.utc)
    quote = SimpleNamespace(timestamp=observed, timestamp_basis="unknown")
    assert labels(quote, observed, 1.0) == ("unknown", "unknown", "unknown")

    quote.timestamp = observed - timedelta(seconds=3)
    quote.timestamp_basis = "provider_tick"
    assert labels(quote, observed, 1.0) == (
        quote.timestamp.isoformat(), "3.00", "2.00",
    )


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


def test_nifty_probe_selects_only_chain_master_matched_quoted_call():
    from datetime import date, timedelta
    from decimal import Decimal

    script = run_path(str(Path("scripts/probe-upstox-v3-feed.py")))
    expiry = date.today() + timedelta(days=7)

    def leg(token, strike, bid=10, ask=11):
        return SimpleNamespace(
            instrument_token=token, expiry=expiry, strike=Decimal(str(strike)),
            option_type="CE", bid_quantity=20, ask_quantity=30,
            quote=SimpleNamespace(bid=Decimal(str(bid)), ask=Decimal(str(ask))),
        )

    contracts = [
        SimpleNamespace(instrument_key="valid", expiry=expiry,
                        strike=Decimal(23000), option_type="CE"),
        SimpleNamespace(instrument_key="wrong-strike", expiry=expiry,
                        strike=Decimal(23200), option_type="CE"),
    ]
    chain = SimpleNamespace(
        underlying="NIFTY", expiry=expiry, underlying_spot=Decimal(23010),
        contracts=[leg("wrong-strike", 23010), leg("valid", 23000),
                   leg("unlisted", 23010)],
    )

    assert script["choose_nifty_atm_call"](chain, contracts, expiry) == (
        "valid", expiry.isoformat(), "23000"
    )


def test_nifty_probe_abstains_when_chain_identity_or_depth_is_missing():
    from datetime import date, timedelta
    from decimal import Decimal

    script = run_path(str(Path("scripts/probe-upstox-v3-feed.py")))
    expiry = date.today() + timedelta(days=7)

    contracts = [SimpleNamespace(instrument_key="call", expiry=expiry,
                                 strike=Decimal(23000), option_type="CE")]
    chain = SimpleNamespace(
        underlying="NIFTY", expiry=expiry, underlying_spot=Decimal(23000),
        contracts=[SimpleNamespace(
            instrument_token="call", expiry=expiry, strike=Decimal(23000),
            option_type="CE", bid_quantity=0, ask_quantity=10,
            quote=SimpleNamespace(bid=Decimal(10), ask=Decimal(11)),
        )],
    )

    assert script["choose_nifty_atm_call"](chain, contracts, expiry) is None


def test_nifty_probe_watchdog_stops_a_stalled_child_without_leaking_stderr(monkeypatch, capsys):
    import subprocess

    script = run_path(str(Path("scripts/probe-upstox-v3-feed.py")))

    def stalled(command, **kwargs):
        assert command[-2:] == ["--nifty-atm-call", "--child"]
        assert kwargs["timeout"] == 90 and kwargs["capture_output"] is True
        raise subprocess.TimeoutExpired(command, kwargs["timeout"],
                                        stderr=b"wss://private.example/?token=secret")

    monkeypatch.setattr(script["subprocess"], "run", stalled)
    script["run_nifty_probe_with_watchdog"]()
    output = capsys.readouterr().out
    assert "process deadline" in output
    assert "private.example" not in output and "secret" not in output
