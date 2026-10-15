import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from test_paper_trading import FakeUpstoxProvider, _token

from apps.api.settings import get_settings
from apps.paper_worker.main import run_cycle
from packages.database.models import AuditEvent, PaperDeployment, PaperOrder, PaperTrade
from packages.market_data.contracts import ExchangeSession, MarketDataProviderError
from packages.paper_trading.session import exchange_today


def armed_setup(client, monkeypatch):
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = {**_token(client), "Idempotency-Key": f"arm_{uuid4().hex}"}
    payload = {
        "strategy_key": "long_call",
        "expiry": provider.expiry.isoformat(),
        "reference_strike": "25000",
        "lots": 1,
        "entry_conditions": [{
            "metric": "underlying_spot", "operator": "GT", "threshold": "25000",
        }],
        "stop_loss_amount": "100",
        "target_profit_amount": "200",
        "trailing_stop_amount": "50",
    }
    return provider, headers, payload, client.app.state.test_session_factory


def test_educational_automatic_entry_and_exit_keep_unverified_provenance(client, monkeypatch):
    provider, headers, payload, factory = armed_setup(client, monkeypatch)
    monkeypatch.setattr(get_settings(), "paper_allow_unverified_quotes", True)
    original_chain = provider.get_option_chain

    async def educational_chain(*args):
        chain = await original_chain(*args)
        for contract in chain.contracts:
            contract.quote.timestamp_basis = "api_response_received"
            contract.quote.book_timestamp_basis = "unknown"
            contract.quote.book_as_of = None
        chain.timestamp_basis = "api_response_received"
        return chain

    provider.get_option_chain = educational_chain
    payload.update(stop_loss_amount="1", target_profit_amount="1", trailing_stop_amount="1")
    armed = client.post("/paper/deployments", headers=headers, json=payload)
    assert armed.status_code == 201, armed.text
    for _ in range(3):
        asyncio.run(run_cycle(factory, provider, get_settings()))
    with factory() as session:
        deployment = session.get(PaperDeployment, UUID(armed.json()["id"]))
        assert deployment.status == "FILLED"
        trades = list(session.scalars(select(PaperTrade)))
        assert len(trades) == 1 and trades[0].status == "CLOSED"
        orders = list(session.scalars(select(PaperOrder)))
        assert len(orders) == 2
        for order in orders:
            for fill in order.fill_snapshot["fills"]:
                assert fill["timestamp_basis"] == "api_response_received"
                assert fill["book_timestamp_basis"] == "unknown"
    ledger = client.get("/paper/account/ledger-reconciliation", headers=headers)
    assert ledger.status_code == 200
    assert ledger.json()["status"] == "MATCHED"


def test_non_upstox_options_provider_can_arm_enter_and_cannot_cross_price(client, monkeypatch):
    backing, headers, payload, factory = armed_setup(client, monkeypatch)

    class AlternateOptionProvider:
        provider_id = "alternate-options"

        def __getattr__(self, name):
            return getattr(backing, name)

    alternate = AlternateOptionProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: alternate)
    armed = client.post("/paper/deployments", headers=headers, json=payload)
    assert armed.status_code == 201, armed.text
    assert client.get("/market-data/option-contracts", headers=headers).status_code == 200
    capabilities = client.get("/market-data/capabilities", headers=headers)
    assert capabilities.json()["provider"] == alternate.provider_id
    assert capabilities.json()["provider_configured"] is True
    assert asyncio.run(run_cycle(factory, alternate, get_settings())) == "HEALTHY"
    saved = client.get("/paper/deployments", headers=headers).json()[0]
    assert saved["status"] == "FILLED"
    with factory() as session:
        trade = session.get(PaperTrade, UUID(saved["trade_id"]))
        assert trade.entry_rule_snapshot["market_data_provider"] == alternate.provider_id

    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: backing)
    shown = client.get("/paper/trades", headers=headers)
    assert shown.status_code == 200
    assert shown.json()["trades"][0]["unrealized_pnl"] is None
    assert "another market provider" in shown.json()["trades"][0]["valuation_warning"]
    refused = client.post(f"/paper/trades/{saved['trade_id']}/close", headers=headers)
    assert refused.status_code == 409
    assert asyncio.run(run_cycle(factory, backing, get_settings())) == "DEGRADED"
    with factory() as session:
        assert session.get(PaperTrade, UUID(saved["trade_id"])).status == "OPEN"
        skipped = list(session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "paper.trade.exit_checked"
        )))
        assert skipped[-1].details["snapshot"]["reason_code"] == "PROVIDER_MISMATCH"


