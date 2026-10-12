"""Catalog provenance and research ranking must stay separate from paper eligibility."""

import importlib.util
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, select, text

from packages.database.base import Base
from packages.database.models import StrategyCatalogEntry, User, WatchlistItem
from packages.market_data.contracts import HistoricalBar
from packages.regime.strategy_fit import describe_stock_context, fit_for_strategy


def test_catalog_migration_seeds_source_and_paper_gate(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "strategy_catalog_migration", Path("alembic/versions/0014_strategy_catalog.py")
    )
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'catalog.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        StrategyCatalogEntry.__table__.drop(connection)
        original_op = migration.op
        migration.op = Operations(MigrationContext.configure(connection))
        try:
            migration.upgrade()
            rows = connection.execute(text(
                "SELECT key, status, implementation_key FROM strategy_catalog ORDER BY sort_order"
            )).all()
            assert len(rows) == 10
            assert sum(row.status == "PAPER_READY" for row in rows) == 5
            assert all(row.implementation_key is None for row in rows[5:])
            sources = {
                row.key: row.details["source_url"]
                for row in connection.execute(select(StrategyCatalogEntry))
            }
            assert sources["raschke_holy_grail"].endswith("/2026/01/august1997.pdf")
            assert sources["turtle_system_1"].endswith("/originalturtlerules.htm")
            migration.downgrade()
            assert connection.execute(text("SELECT count(*) FROM users")).scalar() == 0
        finally:
            migration.op = original_op
    engine.dispose()


def test_catalog_api_requires_auth_and_never_promotes_research_only(client):
    response = client.post(
        "/auth/register",
        json={"email": "catalog@example.com", "password": "a-long-test-password"},
    )
    assert response.status_code == 201
    headers = {"Authorization": "Bearer " + response.json()["access_token"]}
    factory = client.app.state.test_session_factory
    with factory() as session:
        session.add(StrategyCatalogEntry(
            key="published_method", name="Published method", status="RESEARCH_ONLY",
            implementation_key=None, sort_order=1, catalog_version="catalog-v1",
            details={"source_url": "https://example.org/primary", "entry": "Published rule"},
        ))
        session.commit()
    assert client.get("/equity/strategy-catalog").status_code == 401
    result = client.get("/equity/strategy-catalog", headers=headers)
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    assert result.json()[0]["status"] == "RESEARCH_ONLY"
    assert result.json()[0]["implementation_key"] is None
    with factory() as session:
        assert session.scalar(select(StrategyCatalogEntry).where(
            StrategyCatalogEntry.key == "published_method"
        )).status == "RESEARCH_ONLY"


def test_catalog_expansion_preserves_original_rows_and_maps_every_paper_rule(tmp_path):
    from runpy import run_path

    from packages.paper_trading.equity import ENTRY_CRITERIA, STRATEGIES
    from packages.regime.strategy_fit import FITS

    modules = [run_path(str(Path('alembic/versions') / name)) for name in (
        '0014_strategy_catalog.py', '0015_strategy_library_expansion.py',
    )]
    rows = modules[0]['ROWS'] + modules[1]['ROWS']
    assert len(rows) == len({row['key'] for row in rows}) == 20
    paper_keys = {row['implementation_key'] for row in rows if row['status'] == 'PAPER_READY'}
    assert paper_keys == set(STRATEGIES) == set(ENTRY_CRITERIA) == set(FITS)
    for row in modules[1]['ROWS']:
        assert row['details']['source_url'].startswith('https://')
        assert row['details']['trader_or_source'] and row['details']['entry']
        assert row['details']['exit'] and row['details']['stop_loss']
        assert row['details']['limitations']
    engine = create_engine(f"sqlite:///{tmp_path / 'expanded-catalog.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(StrategyCatalogEntry.__table__.insert(), modules[0]['ROWS'])
        with Operations.context(MigrationContext.configure(connection)):
            modules[1]['upgrade']()
            assert connection.execute(text('SELECT count(*) FROM strategy_catalog')).scalar() == 20
            modules[1]['downgrade']()
        assert connection.execute(text('SELECT count(*) FROM strategy_catalog')).scalar() == 10
    engine.dispose()


def test_context_uses_completed_bar_direction_volume_and_volatility():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = []
    for index in range(90):
        close = Decimal(100 + index)
        bars.append(HistoricalBar(
            timestamp=start + timedelta(days=index), open=close,
            high=close + 2, low=close - 2, close=close,
            volume=20000 if index == 89 else 10000,
        ))
    context = describe_stock_context(bars)
    assert context["direction"] == "rising"
    assert context["volume"] == "high"
    assert context["volume_ratio"] == 2
    assert context["as_of"] == bars[-1].timestamp
    assert fit_for_strategy("trend", context)[0] > fit_for_strategy("reversion", context)[0]
    bars[-1].volume = 0
    assert describe_stock_context(bars)["volume"] == "unknown"


def test_falling_context_has_no_long_only_market_fit():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = []
    for index in range(90):
        close = Decimal(200 - index)
        bars.append(HistoricalBar(
            timestamp=start + timedelta(days=index), open=close,
            high=close + 2, low=close - 2, close=close, volume=10000,
        ))
    context = describe_stock_context(bars)
    assert context["direction"] == "falling"
    assert all(fit_for_strategy(key, context)[0] < 2 for key in (
        "trend", "breakout", "pullback", "reversion", "momentum"
    ))


def test_equity_api_shows_three_comparisons_but_no_qualified_setup_in_falling_market(
    client, monkeypatch,
):
    registered = client.post(
        "/auth/register",
        json={"email": "falling@example.com", "password": "a-long-test-password"},
    )
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    now = datetime.now(timezone.utc)
    bars = []
    for index in range(90):
        close = Decimal(200 - index)
        bars.append(HistoricalBar(
            timestamp=now - timedelta(days=91 - index), open=close,
            high=close + 2, low=close - 2, close=close, volume=10000,
        ))

    class Provider:
        provider_id = "fixture"

        async def get_historical(self, instrument_key, start, end, interval):
            return bars

        async def get_quote(self, instrument_key):
            raise RuntimeError("Closed session")

    monkeypatch.setattr("apps.api.equity_routes.get_market_data_provider", Provider)
    factory = client.app.state.test_session_factory
    with factory() as db:
        user = db.scalar(select(User).where(User.email == "falling@example.com"))
        item = WatchlistItem(
            user_id=user.id, provider="fixture", instrument_key="FIXTURE|FALL",
            trading_symbol="FALL", exchange="NSE", segment="NSE_EQ", instrument_type="EQ",
        )
        db.add(item)
        db.commit()
        item_id = str(item.id)
    response = client.post(
        "/equity/strategies", json={
            "watchlist_id": item_id, "budget": "10000", "risk_budget": "200",
        }, headers=headers,
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["market_context"]["direction"] == "falling"
    assert result["top_three"] == [row["key"] for row in result["strategies"][:3]]
    assert len(result["top_three"]) == 3
    assert result["qualified_now"] == []
    assert len(result["strategies"]) == 10
    assert not result["has_positive_replay"]
    assert "None currently demonstrates" in result["recommendation"]
    assert result["ranking_version"] == "profit-review-v6"
    for row in result["strategies"]:
        review = row["profit_review"]
        assert "estimated_profit_probability_pct" in review
        assert review["probability_confidence"] in {"unavailable", "low", "medium", "high"}
        assert review["adaptive_parameter_note"]
