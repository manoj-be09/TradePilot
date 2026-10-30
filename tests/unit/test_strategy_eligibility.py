import importlib.util
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.services.strategy_eligibility import sweep_strategy_eligibility
from apps.api.settings import Settings
from packages.database import models  # noqa: F401
from packages.database.base import Base
from packages.database.models import (
    AuditEvent,
    PaperAccount,
    PaperTrade,
    StrategyDemotion,
    StrategyDrawdownBaseline,
    StrategyEligibilityEvent,
    User,
    UserNotification,
)
from packages.paper_trading.demotion import (
    DemotionLimits,
    PromotionPolicy,
    apply_strategy_eligibility,
    assess_strategy,
    eligibility_history,
    evaluate_demotion,
    reinstate_strategy,
)
from packages.paper_trading.worker_lease import acquire

KEY = "bull_put_spread"
LIMITS = DemotionLimits(loss_streak=5, drawdown_multiple=Decimal(2),
                        fallback_drawdown_per_lot=Decimal(60000))
POLICY = PromotionPolicy(cooldown_days=7, min_trades=3, loss_streak=2)
START = datetime(2026, 11, 2, 4, 0, tzinfo=timezone.utc)


@pytest.fixture
def factory():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(engine)


@pytest.fixture
def session(factory):
    with factory() as active:
        yield active


def _account(session) -> User:
    user = User(email=f"{uuid4().hex}@example.com", password_hash="x", role="VIEWER")
    session.add(user)
    session.flush()
    session.add(PaperAccount(
        user_id=user.id, initial_cash=Decimal(1000000), cash_balance=Decimal(1000000),
    ))
    session.flush()
    return user


def _trade(session, user, *, pnl="100", closed_at=START, opened_at=None, lots=1,
           status="CLOSED", snapshot=None):
    account = session.scalar(select(PaperAccount).where(PaperAccount.user_id == user.id))
    session.add(PaperTrade(
        account_id=account.id, user_id=user.id, strategy_key=KEY, strategy_name="Bull put spread",
        underlying="NIFTY", expiry=START.date(), reference_strike=Decimal(25000), lots=lots,
        lot_size=65, max_loss=Decimal(5000), entry_cash_flow=Decimal(0),
        entry_rule_snapshot=snapshot or {}, peak_unrealized_pnl=Decimal(0), status=status,
        realized_pnl=None if pnl is None else Decimal(pnl), legs={"items": []},
        opened_at=opened_at or (closed_at or START) - timedelta(hours=1), closed_at=closed_at,
    ))
    session.flush()


def _baseline(session, drawdown="1000"):
    session.add(StrategyDrawdownBaseline(
        strategy_key=KEY, max_drawdown_per_lot=Decimal(drawdown), trades=20, source="test",
        details={}, created_at=START - timedelta(days=30),
    ))
    session.flush()


def _apply(session, user, now):
    state = apply_strategy_eligibility(session, user.id, KEY, LIMITS, POLICY, now)
    session.flush()
    return state


def _demote(session, user, at=START):
    """Five losses closing just before `at`, then the step that records the pause."""
    for index in range(5):
        _trade(session, user, pnl="-10", closed_at=at - timedelta(minutes=5 - index))
    state = _apply(session, user, at)
    assert state.stage == "DEMOTED"
    return state


def _start_probation(session, user):
    _demote(session, user)
    started = START + timedelta(days=7)
    assert _apply(session, user, started).stage == "PROBATION"
    return started


def _event_types(session, user):
    return [event.event_type for event in reversed(eligibility_history(session, user.id))]


def test_no_trades_is_eligible_and_records_nothing(session):
    user = _account(session)
    state = _apply(session, user, START)
    assert state.stage == "ELIGIBLE" and state.block_reason is None
    assert _event_types(session, user) == []


def test_breach_is_recorded_once_with_reason_and_time(session):
    user = _account(session)
    state = _demote(session, user)
    assert state.reason == "LOSS_STREAK"
    assert "5 consecutive losing paper trades" in str(state.block_reason)
    assert state.probation_starts_at == START + timedelta(days=7)
    assert _apply(session, user, START + timedelta(hours=1)).stage == "DEMOTED"
    events = eligibility_history(session, user.id)
    assert [event.event_type for event in events] == ["DEMOTED"]
    assert events[0].reason == "LOSS_STREAK"
    assert events[0].occurred_at.replace(tzinfo=timezone.utc) == START
    assert session.scalars(select(AuditEvent).where(
        AuditEvent.event_type == "paper.strategy.demoted"
    )).first() is not None
    assert session.scalars(select(UserNotification).where(
        UserNotification.event_type == "paper.strategy.demoted"
    )).first() is not None


def test_drawdown_breach_of_saved_baseline_demotes(session):
    user = _account(session)
    _baseline(session, "1000")
    _trade(session, user, pnl="-2001")
    state = _apply(session, user, START + timedelta(hours=1))
    assert state.stage == "DEMOTED" and state.reason == "DRAWDOWN"
    assert state.check.limit_source == "BACKTEST"