def test_armed_option_waits_when_provider_changes_before_entry(client, monkeypatch):
    backing, headers, payload, factory = armed_setup(client, monkeypatch)

    class AlternateOptionProvider:
        provider_id = "alternate-options"

        def __getattr__(self, name):
            return getattr(backing, name)

    alternate = AlternateOptionProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: alternate)
    armed = client.post("/paper/deployments", headers=headers, json=payload)
    assert armed.status_code == 201
    assert asyncio.run(run_cycle(factory, backing, get_settings())) == "DEGRADED"
    assert asyncio.run(run_cycle(factory, backing, get_settings())) == "DEGRADED"
    waiting = client.get("/paper/deployments", headers=headers).json()[0]
    assert waiting["detail"] == "Armed option strategy provider differs from the active provider"
    with factory() as session:
        row = session.get(PaperDeployment, UUID(armed.json()["id"]))
        assert row.status == "WAITING_FOR_ENTRY"
        assert row.request_data["market_data_provider"] == alternate.provider_id
        assert list(session.scalars(select(PaperTrade))) == []
        blocks = list(session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "paper.deployment.entry_blocked"
        )))
        assert len(blocks) == 1
    assert asyncio.run(run_cycle(factory, alternate, get_settings())) == "HEALTHY"
    assert client.get("/paper/deployments", headers=headers).json()[0]["status"] == "FILLED"


