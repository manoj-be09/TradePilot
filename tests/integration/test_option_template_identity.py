"""Template upgrades cannot change an accepted paper entry's economic intent."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_option_deployments import armed_setup

from apps.api.settings import get_settings
from apps.paper_worker.main import run_cycle
from packages.database.models import PaperDeployment, PaperOrder, PaperTrade
from packages.paper_trading import strategies
from packages.paper_trading.strategies import StrategyLeg, StrategyRegistry


def _use_template(monkeypatch, template):
    monkeypatch.setattr(strategies, "STRATEGY_REGISTRY", StrategyRegistry(tuple(
        template if item.key == template.key else item for item in strategies.STRATEGIES
    )))


def _strip_setup(client, monkeypatch):
    provider, headers, payload, factory = armed_setup(client, monkeypatch)
    template = strategies.get_strategy("strip")
    assert template is not None and template.version != "1.0.0"
    payload.update(strategy_key="strip", expected_template_version=template.version)
    return provider, headers, payload, factory, template


def _assert_no_fills(client, headers, factory, initial_cash):
    with factory() as session:
        assert list(session.scalars(select(PaperTrade))) == []
        assert list(session.scalars(select(PaperOrder))) == []
    actual_cash = client.get("/paper/account", headers=headers).json()["cash_balance"]
    assert Decimal(actual_cash) == Decimal(initial_cash)


@pytest.mark.parametrize("route", ["/paper/trades", "/paper/deployments"])
@pytest.mark.parametrize("acknowledgement", [None, "1.0.0"])
def test_revised_template_requires_current_acknowledgement(
    client, monkeypatch, route, acknowledgement,
):
    _, headers, payload, factory, _ = _strip_setup(client, monkeypatch)
    initial_cash = client.get("/paper/account", headers=headers).json()["cash_balance"]
    if acknowledgement is None:
        payload.pop("expected_template_version")
    else:
        payload["expected_template_version"] = acknowledgement
    response = client.post(route, headers=headers, json=payload)
    assert response.status_code == 409, response.text
    _assert_no_fills(client, headers, factory, initial_cash)
    assert client.get("/paper/deployments", headers=headers).json() == []


def test_current_strip_acknowledgement_fills_correct_put_call_ratio(client, monkeypatch):
    _, headers, payload, _, template = _strip_setup(client, monkeypatch)
    opened = client.post("/paper/trades", headers=headers, json=payload)
    assert opened.status_code == 201, opened.text
    trade = opened.json()
    assert {leg["option_type"]: leg["quantity_units"] for leg in trade["legs"]} == {
        "PE": 50, "CE": 25,
    }
    assert trade["entry_rule_snapshot"]["template_version"] == template.version


def test_new_arm_pins_server_definition_and_ignores_fake_metadata(client, monkeypatch):
    _, headers, payload, factory, template = _strip_setup(client, monkeypatch)
    payload["strategy_definition"] = {"template_key": "strap", "template_version": "fake"}
    armed = client.post("/paper/deployments", headers=headers, json=payload)
    assert armed.status_code == 201, armed.text
    with factory() as session:
        row = session.get(PaperDeployment, UUID(armed.json()["id"]))
        definition = row.request_data["strategy_definition"]
        assert definition["template_key"] == "strip"
        assert definition["template_version"] == template.version
        assert {
            leg["option_type"]: leg["quantity_ratio"] for leg in definition["template_legs"]
        } == {"PE": 2, "CE": 1}
        assert definition["entry_conditions"] == payload["entry_conditions"]
        assert Decimal(definition["stop_loss_amount"]) == Decimal(payload["stop_loss_amount"])


def test_pinned_definition_change_rejects_worker_entry(client, monkeypatch):
    provider, headers, payload, factory = armed_setup(client, monkeypatch)
    armed = client.post("/paper/deployments", headers=headers, json=payload)
    assert armed.status_code == 201, armed.text
    initial_cash = client.get("/paper/account", headers=headers).json()["cash_balance"]
    with factory() as session:
        row = session.get(PaperDeployment, UUID(armed.json()["id"]))
        data = deepcopy(row.request_data)
        data["strategy_definition"]["template_legs"][0]["quantity_ratio"] = 2
        row.request_data = data
        session.commit()
    asyncio.run(run_cycle(factory, provider, get_settings()))
    saved = client.get("/paper/deployments", headers=headers).json()[0]
    assert saved["status"] == "REJECTED", saved
    _assert_no_fills(client, headers, factory, initial_cash)


def test_registry_upgrade_after_arm_does_not_repin_idempotent_command(client, monkeypatch):
    provider, headers, payload, factory = armed_setup(client, monkeypatch)
    armed = client.post("/paper/deployments", headers=headers, json=payload)
    assert armed.status_code == 201, armed.text
    deployment_id = UUID(armed.json()["id"])
    with factory() as session:
        original = deepcopy(session.get(PaperDeployment, deployment_id).request_data)
    template = strategies.get_strategy("long_call")
    assert template is not None
    _use_template(monkeypatch, replace(template, version="99.0.0"))
    repeated = client.post("/paper/deployments", headers=headers, json=payload)
    assert repeated.status_code == 201, repeated.text
    assert repeated.json() == armed.json()
    with factory() as session:
        assert session.get(PaperDeployment, deployment_id).request_data == original
        assert len(list(session.scalars(select(PaperDeployment)))) == 1
    asyncio.run(run_cycle(factory, provider, get_settings()))
    saved = client.get("/paper/deployments", headers=headers).json()[0]
    assert saved["status"] == "REJECTED", saved
    with factory() as session:
        assert session.get(PaperDeployment, deployment_id).request_data == original
        assert list(session.scalars(select(PaperTrade))) == []


@pytest.mark.parametrize("mutation", ["definition", "request"])
def test_locked_fill_rechecks_pinned_definition_after_quote_io(client, monkeypatch, mutation):
    provider, headers, payload, factory = armed_setup(client, monkeypatch)
    armed = client.post("/paper/deployments", headers=headers, json=payload)
    assert armed.status_code == 201, armed.text
    initial_cash = client.get("/paper/account", headers=headers).json()["cash_balance"]
    original_chain = provider.get_option_chain
    changed = False

    async def change_definition_during_io(*args):
        nonlocal changed
        chain = await original_chain(*args)
        if not changed:
            with factory() as session:
                row = session.get(PaperDeployment, UUID(armed.json()["id"]))
                data = deepcopy(row.request_data)
                if mutation == "definition":
                    data["strategy_definition"]["template_legs"][0]["quantity_ratio"] = 2
                else:
                    data["stop_loss_amount"] = "900"
                row.request_data = data
                session.commit()
            changed = True
        return chain

    provider.get_option_chain = change_definition_during_io
    asyncio.run(run_cycle(factory, provider, get_settings()))
    assert changed, "The fixture must pass the early guard and reach provider I/O"
    saved = client.get("/paper/deployments", headers=headers).json()[0]
    assert saved["status"] == "REJECTED", saved
    _assert_no_fills(client, headers, factory, initial_cash)


def test_unpinned_legacy_strip_rejects_but_remains_readable(client, monkeypatch):
    provider, headers, payload, factory, corrected = _strip_setup(client, monkeypatch)
    legacy = replace(corrected, version="1.0.0", legs=(
        StrategyLeg("PE", "BUY", 0), StrategyLeg("CE", "BUY", 0, 2),
    ))
    _use_template(monkeypatch, legacy)
    payload.pop("expected_template_version")
    armed = client.post("/paper/deployments", headers=headers, json=payload)
    assert armed.status_code == 201, armed.text
    initial_cash = client.get("/paper/account", headers=headers).json()["cash_balance"]
    with factory() as session:
        row = session.get(PaperDeployment, UUID(armed.json()["id"]))
        data = deepcopy(row.request_data)
        data.pop("strategy_definition")
        row.request_data = data
        session.commit()
    _use_template(monkeypatch, corrected)
    listed = client.get("/paper/deployments", headers=headers)
    assert listed.status_code == 200 and listed.json()[0]["status"] == "WAITING_FOR_ENTRY"
    asyncio.run(run_cycle(factory, provider, get_settings()))
    saved = client.get("/paper/deployments", headers=headers).json()[0]
    assert saved["status"] == "REJECTED", saved
    _assert_no_fills(client, headers, factory, initial_cash)


def test_unchanged_unpinned_legacy_template_keeps_original_hash_and_fills(client, monkeypatch):
    provider, headers, payload, factory = armed_setup(client, monkeypatch)
    armed = client.post("/paper/deployments", headers=headers, json=payload)
    assert armed.status_code == 201, armed.text
    with factory() as session:
        row = session.get(PaperDeployment, UUID(armed.json()["id"]))
        data = deepcopy(row.request_data)
        assert "expected_template_version" not in data
        data.pop("strategy_definition")
        row.request_data = data
        original_hash = row.request_hash
        session.commit()
    assert client.post("/paper/deployments", headers=headers, json=payload).json() == armed.json()
    asyncio.run(run_cycle(factory, provider, get_settings()))
    with factory() as session:
        row = session.get(PaperDeployment, UUID(armed.json()["id"]))
        assert row.status == "FILLED"
        assert row.request_hash == original_hash
        assert "strategy_definition" not in row.request_data
        assert len(list(session.scalars(select(PaperTrade)))) == 1


def test_existing_legacy_strip_uses_original_legs_and_idempotent_response(client, monkeypatch):
    provider, headers, payload, factory, corrected = _strip_setup(client, monkeypatch)
    legacy = replace(corrected, version="1.0.0", legs=(
        StrategyLeg("PE", "BUY", 0), StrategyLeg("CE", "BUY", 0, 2),
    ))
    _use_template(monkeypatch, legacy)
    payload.pop("expected_template_version")
    opened = client.post("/paper/trades", headers=headers, json=payload)
    assert opened.status_code == 201, opened.text
    trade = opened.json()
    assert {leg["option_type"]: leg["quantity_units"] for leg in trade["legs"]} == {
        "PE": 25, "CE": 50,
    }
    with factory() as session:
        row = session.get(PaperTrade, UUID(trade["id"]))
        saved_legs = deepcopy(row.legs)
        saved_snapshot = deepcopy(row.entry_rule_snapshot)
    _use_template(monkeypatch, corrected)
    repeated = client.post("/paper/trades", headers=headers, json=payload)
    assert repeated.status_code == 201 and repeated.json() == trade
    provider.price_adjustment = Decimal(10)
    marked = client.get("/paper/trades", headers=headers)
    assert marked.status_code == 200, marked.text
    assert marked.json()["trades"][0]["unrealized_pnl"] == "675.00"
    closed = client.post(f"/paper/trades/{trade['id']}/close", headers=headers)
    assert closed.status_code == 200, closed.text
    assert closed.json()["realized_pnl"] == "675.00"
    with factory() as session:
        row = session.get(PaperTrade, UUID(trade["id"]))
        assert row.legs == saved_legs
        assert row.entry_rule_snapshot == saved_snapshot
        assert len(list(session.scalars(select(PaperTrade)))) == 1
        assert len(list(session.scalars(select(PaperOrder)))) == 2


def test_current_saved_version_acknowledges_strip_without_client_version(client, monkeypatch):
    _, headers, payload, _, _ = _strip_setup(client, monkeypatch)
    saved = client.post("/strategies/versions", headers=headers, json={
        "name": "Reviewed Strip", "strategy_key": "strip", "lots": payload["lots"],
        "entry_conditions": payload["entry_conditions"],
        "stop_loss_amount": payload["stop_loss_amount"],
        "target_profit_amount": payload["target_profit_amount"],
        "trailing_stop_amount": payload["trailing_stop_amount"],
    })
    assert saved.status_code == 201, saved.text
    payload.pop("expected_template_version")
    payload["strategy_version_id"] = saved.json()["id"]
    armed = client.post("/paper/deployments", headers=headers, json=payload)
    assert armed.status_code == 201, armed.text
    opened = client.post("/paper/trades", headers={
        **headers, "Idempotency-Key": f"direct_{uuid4().hex}",
    }, json=payload)
    assert opened.status_code == 201, opened.text
