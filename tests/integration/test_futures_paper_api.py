import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from starlette.testclient import TestClient

from apps.api.services.futures_paper import monitor_futures_exits
from apps.api.settings import get_settings
from packages.database.futures_models import PaperFuturesTrade
from packages.database.models import PaperAccount, User
from packages.market_data.contracts import DiscoveredInstrument, ExchangeSession, Quote

KEY = "NSE_FO|NIFTYFUT"


class FuturesProvider:
    provider_id = "test"

    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self.session_open = now - timedelta(hours=1)
        self.session_close = now + timedelta(hours=2)
        self.bid = Decimal("25000")
        self.ask = Decimal("25002")
        self.basis = "exchange"
        self.expiry = date.today() + timedelta(days=20)

    async def search_instruments(self, query, *, exchange="NSE", segment="EQ"):
        return [
            DiscoveredInstrument(
                instrument_key=KEY, trading_symbol="NIFTY FUT 24 NOV 26", exchange="NSE",
                segment="NSE_FO", instrument_type="FUT", expiry=self.expiry, lot_size=75,
                tick_size=Decimal("0.1"),
            ),
            DiscoveredInstrument(
                instrument_key="NSE_FO|BANKFUT", trading_symbol="BANKNIFTY FUT 24 NOV 26",
                exchange="NSE", segment="NSE_FO", instrument_type="FUT", expiry=self.expiry,
                lot_size=30, tick_size=Decimal("0.1"),
            ),
        ]

    async def get_nfo_session(self, trading_day):
        return ExchangeSession(exchange="NFO", start=self.session_open, end=self.session_close)

    async def get_quote(self, instrument_token):
        now = datetime.now(timezone.utc)
        return Quote(
            instrument_token=instrument_token, timestamp=now, timestamp_basis=self.basis,
            book_as_of=now, book_timestamp_basis=self.basis, last_price=self.bid,
            bid=self.bid, ask=self.ask, bid_quantity=1500, ask_quantity=1500,
        )


def _login(client: TestClient, email: str) -> dict[str, str]:
    auth = client.post("/auth/register", json={"email": email, "password": "a-long-test-password"})
    return {"Authorization": f"Bearer {auth.json()['access_token']}"}


def _open(client, headers, key="futures-key-1", **overrides):
    body = {"instrument_key": KEY, "side": "LONG", "lots": 1, "stop_loss_amount": "6000",
            "target_profit_amount": "3000", "acknowledged_unbounded_risk": True, **overrides}
    return client.post("/paper/futures/trades", headers={**headers, "Idempotency-Key": key},
                       json=body)


def _setup(client, monkeypatch, email="futures@example.com"):
    provider = FuturesProvider()
    monkeypatch.setattr("apps.api.futures_routes.get_market_data_provider", lambda: provider)
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    return provider, _login(client, email)


def test_contracts_list_only_nifty_futures(client, monkeypatch):
    _, headers = _setup(client, monkeypatch)
    response = client.get("/paper/futures/contracts", headers=headers)
    assert response.status_code == 200
    assert [row["instrument_key"] for row in response.json()] == [KEY]
    quote = client.get("/paper/futures/quote", headers=headers, params={"instrument_key": KEY})
    assert quote.status_code == 200
    assert Decimal(quote.json()["margin_per_lot"]) == Decimal("225018.00")


def test_open_blocks_margin_replays_and_reconciles(client, monkeypatch):
    _, headers = _setup(client, monkeypatch)
    opened = _open(client, headers)
    assert opened.status_code == 201, opened.text
    trade = opened.json()
    assert Decimal(trade["entry_price"]) == Decimal("25002")
    assert Decimal(trade["margin_blocked"]) == Decimal("225018.00")
    account = client.get("/paper/account", headers=headers).json()
    assert Decimal(account["cash_balance"]) == Decimal("774982.00")

    assert _open(client, headers).json()["id"] == trade["id"]
    assert _open(client, headers, lots=2).status_code == 409
    ledger = client.get("/paper/account/ledger-reconciliation", headers=headers).json()
    assert ledger["status"] == "MATCHED"
    assert ledger["open_trade_count"] == 1


def test_stop_loss_must_fit_the_per_trade_risk_cap(client, monkeypatch):
    _, headers = _setup(client, monkeypatch)
    response = _open(client, headers, stop_loss_amount="25000")
    assert response.status_code == 409
    assert "per-trade risk cap" in response.json()["detail"]


def test_unverified_quotes_are_blocked_by_default(client, monkeypatch):
    provider, headers = _setup(client, monkeypatch)
    provider.basis = "provider_response"
    response = _open(client, headers)
    assert response.status_code == 409
    assert "not exchange-verified" in response.json()["detail"]