def test_arm_waits_for_new_snapshot_then_worker_opens_once(client, monkeypatch, tmp_path):
    provider, headers, payload, factory = armed_setup(client, monkeypatch)
    original_chain = provider.get_option_chain
    old_as_of = datetime.now(timezone.utc) - timedelta(minutes=1)
    later = False

    async def chain(*args):
        result = await original_chain(*args)
        return result if later else result.model_copy(update={"as_of": old_as_of})

    provider.get_option_chain = chain
    armed = client.post("/paper/deployments", headers=headers, json=payload)
    assert armed.status_code == 201, armed.text
    deployment = armed.json()
    walkthrough = [{"step": "Armed: waiting for a new snapshot", "status": "WAITING_FOR_ENTRY",
                    "account": client.get("/paper/account", headers=headers).json()}]
    assert deployment["status"] == "WAITING_FOR_ENTRY"
    assert deployment["trade_id"] is None
    assert client.get("/paper/trades", headers=headers).json()["trades"] == []
    assert client.post("/paper/deployments", headers=headers, json=payload).json() == deployment
    assert client.post(
        "/paper/deployments", headers=headers, json={**payload, "lots": 2}
    ).status_code == 409

    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    assert client.get("/paper/deployments", headers=headers).json()[0]["status"] == (
        "WAITING_FOR_ENTRY"
    )
    later = True
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    filled = client.get("/paper/deployments", headers=headers).json()[0]
    assert filled["status"] == "FILLED"
    walkthrough.append({"step": "Entry: new snapshot passes rules", "status": "OPEN",
                        "trades": client.get("/paper/trades", headers=headers).json(),
                        "account": client.get("/paper/account", headers=headers).json()})
    with factory() as session:
        trade = session.get(PaperTrade, UUID(filled["trade_id"]))
        assert trade.status == "OPEN"
        assert trade.stop_loss_amount == 100
        assert trade.trailing_stop_amount == 50
        assert len(list(session.scalars(select(PaperOrder)))) == 1
        assert session.get(PaperDeployment, UUID(deployment["id"])).trade_id == trade.id
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    with factory() as session:
        assert len(list(session.scalars(select(PaperOrder)))) == 1
        checks = list(session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "paper.deployment.entry_checked"
        )))
        assert len(checks) == 2
        assert {check.details["snapshot"]["decision"] for check in checks} == {"RULES_PASS"}
        assert all(check.details["snapshot"]["provider"] == "upstox" for check in checks)
        assert all(
            check.details["snapshot"]["leg_quotes"]["status"] == "RECORDED"
            and len(check.details["snapshot"]["leg_quotes"]["legs"]) == 1
            for check in checks
        )
        assert all("token" not in str(check.details).lower() for check in checks)
    provider.price_adjustment = -10
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    with factory() as session:
        closed = session.get(PaperTrade, UUID(filled["trade_id"]))
        assert closed.status == "CLOSED"
        assert closed.exit_reason == "STOP_LOSS"
        assert closed.realized_pnl is not None
        assert len(list(session.scalars(select(PaperOrder)))) == 2
        assert closed.entry_rule_snapshot["entry_market_context"]["version"] == (
            "nifty-option-observed-v1"
        )
        context = closed.entry_rule_snapshot["entry_market_context"]
        assert Decimal(context["underlying_spot"]) > 0
        assert context["observed_at"] == closed.entry_rule_snapshot["market_as_of"]
        assert context["expiry"] == closed.expiry.isoformat()
    result = next(item for item in client.get("/paper/strategies", headers=headers).json()
                  if item["key"] == "long_call")
    entry_report = client.get(
        f"/paper/trades/{filled['trade_id']}/entry-reconciliation", headers=headers
    )
    assert entry_report.status_code == 200
    assert entry_report.headers["cache-control"] == "no-store"
    assert entry_report.json()["status"] == "MATCHED", entry_report.json()
    assert entry_report.json()["evaluated_count"] == 2
    walkthrough.append({"step": "Exit: simulated price decline triggers stop loss",
                        "status": "CLOSED",
                        "trades": client.get("/paper/trades", headers=headers).json(),
                        "account": client.get("/paper/account", headers=headers).json()})
    (tmp_path / "option-cycle.json").write_text(json.dumps({
        "simulation": True, "scope": "Disposable runtime; synthetic market observations",
        "steps": walkthrough, "entry_reconciliation": entry_report.json(),
        "orders": client.get("/paper/orders", headers=headers).json(),
    }, indent=2), encoding="utf-8")
    assert client.get(
        f"/paper/trades/{filled['trade_id']}/entry-reconciliation"
    ).status_code == 401
    other = client.post("/auth/register", json={
        "email": "other-entry-replay@example.com",
        "password": "another-strong-password",
    })
    assert other.status_code == 201
    assert client.get(
        f"/paper/trades/{filled['trade_id']}/entry-reconciliation",
        headers={"Authorization": f"Bearer {other.json()['access_token']}"},
    ).status_code == 404
    assert result["closed_trade_count"] == 1
    assert result["qualified_automated_trade_count"] == 1
    assert result["qualified_policy_variant_count"] == 1
    assert result["estimated_win_probability_pct"] is None
    neutral = next(item for item in result["market_condition_evidence"]
                   if item["condition"] == "NEUTRAL")
    assert neutral["trade_count"] == 1
    assert neutral["average_r"] is None

    with factory() as session:
        close_order = session.scalar(select(PaperOrder).where(PaperOrder.action == "CLOSE"))
        original_close_snapshot = close_order.fill_snapshot
        assert all(
            fill["book_timestamp_basis"] == "exchange" and fill["book_as_of"]
            for fill in original_close_snapshot["fills"]
        )
        close_order.fill_snapshot = {
            **original_close_snapshot,
            "fills": [
                {**fill, "book_timestamp_basis": "unknown"}
                for fill in original_close_snapshot["fills"]
            ],
        }
        session.commit()
    result = next(item for item in client.get("/paper/strategies", headers=headers).json()
                  if item["key"] == "long_call")
    assert result["closed_trade_count"] == 1
    assert result["qualified_automated_trade_count"] == 0
    with factory() as session:
        close_order = session.scalar(select(PaperOrder).where(PaperOrder.action == "CLOSE"))
        close_order.fill_snapshot = original_close_snapshot
        session.commit()

    with factory() as session:
        entry_event = session.scalar(select(AuditEvent).where(
            AuditEvent.event_type == "paper.deployment.entry_checked"
        ).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()))
        original_entry_details = entry_event.details
        changed = {**original_entry_details["snapshot"]}
        changed_metrics = {**changed["rule_metrics"], "underlying_spot": "1"}
        changed["rule_metrics"] = changed_metrics
        entry_event.details = {
            **original_entry_details,
            "snapshot": changed,
            "snapshot_digest": hashlib.sha256(json.dumps(
                changed, sort_keys=True, separators=(",", ":")
            ).encode()).hexdigest(),
        }
        session.commit()
    entry_report = client.get(
        f"/paper/trades/{filled['trade_id']}/entry-reconciliation", headers=headers
    )
    assert entry_report.json()["status"] == "MISMATCH"
    result = next(item for item in client.get("/paper/strategies", headers=headers).json()
                  if item["key"] == "long_call")
    assert result["closed_trade_count"] == 1
    assert result["qualified_automated_trade_count"] == 0
    with factory() as session:
        entry_event = session.scalar(select(AuditEvent).where(
            AuditEvent.event_type == "paper.deployment.entry_checked"
        ).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()))
        entry_event.details = original_entry_details
        session.commit()

    report = client.get(
        f"/paper/trades/{filled['trade_id']}/exit-reconciliation", headers=headers
    )
    assert report.status_code == 200
    assert report.headers["cache-control"] == "no-store"
    assert report.json()["status"] == "MATCHED"
    assert report.json()["evaluated_count"] == 2

    with factory() as session:
        observation = session.scalar(select(AuditEvent).where(
            AuditEvent.event_type == "paper.trade.exit_checked"
        ).order_by(AuditEvent.created_at.desc()))
        original_details = observation.details
        changed_snapshot = {**original_details["snapshot"], "decision": "HOLD"}
        changed_digest = hashlib.sha256(json.dumps(
            changed_snapshot, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        observation.details = {
            **original_details, "snapshot": changed_snapshot, "snapshot_digest": changed_digest,
        }
        session.commit()
    report = client.get(
        f"/paper/trades/{filled['trade_id']}/exit-reconciliation", headers=headers
    )
    assert report.json()["status"] == "MISMATCH"
    result = next(item for item in client.get("/paper/strategies", headers=headers).json()
                  if item["key"] == "long_call")
    assert result["closed_trade_count"] == 1
    assert result["qualified_automated_trade_count"] == 0

    with factory() as session:
        observation = session.scalar(select(AuditEvent).where(
            AuditEvent.event_type == "paper.trade.exit_checked"
        ).order_by(AuditEvent.created_at.desc()))
        observation.details = original_details
        session.commit()

    with factory() as session:
        observation = session.scalar(select(AuditEvent).where(
            AuditEvent.event_type == "paper.trade.exit_checked"
        ).order_by(AuditEvent.created_at.desc()))
        recorded = original_details["snapshot"]
        quote = recorded["leg_quotes"][0]
        altered_price = str(Decimal(quote["selected_side_quote"]) + 1)
        changed_quote = {**quote, "bid": altered_price, "selected_side_quote": altered_price}
        changed_snapshot = {**recorded, "leg_quotes": [changed_quote]}
        observation.details = {
            **original_details, "snapshot": changed_snapshot,
            "snapshot_digest": hashlib.sha256(json.dumps(
                changed_snapshot, sort_keys=True, separators=(",", ":")
            ).encode()).hexdigest(),
        }
        session.commit()
    report = client.get(
        f"/paper/trades/{filled['trade_id']}/exit-reconciliation", headers=headers
    )
    assert report.json()["status"] == "MISMATCH"
    assert "do not reproduce" in report.json()["detail"]
    with factory() as session:
        observation = session.scalar(select(AuditEvent).where(
            AuditEvent.event_type == "paper.trade.exit_checked"
        ).order_by(AuditEvent.created_at.desc()))
        observation.details = original_details
        session.commit()

    with factory() as session:
        close_order = session.scalar(select(PaperOrder).where(PaperOrder.action == "CLOSE"))
        close_order.fill_snapshot = {**close_order.fill_snapshot, "quality_status": "WARN"}
        session.commit()
    result = next(item for item in client.get("/paper/strategies", headers=headers).json()
                  if item["key"] == "long_call")
    assert result["closed_trade_count"] == 1
    assert result["qualified_automated_trade_count"] == 0
    assert result["qualified_policy_variant_count"] == 0
    assert all(item["trade_count"] == 0 for item in result["market_condition_evidence"])


def test_armed_exit_cutoff_is_pinned_before_worker_entry(client, monkeypatch):
    provider, headers, payload, factory = armed_setup(client, monkeypatch)
    original_chain = provider.get_option_chain
    baseline = datetime.now(timezone.utc) - timedelta(minutes=1)
    later = False

    async def chain(*args):
        result = await original_chain(*args)
        return result if later else result.model_copy(update={"as_of": baseline})

    provider.get_option_chain = chain
    settings = get_settings()
    monkeypatch.setattr(settings, "paper_expiry_square_off_minutes_before_close", 20)
    armed = client.post("/paper/deployments", headers=headers, json=payload)
    assert armed.status_code == 201, armed.text
    with factory() as session:
        deployment = session.get(PaperDeployment, UUID(armed.json()["id"]))
        assert deployment.request_data["exit_policy"] == {
            "version": "nifty-option-exit-v1",
            "square_off_minutes_before_close": 20,
        }
    monkeypatch.setattr(settings, "paper_expiry_square_off_minutes_before_close", 30)
    later = True
    assert asyncio.run(run_cycle(factory, provider, settings)) == "HEALTHY"
    filled = client.get("/paper/deployments", headers=headers).json()[0]
    assert filled["status"] == "FILLED"
    with factory() as session:
        trade = session.get(PaperTrade, UUID(filled["trade_id"]))
        assert trade.entry_rule_snapshot["exit_policy"] == {
            "version": "nifty-option-exit-v1",
            "square_off_minutes_before_close": 20,
        }


def test_manual_close_of_automated_entry_is_not_automated_outcome(client, monkeypatch):
    provider, headers, payload, factory = armed_setup(client, monkeypatch)
    original_chain = provider.get_option_chain
    baseline = datetime.now(timezone.utc) - timedelta(minutes=1)
    later = False

    async def chain(*args):
        result = await original_chain(*args)
        return result if later else result.model_copy(update={"as_of": baseline})

    provider.get_option_chain = chain
    armed = client.post("/paper/deployments", headers=headers, json=payload)
    assert armed.status_code == 201, armed.text
    later = True
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    filled = client.get("/paper/deployments", headers=headers).json()[0]
    assert filled["status"] == "FILLED"

    closed = client.post(f"/paper/trades/{filled['trade_id']}/close", headers=headers)
    assert closed.status_code == 200, closed.text
    assert closed.json()["exit_reason"] == "MANUAL"
    with factory() as session:
        orders = list(session.scalars(select(PaperOrder).where(
            PaperOrder.trade_id == UUID(filled["trade_id"])
        )))
        assert len(orders) == 2
        assert all(order.fill_snapshot["quality_status"] == "PASS" for order in orders)

    result = next(item for item in client.get("/paper/strategies", headers=headers).json()
                  if item["key"] == "long_call")
    assert result["closed_trade_count"] == 1
    assert result["qualified_automated_trade_count"] == 0
    assert result["estimated_win_probability_pct"] is None
    assert all(item["trade_count"] == 0 for item in result["market_condition_evidence"])


def test_unmet_option_entry_snapshot_is_durable_and_deduplicated(client, monkeypatch):
    provider, headers, payload, factory = armed_setup(client, monkeypatch)
    payload["entry_conditions"][0]["threshold"] = "99999"
    armed = client.post("/paper/deployments", headers=headers, json=payload)
    assert armed.status_code == 201
    assert armed.json()["entry_conditions"] == payload["entry_conditions"]
    assert armed.json()["stop_loss_amount"] == payload["stop_loss_amount"]
    assert armed.json()["target_profit_amount"] == payload["target_profit_amount"]
    assert armed.json()["trailing_stop_amount"] == payload["trailing_stop_amount"]
    assert client.get("/paper/deployments", headers=headers).json()[0][
        "entry_conditions"
    ] == payload["entry_conditions"]
    saved_chain = asyncio.run(provider.get_option_chain("NIFTY", provider.expiry))

    async def unchanged_chain(*_args):
        return saved_chain

    provider.get_option_chain = unchanged_chain
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    waiting = client.get("/paper/deployments", headers=headers).json()[0]
    assert waiting["status"] == "WAITING_FOR_ENTRY"
    assert waiting["detail"] == "Latest market check: saved entry conditions are not satisfied"
    with factory() as session:
        checks = list(session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "paper.deployment.entry_checked"
        )))
        assert len(checks) == 1
        assert checks[0].correlation_id == armed.json()["id"]
        snapshot = checks[0].details["snapshot"]
        assert snapshot["decision"] == "RULES_NOT_MET"
        assert snapshot["market_as_of"] == saved_chain.as_of.isoformat()
        assert snapshot["rule_metrics"]["underlying_spot"]
        assert snapshot["entry_rule_snapshot"]["results"][0]["passed"] is False
        quote_leg = snapshot["leg_quotes"]["legs"][0]
        observed = next(leg for leg in saved_chain.contracts
                        if leg.instrument_token == quote_leg["instrument_key"])
        assert quote_leg["side"] == "BUY"
        assert quote_leg["selected_side_quote"] == str(observed.quote.ask)
        assert quote_leg["bid"] == str(observed.quote.bid)
        assert quote_leg["quote_timestamp"] == observed.quote.timestamp.isoformat()
        assert list(session.scalars(select(PaperTrade))) == []
    decisions = client.get(
        f"/paper/deployments/{armed.json()['id']}/decisions", headers=headers
    )
    assert decisions.status_code == 200
    assert decisions.headers["cache-control"] == "no-store"
    assert {item["event_type"] for item in decisions.json()} == {
        "paper.deployment.armed", "paper.deployment.entry_checked"
    }
    assert all(item["created_at"].endswith("Z") for item in decisions.json())


