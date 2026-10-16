from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from packages.database.models import PaperAccount, PaperTrade
from packages.market_data.contracts import ExchangeSession, OptionChain, OptionContractQuote, Quote
from packages.market_data.upstox import UpstoxMarketDataProvider, UpstoxOptionContract


class FakeUpstoxProvider(UpstoxMarketDataProvider):
    def __init__(self) -> None:
        super().__init__("test-token")
        self.price_adjustment = Decimal(0)
        self.timestamp_basis = "exchange"
        self.crossed_market = False
        self.expiry = date.today() + timedelta(days=30)
        self.session_open = datetime.now(timezone.utc) - timedelta(hours=1)
        self.session_close = self.session_open + timedelta(hours=2)
        self.contracts = [
            UpstoxOptionContract(
                instrument_key=f"NSE_FO|{strike}{option_type}",
                trading_symbol=f"NIFTY-{strike}-{option_type}",
                expiry=self.expiry,
                strike=Decimal(strike),
                option_type=option_type,
                lot_size=25,
                tick_size=Decimal("0.05"),
            )
            for strike in (24900, 24950, 25000, 25050, 25100)
            for option_type in ("CE", "PE")
        ]

    async def get_nfo_session(self, trading_day):
        return ExchangeSession(
            exchange="NFO", start=self.session_open, end=self.session_close
        )

    async def get_option_contracts(self, underlying="NIFTY", expiry=None):
        return [
            contract for contract in self.contracts if expiry is None or contract.expiry == expiry
        ]

    async def get_option_chain(self, underlying, expiry):
        now = datetime.now(timezone.utc)
        quotes = []
        for option in self.contracts:
            distance = abs(option.strike - Decimal(25000)) / Decimal(50)
            base = max(Decimal(10), Decimal(100) - distance * Decimal(10))
            base += self.price_adjustment
            bid = base
            ask = base - Decimal(1) if self.crossed_market else base + Decimal(1)
            quotes.append(
                OptionContractQuote(
                    instrument_token=option.instrument_key,
                    trading_symbol=option.trading_symbol,
                    expiry=expiry,
                    strike=option.strike,
                    option_type=option.option_type,
                    quote=Quote(
                        instrument_token=option.instrument_key,
                        timestamp=now,
                        timestamp_basis="exchange",
                        book_as_of=now,
                        book_timestamp_basis="exchange",
                        last_price=base + Decimal(1),
                        bid=bid,
                        ask=ask,
                        volume=1000,
                    ),
                    open_interest=10000,
                    previous_open_interest=9000,
                    open_interest_change=1000,
                    change=Decimal(1),
                    bid_quantity=1000,
                    ask_quantity=1000,
                    implied_volatility=Decimal(20),
                    greeks={"delta": Decimal("0.5")},
                )
            )
        return OptionChain(
            underlying=underlying,
            underlying_spot=Decimal(25010),
            expiry=expiry,
            as_of=now,
            timestamp_basis=self.timestamp_basis,
            contracts=quotes,
        )


def _token(client) -> dict[str, str]:
    result = client.post(
        "/auth/register",
        json={"email": "paper@example.com", "password": "a-strong-test-password"},
    )
    return {"Authorization": f"Bearer {result.json()['access_token']}"}


