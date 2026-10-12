"""Exercise separate database sessions, including optional PostgreSQL in CI."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier, Event
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker
from test_paper_trading import FakeUpstoxProvider

from apps.api.main import close_paper_trade, open_paper_trade
from apps.api.paper_schemas import PaperTradeCreateRequest
from apps.api.settings import get_settings
from packages.database.base import Base
from packages.database.models import PaperAccount, PaperOrder, PaperTrade, User
from packages.paper_trading.transactions import lock_account


@pytest.fixture(params=["sqlite", "postgresql"])
def isolated_factory(request, tmp_path):
    if request.param == "postgresql":
        url = os.getenv("TEST_POSTGRES_URL")
        if not url:
            pytest.skip("TEST_POSTGRES_URL not set; PostgreSQL is tested in CI")
        schema = "test_" + uuid4().hex
        admin = create_engine(url)
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'concurrency.db'}")
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        engine.dispose()
        if request.param == "postgresql":
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def seed(factory):
    with factory() as session:
        user = User(email="concurrency@example.com", password_hash="test-only", role="VIEWER")
        session.add(user)
        session.flush()
        session.add(
            PaperAccount(
                user_id=user.id, initial_cash=Decimal(1000000), cash_balance=Decimal(1000000)
            )
        )
        session.commit()
        return user.id


def requests(factory, user_id, provider, keys):
    body = PaperTradeCreateRequest(
        strategy_key="long_call", expiry=provider.expiry, reference_strike=Decimal(25000), lots=1
    )

    def run(key):
        with factory() as session:
            user = session.get(User, user_id)
            try:
                return asyncio.run(open_paper_trade(body, user, session, key))
            except HTTPException as error:
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, key) for key in keys]
        return [future.result(timeout=20) for future in futures]


def barrier_provider(monkeypatch):
    provider = FakeUpstoxProvider()
    barrier = Barrier(2)
    original = provider.get_option_chain

    async def together(underlying, expiry):
        chain = await original(underlying, expiry)
        barrier.wait(timeout=10)
        return chain

    provider.get_option_chain = together
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    return provider


def test_concurrent_retry_posts_one_trade_and_cash_change(isolated_factory, monkeypatch):
    factory = isolated_factory
    user_id = seed(factory)
    provider = barrier_provider(monkeypatch)
    results = requests(factory, user_id, provider, ["same-intent", "same-intent"])
    assert results[0].id == results[1].id
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(PaperTrade)) == 1
        assert session.scalar(select(PaperAccount.cash_balance)) == Decimal("997475.00")


def test_concurrent_reuse_of_key_with_different_request_rejects_second_intent(
    isolated_factory, monkeypatch
):
    factory = isolated_factory
    user_id = seed(factory)
    provider = barrier_provider(monkeypatch)

    def open_with_lots(lots):
        body = PaperTradeCreateRequest(
            strategy_key="long_call",
            expiry=provider.expiry,
            reference_strike=Decimal(25000),
            lots=lots,
        )
        with factory() as session:
            try:
                return asyncio.run(
                    open_paper_trade(body, session.get(User, user_id), session, "shared-intent")
                )
            except HTTPException as error:
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(open_with_lots, lots) for lots in (1, 2)]
        results = [future.result(timeout=20) for future in futures]

    assert sum(result == 409 for result in results) == 1
    winning_lots = next(result.lots for result in results if result != 409)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(PaperTrade)) == 1
        assert session.scalar(select(func.count()).select_from(PaperOrder)) == 1
        assert session.scalar(select(PaperTrade.lots)) == winning_lots
        expected_cash = Decimal("1000000.00") - Decimal("2525.00") * winning_lots
        assert session.scalar(select(PaperAccount.cash_balance)) == expected_cash


def test_concurrent_entries_recheck_risk_under_lock(isolated_factory, monkeypatch):
    monkeypatch.setenv("PAPER_MAX_OPEN_TRADES", "1")
    get_settings.cache_clear()
    try:
        factory = isolated_factory
        user_id = seed(factory)
        provider = barrier_provider(monkeypatch)
        results = requests(factory, user_id, provider, ["first-intent", "second-intent"])
        assert sum(result == 409 for result in results) == 1
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(PaperTrade)) == 1
    finally:
        get_settings.cache_clear()


def test_kill_switch_enabled_during_quote_fetch_prevents_entry(isolated_factory, monkeypatch):
    factory = isolated_factory
    user_id = seed(factory)
    provider = FakeUpstoxProvider()
    reached_quote_fetch = Event()
    resume_quote_fetch = Event()
    original_chain = provider.get_option_chain

    async def delayed_chain(underlying, expiry):
        reached_quote_fetch.set()
        assert resume_quote_fetch.wait(timeout=10)
        return await original_chain(underlying, expiry)

    provider.get_option_chain = delayed_chain
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    body = PaperTradeCreateRequest(
        strategy_key="long_call",
        expiry=provider.expiry,
        reference_strike=Decimal(25000),
        lots=1,
    )

    def open_trade():
        with factory() as session:
            try:
                return asyncio.run(
                    open_paper_trade(body, session.get(User, user_id), session, "switch-race")
                )
            except HTTPException as error:
                return error.status_code

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(open_trade)
        try:
            assert reached_quote_fetch.wait(timeout=10)
            with factory() as session:
                account = lock_account(session, user_id)
                account.kill_switch_enabled = True
                session.commit()
        finally:
            resume_quote_fetch.set()
        assert future.result(timeout=20) == 423

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(PaperTrade)) == 0
        assert session.scalar(select(func.count()).select_from(PaperOrder)) == 0
        assert session.scalar(select(PaperAccount.cash_balance)) == Decimal("1000000.00")


def test_concurrent_closes_post_cash_once(isolated_factory, monkeypatch):
    factory = isolated_factory
    user_id = seed(factory)
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    with factory() as session:
        body = PaperTradeCreateRequest(
            strategy_key="long_call",
            expiry=provider.expiry,
            reference_strike=Decimal(25000),
            lots=1,
        )
        trade = asyncio.run(
            open_paper_trade(body, session.get(User, user_id), session, "open-once")
        )
    barrier_provider(monkeypatch)

    def close():
        with factory() as session:
            try:
                return asyncio.run(close_paper_trade(trade.id, session.get(User, user_id), session))
            except HTTPException as error:
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(close) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]
    assert sum(result == 409 for result in results) == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(PaperOrder)) == 2
        assert session.scalar(select(PaperAccount.cash_balance)) == Decimal("999975.00")