def test_failed_chain_quality_keeps_leg_quote_observations_without_fill(client, monkeypatch):
    provider, headers, payload, factory = armed_setup(client, monkeypatch)
    armed = client.post("/paper/deployments", headers=headers, json=payload)
    assert armed.status_code == 201, armed.text
    provider.crossed_market = True

    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    waiting = client.get("/paper/deployments", headers=headers).json()[0]
    assert waiting["status"] == "WAITING_FOR_ENTRY"
    assert waiting["detail"] == "Latest market check: option-chain quality failed"
    with factory() as session:
        checks = list(session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "paper.deployment.entry_checked"
        )))
        assert len(checks) == 1
        snapshot = checks[0].details["snapshot"]
        assert snapshot["decision"] == "QUALITY_FAIL"
        assert snapshot["leg_quotes"]["status"] == "RECORDED"
        leg = snapshot["leg_quotes"]["legs"][0]
        assert leg["bid"] is not None and leg["ask"] is not None
        assert leg["quote_timestamp"] is not None
        assert list(session.scalars(select(PaperTrade))) == []


def test_worker_rotates_past_five_waiting_option_strategies(client, monkeypatch):
    provider, headers, payload, factory = armed_setup(client, monkeypatch)
    payload["entry_conditions"][0]["threshold"] = "99999"
    armed = client.post("/paper/deployments", headers=headers, json=payload)
    assert armed.status_code == 201, armed.text
    ids = [armed.json()["id"]]
    with factory() as session:
        original = session.get(PaperDeployment, UUID(ids[0]))
        baseline = datetime.now(timezone.utc) - timedelta(minutes=1)
        original.created_at = baseline
        original.updated_at = baseline
        for index in range(1, 6):
            extra_id = uuid4()
            ids.append(str(extra_id))
            session.add(PaperDeployment(
                id=extra_id,
                user_id=original.user_id,
                command_key=f"extra_{index}_{uuid4().hex}",
                request_hash=original.request_hash,
                request_data=dict(original.request_data),
                status="WAITING_FOR_ENTRY",
                detail="Waiting for entry signal",
                trade_id=None,
                created_at=baseline + timedelta(seconds=index),
                updated_at=baseline + timedelta(seconds=index),
            ))
        session.commit()

    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    with factory() as session:
        checked = {row.correlation_id for row in session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "paper.deployment.entry_checked"
        ))}
        assert checked == set(ids[:5])
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    with factory() as session:
        checked = {row.correlation_id for row in session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "paper.deployment.entry_checked"
        ))}
        assert checked == set(ids)
        assert list(session.scalars(select(PaperTrade))) == []