def test_monitor_closes_at_target_and_releases_margin(client, monkeypatch):
    provider, headers = _setup(client, monkeypatch)
    trade_id = _open(client, headers).json()["id"]
    provider.bid, provider.ask = Decimal("25010"), Decimal("25012")
    assert client.post("/paper/monitor", headers=headers).status_code == 200
    trade = client.get("/paper/futures/trades", headers=headers).json()[0]
    assert trade["status"] == "OPEN"
    assert Decimal(trade["unrealized_pnl"]) == Decimal("600.00")

    provider.bid, provider.ask = Decimal("25045"), Decimal("25047")
    result = client.post("/paper/monitor", headers=headers).json()
    assert trade_id in result["closed_trade_ids"]
    trade = client.get("/paper/futures/trades", headers=headers).json()[0]
    assert (trade["status"], trade["exit_reason"]) == ("CLOSED", "TARGET")
    assert Decimal(trade["realized_pnl"]) == Decimal("3225.00")
    account = client.get("/paper/account", headers=headers).json()
    assert Decimal(account["cash_balance"]) == Decimal("1003225.00")
    ledger = client.get("/paper/account/ledger-reconciliation", headers=headers).json()
    assert ledger["status"] == "MATCHED"


def test_short_stop_loss_triggers_on_a_rally(client, monkeypatch):
    provider, headers = _setup(client, monkeypatch)
    assert _open(client, headers, side="SHORT", target_profit_amount=None).status_code == 201
    provider.bid, provider.ask = Decimal("25078"), Decimal("25080")
    client.post("/paper/monitor", headers=headers)
    trade = client.get("/paper/futures/trades", headers=headers).json()[0]
    assert (trade["status"], trade["exit_reason"]) == ("CLOSED", "STOP_LOSS")
    assert Decimal(trade["realized_pnl"]) == Decimal("-6000.00")


def test_manual_close_and_double_close(client, monkeypatch):
    provider, headers = _setup(client, monkeypatch)
    trade_id = _open(client, headers).json()["id"]
    provider.bid, provider.ask = Decimal("24990"), Decimal("24992")
    closed = client.post(f"/paper/futures/trades/{trade_id}/close", headers=headers)
    assert closed.status_code == 200
    assert closed.json()["exit_reason"] == "MANUAL"
    assert Decimal(closed.json()["realized_pnl"]) == Decimal("-900.00")
    again = client.post(f"/paper/futures/trades/{trade_id}/close", headers=headers)
    assert again.status_code == 409


def test_futures_risk_counts_toward_the_shared_open_trade_cap(client, monkeypatch):
    monkeypatch.setenv("PAPER_MAX_OPEN_TRADES", "1")
    get_settings.cache_clear()
    _, headers = _setup(client, monkeypatch)
    assert _open(client, headers).status_code == 201
    second = _open(client, headers, key="futures-key-2")
    assert second.status_code == 409
    assert "Maximum number" in second.json()["detail"]


def test_worker_monitor_marks_without_closing_inside_limits(client, monkeypatch):
    provider, headers = _setup(client, monkeypatch)
    _open(client, headers)
    factory = client.app.state.test_session_factory
    with factory() as session:
        user_id = session.query(User.id).scalar()
        evaluated, closed, warnings = asyncio.run(
            monitor_futures_exits(user_id, session, provider, get_settings())
        )
    assert (evaluated, closed, warnings) == (1, [], [])
    with factory() as session:
        trade = session.query(PaperFuturesTrade).one()
        account = session.query(PaperAccount).one()
        assert trade.status == "OPEN" and trade.last_mark_price == Decimal("25000")
        assert account.cash_balance == Decimal("774982.00")


def test_racing_duplicate_key_replays_instead_of_failing(client, monkeypatch):
    from apps.api.services import futures_paper

    _, headers = _setup(client, monkeypatch)
    first = _open(client, headers).json()
    real_replay = futures_paper._replay
    calls = {"count": 0}

    def replay_misses_twice(*args):
        # Simulate both pre-insert checks racing ahead of the first commit.
        calls["count"] += 1
        return None if calls["count"] <= 2 else real_replay(*args)

    monkeypatch.setattr(futures_paper, "_replay", replay_misses_twice)
    again = _open(client, headers)
    assert again.status_code == 201
    assert again.json()["id"] == first["id"]
    assert calls["count"] == 3
    trades = client.get("/paper/futures/trades", headers=headers).json()
    assert len(trades) == 1
    ledger = client.get("/paper/account/ledger-reconciliation", headers=headers).json()
    assert ledger["status"] == "MATCHED"
