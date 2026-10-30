"""The forward runner arms each frozen version once per expiry and fails closed."""

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.paper_schemas import PaperTradeCreateRequest
from apps.forward_runner.main import freeze, run_cycle
from packages.database import futures_models  # noqa: F401
from packages.database.base import Base
from packages.database.models import PaperDeployment, StrategyVersion, User
from packages.market_data.contracts import ExchangeSession, MarketDataUnavailable
from packages.paper_trading.forward_plan import (
    ForwardPlan,
    load_plan,
    nearest_strike,
    next_expiry,
    save_new_plan,
)

TODAY = date(2026, 11, 3)
NOW = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)
EXPIRY = date(2026, 11, 10)
EMAIL = "forward@example.test"


def _definition(key: str, lots: int = 1) -> dict:
    return {
        "template_key": key, "template_version": "2.0.0", "lots": lots,
        "entry_conditions": [
            {"metric": "median_relative_spread_pct", "operator": "LTE", "threshold": "10"}
        ],
        "stop_loss_amount": "1000.00", "target_profit_amount": "1750.00",
        "trailing_stop_amount": "500.00",
    }


class _Provider:
    provider_id = "fake"

    def __init__(self, *, is_open=True, spot="23040", quality="PASS", error=None):
        self.is_open, self.spot, self.quality, self.error = is_open, spot, quality, error

    async def get_nfo_session(self, _day):
        if not self.is_open:
            return None
        return ExchangeSession("NFO", NOW.replace(hour=3), NOW.replace(hour=10))

    async def get_option_contracts(self, _underlying="NIFTY", _expiry=None):
        if self.error:
            raise self.error
        return [SimpleNamespace(expiry=TODAY), SimpleNamespace(expiry=EXPIRY)]

    async def get_option_chain(self, _underlying, expiry):
        return SimpleNamespace(
            expiry=expiry, underlying_spot=Decimal(self.spot) if self.spot else None,
            quality=SimpleNamespace(status=self.quality),
            contracts=[
                SimpleNamespace(strike=Decimal("23000")),
                SimpleNamespace(strike=Decimal("23050")),
            ],
        )


def _version(user_id, key: str, number: int = 1, name: str | None = None, lots: int = 1):
    return StrategyVersion(
        user_id=user_id, name=name or f"Batch study · {key} · 2.0.0 · {lots}L",
        version_number=number, definition=_definition(key, lots), definition_hash="x",
    )


@pytest.fixture
def world():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        user = User(email=EMAIL, password_hash="not-a-real-hash")
        session.add(user)
        session.flush()
        versions = [_version(user.id, key) for key in ("bear_call_spread", "long_put")]
        session.add_all(versions)
        session.commit()
        plan = ForwardPlan(
            EMAIL, tuple(item.id for item in versions), date(2026, 10, 30), date(2026, 12, 11)
        )
    return factory, plan


def _recording_arm(calls, error=None):
    async def arm(body, _user, _session, key):
        if error:
            raise error
        calls.append((body, key))
    return arm


def _cycle(factory, plan, provider, arm, now=NOW):
    return asyncio.run(run_cycle(factory, provider, plan, arm, now))


def test_arms_every_frozen_version_for_the_next_expiry_at_the_nearest_strike(world):
    factory, plan = world
    calls = []
    assert _cycle(factory, plan, _Provider(), _recording_arm(calls)) == "OK"
    assert [body.strategy_key for body, _ in calls] == ["bear_call_spread", "long_put"]
    assert {body.expiry for body, _ in calls} == {EXPIRY}
    assert {body.reference_strike for body, _ in calls} == {Decimal("23050")}
    assert all(key.startswith("fwd_") and key.endswith("20261110") for _, key in calls)
    assert all(body.stop_loss_amount == Decimal("1000.00") for body, _ in calls)


@pytest.mark.parametrize("status", ["WAITING_FOR_ENTRY", "CANCELLED", "REJECTED", "FILLED"])
def test_a_strategy_already_attempted_for_that_expiry_is_not_armed_again(world, status):
    factory, plan = world
    with factory() as session:
        user_id = session.get(StrategyVersion, plan.version_ids[0]).user_id
        session.add(PaperDeployment(
            user_id=user_id, command_key="earlier-key", request_hash="h", status=status,
            detail="d", created_at=NOW, updated_at=NOW,
            request_data={"strategy_key": "bear_call_spread", "expiry": EXPIRY.isoformat()},
        ))
        session.commit()
    calls = []
    assert _cycle(factory, plan, _Provider(), _recording_arm(calls)) == "OK"
    assert [body.strategy_key for body, _ in calls] == ["long_put"]


