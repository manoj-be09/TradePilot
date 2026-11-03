"""Run with python -m apps.nightly.main; after each close it backs up and checks the paper run.

Each day after 16:30 IST it makes a verified database backup, checks the paper ledger,
writes a forward report to work/forward-reports and runs the research rule engine and the v2
learning loop (when work/learning/hypotheses.json exists) over the imported history. A failed
step is logged and the next step still runs. A ledger mismatch
already pauses new paper cash actions by itself; this job only reads the account, and places
no orders.
"""

import argparse
import logging
import shutil
import sqlite3
import subprocess  # nosec B404
import sys
import time as clock
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from apps.api.settings import get_settings
from packages.backtesting.book_loader import load_books, recorded_spread
from packages.backtesting.published_strategies import Market
from packages.backtesting.rule_engine import run_engine
from packages.database.models import PaperAccount, User
from packages.database.session import build_session_factory
from packages.database.sqlite_backup import BackupError, backup_database
from packages.learning.engine import link_paper_trades, load_hypotheses, run_learning
from packages.learning.features import Context
from packages.learning.forward_bars import merge_books, recorded_books
from packages.paper_trading.forward_plan import ForwardPlan, load_plan
from packages.paper_trading.forward_report import build_report, render_markdown

logger = logging.getLogger("nightly")
IST = ZoneInfo("Asia/Kolkata")
RUN_AFTER = time(16, 30)
DATABASE = Path("work/nifty-local.db")
BACKUPS = Path("work/backups")
REPORTS = Path("work/forward-reports")
DEFAULT_PLAN = Path("work/forward-paper.json")
LOW_DISK_GB = 10.0
# Beside the database: the far strikes the condors need, from scripts/import-far-strikes.py.
WIDE_HISTORY_NAME = "history-wide.db"
# Private: the v2 hypotheses and their pre-registered effects stay out of the repository.
HYPOTHESES = Path("work/learning/hypotheses.json")
RECORDED_LOOKBACK = timedelta(days=60)


def disk_free_gb(path: Path) -> float:
    return shutil.disk_usage(path if path.exists() else Path.cwd()).free / 1024**3


def due(now: datetime, last_run: date | None) -> bool:
    local = now.astimezone(IST)
    return local.time() >= RUN_AFTER and last_run != local.date()


def write_report(
    factory: sessionmaker[Session], plan: ForwardPlan, directory: Path, today: date
) -> str | None:
    """Return the ledger status, or None when there is no account to report on."""
    with factory() as session:
        account = session.scalar(
            select(PaperAccount).join(User, PaperAccount.user_id == User.id)
            .where(User.email == plan.user_email)
        )
        if account is None:
            logger.error("The frozen plan's paper account does not exist")
            return None
        report = build_report(session, plan, account, today)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{today.isoformat()}.md").write_text(render_markdown(report), encoding="utf-8")
    return report.ledger_status


def run_rule_engine(factory: sessionmaker[Session], wide: Path, now: datetime) -> str:
    """Score frozen research rules on newly imported sessions; simulated, places nothing."""
    if not wide.exists():
        return "SKIPPED_NO_WIDE_HISTORY"
    with factory() as session:
        decisions = run_engine(session, _research_market(wide), now)
        session.commit()
    for decision in decisions:
        logger.warning("Rule engine: %s", decision)
    return f"{len(decisions)} decision(s)"


IMPORT_LOOKBACK_DAYS = 45
IMPORT_TIMEOUT_SECONDS = 3600


def import_recent_history(today: date, runner: Callable[..., Any] = subprocess.run) -> str:
    """Import newly expired expiries and their far strikes, so the shadow ledger keeps growing.

    Read-only provider calls through the repository's own scripts; both skip finished contracts.
    """
    first = (today - timedelta(days=IMPORT_LOOKBACK_DAYS)).isoformat()
    commands = (
        [sys.executable, "scripts/import-history.py", "--from", first, "--to",
         today.isoformat(), "--interval", "5m"],
        [sys.executable, "scripts/import-far-strikes.py", "--first-expiry", first],
    )
    for command in commands:
        done = runner(command, timeout=IMPORT_TIMEOUT_SECONDS, check=False,
                      capture_output=True, text=True)
        if done.returncode != 0:
            logger.error("%s failed: %s", command[1], (done.stderr or "")[-300:])
            return f"FAILED ({Path(command[1]).stem})"
    return "OK"


def _research_market(wide: Path) -> Market:
    books = load_books(get_settings().database_url, date.max,
                       [f"sqlite:///{wide.as_posix()}"], regular_hours_only=True)
    return Market(books, recorded_spread("median_pct"))


