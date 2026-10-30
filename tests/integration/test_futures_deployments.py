"""End-to-end armed NIFTY futures templates: arm, wait, fill, exit, roll and settle."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient
from test_paper_trading import FakeUpstoxProvider

from apps.api.services import futures_deployments
from apps.api.services.futures_deployments import process_futures_deployments
from apps.api.settings import get_settings
from apps.paper_worker.main import run_cycle
from packages.database.futures_models import PaperFuturesOrder, PaperFuturesTrade
from packages.database.models import (
    AuditEvent,
    PaperDeployment,
    StrategyDemotion,
    StrategyEligibilityEvent,
)
from packages.market_data.contracts import (
    DiscoveredInstrument,
    MarketDataUnavailable,
    Quote,
)
from packages.market_data.quality import assess_option_chain
from packages.paper_trading.session import exchange_today
from packages.paper_trading.worker_lease import LeaseToken, acquire

NEAR = "NSE_FO|NIFTYNEAR"
FAR = "NSE_FO|NIFTYFAR"
POSITIONAL_LONG = "nifty_futures_positional_long"
POSITIONAL_SHORT = "nifty_futures_positional_short"
INTRADAY_LONG = "nifty_futures_intraday_long"
REAL_REGIME_BIAS = futures_deployments._regime_bias


class Provider(FakeUpstoxProvider):
    def __init__(self) -> None:
        super().__init__()
        today = exchange_today()
        self.near_expiry = today + timedelta(days=20)
        self.far_expiry = today + timedelta(days=48)
        self.lot_size = 75
        self.bid, self.ask = Decimal("25000"), Decimal("25002")
        self.quote_basis = "exchange"
        self.quote_error: Exception | None = None
        self.bias: str | None = "UP"

    async def search_instruments(self, query, *, exchange="NSE", segment="EQ"):
        return [
            DiscoveredInstrument(
                instrument_key=key, trading_symbol=f"NIFTY FUT {expiry}", exchange="NSE",
                segment="NSE_FO", instrument_type="FUT", expiry=expiry, lot_size=self.lot_size,
                tick_size=Decimal("0.1"),
            )
            for key, expiry in ((NEAR, self.near_expiry), (FAR, self.far_expiry))
        ]

    async def get_quote(self, instrument_token):
        if self.quote_error is not None:
            raise self.quote_error
        now = datetime.now(timezone.utc)
        return Quote(
            instrument_token=instrument_token, timestamp=now, timestamp_basis=self.quote_basis,
            book_as_of=now, book_timestamp_basis=self.quote_basis, last_price=self.bid,
            bid=self.bid, ask=self.ask, bid_quantity=1500, ask_quantity=1500,
        )


@pytest.fixture
def setup(client: TestClient, monkeypatch):
    provider = Provider()

    async def bias(_provider, _chain, _settings):
        return provider.bias

    monkeypatch.setattr(futures_deployments, "_regime_bias", bias)
    monkeypatch.setattr("apps.api.futures_routes.get_market_data_provider", lambda: provider)
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    auth = client.post("/auth/register", json={
        "email": "futures-armed@example.com", "password": "a-long-test-password",
    })
    headers = {"Authorization": f"Bearer {auth.json()['access_token']}"}
    return provider, headers, client.app.state.test_session_factory


def _arm(client, headers, strategy=POSITIONAL_LONG, lots=1, key=None):
    return client.post(
        "/paper/futures/deployments",
        headers={**headers, "Idempotency-Key": key or f"arm_{uuid4().hex}"},
        json={"strategy_key": strategy, "expected_template_version": "1", "lots": lots,
              "acknowledged_unbounded_risk": True},
    )


def _cycle(factory, provider):
    return asyncio.run(run_cycle(factory, provider, get_settings()))


def _one_trade(factory) -> PaperFuturesTrade:
    with factory() as session:
        return session.scalars(select(PaperFuturesTrade)).one()


def _deployment(factory, deployment_id: str) -> PaperDeployment:
    with factory() as session:
        return session.get(PaperDeployment, UUID(deployment_id))


def _ledger(client, headers) -> str:
    return client.get("/paper/account/ledger-reconciliation", headers=headers).json()["status"]


def _set_trade_expiry(factory, expiry: date) -> None:
    with factory() as session:
        session.scalars(select(PaperFuturesTrade)).one().expiry = expiry
        session.commit()


def test_templates_are_listed_with_their_risk_and_no_forecast(client, setup):
    _, headers, _ = setup
    rows = client.get("/paper/futures/strategies", headers=headers).json()
    assert {row["key"] for row in rows} == {
        POSITIONAL_LONG, POSITIONAL_SHORT, INTRADAY_LONG, "nifty_futures_intraday_short",
    }
    assert all("not a forecast" in row["note"] for row in rows)


def test_arm_waits_then_fills_at_the_ask_with_margin_and_shows_everywhere(client, setup):
    provider, headers, factory = setup
    provider.bias = "NEUTRAL"
    armed = _arm(client, headers)
    assert armed.status_code == 201, armed.text
    body = armed.json()
    assert (body["instrument_kind"], body["status"]) == ("FUTURES", "WAITING_FOR_ENTRY")
    assert body["trading_symbol"].startswith("NIFTY FUT")
    assert Decimal(body["stop_loss_amount"]) == Decimal("9000.00")

    # Rules are unmet: nothing fills, and the decision is on record with its inputs.
    assert _cycle(factory, provider) == "HEALTHY"
    with factory() as session:
        assert session.scalars(select(PaperFuturesTrade)).first() is None
        check = session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "paper.deployment.entry_checked"
        )).one()
    assert check.details["snapshot"]["decision"] == "RULES_NOT_MET"
    assert check.details["snapshot"]["rule_metrics"]["regime_direction"] == "0"
    decisions = client.get(f"/paper/deployments/{body['id']}/decisions", headers=headers)
    assert decisions.status_code == 200 and len(decisions.json()) >= 2

    provider.bias = "UP"
    assert _cycle(factory, provider) == "HEALTHY"
    trade = _one_trade(factory)
    assert trade.entry_price == Decimal("25002")
    assert trade.strategy_key == POSITIONAL_LONG
    assert trade.deployment_id == UUID(body["id"])
    assert trade.entry_snapshot["entry_rules"]["results"][0]["passed"] is True
    assert _deployment(factory, body["id"]).status == "FILLED"
    account = client.get("/paper/account", headers=headers).json()
    assert account["open_trade_count"] == 1
    assert Decimal(account["open_max_loss"]) == Decimal("9000.00")
    assert Decimal(account["cash_balance"]) == Decimal(1_000_000) - trade.margin_blocked
    listed = client.get("/paper/futures/trades", headers=headers).json()[0]
    assert listed["strategy_name"] == "Positional long future"
    assert _ledger(client, headers) == "MATCHED"

    # A later cycle must not open a second position for the filled deployment.
    _cycle(factory, provider)
    assert _one_trade(factory).id == trade.id


def test_short_template_fills_at_the_bid(client, setup):
    provider, headers, factory = setup
    provider.bias = "DOWN"
    assert _arm(client, headers, POSITIONAL_SHORT).status_code == 201
    _cycle(factory, provider)
    trade = _one_trade(factory)
    assert (trade.side, trade.entry_price) == ("SHORT", Decimal("25000"))


def test_arm_is_idempotent_and_refuses_duplicates_and_unknown_templates(client, setup):
    _, headers, _ = setup
    first = _arm(client, headers, key="arm_same_key_1")
    assert _arm(client, headers, key="arm_same_key_1").json()["id"] == first.json()["id"]
    assert _arm(client, headers, lots=2, key="arm_same_key_1").status_code == 409
    assert _arm(client, headers).status_code == 409
    assert _arm(client, headers, "long_call").status_code == 422
    stale = client.post(
        "/paper/futures/deployments", headers={**headers, "Idempotency-Key": "arm_old_version"},
        json={"strategy_key": INTRADAY_LONG, "expected_template_version": "0", "lots": 1,
              "acknowledged_unbounded_risk": True},
    )
    assert stale.status_code == 409


def test_arm_refuses_a_stop_above_the_per_trade_cap(client, setup):
    _, headers, _ = setup
    # 120 points x 75 x 3 lots = 27,000, above 2% of 1,000,000.
    refused = _arm(client, headers, lots=3)
    assert refused.status_code == 409
    assert "per-trade risk cap of ₹20000.00" in refused.json()["detail"]
    assert _arm(client, headers, lots=2).status_code == 201


def test_kill_switch_blocks_arming_and_fills_but_never_exits(client, setup):
    provider, headers, factory = setup
    armed = _arm(client, headers).json()
    assert client.put(
        "/paper/account/kill-switch", headers=headers, json={"enabled": True}
    ).status_code == 200
    assert _arm(client, headers, INTRADAY_LONG).status_code == 423
    _cycle(factory, provider)
    with factory() as session:
        assert session.scalars(select(PaperFuturesTrade)).first() is None
    waiting = _deployment(factory, armed["id"])
    assert (waiting.status, "kill switch" in waiting.detail) == ("WAITING_FOR_ENTRY", True)

    client.put("/paper/account/kill-switch", headers=headers, json={"enabled": False})
    _cycle(factory, provider)
    assert _one_trade(factory).status == "OPEN"
    client.put("/paper/account/kill-switch", headers=headers, json={"enabled": True})
    provider.bid, provider.ask = Decimal("24870"), Decimal("24872")
    _cycle(factory, provider)
    assert _one_trade(factory).exit_reason == "STOP_LOSS"


def test_aggregate_and_daily_limits_block_the_fill_and_keep_it_armed(client, setup, monkeypatch):
    provider, headers, factory = setup
    armed = _arm(client, headers).json()
    monkeypatch.setattr(get_settings(), "paper_max_portfolio_risk_pct", 0.5)
    _cycle(factory, provider)
    assert "portfolio cap" in _deployment(factory, armed["id"]).detail
    monkeypatch.setattr(get_settings(), "paper_max_portfolio_risk_pct", 10)
    monkeypatch.setattr(get_settings(), "paper_daily_loss_limit_pct", 0.5)
    _cycle(factory, provider)
    assert "Daily paper loss budget" in _deployment(factory, armed["id"]).detail
    with factory() as session:
        assert session.scalars(select(PaperFuturesTrade)).first() is None
    assert _deployment(factory, armed["id"]).status == "WAITING_FOR_ENTRY"


def test_margin_above_cash_blocks_the_fill(client, setup):
    provider, headers, factory = setup
    armed = _arm(client, headers).json()
    # Margin scales with price, so this contract needs more than the ₹1,000,000 of cash.
    provider.bid, provider.ask = Decimal("130000"), Decimal("130010")
    _cycle(factory, provider)
    assert "futures margin" in _deployment(factory, armed["id"]).detail
    with factory() as session:
        assert session.scalars(select(PaperFuturesTrade)).first() is None


@pytest.mark.parametrize("mutate", ["unverified", "wide", "provider_error"])
def test_bad_quotes_never_fill(client, setup, mutate):
    provider, headers, factory = setup
    armed = _arm(client, headers).json()
    if mutate == "unverified":
        provider.quote_basis = "provider_response"
    elif mutate == "wide":
        provider.bid, provider.ask = Decimal("24000"), Decimal("26000")
    else:
        provider.quote_error = MarketDataUnavailable("feed down")
    _cycle(factory, provider)
    with factory() as session:
        assert session.scalars(select(PaperFuturesTrade)).first() is None
    assert _deployment(factory, armed["id"]).status == "WAITING_FOR_ENTRY"


def test_stop_target_and_trailing_exits_release_margin(client, setup):
    provider, headers, factory = setup
    _arm(client, headers)
    _cycle(factory, provider)
    # Target is 240 points from the 25002 ask; exits price at the bid.
    provider.bid, provider.ask = Decimal("25100"), Decimal("25102")
    _cycle(factory, provider)
    trade = _one_trade(factory)
    assert trade.status == "OPEN" and trade.peak_unrealized_pnl == Decimal("7350.00")
    # The 120-point trail sits 22 points below entry from a 98-point peak.
    provider.bid, provider.ask = Decimal("24980"), Decimal("24982")
    _cycle(factory, provider)
    trade = _one_trade(factory)
    assert (trade.status, trade.exit_reason) == ("CLOSED", "TRAILING_STOP")
    assert trade.realized_pnl == Decimal("-1650.00")
    account = client.get("/paper/account", headers=headers).json()
    assert Decimal(account["cash_balance"]) == Decimal("998350.00")
    assert account["open_trade_count"] == 0
    assert _ledger(client, headers) == "MATCHED"


def test_target_exit(client, setup):
    provider, headers, factory = setup
    _arm(client, headers)
    _cycle(factory, provider)
    provider.bid, provider.ask = Decimal("25250"), Decimal("25252")
    _cycle(factory, provider)
    trade = _one_trade(factory)
    assert (trade.exit_reason, trade.realized_pnl) == ("TARGET", Decimal("18600.00"))


def test_intraday_refuses_entry_in_the_closing_window_and_squares_off(client, setup):
    provider, headers, factory = setup
    armed = _arm(client, headers, INTRADAY_LONG).json()
    open_close = provider.session_close
    provider.session_close = datetime.now(timezone.utc) + timedelta(minutes=10)
    _cycle(factory, provider)
    assert "daily square-off" in _deployment(factory, armed["id"]).detail
    with factory() as session:
        assert session.scalars(select(PaperFuturesTrade)).first() is None

    provider.session_close = open_close
    _cycle(factory, provider)
    trade = _one_trade(factory)
    assert trade.status == "OPEN" and trade.entry_snapshot["exit_policy"]["intraday"] is True
    provider.session_close = datetime.now(timezone.utc) + timedelta(minutes=10)
    _cycle(factory, provider)
    trade = _one_trade(factory)
    assert (trade.status, trade.exit_reason) == ("CLOSED", "SESSION_SQUARE_OFF")
    assert _ledger(client, headers) == "MATCHED"


def test_intraday_left_open_overnight_exits_at_the_next_check(client, setup):
    provider, headers, factory = setup
    _arm(client, headers, INTRADAY_LONG)
    _cycle(factory, provider)
    with factory() as session:
        trade = session.scalars(select(PaperFuturesTrade)).one()
        trade.opened_at = trade.opened_at - timedelta(days=1)
        session.commit()
    _cycle(factory, provider)
    assert _one_trade(factory).exit_reason == "SESSION_SQUARE_OFF"


def test_trade_times_are_labelled_utc_on_sqlite(client, setup):
    provider, headers, factory = setup
    _arm(client, headers)
    _cycle(factory, provider)
    trade_id = _one_trade(factory).id
    client.post(f"/paper/futures/trades/{trade_id}/close", headers=headers)
    row = client.get("/paper/futures/trades", headers=headers).json()[0]
    for field in ("opened_at", "closed_at", "last_marked_at"):
        assert row[field].endswith(("Z", "+00:00")), row[field]


def test_positional_holds_through_the_closing_window(client, setup):
    provider, headers, factory = setup
    _arm(client, headers)
    _cycle(factory, provider)
    provider.session_close = datetime.now(timezone.utc) + timedelta(minutes=10)
    _cycle(factory, provider)
    assert _one_trade(factory).status == "OPEN"


def test_expiry_day_square_off_still_applies_to_a_position_on_the_last_day(client, setup):
    provider, headers, factory = setup
    _arm(client, headers)
    _cycle(factory, provider)
    _set_trade_expiry(factory, exchange_today())
    provider.session_close = datetime.now(timezone.utc) + timedelta(minutes=10)
    _cycle(factory, provider)
    assert _one_trade(factory).exit_reason == "EXPIRY_SQUARE_OFF"


def test_new_arms_skip_a_contract_inside_the_rollover_window(client, setup):
    provider, headers, factory = setup
    provider.near_expiry = exchange_today() + timedelta(days=2)
    body = _arm(client, headers).json()
    assert body["expiry"] == provider.far_expiry.isoformat()
    _cycle(factory, provider)
    assert _one_trade(factory).instrument_key == FAR


def test_a_waiting_strategy_rolls_to_the_next_contract(client, setup):
    provider, headers, factory = setup
    provider.bias = "NEUTRAL"
    body = _arm(client, headers).json()
    assert body["expiry"] == provider.near_expiry.isoformat()
    provider.near_expiry = exchange_today() + timedelta(days=1)
    _cycle(factory, provider)
    shown = client.get("/paper/deployments", headers=headers).json()[0]
    assert shown["expiry"] == provider.far_expiry.isoformat()
    provider.bias = "UP"
    _cycle(factory, provider)
    assert _one_trade(factory).instrument_key == FAR


def test_rollover_closes_and_rearms_without_reopening_until_rules_pass(client, setup):
    provider, headers, factory = setup
    _arm(client, headers)
    _cycle(factory, provider)
    provider.bias = "NEUTRAL"
    provider.near_expiry = exchange_today() + timedelta(days=2)
    _set_trade_expiry(factory, provider.near_expiry)
    _cycle(factory, provider)
    with factory() as session:
        old = session.scalars(select(PaperFuturesTrade)).one()
        assert (old.status, old.exit_reason) == ("CLOSED", "ROLLOVER")
        waiting = session.scalars(select(PaperDeployment).where(
            PaperDeployment.status == "WAITING_FOR_ENTRY"
        )).one()
        assert waiting.command_key == f"roll_{old.id.hex}"
        assert waiting.instrument_kind == "FUTURES"

    # Still flat while the rules are unmet; the risk stays released.
    _cycle(factory, provider)
    with factory() as session:
        assert len(list(session.scalars(select(PaperFuturesTrade)))) == 1
    assert client.get("/paper/account", headers=headers).json()["open_trade_count"] == 0

    provider.bias = "UP"
    _cycle(factory, provider)
    with factory() as session:
        trades = list(session.scalars(
            select(PaperFuturesTrade).where(PaperFuturesTrade.status == "OPEN")
        ))
        assert [trade.instrument_key for trade in trades] == [FAR]
        assert session.scalars(select(PaperDeployment).where(
            PaperDeployment.status == "WAITING_FOR_ENTRY"
        )).first() is None
    assert _ledger(client, headers) == "MATCHED"


def test_manual_ticket_has_no_rollover_or_daily_square_off(client, setup):
    provider, headers, factory = setup
    opened = client.post(
        "/paper/futures/trades", headers={**headers, "Idempotency-Key": "manual-ticket-1"},
        json={"instrument_key": NEAR, "side": "LONG", "lots": 1, "stop_loss_amount": "6000",
              "acknowledged_unbounded_risk": True},
    )
    assert opened.status_code == 201, opened.text
    _set_trade_expiry(factory, exchange_today() + timedelta(days=1))
    provider.session_close = datetime.now(timezone.utc) + timedelta(minutes=10)
    _cycle(factory, provider)
    assert _one_trade(factory).status == "OPEN"


def test_a_lost_lease_cannot_fill(client, setup):
    provider, headers, factory = setup
    armed = _arm(client, headers).json()
    with factory() as session:
        acquire(session, "current-owner", 120)
    stale = LeaseToken("old-owner", 0)
    checked, filled, _ = asyncio.run(
        process_futures_deployments(factory, provider, stale, get_settings())
    )
    assert (checked, filled) == (0, 0)
    with factory() as session:
        assert session.scalars(select(PaperFuturesTrade)).first() is None
    assert _deployment(factory, armed["id"]).status == "WAITING_FOR_ENTRY"


def test_a_cancel_stops_entry(client, setup):
    provider, headers, factory = setup
    provider.bias = "NEUTRAL"
    armed = _arm(client, headers, INTRADAY_LONG).json()
    cancelled = client.post(f"/paper/deployments/{armed['id']}/cancel", headers=headers)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    provider.bias = "UP"
    _cycle(factory, provider)
    with factory() as session:
        assert session.scalars(select(PaperFuturesTrade)).first() is None


def test_template_change_rejects_the_saved_rule(client, setup):
    provider, headers, factory = setup
    armed = _arm(client, headers).json()
    with factory() as session:
        row = session.get(PaperDeployment, UUID(armed["id"]))
        row.request_data = {**row.request_data, "strategy_definition": {"key": "old"}}
        session.commit()
    _cycle(factory, provider)
    assert _deployment(factory, armed["id"]).status == "REJECTED"


def test_lot_size_change_rejects_instead_of_rescaling_risk(client, setup):
    provider, headers, factory = setup
    armed = _arm(client, headers).json()
    provider.lot_size = 65
    _cycle(factory, provider)
    assert _deployment(factory, armed["id"]).status == "REJECTED"


def test_closed_futures_count_toward_strategy_demotion(client, setup, monkeypatch):
    provider, headers, factory = setup
    monkeypatch.setattr(get_settings(), "paper_demotion_loss_streak", 2)
    for _ in range(2):
        provider.bid, provider.ask = Decimal("25000"), Decimal("25002")
        assert _arm(client, headers).status_code == 201
        _cycle(factory, provider)
        provider.bid, provider.ask = Decimal("24870"), Decimal("24872")
        _cycle(factory, provider)
    provider.bid, provider.ask = Decimal("25000"), Decimal("25002")
    refused = _arm(client, headers)
    assert refused.status_code == 423 and "Paused after" in refused.json()["detail"]
    rows = client.get("/paper/strategies/demotions", headers=headers).json()
    assert any(row["strategy_key"] == POSITIONAL_LONG and row["paused"] for row in rows)


def test_regime_input_uses_the_worker_provider_and_stays_unknown_without_bars(client, setup):
    provider, _, _ = setup

    async def no_bars(*_args, **_kwargs):
        raise MarketDataUnavailable("history down")

    provider.get_historical = no_bars
    chain = asyncio.run(provider.get_option_chain("NIFTY", provider.expiry))
    chain = chain.model_copy(update={"quality": assess_option_chain(chain)})
    assert asyncio.run(REAL_REGIME_BIAS(provider, chain, get_settings())) is None


def _demote_then_cool_off(client, headers, factory, provider):
    """Two stopped trades pause the template; moving the pause back ends its cooling-off."""
    for _ in range(2):
        provider.bid, provider.ask = Decimal("25000"), Decimal("25002")
        assert _arm(client, headers).status_code == 201
        _cycle(factory, provider)
        provider.bid, provider.ask = Decimal("24870"), Decimal("24872")
        _cycle(factory, provider)
    provider.bid, provider.ask = Decimal("25000"), Decimal("25002")
    assert _arm(client, headers).status_code == 423
    with factory() as session:
        demotion = session.scalars(select(StrategyDemotion)).one()
        assert demotion.strategy_key == POSITIONAL_LONG
        demotion.demoted_at = demotion.demoted_at - timedelta(days=30)
        session.commit()


def test_worker_sweep_records_a_futures_pause_without_an_entry_attempt(client, setup, monkeypatch):
    provider, headers, factory = setup
    monkeypatch.setattr(get_settings(), "paper_demotion_loss_streak", 2)
    for _ in range(2):
        provider.bid, provider.ask = Decimal("25000"), Decimal("25002")
        assert _arm(client, headers).status_code == 201
        _cycle(factory, provider)
        provider.bid, provider.ask = Decimal("24870"), Decimal("24872")
        _cycle(factory, provider)
    # Nothing tried to enter since the second stop; only the sweep can have recorded it.
    _cycle(factory, provider)
    with factory() as session:
        assert session.scalars(select(StrategyDemotion)).one().status == "ACTIVE"


def test_probation_limits_futures_to_one_lot_and_one_position(client, setup, monkeypatch):
    provider, headers, factory = setup
    monkeypatch.setattr(get_settings(), "paper_demotion_loss_streak", 2)
    _demote_then_cool_off(client, headers, factory, provider)

    too_big = _arm(client, headers, lots=2)
    assert too_big.status_code == 423 and "at most 1 lot" in too_big.json()["detail"]
    armed = _arm(client, headers)
    assert armed.status_code == 201, armed.text
    second = _arm(client, headers)
    assert second.status_code == 423 and "one position or armed entry" in second.json()["detail"]

    _cycle(factory, provider)
    assert _deployment(factory, armed.json()["id"]).status == "FILLED"
    held = _arm(client, headers)
    assert held.status_code == 423 and "one position or armed entry" in held.json()["detail"]
    with factory() as session:
        history = list(session.scalars(select(StrategyEligibilityEvent)))
    assert "PROBATION_STARTED" in {event.event_type for event in history}


def test_probation_blocks_a_saved_two_lot_fill_and_keeps_it_waiting(client, setup, monkeypatch):
    provider, headers, factory = setup
    monkeypatch.setattr(get_settings(), "paper_demotion_loss_streak", 2)
    provider.bias = "NEUTRAL"
    waiting = _arm(client, headers, lots=2).json()
    provider.bias = "UP"
    with factory() as session:
        row = session.get(PaperDeployment, UUID(waiting["id"]))
        user_id = row.user_id
        session.add(StrategyDemotion(
            user_id=user_id, strategy_key=POSITIONAL_LONG, status="ACTIVE", reason="LOSS_STREAK",
            details={"summary": "test losses"},
            demoted_at=datetime.now(timezone.utc) - timedelta(days=30),
        ))
        session.commit()
    _cycle(factory, provider)
    with factory() as session:
        assert session.scalars(select(PaperFuturesTrade)).first() is None
    row = _deployment(factory, waiting["id"])
    assert row.status == "WAITING_FOR_ENTRY" and "at most 1 lot" in row.detail


def test_expired_position_warns_then_settles_once_at_the_reviewed_close(client, setup):
    provider, headers, factory = setup
    _arm(client, headers)
    _cycle(factory, provider)
    trade = _one_trade(factory)
    settle = {
        "settlement_date": (exchange_today() - timedelta(days=1)).isoformat(),
        "final_index_value": "25102.00",
        "source_reference": "operator-reviewed test source",
        "confirmed_final": True,
    }
    url = f"/paper/futures/trades/{trade.id}/expiry-settlement"
    assert client.post(url, headers=headers, json=settle).status_code == 409  # Not expired yet.

    _set_trade_expiry(factory, exchange_today() - timedelta(days=1))
    assert _cycle(factory, provider) == "DEGRADED"
    assert _one_trade(factory).status == "OPEN"

    assert client.post(
        url, headers=headers, json={**settle, "confirmed_final": False}
    ).status_code == 422
    wrong_day = {**settle, "settlement_date": exchange_today().isoformat()}
    assert client.post(url, headers=headers, json=wrong_day).status_code == 422

    settled = client.post(url, headers=headers, json=settle)
    assert settled.status_code == 200, settled.text
    assert settled.json()["exit_reason"] == "EXPIRY_SETTLEMENT"
    assert Decimal(settled.json()["realized_pnl"]) == Decimal("7500.00")
    assert client.post(url, headers=headers, json=settle).json()["id"] == str(trade.id)
    conflicting = {**settle, "final_index_value": "25000.00"}
    assert client.post(url, headers=headers, json=conflicting).status_code == 409
    with factory() as session:
        assert len(list(session.scalars(select(PaperFuturesOrder).where(
            PaperFuturesOrder.action == "CLOSE"
        )))) == 1
    assert _ledger(client, headers) == "MATCHED"
