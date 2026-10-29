import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.alert_relay.main import _deliver
from packages.alerts.relay import AlertCollector
from packages.alerts.telegram import TelegramError, TelegramSender
from packages.database.base import Base
from packages.database.models import User, UserNotification, WorkerLease

NOW = datetime(2026, 10, 30, 10, 0, tzinfo=timezone.utc)
TOKEN = "123456:secret-bot-token"


def _factory():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[
        User.__table__, UserNotification.__table__, WorkerLease.__table__,
    ])
    return sessionmaker(engine)


def _user(session, email):
    user = User(email=email, password_hash="x", role="TRADER")
    session.add(user)
    session.flush()
    return user


def _lease(session, status, heartbeat):
    session.merge(WorkerLease(name="paper-exits", owner="w", generation=1, lease_until=heartbeat,
                              heartbeat_at=heartbeat, status=status, detail=""))


def test_only_the_named_accounts_new_notifications_are_sent_once():
    factory = _factory()
    with factory() as session:
        owner, friend = _user(session, "owner@example.com"), _user(session, "friend@example.com")
        _lease(session, "HEALTHY", NOW)
        session.add_all([
            UserNotification(user_id=owner.id, event_type="X", title="Old", message="before start",
                             created_at=NOW - timedelta(hours=1)),
            UserNotification(user_id=owner.id, event_type="X", title="Trade closed",
                             message="Bull put spread, -42.25",
                             created_at=NOW + timedelta(seconds=5)),
            UserNotification(user_id=friend.id, event_type="X", title="Friend trade",
                             message="not yours", created_at=NOW + timedelta(seconds=6)),
        ])
        session.commit()
        collector = AlertCollector(user_email=" Owner@Example.com ", since=NOW, interval_seconds=15)
        first = collector.collect(session, NOW + timedelta(seconds=10))
        again = collector.collect(session, NOW + timedelta(seconds=20))
    assert first == ["TradePilot (paper): Trade closed\nBull put spread, -42.25"]
    assert again == []


def test_worker_trouble_is_reported_on_change_and_on_recovery():
    factory = _factory()
    with factory() as session:
        _user(session, "owner@example.com")
        collector = AlertCollector(user_email="owner@example.com", since=NOW, interval_seconds=15)
        assert collector.collect(session, NOW) == [
            "TradePilot (paper): Exit worker has not started. "
            "Automatic exits and entries are not running.",
        ]
        _lease(session, "HEALTHY", NOW)
        assert collector.collect(session, NOW + timedelta(seconds=5)) == [
            "TradePilot (paper): Exit worker is running normally again.",
        ]
        assert collector.collect(session, NOW + timedelta(seconds=10)) == []
        # No heartbeat for more than 3 intervals + 10 s: stale, reported once.
        stale = collector.collect(session, NOW + timedelta(seconds=60))
        assert len(stale) == 1 and "stopped updating" in stale[0]
        assert collector.collect(session, NOW + timedelta(seconds=90)) == []


def test_a_healthy_worker_at_start_sends_nothing():
    factory = _factory()
    with factory() as session:
        _user(session, "owner@example.com")
        _lease(session, "HEALTHY", NOW)
        collector = AlertCollector(user_email="owner@example.com", since=NOW, interval_seconds=15)
        assert collector.collect(session, NOW + timedelta(seconds=5)) == []


def test_sender_posts_to_the_chat_and_never_leaks_the_token():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200 if b"ok" in request.content else 401)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sender = TelegramSender(TOKEN, "42", client)
            await sender.send("ok message")
            with pytest.raises(TelegramError) as failure:
                await sender.send("rejected")
            return str(failure.value)

    message = asyncio.run(run())
    assert seen[0].url.path == f"/bot{TOKEN}/sendMessage"
    assert b'"chat_id":"42"' in seen[0].content.replace(b" ", b"")
    assert TOKEN not in message and "401" in message


def test_failed_messages_keep_order_and_are_dropped_after_three_attempts():
    class Flaky:
        def __init__(self, fail_on):
            self.fail_on, self.sent = fail_on, []

        async def send(self, text):
            if text == self.fail_on:
                raise TelegramError("down")
            self.sent.append(text)

    sender = Flaky("b")
    queue = [("a", 0), ("b", 0), ("c", 0)]
    queue = asyncio.run(_deliver(sender, queue))
    assert sender.sent == ["a"] and queue == [("b", 1), ("c", 0)]
    queue = asyncio.run(_deliver(sender, queue))
    queue = asyncio.run(_deliver(sender, queue))
    assert queue == [("c", 0)]
    assert asyncio.run(_deliver(sender, queue)) == [] and sender.sent == ["a", "c"]