@pytest.mark.parametrize("provider", [
    _Provider(is_open=False), _Provider(quality="FAIL"), _Provider(spot=None),
    _Provider(error=MarketDataUnavailable("down")),
], ids=["closed", "failed-quality", "no-spot", "provider-down"])
def test_nothing_is_armed_without_an_open_market_and_usable_quotes(world, provider):
    factory, plan = world
    calls = []
    assert _cycle(factory, plan, provider, _recording_arm(calls)) in {"CLOSED", "NO_DATA"}
    assert calls == []


def test_paused_entries_stop_the_cycle_and_other_refusals_skip_one_strategy(world):
    factory, plan = world
    assert _cycle(factory, plan, _Provider(),
                  _recording_arm([], HTTPException(423, "Paused"))) == "PAUSED"
    assert _cycle(factory, plan, _Provider(),
                  _recording_arm([], HTTPException(409, "Demoted"))) == "OK"


def test_a_frozen_definition_with_invalid_rules_is_skipped_not_fatal(world):
    factory, plan = world
    with factory() as session:
        version = session.get(StrategyVersion, plan.version_ids[0])
        version.definition = {**version.definition, "entry_conditions": [{"metric": "unknown"}]}
        session.commit()
    calls = []
    assert _cycle(factory, plan, _Provider(), _recording_arm(calls)) == "OK"
    assert [body.strategy_key for body, _ in calls] == ["long_put"]


def test_the_rebuilt_request_matches_the_frozen_definition_the_endpoint_compares():
    from apps.api.main import _strategy_version_definition
    from packages.paper_trading.strategies import get_strategy

    strategy = get_strategy("long_put")
    assert strategy is not None
    frozen = _definition("long_put")
    first = PaperTradeCreateRequest(
        strategy_key="long_put", expiry=EXPIRY, reference_strike=Decimal("23050"), lots=1,
        entry_conditions=frozen["entry_conditions"], stop_loss_amount="1000.00",
        target_profit_amount="1750.00", trailing_stop_amount="500.00",
    )
    saved = _strategy_version_definition(first, strategy)
    rebuilt = PaperTradeCreateRequest(
        strategy_key=saved["template_key"], expected_template_version=saved["template_version"],
        expiry=EXPIRY, reference_strike=Decimal("23000"), lots=saved["lots"],
        entry_conditions=saved["entry_conditions"],
        stop_loss_amount=saved["stop_loss_amount"],
        target_profit_amount=saved["target_profit_amount"],
        trailing_stop_amount=saved["trailing_stop_amount"],
    )
    assert _strategy_version_definition(rebuilt, strategy) == saved


def test_the_period_ends_on_its_end_date(world):
    factory, plan = world
    calls = []
    after = datetime(2026, 12, 12, 5, 0, tzinfo=timezone.utc)
    assert _cycle(factory, plan, _Provider(), _recording_arm(calls), after) == "FINISHED"
    assert calls == []


def test_freeze_pins_the_latest_one_lot_batch_version_of_each_template(world):
    factory, _ = world
    with factory() as session:
        user_id = session.scalar(select(User.id))
        newer = _version(user_id, "long_put", number=2)
        session.add_all([
            newer,
            _version(user_id, "strip", name="My own rules"),
            _version(user_id, "guts", lots=2),
        ])
        session.commit()
        plan = freeze(session, EMAIL, date(2026, 10, 30), 6)
        assert newer.id in plan.version_ids and len(plan.version_ids) == 2
    assert plan.end == date(2026, 12, 11)


def test_a_frozen_plan_round_trips_and_cannot_be_overwritten(tmp_path):
    path = tmp_path / "plan.json"
    plan = ForwardPlan(EMAIL, (uuid4(),), date(2026, 10, 30), date(2026, 12, 11))
    save_new_plan(path, plan)
    assert load_plan(path) == plan
    with pytest.raises(FileExistsError):
        save_new_plan(path, plan)


def test_next_expiry_skips_today_and_nearest_strike_handles_empty():
    assert next_expiry([TODAY, EXPIRY], TODAY) == EXPIRY
    assert next_expiry([TODAY], TODAY) is None
    assert nearest_strike([], Decimal("1")) is None
