"""A call calendar opens, is valued and exits with each leg priced from its own expiry's chain."""

import asyncio
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from test_paper_trading import FakeUpstoxProvider, _token

from apps.api.settings import get_settings
from apps.paper_worker.main import run_cycle
from packages.database.models import PaperOrder, PaperTrade
from packages.market_data.upstox import UpstoxOptionContract


class CalendarProvider(FakeUpstoxProvider):
    """Two weekly expiries; the later one's options cost 30 more and can be moved alone."""

    def __init__(self) -> None:
        super().__init__()
        self.later = self.expiry + timedelta(days=7)
        self.later_adjustment = Decimal(0)
        self.later_available = True
        self.contracts += [
            UpstoxOptionContract(
                instrument_key=f"NSE_FO|L{contract.strike}{contract.option_type}",
                trading_symbol=f"NIFTY-L-{contract.strike}-{contract.option_type}",
                expiry=self.later, strike=contract.strike, option_type=contract.option_type,
                lot_size=25, tick_size=Decimal("0.05"),
            )
            for contract in list(self.contracts)
        ]

    async def get_option_chain(self, underlying, expiry):
        if expiry == self.later and not self.later_available:
            from packages.market_data.contracts import MarketDataUnavailable
            raise MarketDataUnavailable("later expiry unavailable")
        chain = await super().get_option_chain(underlying, expiry)
        extra = Decimal(30) + self.later_adjustment if expiry == self.later else Decimal(0)
        contracts = []
        for item in chain.contracts:
            if item.expiry != expiry or not any(
                c.instrument_key == item.instrument_token and c.expiry == expiry
                for c in self.contracts
            ):
                continue
            quote = item.quote.model_copy(update={
                "bid": item.quote.bid + extra, "ask": item.quote.ask + extra,
                "last_price": item.quote.last_price + extra,
            })
            contracts.append(item.model_copy(update={"quote": quote}))
        return chain.model_copy(update={"contracts": contracts})


def _open(client, monkeypatch, key="call_calendar", **rules):
    provider = CalendarProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = {**_token(client), "Idempotency-Key": "calendar-entry"}
    opened = client.post("/paper/trades", headers=headers, json={
        "strategy_key": key, "expiry": provider.expiry.isoformat(),
        "reference_strike": "25000", "lots": 1, **rules,
    })
    assert opened.status_code == 201, opened.text
    return provider, headers, opened.json(), client.app.state.test_session_factory


def test_a_calendar_opens_for_a_debit_with_its_later_leg_on_the_next_expiry(client, monkeypatch):
    provider, headers, trade, _ = _open(client, monkeypatch)
    near, far = trade["legs"]
    assert (near["side"], near["expiry"], Decimal(near["entry_price"])) == ("SELL", None, 100)
    assert (far["side"], far["expiry"]) == ("BUY", provider.later.isoformat())
    assert Decimal(far["entry_price"]) == 131  # the later expiry's ask
    assert Decimal(trade["max_loss"]) == (131 - 100) * 25

    listed = client.get("/paper/trades", headers=headers).json()["trades"][0]
    # Marked at the exit side of each chain: buy the near call back at 101, sell the far at 130.
    assert Decimal(listed["unrealized_pnl"]) == (130 - 101 - 31) * 25


def test_the_worker_stops_a_calendar_out_on_its_later_leg_alone(client, monkeypatch):
    provider, _, trade, factory = _open(client, monkeypatch, stop_loss_amount="300")
    provider.later_adjustment = Decimal(-20)
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    with factory() as session:
        saved = session.get(PaperTrade, UUID(trade["id"]))
        assert (saved.status, saved.exit_reason) == ("CLOSED", "STOP_LOSS")
        assert saved.realized_pnl == (110 - 101 - 31) * 25
        orders = [order for order in session.scalars(select(PaperOrder))
                  if order.trade_id == saved.id]
        assert len(orders) == 2  # one entry, one exit


def test_a_calendar_is_not_priced_when_its_later_chain_is_missing(client, monkeypatch):
    provider, headers, trade, factory = _open(client, monkeypatch, stop_loss_amount="1")
    provider.later_available = False
    asyncio.run(run_cycle(factory, provider, get_settings()))
    with factory() as session:
        assert session.get(PaperTrade, UUID(trade["id"])).status == "OPEN"
    listed = client.get("/paper/trades", headers=headers).json()["trades"][0]
    assert listed["unrealized_pnl"] is None


def test_a_manual_close_prices_the_later_leg_from_its_own_chain(client, monkeypatch):
    provider, headers, trade, _ = _open(client, monkeypatch)
    provider.later_adjustment = Decimal(10)
    closed = client.post(f"/paper/trades/{trade['id']}/close", headers=headers)
    assert closed.status_code == 200, closed.text
    assert Decimal(closed.json()["realized_pnl"]) == (140 - 101 - 31) * 25


def test_a_put_calendar_risks_its_debit_plus_the_later_put_trading_below_intrinsic(
        client, monkeypatch):
    provider, _, trade, _ = _open(client, monkeypatch, key="calendar_put_spread")
    near, far = trade["legs"]
    assert (near["option_type"], near["side"], far["side"]) == ("PE", "SELL", "BUY")
    assert far["expiry"] == provider.later.isoformat()
    debit = (131 - 100) * 25
    days = (provider.later - provider.expiry).days
    allowance = Decimal(25000) * (1 - 1 / (1 + Decimal("0.10") * days / 365)) * 25
    assert Decimal(trade["max_loss"]) == (debit + allowance).quantize(Decimal("0.01"))


def test_a_put_diagonal_with_its_bought_put_above_risks_only_the_debit(client, monkeypatch):
    provider, _, trade, _ = _open(client, monkeypatch, key="diagonal_put_spread")
    near, far = trade["legs"]
    assert (Decimal(near["strike"]), Decimal(near["entry_price"])) == (24900, 80)
    assert (Decimal(far["strike"]), Decimal(far["entry_price"])) == (25000, 131)
    assert Decimal(trade["max_loss"]) == (131 - 80) * 25
