from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from test_paper_trading import FakeUpstoxProvider, _token

from packages.database.models import PaperOrder, PaperTrade
from packages.market_data.contracts import (
    DiscoveredInstrument,
    ExchangeSession,
    HistoricalBar,
)
from packages.paper_trading.official_index_close import (
    OfficialCloseUnavailable,
    OfficialIndexClose,
    archive_url,
)


def test_expired_paper_option_requires_preview_and_confirmed_source(client, monkeypatch):
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = _token(client)
    opened = client.post("/paper/trades", headers={
        **headers, "Idempotency-Key": f"entry_{uuid4().hex}",
    }, json={
        "strategy_key": "long_call", "expiry": provider.expiry.isoformat(),
        "reference_strike": "25000", "lots": 1,
    })
    assert opened.status_code == 201, opened.text
    trade_id = opened.json()["id"]
    factory = client.app.state.test_session_factory
    expired = date.today() - timedelta(days=1)
    with factory() as session:
        trade = session.get(PaperTrade, UUID(trade_id))
        trade.expiry = expired
        session.commit()
    zone = ZoneInfo("Asia/Kolkata")

    async def session_for_expiry(day):
        assert day == expired
        return ExchangeSession(
            exchange="NSE",
            start=datetime.combine(day, time(9, 15), tzinfo=zone),
            end=datetime.combine(day, time(15, 30), tzinfo=zone),
        )

    async def search_index(_query, *, exchange, segment):
        assert exchange == "NSE" and segment == "INDEX"
        return [DiscoveredInstrument(
            instrument_key="NSE_INDEX|Nifty 50", trading_symbol="NIFTY 50",
            name="Nifty 50", exchange="NSE", segment="INDEX",
            instrument_type="INDEX",
        )]

    async def daily_index(_key, _start, _end, interval):
        assert interval == "1d"
        return [HistoricalBar(
            timestamp=datetime.combine(expired, time(9, 15), tzinfo=zone),
            open=Decimal("25075"), high=Decimal("25125"),
            low=Decimal("25050"), close=Decimal("25100"), volume=0,
        )]

    provider.get_equity_session = session_for_expiry
    provider.search_instruments = search_index
    provider.get_historical = daily_index
    reference_url = f"/paper/trades/{trade_id}/expiry-settlement/index-close-reference"
    official_url = (
        f"/paper/trades/{trade_id}/expiry-settlement/official-index-close-reference"
    )
    official_preview_url = f"/paper/trades/{trade_id}/expiry-settlement/official-preview"
    assert client.get(official_url).status_code == 401
    assert client.get(official_preview_url).status_code == 401

    async def official_close(day):
        assert day == expired
        return OfficialIndexClose(
            close=Decimal("25100.00"),
            source_url="https://archives.nseindia.com/content/indices/example.csv",
            sha256="a" * 64,
            retrieved_at=datetime.now(zone),
        )

    monkeypatch.setattr("apps.api.main.fetch_nifty_close", official_close)
    official = client.get(official_url, headers=headers)
    assert official.status_code == 200, official.text
    assert official.headers["cache-control"] == "no-store"
    assert official.json()["status"] == "available"
    assert official.json()["daily_close"] == "25100.00"
    official_preview = client.get(official_preview_url, headers=headers)
    assert official_preview.status_code == 200, official_preview.text
    assert official_preview.headers["cache-control"] == "no-store"
    assert official_preview.json()["status"] == "available"
    assert official_preview.json()["archive_sha256"] == "a" * 64
    assert official_preview.json()["preview"]["final_index_value"] == "25100.00"
    assert official_preview.json()["preview"]["settlement_cash_flow"] == "2500.00"
    assert official_preview.json()["preview"]["realized_pnl"] == "-25.00"
    assert "corrections" in official_preview.json()["warning"]
    with factory() as session:
        assert session.get(PaperTrade, UUID(trade_id)).status == "OPEN"
        assert len(list(session.scalars(select(PaperOrder)))) == 1

    async def missing_official(_day):
        raise OfficialCloseUnavailable("NSE daily index archive is unavailable.")

    monkeypatch.setattr("apps.api.main.fetch_nifty_close", missing_official)
    missing_official_response = client.get(official_url, headers=headers)
    assert missing_official_response.status_code == 200
    assert missing_official_response.json()["status"] == "unavailable"
    missing_preview = client.get(official_preview_url, headers=headers)
    assert missing_preview.status_code == 200
    assert missing_preview.json()["status"] == "unavailable"
    assert missing_preview.json()["preview"] is None
    monkeypatch.setattr("apps.api.main.fetch_nifty_close", official_close)
    assert client.get(reference_url).status_code == 401
    reference = client.get(reference_url, headers=headers)
    assert reference.status_code == 200, reference.text
    assert reference.headers["cache-control"] == "no-store"
    assert reference.json()["status"] == "available"
    assert reference.json()["daily_close"] == "25100"
    assert "not verified as the official" in reference.json()["warning"]
    with factory() as session:
        assert session.get(PaperTrade, UUID(trade_id)).status == "OPEN"
        assert len(list(session.scalars(select(PaperOrder)))) == 1

    async def duplicate_index(*_args):
        candle = (await daily_index(None, None, None, "1d"))[0]
        return [candle, candle]

    provider.get_historical = duplicate_index
    missing = client.get(reference_url, headers=headers)
    assert missing.status_code == 200 and missing.json()["status"] == "unavailable"
    provider.get_historical = daily_index
    body = {
        "settlement_date": expired.isoformat(),
        "final_index_value": "25100.00",
        "source_reference": "NSE final NIFTY settlement report for expiry",
    }
    preview = client.post(
        f"/paper/trades/{trade_id}/expiry-settlement/preview", headers=headers, json=body,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["settlement_cash_flow"] == "2500.00"
    assert preview.json()["realized_pnl"] == "-25.00"
    with factory() as session:
        assert session.get(PaperTrade, UUID(trade_id)).status == "OPEN"
        assert len(list(session.scalars(select(PaperOrder)))) == 1
    endpoint = f"/paper/trades/{trade_id}/expiry-settlement"
    assert client.post(endpoint, headers=headers, json=body).status_code == 422
    assert client.post(endpoint, headers=headers, json={
        **body, "settlement_date": date.today().isoformat(), "confirmed_final": True,
    }).status_code == 422
    settled = client.post(endpoint, headers=headers, json={
        **body, "confirmed_final": True,
    })
    assert settled.status_code == 200, settled.text
    assert settled.json()["exit_reason"] == "EXPIRY_SETTLEMENT"
    assert settled.json()["realized_pnl"] == "-25.00"
    assert client.get("/paper/account", headers=headers).json()["cash_balance"] == "999975.00"
    assert client.post(endpoint, headers=headers, json={
        **body, "confirmed_final": True,
    }).status_code == 200
    assert client.post(endpoint, headers=headers, json={
        **body, "final_index_value": "25100", "confirmed_final": True,
    }).status_code == 200
    assert client.post(endpoint, headers=headers, json={
        **body, "final_index_value": "25101.00", "confirmed_final": True,
    }).status_code == 409
    with factory() as session:
        orders = list(session.scalars(
            select(PaperOrder).where(PaperOrder.trade_id == UUID(trade_id))
        ))
        assert len(orders) == 2
        close_order = next(order for order in orders if order.action == "CLOSE")
        assert close_order.fill_snapshot["quality_status"] == "OPERATOR_RECORDED"
        assert close_order.net_cash_flow == Decimal("2500.00")
    strategy = next(row for row in client.get("/paper/strategies", headers=headers).json()
                    if row["key"] == "long_call")
    assert strategy["qualified_automated_trade_count"] == 0


def test_expiry_preview_is_account_scoped(client, monkeypatch):
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    first = _token(client)
    opened = client.post("/paper/trades", headers={
        **first, "Idempotency-Key": f"entry_{uuid4().hex}",
    }, json={
        "strategy_key": "long_call", "expiry": provider.expiry.isoformat(),
        "reference_strike": "25000", "lots": 1,
    }).json()
    other_token = client.post("/auth/register", json={
        "email": "other-settlement@example.com", "password": "another-strong-password",
    }).json()["access_token"]
    other = {"Authorization": f"Bearer {other_token}"}
    body = {
        "settlement_date": provider.expiry.isoformat(),
        "final_index_value": "25100.00",
        "source_reference": "NSE final NIFTY settlement report",
        "confirmed_final": True,
    }
    assert client.post(
        f"/paper/trades/{opened['id']}/expiry-settlement/preview",
        headers=other, json=body,
    ).status_code == 404
    assert client.post(
        f"/paper/trades/{opened['id']}/expiry-settlement",
        headers=other, json=body,
    ).status_code == 404
    assert client.get(
        f"/paper/trades/{opened['id']}/expiry-settlement/index-close-reference",
        headers=other,
    ).status_code == 404
    assert client.get(
        f"/paper/trades/{opened['id']}/expiry-settlement/official-index-close-reference",
        headers=other,
    ).status_code == 404
    assert client.get(
        f"/paper/trades/{opened['id']}/expiry-settlement/official-preview",
        headers=other,
    ).status_code == 404


def test_nse_archive_confirmation_rechecks_fingerprint_before_paper_cash(client, monkeypatch):
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = _token(client)
    opened = client.post("/paper/trades", headers={
        **headers, "Idempotency-Key": f"entry_{uuid4().hex}",
    }, json={
        "strategy_key": "long_call", "expiry": provider.expiry.isoformat(),
        "reference_strike": "25000", "lots": 1,
    })
    assert opened.status_code == 201
    trade_id = opened.json()["id"]
    expired = date.today() - timedelta(days=1)
    factory = client.app.state.test_session_factory
    with factory() as session:
        session.get(PaperTrade, UUID(trade_id)).expiry = expired
        session.commit()

    async def official_close(_day):
        return OfficialIndexClose(
            close=Decimal("25100.00"), source_url=archive_url(expired),
            sha256="a" * 64, retrieved_at=datetime.now(ZoneInfo("Asia/Kolkata")),
        )

    monkeypatch.setattr("apps.api.main.fetch_nifty_close", official_close)
    endpoint = f"/paper/trades/{trade_id}/expiry-settlement"
    body = {
        "settlement_date": expired.isoformat(), "final_index_value": "25100.00",
        "source_reference": archive_url(expired), "archive_sha256": "a" * 64,
        "confirmed_final": True,
    }
    assert client.post(endpoint, headers=headers, json={
        **body, "archive_sha256": "b" * 64,
    }).status_code == 409
    with factory() as session:
        assert session.get(PaperTrade, UUID(trade_id)).status == "OPEN"
        assert len(list(session.scalars(select(PaperOrder)))) == 1
    settled = client.post(endpoint, headers=headers, json=body)
    assert settled.status_code == 200, settled.text
    with factory() as session:
        order = session.scalar(select(PaperOrder).where(
            PaperOrder.trade_id == UUID(trade_id), PaperOrder.action == "CLOSE",
        ))
        assert order.fill_snapshot["settlement_reference"]["archive_sha256"] == "a" * 64

    async def unavailable(_day):
        raise OfficialCloseUnavailable("Archive temporarily unavailable")

    monkeypatch.setattr("apps.api.main.fetch_nifty_close", unavailable)
    assert client.post(endpoint, headers=headers, json=body).status_code == 200