def test_paper_workflow_is_authenticated_and_persists_simulated_fills(client, monkeypatch):
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    assert client.get("/paper/strategies").status_code == 401
    headers = _token(client)

    strategies = client.get("/paper/strategies", headers=headers)
    assert strategies.status_code == 200
    assert {item["key"] for item in strategies.json()} == {
        "long_call",
        "long_put",
        "bull_call_spread",
        "bear_put_spread",
        "bull_put_spread",
        "bear_call_spread",
        "iron_condor",
        "long_straddle",
        "long_strangle",
    }
    assert all(item["closed_trade_count"] == 0 for item in strategies.json())
    assert all(item["observed_win_rate_pct"] is None for item in strategies.json())
    account = client.get("/paper/account", headers=headers)
    assert account.status_code == 200
    assert Decimal(account.json()["initial_cash"]) == Decimal("1000000")
    assert account.json()["live_trading_enabled"] is False
    empty_ledger = client.get("/paper/account/ledger-reconciliation", headers=headers)
    assert empty_ledger.status_code == 200
    assert empty_ledger.headers["cache-control"] == "no-store"
    assert empty_ledger.json()["status"] == "MATCHED"
    assert empty_ledger.json()["order_count"] == 0

    enabled = client.put("/paper/account/kill-switch", json={"enabled": True}, headers=headers)
    assert enabled.status_code == 200
    payload = {
        "strategy_key": "long_call",
        "expiry": provider.expiry.isoformat(),
        "reference_strike": "25000",
        "lots": 1,
    }
    paused = client.post(
        "/paper/trades", json=payload, headers={**headers, "Idempotency-Key": str(uuid4())}
    )
    assert paused.status_code == 423
    client.put("/paper/account/kill-switch", json={"enabled": False}, headers=headers)

    opened = client.post(
        "/paper/trades", json=payload, headers={**headers, "Idempotency-Key": str(uuid4())}
    )
    assert opened.status_code == 201, opened.text
    trade = opened.json()
    assert trade["status"] == "OPEN"
    assert trade["max_loss"] == "2525.00"
    assert Decimal(trade["legs"][0]["entry_price"]) == Decimal("101")
    assert trade["execution_warnings"] == []
    open_ledger = client.get("/paper/account/ledger-reconciliation", headers=headers)
    assert open_ledger.json()["status"] == "MATCHED"
    assert open_ledger.json()["order_count"] == 1
    assert open_ledger.json()["open_trade_count"] == 1

    provider.price_adjustment = Decimal(10)
    current = client.get("/paper/trades", headers=headers)
    assert current.status_code == 200
    assert current.json()["trades"][0]["unrealized_pnl"] == "225.00"

    closed = client.post(f"/paper/trades/{trade['id']}/close", headers=headers)
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "CLOSED"
    assert closed.json()["realized_pnl"] == "225.00"
    refreshed_strategies = client.get("/paper/strategies", headers=headers).json()
    long_call_stats = next(item for item in refreshed_strategies if item["key"] == "long_call")
    assert long_call_stats["closed_trade_count"] == 1
    assert long_call_stats["winning_trade_count"] == 1
    assert long_call_stats["observed_win_rate_pct"] == "100.00"
    assert long_call_stats["qualified_automated_trade_count"] == 0
    assert long_call_stats["estimated_win_probability_pct"] is None
    orders = client.get("/paper/orders", headers=headers)
    assert [order["action"] for order in orders.json()] == ["CLOSE", "OPEN"]
    assert all(order["fill_snapshot"]["paper_only"] for order in orders.json())
    account_after_close = client.get("/paper/account", headers=headers).json()
    assert account_after_close["cash_balance"] == "1000225.00"
    assert all(order["fill_snapshot"]["modeled_costs"]["model_id"] ==
               "option-premium-turnover-illustrative-v1" for order in orders.json())
    costs = client.get("/paper/trades", headers=headers).json()["trades"][0]["modeled_costs"]
    assert costs["coverage"] == "COMPLETE"
    assert costs["recorded_modeled_charges"] == "5.28"
    assert costs["pnl_after_modeled_charges"] == "219.72"
    assert costs["complete_broker_costs"] is False

    closed_ledger = client.get("/paper/account/ledger-reconciliation", headers=headers)
    assert closed_ledger.json()["status"] == "MATCHED"
    assert closed_ledger.json()["expected_cash_balance"] == "1000225.00"
    assert closed_ledger.json()["order_count"] == 2
    assert closed_ledger.json()["closed_trade_count"] == 1
    event_types = {
        item["event_type"]
        for item in client.get("/notifications", headers=headers).json()["notifications"]
    }
    assert {
        "paper.kill_switch.changed",
        "paper.trade.opened",
        "paper.trade.closed",
    }.issubset(event_types)