def test_assess_never_writes(session):
    user = _account(session)
    for index in range(5):
        _trade(session, user, pnl="-10", closed_at=START - timedelta(minutes=5 - index))
    state = assess_strategy(session, user.id, KEY, LIMITS, POLICY, START)
    assert state.stage == "DEMOTED" and state.due == "DEMOTED"
    session.flush()
    assert _event_types(session, user) == []


@pytest.mark.parametrize("fields", [{"pnl": None}, {"closed_at": None}])
def test_closed_trade_without_usable_pnl_fails_closed(session, fields):
    user = _account(session)
    _trade(session, user, **fields)
    state = _apply(session, user, START + timedelta(hours=1))
    assert state.stage == "DEMOTED" and state.reason == "EVIDENCE_MISSING"
    assert "no usable P&L" in str(state.block_reason)


def test_expiry_tests_and_open_positions_are_not_evidence(session):
    user = _account(session)
    _trade(session, user, pnl=None, snapshot={"testing_only": True})
    _trade(session, user, pnl=None, closed_at=None, status="OPEN")
    assert _apply(session, user, START + timedelta(hours=1)).stage == "ELIGIBLE"


def test_cooling_off_must_pass_before_probation(session):
    user = _account(session)
    _demote(session, user)
    just_before = START + timedelta(days=7) - timedelta(seconds=1)
    assert _apply(session, user, just_before).stage == "DEMOTED"
    state = _apply(session, user, START + timedelta(days=7))
    assert state.stage == "PROBATION"
    assert _event_types(session, user) == ["DEMOTED", "PROBATION_STARTED"]


def test_promotion_needs_trade_count_positive_total_and_baseline(session):
    user = _account(session)
    _baseline(session, "1000")
    started = _start_probation(session, user)
    for day in range(1, 3):
        _trade(session, user, pnl="50", closed_at=started + timedelta(days=day))
    state = _apply(session, user, started + timedelta(days=3))
    assert state.stage == "PROBATION"
    assert state.probation_trades == 2
    assert state.promotion_needs == ("1 more closed probation trade(s)",)
    _trade(session, user, pnl="-40", closed_at=started + timedelta(days=4))
    promoted = _apply(session, user, started + timedelta(days=5))
    assert promoted.stage == "ELIGIBLE"
    assert _event_types(session, user)[-1] == "PROMOTED"
    assert eligibility_history(session, user.id)[0].reason == "PROBATION_PASSED"
    # Promotion starts a clean window: earlier losses cannot demote it again.
    assert _apply(session, user, started + timedelta(days=6)).stage == "ELIGIBLE"


def test_promotion_refused_while_total_is_not_positive(session):
    user = _account(session)
    _baseline(session, "1000")
    started = _start_probation(session, user)
    for day, pnl in ((1, "50"), (2, "-60"), (3, "10")):
        _trade(session, user, pnl=pnl, closed_at=started + timedelta(days=day))
    state = _apply(session, user, started + timedelta(days=4))
    assert state.stage == "PROBATION"
    assert any("positive total" in need for need in state.promotion_needs)


def test_promotion_fails_closed_without_a_saved_baseline(session):
    user = _account(session)
    started = _start_probation(session, user)
    for day in range(1, 5):
        _trade(session, user, pnl="50", closed_at=started + timedelta(days=day))
    state = _apply(session, user, started + timedelta(days=5))
    assert state.stage == "PROBATION"
    assert state.promotion_needs == ("a saved backtest drawdown baseline",)


def test_positions_opened_before_probation_cannot_earn_promotion(session):
    user = _account(session)
    _baseline(session, "1000")
    started = _start_probation(session, user)
    for day in range(1, 4):
        _trade(session, user, pnl="50", closed_at=started + timedelta(days=day),
               opened_at=started - timedelta(days=1))
    state = _apply(session, user, started + timedelta(days=5))
    assert state.stage == "PROBATION" and state.probation_trades == 0


def test_probation_breach_restarts_cooling_off(session):
    user = _account(session)
    _baseline(session, "1000")
    started = _start_probation(session, user)
    for day in (1, 2):
        _trade(session, user, pnl="-10", closed_at=started + timedelta(days=day))
    failed_at = started + timedelta(days=3)
    state = _apply(session, user, failed_at)
    assert state.stage == "DEMOTED" and state.reason == "LOSS_STREAK"
    assert state.probation_starts_at == failed_at + timedelta(days=7)
    assert _event_types(session, user)[-1] == "PROBATION_FAILED"
    cooling = _apply(session, user, failed_at + timedelta(days=6))
    assert cooling.stage == "DEMOTED"
    assert cooling.probation_starts_at == failed_at + timedelta(days=7)
    assert _apply(session, user, failed_at + timedelta(days=7)).stage == "PROBATION"
    assert _event_types(session, user)[-2:] == ["PROBATION_FAILED", "PROBATION_STARTED"]


