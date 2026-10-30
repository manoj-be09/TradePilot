"""Nightly backups must be proven readable; the report counts only the frozen strategies."""

import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import apps.nightly.main as nightly
from apps.nightly.main import IST, due, run_once
from packages.database import futures_models  # noqa: F401
from packages.database.base import Base
from packages.database.models import PaperAccount, PaperDeployment, PaperTrade, User
from packages.database.sqlite_backup import BackupError, backup_database
from packages.paper_trading.forward_plan import ForwardPlan
from packages.paper_trading.forward_report import build_report, drawdown, render_markdown

EMAIL = "nightly@example.test"
NOW = datetime(2026, 11, 3, 11, 30, tzinfo=timezone.utc)
FROZEN = uuid4()
PLAN = ForwardPlan(EMAIL, (FROZEN,), date(2026, 10, 30), date(2026, 12, 11))


@pytest.fixture
def factory():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _account(session) -> PaperAccount:
    user = User(email=EMAIL, password_hash="not-a-real-hash")
    session.add(user)
    session.flush()
    account = PaperAccount(
        user_id=user.id, initial_cash=Decimal("1000000"), cash_balance=Decimal("1000000"),
    )
    session.add(account)
    session.flush()
    return account


def _trade(session, account, pnl, day, version=FROZEN, status="CLOSED", key="long_put"):
    trade = PaperTrade(
        account_id=account.id, user_id=account.user_id, strategy_key=key, strategy_name=key,
        underlying="NIFTY", expiry=date(2026, 11, 3), reference_strike=Decimal("23000"), lots=1,
        lot_size=65, max_loss=Decimal("5000"), entry_cash_flow=Decimal("0"),
        realized_pnl=pnl, status=status, legs=[],
        closed_at=NOW + timedelta(days=day) if status == "CLOSED" else None,
    )
    session.add(trade)
    session.flush()
    session.add(PaperDeployment(
        user_id=account.user_id, command_key=uuid4().hex, request_hash="h", status="FILLED",
        detail="d", trade_id=trade.id, created_at=NOW, updated_at=NOW,
        request_data={"strategy_key": key, "strategy_version_id": str(version)},
    ))
    session.flush()


def _sqlite_file(path):
    connection = sqlite3.connect(path)
    connection.execute("create table t (x integer)")
    connection.execute("insert into t values (1)")
    connection.commit()
    connection.close()


def test_a_backup_is_a_readable_copy_and_only_the_newest_are_kept(tmp_path):
    source = tmp_path / "nifty.db"
    _sqlite_file(source)
    backups = tmp_path / "backups"
    for day in range(4):
        made = backup_database(source, backups, NOW + timedelta(days=day), keep=2)
    connection = sqlite3.connect(made)
    assert connection.execute("select x from t").fetchone() == (1,)
    connection.close()
    assert len(list(backups.glob("*.db"))) == 2
    assert not list(backups.glob("*.partial"))


def test_a_backup_of_a_write_ahead_log_database_is_one_clean_file(tmp_path):
    source = tmp_path / "nifty.db"
    _sqlite_file(source)
    connection = sqlite3.connect(source)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.close()
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "nifty-old.partial").write_bytes(b"interrupted copy")
    (backups / "nifty-old.partial-wal").write_bytes(b"")
    made = backup_database(source, backups, NOW)
    assert [item.name for item in backups.iterdir()] == [made.name]
    copy = sqlite3.connect(made)
    assert copy.execute("select x from t").fetchone() == (1,)
    copy.close()


def test_low_disk_space_is_reported(tmp_path, factory, monkeypatch, caplog):
    monkeypatch.setattr(nightly, "disk_free_gb", lambda _path: 4.0)
    with factory() as session:
        _account(session)
        session.commit()
    results = run_once(
        factory, PLAN, tmp_path / "absent.db", tmp_path / "backups", tmp_path / "reports", NOW,
    )
    assert results["disk_free_gb"] == "4.0"
    assert "Low disk space" in caplog.text
    assert "Disk free: 4.0 GB. LOW" in (tmp_path / "reports" / "2026-11-03.md").read_text()


def test_a_missing_database_is_an_error_and_a_corrupt_one_leaves_no_copy(tmp_path):
    with pytest.raises(BackupError):
        backup_database(tmp_path / "absent.db", tmp_path / "backups", NOW)
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"this is not a database" * 100)
    with pytest.raises(sqlite3.DatabaseError):
        backup_database(broken, tmp_path / "backups", NOW)
    assert not list((tmp_path / "backups").glob("*.db"))


def test_drawdown_measures_the_largest_fall_from_a_peak():
    assert drawdown([]) == 0
    assert drawdown([Decimal(100), Decimal(-30), Decimal(-50), Decimal(200)]) == 80
    assert drawdown([Decimal(-40), Decimal(10)]) == 40


def test_the_report_counts_only_frozen_versions_and_orders_by_time(factory):
    with factory() as session:
        account = _account(session)
        _trade(session, account, Decimal("100"), 2)
        _trade(session, account, Decimal("-50"), 1)
        _trade(session, account, None, 0, status="OPEN")
        _trade(session, account, Decimal("9999"), 0, version=uuid4(), key="strip")
        session.commit()
        report = build_report(session, PLAN, account, date(2026, 11, 3))
    assert [row.strategy_key for row in report.rows] == ["long_put"]
    row = report.rows[0]
    assert (row.armed, row.open_trades, row.closed_trades, row.wins) == (3, 1, 2, 1)
    assert row.realized_pnl == Decimal("50") and row.worst_drawdown == Decimal("50")
    text = render_markdown(report)
    assert "Simulated" in text and "long put" in text and "strip" not in text


def test_due_once_per_day_after_the_close_in_india_time():
    before = datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)  # 15:30 IST
    after = datetime(2026, 11, 3, 11, 30, tzinfo=timezone.utc)  # 17:00 IST
    assert not due(before, None)
    assert due(after, None)
    assert not due(after, after.astimezone(IST).date())
    assert due(after, date(2026, 11, 2))


def test_a_failed_backup_does_not_stop_the_report(tmp_path, factory):
    with factory() as session:
        _account(session)
        session.commit()
    results = run_once(
        factory, PLAN, tmp_path / "absent.db", tmp_path / "backups", tmp_path / "reports", NOW,
    )
    assert results["backup"] == "FAILED"
    assert results["ledger"] in {"MATCHED", "MISMATCH"}
    assert (tmp_path / "reports" / "2026-11-03.md").exists()


def test_no_backup_runs_while_the_market_is_open(tmp_path, factory):
    source = tmp_path / "nifty.db"
    _sqlite_file(source)
    midday = datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc)  # 11:30 IST
    results = run_once(
        factory, PLAN, source, tmp_path / "backups", tmp_path / "reports", midday,
    )
    assert results["backup"] == "SKIPPED_BEFORE_CLOSE"
    assert not (tmp_path / "backups").exists()


def test_no_account_is_reported_rather_than_raised(tmp_path, factory):
    results = run_once(
        factory, PLAN, tmp_path / "absent.db", tmp_path / "backups", tmp_path / "reports", NOW,
    )
    assert results["ledger"] == "NO_ACCOUNT"
