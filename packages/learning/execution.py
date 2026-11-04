"""Execution learning: how far paper fills trail the simulation, recalibrated weekly. Simulated.

Each Friday night the median gap between paper and shadow results on the same sessions (last 60
days, at least five pairs) becomes a new version of the cost model in the private
`work/learning/cost-model.json`. Versions are append-only. A new machine idea is frozen with the
latest version's slippage in its params (`cost_version`, `slippage`), and its shadow results are
charged that amount per trade for life, so a recalibration never changes evidence already counted.

`frictions` reads the same lesson off every closed paper trade: a stop or target smaller than
about twice the bid/ask spread paid at entry is decided by the spread, not the market.
"""

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from packages.database.models import LearningObservation, PaperTrade

COST_MODEL = Path("work/learning/cost-model.json")
WINDOW = timedelta(days=60)
MIN_PAIRS = 5
CALIBRATION_WEEKDAY = 4
SPREAD_MULTIPLE = 2.0
QUICK_EXIT = timedelta(minutes=10)


def paper_gaps(session: Session, since: date) -> list[float]:
    """Paper minus shadow on the same hypothesis and session."""
    paper, shadow = aliased(LearningObservation), aliased(LearningObservation)
    rows = session.execute(
        select(paper.net_pnl, shadow.net_pnl)
        .join(shadow, (shadow.hypothesis_id == paper.hypothesis_id)
              & (shadow.session == paper.session) & (shadow.source == "SHADOW"))
        .where(paper.source == "PAPER", paper.session >= since))
    return [float(filled) - float(simulated) for filled, simulated in rows]


def versions(path: Path = COST_MODEL) -> list[dict[str, Any]]:
    return list(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else []


def current(path: Path = COST_MODEL) -> dict[str, Any] | None:
    found = versions(path)
    return found[-1] if found else None


def calibrate(session: Session, today: date, path: Path = COST_MODEL) -> dict[str, Any] | None:
    """Add this week's version on Fridays when there is enough evidence; returns it."""
    found = versions(path)
    if today.weekday() != CALIBRATION_WEEKDAY or (found and found[-1]["made"] == str(today)):
        return None
    gaps = paper_gaps(session, today - WINDOW)
    if len(gaps) < MIN_PAIRS:
        return None
    # Paper doing better than shadow is luck, not a negative cost.
    entry = {"version": len(found) + 1, "made": str(today), "pairs": len(gaps),
             "median_gap": round(median(gaps), 2),
             "slippage_per_trade": round(max(0.0, -median(gaps)), 2)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([*found, entry], indent=2) + "\n", encoding="utf-8")
    return entry


def pinned(params: dict[str, Any], model: dict[str, Any] | None) -> dict[str, Any]:
    """Params with the cost model frozen in; unchanged when there is none yet."""
    if model is None:
        return params
    return {**params, "cost_version": model["version"], "slippage": model["slippage_per_trade"]}


def entry_spread(legs: list[dict[str, Any]]) -> float | None:
    """Rupees of bid/ask spread across a trade's legs at entry; None if a quote is missing."""
    try:
        return sum((float(leg["entry_ask"]) - float(leg["entry_bid"])) * int(leg["quantity_units"])
                   for leg in legs)
    except (KeyError, TypeError, ValueError):
        return None


def frictions(session: Session, since: date) -> list[dict[str, Any]]:
    """Per strategy: median entry spread, quick exits, and whether a stop or target is too tight."""
    trades = session.scalars(select(PaperTrade).where(
        PaperTrade.status == "CLOSED", PaperTrade.opened_at >= datetime.combine(since, time.min)))
    by_key: dict[str, list[PaperTrade]] = {}
    for trade in trades:
        by_key.setdefault(trade.strategy_key, []).append(trade)
    rows = []
    for key, group in sorted(by_key.items()):
        spreads = [value for trade in group
                   if (value := entry_spread(trade.legs.get("items", []))) is not None]
        if not spreads:
            continue
        spread = median(spreads)
        stop = median(float(trade.stop_loss_amount or 0) for trade in group)
        target = median(float(trade.target_profit_amount or 0) for trade in group)
        tight = [name for name, value in (("stop", stop), ("target", target))
                 if 0 < value < SPREAD_MULTIPLE * spread]
        rows.append({"strategy": key, "trades": len(group), "median_spread": round(spread, 2),
                     "stop": stop, "target": target,
                     "quick_exits": sum(trade.closed_at is not None
                                        and trade.closed_at - trade.opened_at < QUICK_EXIT
                                        for trade in group),
                     "tight": tight})
    return rows