def test_probation_drawdown_limit_is_one_times_the_baseline(session):
    user = _account(session)
    _baseline(session, "1000")
    started = _start_probation(session, user)
    _trade(session, user, pnl="-1001", closed_at=started + timedelta(days=1))
    state = _apply(session, user, started + timedelta(days=2))
    assert state.stage == "DEMOTED" and state.reason == "DRAWDOWN"
    assert state.check.drawdown_limit_per_lot == Decimal("1000.00")


def test_owner_reinstatement_is_recorded(session):
    user = _account(session)
    _demote(session, user, at=datetime.now(timezone.utc) - timedelta(hours=1))
    assert reinstate_strategy(session, user.id, KEY) is not None
    session.flush()
    assert eligibility_history(session, user.id)[0].event_type == "REINSTATED"
    assert eligibility_history(session, user.id)[0].reason == "OWNER_REVIEW"
    assert reinstate_strategy(session, user.id, KEY) is None
    assert _apply(session, user, datetime.now(timezone.utc)).stage == "ELIGIBLE"


def test_missing_evidence_outranks_other_rules():
    check = evaluate_demotion(
        [Decimal(-1)] * 5, LIMITS, None, trades_missing_evidence=2,
    )
    assert check.reason == "EVIDENCE_MISSING"
    assert check.snapshot()["trades_missing_evidence"] == 2


def test_policy_is_validated_and_settings_build_it():
    with pytest.raises(ValueError):
        PromotionPolicy(cooldown_days=0, min_trades=5, loss_streak=3)
    with pytest.raises(ValueError):
        PromotionPolicy(cooldown_days=7, min_trades=2, loss_streak=3)
    assert Settings(_env_file=None).paper_promotion_policy == PromotionPolicy(  # type: ignore[call-arg]
        cooldown_days=7, min_trades=5, loss_streak=3,
    )


def test_worker_sweep_records_a_breach_without_any_entry_attempt(factory):
    with factory() as session:
        user = _account(session)
        user_id = user.id
        for index in range(5):
            _trade(session, user, pnl="-10", closed_at=START + timedelta(minutes=index))
        session.commit()
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    with factory() as session:
        token = acquire(session, "worker-a", 120)
    assert token is not None
    assert sweep_strategy_eligibility(factory, token, settings) == []
    assert sweep_strategy_eligibility(factory, token, settings) == []
    with factory() as session:
        assert _event_types_for(session, user_id) == ["DEMOTED"]


def test_worker_sweep_failure_is_a_warning_and_records_nothing(factory, monkeypatch):
    with factory() as session:
        user = _account(session)
        user_id = user.id
        _trade(session, user)
        session.commit()
        token = acquire(session, "worker-a", 120)
    assert token is not None

    def explode(*_args, **_kwargs):
        raise RuntimeError("secret provider text")

    monkeypatch.setattr(
        "apps.api.services.strategy_eligibility.apply_strategy_eligibility", explode,
    )
    warnings = sweep_strategy_eligibility(
        factory, token, Settings(_env_file=None),  # type: ignore[call-arg]
    )
    assert warnings == ["Strategy eligibility check failed; entries re-check it"]
    with factory() as session:
        assert _event_types_for(session, user_id) == []


def test_worker_without_the_lease_records_nothing(factory):
    with factory() as session:
        user = _account(session)
        user_id = user.id
        for index in range(5):
            _trade(session, user, pnl="-10", closed_at=START + timedelta(minutes=index))
        session.commit()
        token = acquire(session, "worker-a", 120)
    assert token is not None
    stale = type(token)(**{**token.__dict__, "generation": token.generation + 1})
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert sweep_strategy_eligibility(factory, stale, settings) == []
    with factory() as session:
        assert _event_types_for(session, user_id) == []


def _event_types_for(session, user_id):
    return [row.event_type for row in session.scalars(
        select(StrategyEligibilityEvent).where(StrategyEligibilityEvent.user_id == user_id)
    )]


def test_migration_backfills_existing_demotions(factory):
    engine = factory.kw["bind"]
    StrategyEligibilityEvent.__table__.drop(engine)
    with factory() as session:
        user = _account(session)
        session.add_all([
            StrategyDemotion(
                user_id=user.id, strategy_key=KEY, status="REINSTATED", reason="DRAWDOWN",
                details={"summary": "an old drawdown"}, demoted_at=START,
                reinstated_at=START + timedelta(days=1),
            ),
            StrategyDemotion(
                user_id=user.id, strategy_key="iron_condor", status="ACTIVE",
                reason="LOSS_STREAK", details={}, demoted_at=START + timedelta(days=2),
            ),
        ])
        session.commit()
        user_id = user.id
    path = Path(__file__).parents[2] / "alembic/versions/0038_strategy_eligibility_events.py"
    spec = importlib.util.spec_from_file_location("migration_0038", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
    with factory() as session:
        rows = eligibility_history(session, user_id)
    assert [(row.strategy_key, row.event_type, row.summary) for row in rows] == [
        ("iron_condor", "DEMOTED", "LOSS_STREAK"),
        (KEY, "REINSTATED", "Reinstated by the owner after review"),
        (KEY, "DEMOTED", "an old drawdown"),
    ]