def test_paper_ledger_reports_mismatches_without_changing_saved_cash(client, monkeypatch):
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    assert client.get("/paper/account/ledger-reconciliation").status_code == 401
    headers = _token(client)
    opened = client.post(
        "/paper/trades", json={
            "strategy_key": "long_call", "expiry": provider.expiry.isoformat(),
            "reference_strike": "25000", "lots": 1,
        }, headers={**headers, "Idempotency-Key": str(uuid4())},
    )
    assert opened.status_code == 201, opened.text
    provider.price_adjustment = Decimal(10)
    assert client.post(
        f"/paper/trades/{opened.json()['id']}/close", headers=headers
    ).status_code == 200
    factory = client.app.state.test_session_factory
    with factory() as session:
        account = session.scalar(select(PaperAccount))
        trade = session.scalar(select(PaperTrade))
        account.cash_balance += Decimal("1.00")
        trade.realized_pnl += Decimal("2.00")
        session.commit()
    report = client.get("/paper/account/ledger-reconciliation", headers=headers)
    assert report.json()["status"] == "MISMATCH"
    assert report.json()["cash_difference"] == "1.00"
    assert report.json()["issue_count"] == 2
    assert client.get("/paper/account", headers=headers).json()["cash_balance"] == (
        "1000226.00"
    )
    blocked = client.post(
        "/paper/trades", json={
            "strategy_key": "long_call", "expiry": provider.expiry.isoformat(),
            "reference_strike": "25000", "lots": 1,
        }, headers={**headers, "Idempotency-Key": str(uuid4())},
    )
    assert blocked.status_code == 409
    assert "ledger mismatch" in blocked.json()["detail"]
    assert client.get("/paper/account", headers=headers).json()["cash_balance"] == (
        "1000226.00"
    )
    assert client.put(
        "/paper/account/kill-switch", json={"enabled": True}, headers=headers,
    ).status_code == 200
    other = client.post("/auth/register", json={
        "email": "other-ledger@example.com", "password": "another-strong-password",
    })
    assert other.status_code == 201
    own = client.get("/paper/account/ledger-reconciliation", headers={
        "Authorization": f"Bearer {other.json()['access_token']}"
    })
    assert own.json()["status"] == "MATCHED"
    assert own.json()["order_count"] == 0


def test_paper_events_create_user_scoped_notifications(client, monkeypatch):
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = _token(client)
    opened = client.post(
        "/paper/trades",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={
            "strategy_key": "long_call",
            "expiry": provider.expiry.isoformat(),
            "reference_strike": "25000",
            "lots": 1,
        },
    )
    assert opened.status_code == 201, opened.text

    notifications = client.get("/notifications", headers=headers)
    assert notifications.status_code == 200, notifications.text
    assert notifications.json()["unread_count"] == 1
    notice = notifications.json()["notifications"][0]
    assert notice["event_type"] == "paper.trade.opened"
    assert notice["details"]["trade_id"] == opened.json()["id"]

    marked = client.post(f"/notifications/{notice['id']}/read", headers=headers)
    assert marked.status_code == 200, marked.text
    assert marked.json()["read_at"] is not None
    assert client.get("/notifications", headers=headers).json()["unread_count"] == 0

    second_headers = {
        "Authorization": "Bearer "
        + client.post(
            "/auth/register",
            json={
                "email": "notification-other@example.com",
                "password": "a-strong-test-password",
            },
        ).json()["access_token"]
    }
    assert client.get("/notifications", headers=second_headers).json()["notifications"] == []
    other_user_read = client.post(f"/notifications/{notice['id']}/read", headers=second_headers)
    assert other_user_read.status_code == 404


def test_paper_risk_cap_rejects_excessive_position_size(client, monkeypatch):
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = _token(client)
    response = client.post(
        "/paper/trades",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={
            "strategy_key": "long_call",
            "expiry": provider.expiry.isoformat(),
            "reference_strike": "25000",
            "lots": 10,
        },
    )
    assert response.status_code == 409
    assert "per-trade risk cap" in response.json()["detail"]
    assert client.get("/paper/trades", headers=headers).json()["trades"] == []


