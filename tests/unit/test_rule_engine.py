from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from packages.backtesting.published_strategies import Market, TradeResult
from packages.backtesting.rich_premium import night_rules
from packages.backtesting.rule_engine import (
    ENGINE_RULES,
    T_BAR,
    EngineRule,
    demotion_reason,
    evidence,
    promotion_needs,
    run_engine,
)
from packages.database import models  # noqa: F401
from packages.database.base import Base
from packages.database.models import ResearchRuleEvent, ResearchRuleVersion, ResearchShadowTrade

START = date(2026, 11, 2)
MARKET = Market([], 0.0)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _rule(version: int = 1, forward_from: date = START) -> EngineRule:
    return EngineRule("condor", version, forward_from, {"short_delta": 0.15, "threshold": 1.1,
                      "hold_overnight": True, "entry_time": "09:30", "into_expiry": True})


def _trades(values: list[float], first: date = START) -> list[TradeResult]:
    return [TradeResult(first + timedelta(days=i), first + timedelta(days=i + 1), value, 0.0)
            for i, value in enumerate(values)]


def _strong(n: int = 40) -> list[float]:
    return [1000.0 + 10 * (i % 3) for i in range(n)]


def _at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 11, 30, tzinfo=timezone.utc)


def _stage(session: Session, version: int = 1) -> str:
    return session.scalar(select(ResearchRuleVersion.stage)
                          .where(ResearchRuleVersion.version == version))


def _events(session: Session) -> list[tuple[int, str]]:
    return [(e.version, e.event_type) for e in session.scalars(
        select(ResearchRuleEvent).order_by(ResearchRuleEvent.occurred_at, ResearchRuleEvent.id))]


def test_registry_matches_the_frozen_night_rules():
    def cells(run):
        return dict(zip(run.__code__.co_freevars,
                        [cell.cell_contents for cell in run.__closure__], strict=True))
    for frozen, rule in zip(night_rules(), ENGINE_RULES, strict=True):
        assert frozen.name == rule.key
        assert cells(rule.runner()) == cells(frozen.run)
        assert rule.forward_from == START


def test_bar_is_bonferroni_over_every_rule_tried():
    assert 4.4 < T_BAR < 4.5
    noisy = evidence([3000.0, -2500.0] * 15 + [3000.0], days=120)
    assert any("t value" in need for need in promotion_needs(noisy))
    assert promotion_needs(evidence(_strong(), days=120)) == []
    assert any("day(s)" in need for need in promotion_needs(evidence(_strong(), days=30)))
    assert any("trade(s)" in need for need in promotion_needs(evidence(_strong(10), days=120)))


def test_shadow_counts_only_sessions_from_forward_from_and_is_idempotent(session):
    early = _trades([500.0], START - timedelta(days=3))
    run = lambda rule, market: early + _trades([100.0, -50.0])  # noqa: E731
    run_engine(session, MARKET, _at(START + timedelta(days=5)), [_rule()], run)
    run_engine(session, MARKET, _at(START + timedelta(days=6)), [_rule()], run)
    trades = session.scalars(select(ResearchShadowTrade)).all()
    assert sorted(float(t.net_pnl) for t in trades) == [-50.0, 100.0]
    assert _events(session) == [(1, "REGISTERED")]
    row = session.scalar(select(ResearchRuleVersion))
    assert row.stage == "SHADOW" and row.latest_check["trades"] == 2 and row.latest_check["needs"]


def test_frozen_version_cannot_change(session):
    run_engine(session, MARKET, _at(START), [_rule()], lambda r, m: [])
    changed = EngineRule("condor", 1, START, {**_rule().params, "threshold": 1.2})
    with pytest.raises(ValueError, match="frozen record"):
        run_engine(session, MARKET, _at(START), [changed], lambda r, m: [])


def test_promotes_after_the_bar_then_demotes_on_a_loss_streak(session):
    values = _strong()
    run_engine(session, MARKET, _at(START + timedelta(days=120)), [_rule()],
               lambda r, m: _trades(values))
    assert _stage(session) == "PAPER_APPROVED"
    later = _trades([-100.0] * 5, START + timedelta(days=130))
    run_engine(session, MARKET, _at(START + timedelta(days=140)), [_rule()],
               lambda r, m: _trades(values) + later)
    assert _stage(session) == "DEMOTED"
    assert _events(session) == [(1, "REGISTERED"), (1, "PROMOTED"), (1, "DEMOTED")]