def test_retryable_quote_and_kill_switch_blocks_are_recorded_once(client, monkeypatch):
    provider, headers, payload, factory = armed_setup(client, monkeypatch)
    armed = client.post("/paper/deployments", headers=headers, json=payload)
    assert armed.status_code == 201, armed.text
    original_chain = provider.get_option_chain

    async def insufficient_size(*args):
        chain = await original_chain(*args)
        return chain.model_copy(update={"contracts": [
            leg.model_copy(update={"ask_quantity": 0})
            if leg.strike == 25000 and leg.option_type == "CE" else leg
            for leg in chain.contracts
        ]})

    provider.get_option_chain = insufficient_size
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    waiting = client.get("/paper/deployments", headers=headers).json()[0]
    assert waiting["status"] == "WAITING_FOR_ENTRY"
    assert "quote size is insufficient" in waiting["detail"]
    with factory() as session:
        blocks = list(session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "paper.deployment.entry_blocked"
        )))
        assert len(blocks) == 1
        assert blocks[0].correlation_id == armed.json()["id"]
        assert blocks[0].details["status_code"] == 409
        assert "quote size is insufficient" in blocks[0].details["reason"]
        assert list(session.scalars(select(PaperTrade))) == []
        assert list(session.scalars(select(PaperOrder))) == []
        checks = list(session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "paper.deployment.entry_checked"
        )))
        assert all(check.details["snapshot"]["decision"] == "RULES_PASS"
                   for check in checks)

    provider.get_option_chain = original_chain
    switched = client.put(
        "/paper/account/kill-switch", headers=headers, json={"enabled": True}
    )
    assert switched.status_code == 200
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    waiting = client.get("/paper/deployments", headers=headers).json()[0]
    assert waiting["status"] == "WAITING_FOR_ENTRY"
    assert "kill switch" in waiting["detail"]
    with factory() as session:
        blocks = list(session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "paper.deployment.entry_blocked"
        )))
        assert len(blocks) == 2
        assert blocks[-1].details["status_code"] == 423
        assert "kill switch" in blocks[-1].details["reason"]
        assert list(session.scalars(select(PaperTrade))) == []
    client.put("/paper/account/kill-switch", headers=headers, json={"enabled": False})
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    assert client.get("/paper/deployments", headers=headers).json()[0]["status"] == "FILLED"
    decisions = client.get(
        f"/paper/deployments/{armed.json()['id']}/decisions", headers=headers
    )
    assert [row["event_type"] for row in decisions.json()].count(
        "paper.deployment.entry_blocked"
    ) == 2