def test_defined_risk_spread_records_both_option_legs(client, monkeypatch):
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = _token(client)
    response = client.post(
        "/paper/trades",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={
            "strategy_key": "bull_call_spread",
            "expiry": provider.expiry.isoformat(),
            "reference_strike": "25000",
            "lots": 1,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["max_loss"] == "275.00"
    assert [(leg["side"], Decimal(leg["strike"])) for leg in response.json()["legs"]] == [
        ("BUY", Decimal("25000")),
        ("SELL", Decimal("25050")),
    ]


def test_warn_quality_is_disclosed_and_fail_quality_blocks_new_trades(client, monkeypatch):
    monkeypatch.setenv("PAPER_ALLOW_UNVERIFIED_QUOTES", "true")
    from apps.api.settings import get_settings

    get_settings.cache_clear()
    provider = FakeUpstoxProvider()
    provider.timestamp_basis = "api_response_received"
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = _token(client)
    payload = {
        "strategy_key": "long_call",
        "expiry": provider.expiry.isoformat(),
        "reference_strike": "25000",
        "lots": 1,
    }
    warned = client.post(
        "/paper/trades", json=payload, headers={**headers, "Idempotency-Key": str(uuid4())}
    )
    assert warned.status_code == 201, warned.text
    assert warned.json()["execution_warnings"]

    other_headers = {
        "Authorization": "Bearer "
        + client.post(
            "/auth/register",
            json={"email": "second-paper@example.com", "password": "a-strong-test-password"},
        ).json()["access_token"]
    }
    provider.crossed_market = True
    blocked = client.post(
        "/paper/trades", json=payload, headers={**other_headers, "Idempotency-Key": str(uuid4())}
    )
    assert blocked.status_code == 409
    assert "quality is FAIL" in blocked.json()["detail"]["message"]
    assert client.get("/paper/trades", headers=other_headers).json()["trades"] == []


def test_credit_spreads_are_risk_sized_from_expiry_payoff(client, monkeypatch):
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = _token(client)
    for strategy_key in ("bull_put_spread", "bear_call_spread"):
        response = client.post(
            "/paper/trades",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={
                "strategy_key": strategy_key,
                "expiry": provider.expiry.isoformat(),
                "reference_strike": "25000",
                "lots": 1,
            },
        )
        assert response.status_code == 201, response.text
        assert Decimal(response.json()["entry_cash_flow"]) == Decimal("225.00")
        assert Decimal(response.json()["max_loss"]) == Decimal("1025.00")


def test_iron_condor_records_all_legs_and_bounded_maximum_loss(client, monkeypatch):
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = _token(client)
    response = client.post(
        "/paper/trades",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={
            "strategy_key": "iron_condor",
            "expiry": provider.expiry.isoformat(),
            "reference_strike": "25000",
            "lots": 1,
        },
    )
    assert response.status_code == 201, response.text
    assert len(response.json()["legs"]) == 4
    assert Decimal(response.json()["entry_cash_flow"]) == Decimal("450.00")
    assert Decimal(response.json()["max_loss"]) == Decimal("800.00")


def test_custom_bull_call_spread_uses_shared_quote_and_risk_controls(client, monkeypatch):
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = _token(client)
    response = client.post(
        "/paper/trades",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={
            "strategy_key": "custom",
            "expiry": provider.expiry.isoformat(),
            "lots": 1,
            "custom_legs": [
                {"option_type": "CE", "side": "BUY", "strike": "25000", "ratio": 1},
                {"option_type": "CE", "side": "SELL", "strike": "25050", "ratio": 1},
            ],
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["strategy_key"] == "custom"
    assert response.json()["reference_strike"] == "25000.00"
    assert response.json()["max_loss"] == "275.00"
    assert len(response.json()["legs"]) == 2
    assert [leg["quantity_units"] for leg in response.json()["legs"]] == [25, 25]


def test_custom_unbounded_short_call_is_rejected_before_paper_order(client, monkeypatch):
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = _token(client)
    response = client.post(
        "/paper/trades",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={
            "strategy_key": "custom",
            "expiry": provider.expiry.isoformat(),
            "lots": 1,
            "custom_legs": [{"option_type": "CE", "side": "SELL", "strike": "25000", "ratio": 1}],
        },
    )

    assert response.status_code == 409
    assert "unbounded maximum loss" in response.json()["detail"]
    assert client.get("/paper/trades", headers=headers).json()["trades"] == []


def test_no_code_entry_conditions_are_rechecked_and_persisted_from_server_snapshot(
    client, monkeypatch
):
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = _token(client)
    response = client.post(
        "/paper/trades",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={
            "strategy_key": "long_call",
            "expiry": provider.expiry.isoformat(),
            "reference_strike": "25000",
            "lots": 1,
            "entry_conditions": [
                {"metric": "put_call_ratio_oi", "operator": "GTE", "threshold": "0.9"}
            ],
        },
    )

    assert response.status_code == 201, response.text
    condition = response.json()["entry_rule_snapshot"]["results"][0]
    assert condition["actual"] == "1"
    assert condition["passed"] is True


def test_unsatisfied_no_code_condition_rejects_trade_without_order(client, monkeypatch):
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = _token(client)
    response = client.post(
        "/paper/trades",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={
            "strategy_key": "long_call",
            "expiry": provider.expiry.isoformat(),
            "reference_strike": "25000",
            "lots": 1,
            "entry_conditions": [
                {"metric": "put_call_ratio_oi", "operator": "GT", "threshold": "1.2"}
            ],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["conditions"][0]["passed"] is False
    assert client.get("/paper/trades", headers=headers).json()["trades"] == []


def test_paper_stop_loss_closes_position_from_fresh_quotes_and_logs_rule(client, monkeypatch):
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = _token(client)
    opened = client.post(
        "/paper/trades",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={
            "strategy_key": "long_call",
            "expiry": provider.expiry.isoformat(),
            "reference_strike": "25000",
            "lots": 1,
            "stop_loss_amount": "100",
        },
    )
    assert opened.status_code == 201, opened.text
    trade_id = opened.json()["id"]
    provider.price_adjustment = Decimal("-4")

    monitored = client.post("/paper/monitor", headers=headers)

    assert monitored.status_code == 200, monitored.text
    assert monitored.json()["closed_trade_ids"] == [trade_id]
    trade = client.get("/paper/trades", headers=headers).json()["trades"][0]
    assert trade["status"] == "CLOSED"
    assert trade["exit_reason"] == "STOP_LOSS"
    assert trade["realized_pnl"] == "-125.00"
    close_order = client.get("/paper/orders", headers=headers).json()[0]
    assert close_order["action"] == "CLOSE"
    assert close_order["fill_snapshot"]["exit_reason"] == "STOP_LOSS"
    notifications = client.get("/notifications", headers=headers).json()["notifications"]
    assert notifications[0]["event_type"] == "paper.trade.exit_rule_triggered"


def test_paper_profit_target_closes_position(client, monkeypatch):
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = _token(client)
    opened = client.post(
        "/paper/trades",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={
            "strategy_key": "long_call",
            "expiry": provider.expiry.isoformat(),
            "reference_strike": "25000",
            "lots": 1,
            "target_profit_amount": "200",
        },
    )
    assert opened.status_code == 201, opened.text
    provider.price_adjustment = Decimal("10")

    monitored = client.post("/paper/monitor", headers=headers)

    assert monitored.status_code == 200, monitored.text
    assert monitored.json()["closed_trade_ids"] == [opened.json()["id"]]
    trade = client.get("/paper/trades", headers=headers).json()["trades"][0]
    assert trade["exit_reason"] == "TARGET"
    assert trade["realized_pnl"] == "225.00"


def test_paper_trailing_stop_persists_peak_and_closes_on_drawdown(client, monkeypatch):
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = _token(client)
    opened = client.post(
        "/paper/trades",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={
            "strategy_key": "long_call",
            "expiry": provider.expiry.isoformat(),
            "reference_strike": "25000",
            "lots": 1,
            "trailing_stop_amount": "200",
        },
    )
    assert opened.status_code == 201, opened.text
    trade_id = opened.json()["id"]
    provider.price_adjustment = Decimal("20")
    peak = client.post("/paper/monitor", headers=headers)
    assert peak.status_code == 200, peak.text
    assert peak.json()["unrealized_pnl"][trade_id] == "475.00"
    current = client.get("/paper/trades", headers=headers).json()["trades"][0]
    assert current["peak_unrealized_pnl"] == "475.00"

    provider.price_adjustment = Decimal("10")
    drawdown = client.post("/paper/monitor", headers=headers)

    assert drawdown.status_code == 200, drawdown.text
    assert drawdown.json()["closed_trade_ids"] == [trade_id]
    trade = client.get("/paper/trades", headers=headers).json()["trades"][0]
    assert trade["exit_reason"] == "TRAILING_STOP"
    assert trade["realized_pnl"] == "225.00"


def test_daily_loss_budget_blocks_new_paper_risk(client, monkeypatch):
    monkeypatch.setenv("PAPER_DAILY_LOSS_LIMIT_PCT", "1")
    from apps.api.settings import get_settings

    get_settings.cache_clear()
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = _token(client)
    payload = {
        "strategy_key": "long_call",
        "expiry": provider.expiry.isoformat(),
        "reference_strike": "25000",
        "lots": 1,
    }
    for _ in range(3):
        response = client.post(
            "/paper/trades", json=payload,
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 201, response.text
    account = client.get("/paper/account", headers=headers).json()
    assert account["daily_loss_limit"] == "10000.00"
    assert account["daily_risk_remaining"] == "2425.00"
    blocked = client.post(
        "/paper/trades", json=payload,
        headers={**headers, "Idempotency-Key": str(uuid4())},
    )
    assert blocked.status_code == 409
    assert "Daily paper loss budget" in blocked.json()["detail"]
    assert len(client.get("/paper/trades", headers=headers).json()["trades"]) == 3


def test_daily_realized_pnl_uses_india_trading_day(client, monkeypatch):
    from datetime import time
    from uuid import UUID
    from zoneinfo import ZoneInfo

    from sqlalchemy import select

    from packages.database.models import PaperTrade
    from packages.paper_trading.session import exchange_today

    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = _token(client)
    opened = client.post(
        "/paper/trades",
        json={
            "strategy_key": "long_call", "expiry": provider.expiry.isoformat(),
            "reference_strike": "25000", "lots": 1,
        },
        headers={**headers, "Idempotency-Key": str(uuid4())},
    )
    assert opened.status_code == 201, opened.text
    provider.price_adjustment = Decimal("-10")
    closed = client.post(f"/paper/trades/{opened.json()['id']}/close", headers=headers)
    assert closed.status_code == 200, closed.text

    local_zone = ZoneInfo("Asia/Kolkata")
    today = exchange_today()
    with client.app.state.test_session_factory() as session:
        trade = session.scalar(select(PaperTrade).where(PaperTrade.id == UUID(opened.json()["id"])))
        assert trade is not None
        trade.realized_pnl = Decimal("-500")
        trade.closed_at = datetime.combine(
            today, time(0, 1), tzinfo=local_zone
        ).astimezone(timezone.utc)
        session.commit()
    assert client.get(
        "/paper/account", headers=headers
    ).json()["daily_realized_pnl"] == "-500.00"

    with client.app.state.test_session_factory() as session:
        trade = session.scalar(select(PaperTrade).where(PaperTrade.id == UUID(opened.json()["id"])))
        assert trade is not None
        trade.closed_at = datetime.combine(
            today - timedelta(days=1), time(23, 59), tzinfo=local_zone
        ).astimezone(timezone.utc)
        session.commit()
    assert client.get(
        "/paper/account", headers=headers
    ).json()["daily_realized_pnl"] == "0.00"
