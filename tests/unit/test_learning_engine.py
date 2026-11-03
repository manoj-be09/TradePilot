"""The v2 loop moves frozen hypotheses only on forward evidence, and keeps one error budget."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from packages.backtesting.published_strategies import Market, TradeResult
from packages.database.base import Base
from packages.database.models import LearningEvent, LearningHypothesis, LearningObservation
from packages.learning import engine
from packages.learning.budget import alpha_for
from packages.learning.engine import Hypothesis, Kind, record_observation, run_learning

_START = date(2026, 11, 2)
_NOW = datetime(2027, 6, 1, 12, tzinfo=timezone.utc)
_STRONG = {"expected_mean": 3_000.0, "expected_sd": 4_000.0, "max_loss": 20_000.0,
           "trades_per_session": 1.0}


def _days(count: int) -> list[date]:
    return [_START + timedelta(days=offset) for offset in range(count)]


def _results(params, _market) -> list[TradeResult]:
    return [TradeResult(day, day, value, 0.0, 5_000.0)
            for day, value in zip(_days(len(params["pnl"])), params["pnl"], strict=True)]


@pytest.fixture(autouse=True)
def fake_kind(monkeypatch):
    monkeypatch.setitem(engine.KINDS, "fake", Kind(_results, lambda params, result:
                                                   (20_000.0, 5_000.0)))


@pytest.fixture
def session():
    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    with Session(database) as active:
        yield active


def _hypothesis(key: str, pnl: list[float], family: str = "night", **options) -> Hypothesis:
    return Hypothesis(key=key, family=family, kind="fake", params={"pnl": pnl},
                      forward_from=_START, origin=options.pop("origin", "HUMAN"),
                      rationale="test", preregistration=options.pop("prereg", _STRONG),
                      **options)


def _row(session, key) -> LearningHypothesis:
    return session.scalar(select(LearningHypothesis).where(LearningHypothesis.key == key))


_WINNER = [4_000.0] * 120
_MARKET = Market([], 0.0)


def test_an_underpowered_idea_is_refused_without_spending_the_budget(session):
    weak = {**_STRONG, "expected_mean": 20.0}
    run_learning(session, _MARKET, _NOW, [_hypothesis("weak", [100.0], prereg=weak),
                                          _hypothesis("strong", [100.0])])
    assert _row(session, "weak").stage == "UNDERPOWERED"
    assert _row(session, "weak").budget_sequence is None
    assert _row(session, "strong").budget_sequence == 1
    assert _row(session, "strong").alpha == alpha_for(1)
    assert session.scalars(select(LearningObservation)).all()[0].session == _START


def test_a_frozen_hypothesis_cannot_change_and_reruns_record_nothing_twice(session):
    run_learning(session, _MARKET, _NOW, [_hypothesis("rule", [100.0, -50.0])])
    run_learning(session, _MARKET, _NOW, [_hypothesis("rule", [100.0, -50.0])])
    assert len(session.scalars(select(LearningObservation)).all()) == 2
    with pytest.raises(ValueError):
        run_learning(session, _MARKET, _NOW, [_hypothesis("rule", [100.0, -50.0, 7.0])])


def test_strong_forward_evidence_moves_to_probation_and_paper_confirms_approval(session):
    run_learning(session, _MARKET, _NOW, [_hypothesis("rule", _WINNER)])
    row = _row(session, "rule")
    assert row.stage == "PROBATION"
    assert row.latest_check["evidence"] >= row.latest_check["bar"]
    for day in _days(10):
        record_observation(session, row, day, "PAPER", 3_500.0, 20_000.0, 5_000.0, _NOW)
    run_learning(session, _MARKET, _NOW, [_hypothesis("rule", _WINNER)])
    assert _row(session, "rule").stage == "APPROVED"
    trail = [event.event_type for event in session.scalars(
        select(LearningEvent).order_by(LearningEvent.occurred_at))]
    assert trail == ["REGISTERED", "TO_PROBATION", "APPROVED"]


def test_paper_far_below_shadow_is_demoted(session):
    run_learning(session, _MARKET, _NOW, [_hypothesis("rule", _WINNER)])
    for day in _days(10):
        record_observation(session, _row(session, "rule"), day, "PAPER", 500.0, 20_000.0,
                           5_000.0, _NOW)
    run_learning(session, _MARKET, _NOW, [_hypothesis("rule", _WINNER)])
    assert _row(session, "rule").stage == "DEMOTED"


def test_too_few_trades_or_a_better_baseline_keep_it_in_shadow(session):
    run_learning(session, _MARKET, _NOW, [
        _hypothesis("short", [4_000.0] * 10),
        _hypothesis("every_day", [4_500.0] * 120, baseline=True),
        _hypothesis("filtered", _WINNER, baseline_key="every_day"),
    ])
    assert _row(session, "short").stage == "SHADOW"
    assert _row(session, "every_day").stage == "BASELINE"
    assert _row(session, "every_day").budget_sequence is None
    filtered = _row(session, "filtered")
    assert filtered.stage == "SHADOW"
    assert "a higher average than its baseline" in filtered.latest_check["needs"]


def test_a_losing_rule_is_retired_on_futility(session):
    loser = [3_000.0, -16_000.0, 3_000.0] * 30
    run_learning(session, _MARKET, _NOW, [_hypothesis("loser", loser)])
    assert _row(session, "loser").stage == "RETIRED"


def test_a_challenger_must_beat_the_champion_and_a_demoted_one_rolls_back(session):
    run_learning(session, _MARKET, _NOW, [_hypothesis("v1", _WINNER)])
    for day in _days(10):
        record_observation(session, _row(session, "v1"), day, "PAPER", 4_000.0, 20_000.0,
                           5_000.0, _NOW)
    run_learning(session, _MARKET, _NOW, [_hypothesis("v1", _WINNER)])
    assert _row(session, "v1").stage == "APPROVED"

    later = _NOW + timedelta(days=1)
    weaker, better = [3_800.0] * 120, [4_500.0] * 120
    hypotheses = [_hypothesis("v1", _WINNER), _hypothesis("v2", weaker),
                  _hypothesis("v3", better)]
    run_learning(session, _MARKET, later, hypotheses)
    for key, value in (("v2", 3_700.0), ("v3", 4_400.0)):
        for day in _days(10):
            record_observation(session, _row(session, key), day, "PAPER", value, 20_000.0,
                               5_000.0, later)
    run_learning(session, _MARKET, later, hypotheses)
    assert _row(session, "v2").stage == "PROBATION"
    assert "a higher average than v1" in _row(session, "v2").latest_check["needs"][0]
    assert _row(session, "v3").stage == "APPROVED"
    assert _row(session, "v1").stage == "SUPERSEDED"

    # v3 then loses five paper trades in a row after approval and v1 comes back.
    approved_on = _row(session, "v3").stage_since.date()
    for offset in range(1, 6):
        record_observation(session, _row(session, "v3"), approved_on + timedelta(days=offset),
                           "PAPER", -1_000.0, 20_000.0, 5_000.0, later)
    run_learning(session, _MARKET, later + timedelta(days=6), hypotheses)
    assert _row(session, "v3").stage == "DEMOTED"
    assert _row(session, "v1").stage == "APPROVED"


def test_override_skips_the_power_check_but_never_its_evidence(session):
    weak = {**_STRONG, "expected_mean": 20.0}
    run_learning(session, _MARKET, _NOW, [_hypothesis("owner", [100.0] * 5, prereg=weak,
                                                      override=True)])
    row = _row(session, "owner")
    assert row.stage == "SHADOW" and row.override and row.budget_sequence == 1
