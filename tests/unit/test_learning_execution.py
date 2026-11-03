"""Paper-vs-shadow gaps become append-only cost versions, frozen into new ideas only."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from packages.backtesting.published_strategies import Market, TradeResult
from packages.database.base import Base
from packages.database.models import LearningHypothesis, LearningObservation
from packages.learning import engine
from packages.learning.engine import Hypothesis, Kind, record_observation, run_learning
from packages.learning.execution import calibrate, current, paper_gaps, pinned

_NOW = datetime(2026, 12, 4, 12, tzinfo=timezone.utc)
_FRIDAY = date(2026, 12, 4)
_START = date(2026, 11, 2)
_PREREG = {"expected_mean": 3_000.0, "expected_sd": 4_000.0, "max_loss": 20_000.0,
           "trades_per_session": 1.0}


@pytest.fixture
def session(monkeypatch):
    def run(params, _market):
        return [TradeResult(_START + timedelta(days=offset), _START, 1_000.0, 0.0, 5_000.0)
                for offset in range(3)]

    monkeypatch.setitem(engine.KINDS, "fake", Kind(run, lambda params, result: (20_000.0,
                                                                                5_000.0)))
    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    with Session(database) as active:
        yield active


def _hypothesis(key: str, **params) -> Hypothesis:
    return Hypothesis(key=key, family="f", kind="fake", params=params, forward_from=_START,
                      origin="HUMAN", rationale="t", preregistration=_PREREG)


def test_weekly_versions_come_from_paper_shadow_gaps_and_never_change(session, tmp_path):
    run_learning(session, Market([], 0.0), _NOW, [_hypothesis("rule")])
    row = session.scalar(select(LearningHypothesis))
    for offset, paper in enumerate((700.0, 800.0, 900.0)):
        record_observation(session, row, _START + timedelta(days=offset), "PAPER", paper,
                           20_000.0, 5_000.0, _NOW)
    session.flush()
    assert sorted(paper_gaps(session, _START)) == [-300.0, -200.0, -100.0]
    path = tmp_path / "cost.json"
    assert calibrate(session, _FRIDAY, path) is None  # Three pairs are not enough.
    for offset in range(3, 6):
        day = _START + timedelta(days=offset)
        record_observation(session, row, day, "SHADOW", 1_000.0, 20_000.0, 5_000.0, _NOW)
        record_observation(session, row, day, "PAPER", 800.0, 20_000.0, 5_000.0, _NOW)
    session.flush()
    assert calibrate(session, _FRIDAY - timedelta(days=1), path) is None  # Not a Friday.
    first = calibrate(session, _FRIDAY, path)
    assert first == {"version": 1, "made": "2026-12-04", "pairs": 6, "median_gap": -200.0,
                     "slippage_per_trade": 200.0}
    assert calibrate(session, _FRIDAY, path) is None and current(path) == first
    assert pinned({"a": 1}, first) == {"a": 1, "cost_version": 1, "slippage": 200.0}
    assert pinned({"a": 1}, None) == {"a": 1}


def test_a_pinned_slippage_is_charged_on_every_shadow_trade(session):
    run_learning(session, Market([], 0.0), _NOW, [_hypothesis("costed", slippage=250.0)])
    values = [float(item.net_pnl) for item in session.scalars(select(LearningObservation))]
    assert values == [750.0, 750.0, 750.0]
