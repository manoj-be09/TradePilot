"""GRID and LLM ideas stay inside the allowed space, are pre-registered, and are saved once."""

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from packages.backtesting.published_strategies import Market
from packages.database.base import Base
from packages.database.models import LearningHypothesis
from packages.learning import engine, ideas
from packages.learning.engine import Kind, load_hypotheses, record_observation

_PARAMS = {"short_delta": 0.15, "threshold": 1.1, "hold_overnight": True, "entry_time": "09:30",
           "into_expiry": True}
_NOW = datetime(2026, 12, 1, 12, tzinfo=timezone.utc)
_TODAY = date(2026, 12, 1)  # A Tuesday.


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setitem(engine.KINDS, "rich_condor", Kind(lambda params, market: [],
                                                          lambda params, result: (1.0, 1.0)))
    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    with Session(database) as active:
        yield active


def _parent(session: Session, trades: int = 25, value: float = 2_000.0) -> LearningHypothesis:
    row = LearningHypothesis(
        key="night_a", family="night", kind="rich_condor", params=_PARAMS,
        forward_from=date(2026, 11, 2), origin="HUMAN", override=False, rationale="r",
        preregistration={"max_loss": 20_000.0}, stage="SHADOW", stage_since=_NOW,
        latest_check={"evidence": 5.0}, created_at=_NOW)
    session.add(row)
    session.flush()
    for offset in range(trades):
        record_observation(session, row, date(2026, 11, 2 + offset), "SHADOW",
                           value + (offset % 2) * 100, 20_000.0, 5_000.0, _NOW)
    session.flush()
    return row


def test_neighbours_move_one_parameter_one_step_inside_the_ranges():
    found = dict(ideas.neighbours(_PARAMS))
    assert set(found) == {"short_delta0.1", "short_delta0.2", "threshold1", "threshold1.2",
                          "at0920", "at0940"}
    assert found["at0920"]["entry_time"] == "09:20"
    assert found["threshold1.2"]["short_delta"] == 0.15
    edge = {**_PARAMS, "short_delta": 0.05, "entry_time": "09:20"}
    assert "short_delta0" not in dict(ideas.neighbours(edge))
    assert "at0910" not in dict(ideas.neighbours(edge))
    assert not ideas.in_range({**_PARAMS, "entry_time": "09:23"})
    assert not ideas.in_range({**_PARAMS, "filters": [{"feature": "moon", "max": 1}]})


def test_grid_ideas_come_from_proven_shadow_rules_and_drop_overfit_batches(session, monkeypatch):
    _parent(session)
    monkeypatch.setattr(ideas, "overfit_probability", lambda matrix: 0.2)
    found = ideas.grid_ideas(session, Market([], 0.0), _TODAY, set())
    assert len(found) == ideas.MAX_GRID_A_NIGHT
    first = found[0]
    assert first.origin == "GRID" and first.forward_from == date(2026, 12, 2)
    assert first.preregistration["expected_mean"] == pytest.approx(2_050 * ideas.SHRINK, rel=0.01)
    assert first.preregistration["max_loss"] == 20_000.0
    assert ideas.grid_ideas(session, Market([], 0.0), _TODAY, {item.key for item in found}) == []
    monkeypatch.setattr(ideas, "overfit_probability", lambda matrix: 0.8)
    assert ideas.grid_ideas(session, Market([], 0.0), _TODAY, set()) == []


def test_too_few_or_losing_shadow_trades_are_no_parent(session):
    _parent(session, trades=5)
    assert ideas.parent_stats(session) == []


class _Client:
    def __init__(self, payload, stop="end_turn"):
        self.sent: dict = {}
        text = SimpleNamespace(type="text", text=json.dumps(payload))
        response = SimpleNamespace(stop_reason=stop, content=[text])

        def create(**kwargs):
            self.sent = kwargs
            return response

        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))


def _idea(name: str, **changes) -> dict:
    return {"name": name, "rationale": "Event days carry gap risk", **_PARAMS,
            "filters": [{"feature": "event_next", "equals": False}], "expected_mean": 800.0,
            "expected_sd": 5_000.0, "trades_per_session": 0.4, **changes}


def test_llm_ideas_are_checked_against_the_allowed_space(session):
    client = _Client({"ideas": [_idea("Skip event nights"),
                                _idea("Too far", short_delta=0.6)]})
    found = ideas.llm_ideas(session, _TODAY, set(), lambda: client)
    [kept] = found
    assert kept.key == "llm_20261201_skip_event_nights" and kept.origin == "LLM"
    assert kept.params["filters"] == [{"feature": "event_next", "equals": False}]
    assert kept.preregistration["expected_mean"] == 800.0
    assert client.sent["model"] == "claude-opus-5-5" and client.sent["fallbacks"] == "default"
    assert client.sent["output_config"]["format"]["type"] == "json_schema"
    refused = _Client({"ideas": [_idea("x")]}, stop="refusal")
    assert ideas.llm_ideas(session, _TODAY, set(), lambda: refused) == []
    assert ideas.llm_ideas(session, _TODAY, set(), lambda: None) == []


def test_ideas_are_saved_once_and_the_llm_runs_only_on_fridays(session, tmp_path, monkeypatch):
    _parent(session)
    monkeypatch.setattr(ideas, "overfit_probability", lambda matrix: 0.2)
    calls: list = []

    def factory():
        calls.append(1)
        return _Client({"ideas": [_idea("Skip event nights")]})

    tuesday = ideas.generate(session, Market([], 0.0), _TODAY, set(), factory)
    assert calls == [] and all(item.origin == "GRID" for item in tuesday)
    friday = date(2026, 12, 4)
    found = ideas.generate(session, Market([], 0.0), friday, set(), factory)
    assert calls == [1] and found[-1].origin == "LLM"
    path = tmp_path / "generated.json"
    ideas.save_generated(path, found)
    ideas.save_generated(path, [])
    loaded = load_hypotheses(path)
    assert [item.key for item in loaded] == [item.key for item in found]
    assert loaded[-1].forward_from == date(2026, 12, 5)
    again = ideas.generate(session, Market([], 0.0), friday, {item.key for item in loaded},
                           factory)
    assert calls == [1] and again == []
    assert session.scalars(select(LearningHypothesis)).all()[0].key == "night_a"