def test_cancelled_or_expired_deployment_never_opens(client, monkeypatch):
    provider, headers, payload, factory = armed_setup(client, monkeypatch)
    armed = client.post("/paper/deployments", headers=headers, json=payload).json()
    cancel = client.post(f"/paper/deployments/{armed['id']}/cancel", headers=headers)
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "CANCELLED"
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    with factory() as session:
        assert list(session.scalars(select(PaperTrade))) == []

    headers["Idempotency-Key"] = f"arm_{uuid4().hex}"
    second = client.post("/paper/deployments", headers=headers, json=payload).json()
    with factory() as session:
        row = session.get(PaperDeployment, UUID(second["id"]))
        row.request_data = {**row.request_data, "expiry": "2020-01-01"}
        session.commit()
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    assert client.get("/paper/deployments", headers=headers).json()[0]["status"] == "EXPIRED"
    with factory() as session:
        assert list(session.scalars(select(PaperTrade))) == []


def test_expiry_day_cutoff_expires_waiting_deployment_without_fill(client, monkeypatch):
    provider, headers, payload, factory = armed_setup(client, monkeypatch)
    armed = client.post("/paper/deployments", headers=headers, json=payload)
    assert armed.status_code == 201, armed.text
    with factory() as session:
        row = session.get(PaperDeployment, UUID(armed.json()["id"]))
        row.request_data = {**row.request_data, "expiry": exchange_today().isoformat()}
        session.commit()

    async def closing_session(_trading_day):
        now = datetime.now(timezone.utc)
        return ExchangeSession("NFO", now - timedelta(hours=6), now + timedelta(minutes=10))

    monkeypatch.setattr(provider, "get_nfo_session", closing_session)
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    with factory() as session:
        row = session.get(PaperDeployment, UUID(armed.json()["id"]))
        assert row.status == "EXPIRED"
        assert "entry window ended" in row.detail
        assert list(session.scalars(select(PaperTrade))) == []
        assert list(session.scalars(select(PaperOrder))) == []
        events = list(session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "paper.deployment.expired"
        )))
        assert len(events) == 1