def test_shadow_breach_of_the_loss_limit_retires(session):
    run_engine(session, MARKET, _at(START + timedelta(days=10)), [_rule()],
               lambda r, m: _trades([500.0, -25_000.0]))
    assert _stage(session) == "RETIRED"


def test_challenger_supersedes_and_a_demoted_challenger_rolls_back(session):
    champion, challenger = _rule(1), _rule(2, START + timedelta(days=30))
    base = _strong(60)

    def run(rule, market):
        if rule.version == 1:
            return _trades(base)
        return _trades([2000.0 + 10 * (i % 3) for i in range(40)], challenger.forward_from)
    run_engine(session, MARKET, _at(START + timedelta(days=100)), [champion, challenger], run)
    assert _stage(session, 1) == "PAPER_APPROVED" and _stage(session, 2) == "SHADOW"
    run_engine(session, MARKET, _at(START + timedelta(days=125)), [champion, challenger], run)
    assert _stage(session, 1) == "SUPERSEDED" and _stage(session, 2) == "PAPER_APPROVED"

    def worse(rule, market):
        if rule.version == 1:
            return run(rule, market)
        return run(rule, market) + _trades([-100.0] * 5, START + timedelta(days=130))
    run_engine(session, MARKET, _at(START + timedelta(days=140)), [champion, challenger], worse)
    assert _stage(session, 2) == "DEMOTED" and _stage(session, 1) == "PAPER_APPROVED"
    assert (1, "ROLLED_BACK") in _events(session)


def test_challenger_must_beat_the_champion_over_the_same_sessions(session):
    champion, challenger = _rule(1), _rule(2, START + timedelta(days=30))
    run = lambda rule, market: _trades(_strong(160))  # noqa: E731
    run_engine(session, MARKET, _at(START + timedelta(days=125)), [champion, challenger], run)
    assert _stage(session, 1) == "PAPER_APPROVED" and _stage(session, 2) == "SHADOW"
    row = session.scalar(select(ResearchRuleVersion).where(ResearchRuleVersion.version == 2))
    assert any("higher average" in need for need in row.latest_check["needs"])


def test_drift_demotes_when_results_fall_well_below_the_shadow_record():
    before = [1000.0 + i % 3 for i in range(30)]
    assert demotion_reason(before, [10.0 + i % 5 for i in range(10)]) == "DRIFT"
    assert demotion_reason(before, [3000.0, -1200.0] * 5) is None
    assert demotion_reason(before, [100.0, -30_000.0]) == "WORST_TRADE"


def test_every_same_expiry_template_is_a_rule_frozen_from_10_november():
    from packages.backtesting.rule_engine import TEMPLATE_RULES
    from packages.paper_trading.strategies import STRATEGIES

    assert [rule.key for rule in TEMPLATE_RULES] == [f"template:{s.key}" for s in STRATEGIES]
    assert {rule.forward_from for rule in TEMPLATE_RULES} == {date(2026, 11, 10)}
    assert not any(rule.override for rule in TEMPLATE_RULES)  # nothing trades unpromoted


def test_template_rules_are_scored_from_replayed_expiries_and_promotion_is_visible(
        session, monkeypatch):
    from packages.backtesting import rule_engine
    from packages.backtesting.history_backtest import BacktestConfig
    from packages.paper_trading.strategies import get_strategy

    seen = []

    def fake_backtest(histories, config):
        seen.append((config.strategy.key, config.template_rules, config.entry_time))
        trade = type("T", (), {"entry_at": _at(date(2026, 11, 11)), "expiry": date(2026, 11, 17),
                               "gross_pnl": 500, "charges": 100})
        return type("R", (), {"trades": [trade]})

    monkeypatch.setattr(rule_engine, "run_backtest", fake_backtest)
    rule = next(r for r in rule_engine.TEMPLATE_RULES if r.key == "template:long_put")
    runner = rule_engine.template_runner([], BacktestConfig(strategy=get_strategy("long_put")))
    run_engine(session, None, _at(date(2026, 11, 12)), [rule], runner)
    trade = session.scalar(select(ResearchShadowTrade))
    assert (trade.session, float(trade.net_pnl)) == (date(2026, 11, 11), 400.0)
    assert seen[0][:2] == ("long_put", True)
    assert rule_engine.promoted_templates(session) == set()
    session.scalar(select(ResearchRuleVersion)).stage = "PAPER_APPROVED"
    assert rule_engine.promoted_templates(session) == {"long_put"}
    # A template whose definition changed stops adding evidence to the old version.
    changed = EngineRule(rule.key, 1, rule.forward_from,
                         {**rule.params, "template_version": "9.9.9"}, override=False)
    assert runner(changed, None) == []
