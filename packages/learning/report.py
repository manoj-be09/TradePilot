"""The weekly evidence report, written privately each Friday night. Simulated, not advice.

It lists the error budget, every hypothesis's stage, forward evidence and what it still needs,
the paper-vs-shadow gap, the week's decisions, the current cost model and, from all closed
paper trades in the last 30 days, the strategies whose stop or target sits inside the entry
spread, in
`work/learning/reports/YYYY-MM-DD.md`. The folder is private: results stay out of the repository.
"""

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.database.models import LearningEvent
from packages.learning.engine import budget_summary, hypotheses
from packages.learning.execution import SPREAD_MULTIPLE, current, frictions

REPORTS = Path("work/learning/reports")
REPORT_WEEKDAY = 4
IST = ZoneInfo("Asia/Kolkata")


def _money(value: Any) -> str:
    return "n/a" if value is None else f"₹{float(value):,.0f}"


def weekly_report(session: Session, today: date, cost: dict[str, Any] | None) -> str:
    budget = budget_summary(session)
    lines = [
        f"# Learning loop, week to {today.isoformat()}", "",
        "Simulated paper research. Nothing here is a forecast or investment advice.", "",
        f"- Ideas tested: {budget['hypotheses_tested']}; error budget left "
        f"{budget['remaining_alpha']:.4%} of 5%; next slice {budget['next_slice']:.2e}.",
        "- Cost model: " + (f"v{cost['version']} from {cost['pairs']} paper fills, "
                            f"{_money(cost['slippage_per_trade'])} a trade" if cost
                            else "not calibrated yet"),
        "", "## Hypotheses", "",
        "| Idea | Origin | Stage | Trades | Average | Evidence / bar | Paper vs shadow | Needs |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in hypotheses(session):
        check = row.latest_check or {}
        gap = check.get("paper_vs_shadow")
        twin = (f"{_money(gap['paper_average'])} vs {_money(gap['shadow_average'])} "
                f"({gap['sessions']})" if gap else "none")
        bar = check.get("bar")
        evidence = (f"{check['evidence']:.1f} / {bar:,.0f}" if "evidence" in check
                    and bar is not None else "n/a")
        lines.append(f"| {row.key} | {row.origin}{' (override)' if row.override else ''} | "
                     f"{row.stage} | {check.get('trades', 0)} | {_money(check.get('average'))} | "
                     f"{evidence} | {twin} | {'; '.join(check.get('needs', [])) or '-'} |")
    since = datetime.combine(today - timedelta(days=6), time.min, IST)
    week = session.scalars(select(LearningEvent).where(LearningEvent.occurred_at >= since)
                           .order_by(LearningEvent.occurred_at)).all()
    lines += ["", "## Decisions this week", ""]
    lines += [f"- {event.key}: {event.event_type.lower().replace('_', ' ')} ({event.reason}) "
              f"{event.summary}" for event in week] or ["- none"]
    lines += ["", "## Execution friction, closed paper trades in the last 30 days", "",
              f"A stop or target under {SPREAD_MULTIPLE:g}× the entry spread is decided by the "
              "spread. Quick exits closed within 10 minutes.", "",
              "| Strategy | Trades | Median entry spread | Stop | Target | Quick exits "
              "| Too tight |",
              "|---|---|---|---|---|---|---|"]
    lines += [f"| {row['strategy']} | {row['trades']} | {_money(row['median_spread'])} | "
              f"{_money(row['stop'])} | {_money(row['target'])} | {row['quick_exits']} | "
              f"{', '.join(row['tight']) or '-'} |"
              for row in frictions(session, today - timedelta(days=30))] or ["| none |"]
    return "\n".join(lines) + "\n"


def write_weekly(session: Session, today: date, folder: Path = REPORTS) -> Path | None:
    if today.weekday() != REPORT_WEEKDAY:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{today.isoformat()}.md"
    path.write_text(weekly_report(session, today, current()), encoding="utf-8")
    return path