def test_expiry_day_session_outage_preserves_waiting_deployment(client, monkeypatch):
    provider, headers, payload, factory = armed_setup(client, monkeypatch)
    armed = client.post("/paper/deployments", headers=headers, json=payload).json()
    with factory() as session:
        row = session.get(PaperDeployment, UUID(armed["id"]))
        row.request_data = {**row.request_data, "expiry": exchange_today().isoformat()}
        session.commit()

    async def unavailable_session(_trading_day):
        raise MarketDataProviderError("private-provider-detail")

    monkeypatch.setattr(provider, "get_nfo_session", unavailable_session)
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "DEGRADED"
    waiting = client.get("/paper/deployments", headers=headers).json()[0]
    assert waiting["detail"] == "NFO session unavailable; expiry-day entries remain waiting"
    assert "private-provider-detail" not in waiting["detail"]
    with factory() as session:
        assert session.get(PaperDeployment, UUID(armed["id"])).status == "WAITING_FOR_ENTRY"
        assert list(session.scalars(select(PaperTrade))) == []

def test_arm_requires_rules_and_is_user_scoped(client, monkeypatch):
    _, headers, payload, _ = armed_setup(client, monkeypatch)
    assert client.post("/paper/deployments", json=payload).status_code == 401
    assert client.post(
        "/paper/deployments", headers=headers,
        json={**payload, "entry_conditions": []},
    ).status_code == 422
    first = client.post("/paper/deployments", headers=headers, json=payload).json()
    other_token = client.post("/auth/register", json={
        "email": "other-arm@example.com", "password": "another-strong-password",
    }).json()["access_token"]
    other = {"Authorization": f"Bearer {other_token}"}
    assert client.get("/paper/deployments", headers=other).json() == []
    assert client.post(f"/paper/deployments/{first['id']}/cancel", headers=other).status_code == 404
    assert client.get(
        f"/paper/deployments/{first['id']}/decisions", headers=other
    ).status_code == 404
