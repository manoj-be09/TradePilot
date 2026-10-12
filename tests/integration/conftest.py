from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from apps.api.auth import get_session
from apps.api.main import app
from packages.database import models  # noqa: F401
from packages.database.base import Base


@pytest.fixture
def client(monkeypatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("JWT_SECRET", "integration-tests-use-a-long-random-secret")
    monkeypatch.setenv("UPSTOX_ANALYTICS_TOKEN", "")
    monkeypatch.setenv("PAPER_ALLOW_UNVERIFIED_QUOTES", "false")
    from apps.api.settings import get_settings

    get_settings.cache_clear()
    from apps.api.login_throttle import get_login_throttle, get_registration_throttle

    get_login_throttle.cache_clear()
    get_registration_throttle.cache_clear()
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    app.state.test_session_factory = factory

    def override_session() -> Generator[Session, None, None]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    del app.state.test_session_factory
    Base.metadata.drop_all(engine)
    engine.dispose()
    get_login_throttle.cache_clear()
    get_registration_throttle.cache_clear()
    get_settings.cache_clear()

@pytest.fixture(autouse=True)
def clear_settings_cache_around_test() -> Generator[None, None, None]:
    from apps.api.settings import get_settings

    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()

