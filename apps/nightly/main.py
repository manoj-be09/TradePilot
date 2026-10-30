"""Run with python -m apps.nightly.main; after each close it backs up and checks the paper run.

Each day after 16:30 IST it makes a verified database backup, checks the paper ledger and
writes a forward report to work/forward-reports. A failed step is logged and the next step
still runs. A ledger mismatch already pauses new paper cash actions by itself; this job
only reads the account, and places no orders.
"""

import argparse
import logging
import sqlite3
import time as clock
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from packages.database.models import PaperAccount, User
from packages.database.session import build_session_factory
from packages.database.sqlite_backup import BackupError, backup_database
from packages.paper_trading.forward_plan import ForwardPlan, load_plan
from packages.paper_trading.forward_report import build_report, render_markdown

logger = logging.getLogger("nightly")
IST = ZoneInfo("Asia/Kolkata")
RUN_AFTER = time(16, 30)
DATABASE = Path("work/nifty-local.db")
BACKUPS = Path("work/backups")
REPORTS = Path("work/forward-reports")
DEFAULT_PLAN = Path("work/forward-paper.json")


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


def run_once(
    factory: sessionmaker[Session], plan: ForwardPlan, database: Path, backups: Path,
    reports: Path, now: datetime,
) -> dict[str, str]:
    results: dict[str, str] = {}
    try:
        if now.astimezone(IST).time() < RUN_AFTER:
            # Its read lock would block the worker's writes while positions need managing.
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
    if results["ledger"] != "MATCHED":
        logger.error("Ledger needs review: %s", results["ledger"])
    return results


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    plan = load_plan(args.plan)
    factory = build_session_factory()
    last_run: date | None = None
    while True:
        now = datetime.now(timezone.utc)
        if args.once or due(now, last_run):
            results = run_once(factory, plan, DATABASE, BACKUPS, REPORTS, now)
            logger.info("Nightly: %s", results)
            last_run = now.astimezone(IST).date()
        if args.once or last_run is not None and last_run > plan.end:
            return
        clock.sleep(300)


if __name__ == "__main__":
    main()
