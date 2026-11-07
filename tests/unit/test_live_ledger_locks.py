"""Live order ledger, signed releases, arming, the send locks, the halt API and nightly check."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import apps.nightly.main as nightly
from apps.api.live_routes import LiveHaltRequest, halt_live, live_status
from packages.broker_upstox.halt import set_live_halt
from packages.broker_upstox.ledger import apply_broker_row, expected_positions, move, record_order
from packages.broker_upstox.orders import IllegalOrderTransition, LiveOrderRequest, OrderState
from packages.broker_upstox.releases import create_release, revoke_release, send_blockers, set_armed
from packages.database.base import Base
from packages.database.models import (
    AuditEvent,
    LiveArmEvent,
    LiveHaltEvent,
    LiveOrder,
    LiveOrderEvent,
    LiveRelease,
    User,
    UserNotification,
)

KEY = "k" * 32
TODAY = date(2026, 12, 18)
CE = "NSE_FO|1001"


@pytest.fixture
def factory():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[
        User.__table__, UserNotification.__table__, AuditEvent.__table__,
        LiveHaltEvent.__table__, LiveRelease.__table__, LiveArmEvent.__table__,
        LiveOrder.__table__, LiveOrderEvent.__table__,
    ])
    return sessionmaker(engine)


@pytest.fixture
def session(factory):
    with factory() as session:
        yield session


def _owner(session) -> User:
    user = User(email=f"{uuid4().hex[:8]}@example.com", password_hash="x", role="TRADER")
    session.add(user)
    session.commit()
    return user


def _release(session, **changes) -> LiveRelease:
    args = dict(key=KEY, owner_user_id=_owner(session).id, strategy_key="long_straddle",
                max_lots=1, max_trade_loss=Decimal("20000"), daily_loss_cap=Decimal("25000"),
                valid_until=date(2027, 1, 31))
    return create_release(session, **(args | changes))


def _open_all_locks(session, release: LiveRelease) -> None:
    set_live_halt(session, halted=False, actor="owner", reason="pilot")
    set_armed(session, release.id, armed=True, actor="owner", reason="pilot")


def _blockers(session, release_id: UUID, **changes) -> list[str]:
    args = dict(live_enabled=True, release_id=release_id, key=KEY, today=TODAY)
    return send_blockers(session, **(args | changes))


# --- the locks -------------------------------------------------------------------------

def test_new_release_is_blocked_by_halt_and_arm(session):
    release = _release(session)
    assert _blockers(session, release.id) == ["LIVE_HALTED", "NOT_ARMED"]


def test_all_locks_open_only_when_every_condition_holds(session):
    release = _release(session)
    _open_all_locks(session, release)
    assert _blockers(session, release.id) == []
    assert _blockers(session, release.id, live_enabled=False) == ["LIVE_DISABLED"]
    assert _blockers(session, release.id, today=date(2027, 2, 1)) == ["RELEASE_EXPIRED"]
    assert _blockers(session, release.id, key="x" * 32) == ["RELEASE_SIGNATURE"]
    assert _blockers(session, release.id, key="short") == ["RELEASE_SIGNATURE"]
    assert _blockers(session, uuid4()) == ["RELEASE_MISSING"]


def test_tampered_release_fails_its_signature(session):
    release = _release(session)
    _open_all_locks(session, release)
    release.max_lots = 5
    session.commit()
    assert _blockers(session, release.id) == ["RELEASE_SIGNATURE"]


def test_disarm_and_revoke_close_the_locks(session):
    release = _release(session)
    _open_all_locks(session, release)
    set_armed(session, release.id, armed=False, actor="owner", reason="pause")
    assert _blockers(session, release.id) == ["NOT_ARMED"]
    set_armed(session, release.id, armed=True, actor="owner", reason="resume")
    revoke_release(session, release.id)
    assert _blockers(session, release.id) == ["RELEASE_REVOKED"]


def test_live_setting_itself_stays_locked():
    from apps.api.settings import Settings
    assert Settings(live_trading_enabled=True).effective_live_trading_enabled is False


def test_release_inputs_are_checked(session):
    with pytest.raises(ValueError):
        _release(session, max_lots=0)
    with pytest.raises(ValueError):
        _release(session, key="short")
    with pytest.raises(ValueError):
        set_armed(session, uuid4(), armed=True, actor="owner", reason="x")


def test_locks_fail_closed_when_tables_are_missing():
    bare = sessionmaker(create_engine("sqlite://"))()
    assert _blockers(bare, uuid4()) == ["LIVE_HALTED", "LOCKS_UNREADABLE"]


# --- the ledger ------------------------------------------------------------------------

def _request(tag="tp-1-0-0", side="BUY", quantity=65):
    return LiveOrderRequest(CE, side, quantity, Decimal("100.00"), tag)


def test_order_lifecycle_is_recorded_and_alerted(session):
    release = _release(session)
    order = record_order(session, release.id, _request())
    move(session, order, OrderState.SENT)
    move(session, order, OrderState.UNKNOWN)  # the send timed out
    apply_broker_row(session, order, status="complete", filled_quantity=65,
                     average_price=Decimal("100.10"), broker_order_id="B1")
    assert (order.state, order.filled_quantity, order.broker_order_id) == ("FILLED", 65, "B1")
    states = session.scalars(select(LiveOrderEvent.state).order_by(LiveOrderEvent.occurred_at))
    assert list(states) == ["NEW", "SENT", "UNKNOWN", "FILLED"]
    alerts = session.scalars(select(UserNotification.event_type)
                             .where(UserNotification.user_id == release.owner_user_id))
    assert set(alerts) == {"live.order.sent", "live.order.unknown", "live.order.filled"}


def test_illegal_update_is_never_stored(session):
    release = _release(session)
    order = record_order(session, release.id, _request())
    with pytest.raises(IllegalOrderTransition):
        move(session, order, OrderState.FILLED)
    session.rollback()
    assert session.get(LiveOrder, order.id).state == "NEW"
    move(session, order, OrderState.SENT)
    move(session, order, OrderState.ACKNOWLEDGED, filled_quantity=0)
    move(session, order, OrderState.PARTIAL, filled_quantity=30)
    with pytest.raises(ValueError):
        move(session, order, OrderState.PARTIAL, filled_quantity=20)


def test_a_tag_is_recorded_only_once(session):
    release = _release(session)
    record_order(session, release.id, _request())
    with pytest.raises(IntegrityError):
        record_order(session, release.id, _request())


def test_expected_positions_net_the_fills(session):
    release = _release(session)
    for tag, side, filled in (("a", "BUY", 65), ("b", "SELL", 65), ("c", "SELL", 30)):
        order = record_order(session, release.id, _request(tag, side))
        move(session, order, OrderState.SENT)
        apply_broker_row(session, order, status="open", filled_quantity=filled,
                         average_price=None, broker_order_id=tag)
    assert expected_positions(session) == {CE: -30}


# --- halt API --------------------------------------------------------------------------

def test_dashboard_can_halt_but_status_starts_halted(session):
    user = _owner(session)
    assert live_status(user, session).halted is True
    set_live_halt(session, halted=False, actor="owner", reason="pilot")
    assert live_status(user, session).halted is False
    status = halt_live(LiveHaltRequest(reason="Halted from the dashboard"), user, session)
    assert status.halted is True and status.live_enabled is False
    assert session.scalar(select(AuditEvent.event_type)) == "live.halted"


# --- nightly ---------------------------------------------------------------------------

def test_nightly_check_skips_without_a_token(factory, monkeypatch):
    monkeypatch.delenv("UPSTOX_LIVE_READ_TOKEN", raising=False)
    assert nightly.run_live_reconcile(factory) == "SKIPPED_NO_TOKEN"


def test_nightly_check_compares_broker_with_ledger(factory, monkeypatch):
    monkeypatch.setenv("UPSTOX_LIVE_READ_TOKEN", "token")

    async def positions(self):
        return {CE: 65}

    monkeypatch.setattr(nightly.PortfolioReader, "fo_positions", positions)
    assert nightly.run_live_reconcile(factory) == "MISMATCH (1)"
    with factory() as session:
        order = record_order(session, _release(session).id, _request())
        move(session, order, OrderState.SENT)
        apply_broker_row(session, order, status="complete", filled_quantity=65,
                         average_price=None, broker_order_id="B1")
    assert nightly.run_live_reconcile(factory) == "MATCHED"