def run_learning_loop(
    factory: sessionmaker[Session], wide: Path, hypotheses: Path, now: datetime,
) -> str:
    """Judge the v2 hypotheses on forward sessions; simulated, places nothing."""
    if not hypotheses.exists():
        return "SKIPPED_NO_HYPOTHESES"
    if not wide.exists():
        return "SKIPPED_NO_WIDE_HISTORY"
    # shortcut: loads the books again after the rule engine; share them if the night runs long.
    history = _research_market(wide)
    # Recorder minutes fill live sessions the imports do not have yet, at no API cost.
    recorded = recorded_books(get_settings().database_url, now.date() - RECORDED_LOOKBACK)
    market = Market(merge_books(history.books, recorded), history.half_spread_pct)
    with factory() as session:
        link_paper_trades(session, now)
        decisions = run_learning(session, market, now, load_hypotheses(hypotheses),
                                 Context.load(get_settings().database_url))
        session.commit()
    for decision in decisions:
        logger.warning("Learning: %s", decision)
    return f"{len(decisions)} decision(s)"


def run_once(
    factory: sessionmaker[Session], plan: ForwardPlan, database: Path, backups: Path,
    reports: Path, now: datetime,
) -> dict[str, str]:
    results: dict[str, str] = {}
    try:
        if now.astimezone(IST).time() < RUN_AFTER:
            # Heavy disk work; keep it away from the session.
            results["backup"] = "SKIPPED_BEFORE_CLOSE"
        else:
            results["backup"] = backup_database(database, backups, now.astimezone(IST)).name
    except (BackupError, sqlite3.Error, OSError) as error:
        logger.error("Backup failed: %s", error)
        results["backup"] = "FAILED"
    try:
        status = write_report(factory, plan, reports, now.astimezone(IST).date())
        results["ledger"] = status or "NO_ACCOUNT"
    except (SQLAlchemyError, OSError) as error:
        logger.error("Report failed: %s", type(error).__name__)
        results["ledger"] = "FAILED"
    try:
        wide = database.parent / WIDE_HISTORY_NAME
        results["import"] = (
            "SKIPPED_BEFORE_CLOSE" if now.astimezone(IST).time() < RUN_AFTER
            # Only where the far-strike history was set up by hand once (RUNBOOK.md).
            else "SKIPPED_NO_WIDE_HISTORY" if not wide.exists()
            else import_recent_history(now.astimezone(IST).date())
        )
    except (OSError, subprocess.SubprocessError) as error:
        logger.error("History import failed: %s", type(error).__name__)
        results["import"] = "FAILED"
    try:
        results["rule_engine"] = (
            run_rule_engine(factory, database.parent / WIDE_HISTORY_NAME, now)
            if now.astimezone(IST).time() >= RUN_AFTER else "SKIPPED_BEFORE_CLOSE"
        )
    except Exception as error:  # noqa: BLE001 - one failed step must not stop the others
        logger.error("Rule engine failed: %s", type(error).__name__)
        results["rule_engine"] = "FAILED"
    try:
        results["learning"] = (
            run_learning_loop(factory, database.parent / WIDE_HISTORY_NAME, HYPOTHESES, now)
            if now.astimezone(IST).time() >= RUN_AFTER else "SKIPPED_BEFORE_CLOSE"
        )
    except Exception as error:  # noqa: BLE001 - one failed step must not stop the others
        logger.error("Learning loop failed: %s", type(error).__name__)
        results["learning"] = "FAILED"
    if results["ledger"] != "MATCHED":
        logger.error("Ledger needs review: %s", results["ledger"])
    free = disk_free_gb(database.parent)
    results["disk_free_gb"] = f"{free:.1f}"
    if free < LOW_DISK_GB:
        logger.error("Low disk space: %.1f GB free. The recorder and backups need room.", free)
    reports.mkdir(parents=True, exist_ok=True)
    day_report = reports / f"{now.astimezone(IST).date().isoformat()}.md"
    if day_report.exists():
        warning = " LOW: free up space soon." if free < LOW_DISK_GB else ""
        with day_report.open("a", encoding="utf-8") as handle:
            handle.write(f"\nDisk free: {free:.1f} GB.{warning}\n")
    return results


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    plan = load_plan(args.plan)
    factory = build_session_factory()
    # A restart later the same day must not repeat a full backup.
    last_run: date | None = None
    today = datetime.now(timezone.utc).astimezone(IST).date()
    if (REPORTS / f"{today.isoformat()}.md").exists() and any(BACKUPS.glob(f"*{today:%Y%m%d}*.db")):
        last_run = today
    while True:
        now = datetime.now(timezone.utc)
        if args.once or due(now, last_run):
            results = run_once(factory, plan, DATABASE, BACKUPS, REPORTS, now)
            logger.info("Nightly: %s", results)
            last_run = now.astimezone(IST).date()
        # Keeps running after the forward period: the rule engine needs months of sessions.
        if args.once:
            return
        clock.sleep(300)


if __name__ == "__main__":
    main()
