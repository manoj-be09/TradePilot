"""The idea generator: GRID neighbours and LLM-written hypotheses, each pre-registered. Simulated.

GRID takes the night's best shadow rules (enough trades, a positive average) and declares their
neighbours: one parameter moved one step either way. A batch is dropped when its variants'
backtests overfit (CSCV probability above one half). LLM ideas come from Claude once a week, from
a description of the parameter space and aggregate shadow results only; every proposal is checked
against the allowed ranges before it is kept. Both are frozen from the next session, carry their
rationale and pre-registered effect, and meet the reject-only screen like any machine idea. The
owner decided on 3 November 2026 that they need no approval. Nothing here trades.
"""

import json
import logging
import os
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.backtesting.published_strategies import Market
from packages.database.models import LearningHypothesis, LearningObservation
from packages.learning.engine import KINDS, MIN_TRADES, Hypothesis
from packages.learning.features import validate
from packages.learning.screen import PBO_BAR, overfit_probability

logger = logging.getLogger("learning.ideas")
GENERATED = Path("work/learning/generated.json")
MAX_GRID_A_NIGHT = 6
MAX_LLM_A_WEEK = 3
LLM_WEEKDAY = 4  # Friday night, after the week's sessions.
LLM_MODEL = "claude-opus-5-5"
# Neighbours rarely do as well as the rule they came from; expect half its forward average.
SHRINK = 0.5
RANGES: dict[str, tuple[float, float]] = {"short_delta": (0.05, 0.35), "threshold": (0.6, 2.0)}
FIRST_ENTRY, LAST_ENTRY = "09:20", "10:30"
STEPS: dict[str, float] = {"short_delta": 0.05, "threshold": 0.1, "entry_minutes": 10}
ParentStats = dict[str, Any]


def _entry(value: str, minutes: int) -> str | None:
    moment = datetime.strptime(value, "%H:%M") + timedelta(minutes=minutes)
    shifted = moment.strftime("%H:%M")
    return shifted if FIRST_ENTRY <= shifted <= LAST_ENTRY else None


def in_range(params: dict[str, Any]) -> bool:
    try:
        validate(params.get("filters", []))
        entry = params["entry_time"]
        return (all(low <= float(params[name]) <= high for name, (low, high) in RANGES.items())
                and FIRST_ENTRY <= entry <= LAST_ENTRY and _entry(entry, 0) == entry
                and int(entry[3:]) % 5 == 0
                and isinstance(params["hold_overnight"], bool)
                and isinstance(params["into_expiry"], bool))
    except (KeyError, TypeError, ValueError):
        return False


def neighbours(params: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """One parameter one step either way, inside the allowed ranges."""
    found = []
    for name in ("short_delta", "threshold"):
        for sign in (-1, 1):
            value = round(float(params[name]) + sign * STEPS[name], 2)
            changed = {**params, name: value}
            if in_range(changed):
                found.append((f"{name}{value:g}", changed))
    for sign in (-1, 1):
        shifted = _entry(params["entry_time"], sign * int(STEPS["entry_minutes"]))
        if shifted is not None:
            found.append((f"at{shifted.replace(':', '')}", {**params, "entry_time": shifted}))
    return found


def parent_stats(session: Session) -> list[ParentStats]:
    """Shadow rules with enough forward trades and a positive average, best evidence first."""
    found = []
    for row in session.scalars(select(LearningHypothesis).where(
            LearningHypothesis.stage.in_(("SHADOW", "PROBATION", "APPROVED")),
            LearningHypothesis.kind == "rich_condor")):
        values = [float(value) for value in session.scalars(select(LearningObservation.net_pnl)
                  .where(LearningObservation.hypothesis_id == row.id,
                         LearningObservation.source == "SHADOW"))]
        if len(values) >= MIN_TRADES and mean(values) > 0:
            found.append({"row": row, "trades": len(values), "average": mean(values),
                          "sd": pstdev(values), "evidence": row.latest_check.get("evidence", 0)})
    return sorted(found, key=lambda item: -float(item["evidence"]))


def _matrix(params: Sequence[dict[str, Any]], market: Market, before: date) -> list[list[float]]:
    sessions = [day for day in market.sessions if day < before]
    rows = []
    for item in params:
        by_day = {result.session: result.net_pnl for result in KINDS["rich_condor"].run(
            item, market)}
        rows.append([by_day.get(day, 0.0) for day in sessions])
    return rows


def grid_ideas(session: Session, market: Market, today: date, taken: set[str]) -> list[Hypothesis]:
    ideas: list[Hypothesis] = []
    start = today + timedelta(days=1)
    for parent in parent_stats(session):
        row = parent["row"]
        batch = [(f"{row.key}__{suffix}", changed) for suffix, changed in neighbours(row.params)
                 if f"{row.key}__{suffix}" not in taken]
        if not batch:
            continue
        overfit = overfit_probability(_matrix([row.params] + [p for _, p in batch], market, start))
        if overfit > PBO_BAR:
            logger.warning("GRID batch around %s dropped: overfit probability %.2f", row.key,
                           overfit)
            continue
        days = max(1, len([day for day in market.sessions if day >= row.forward_from]))
        prereg = {**row.preregistration, "expected_mean": parent["average"] * SHRINK,
                  "expected_sd": max(parent["sd"], 1.0),
                  "trades_per_session": min(1.0, parent["trades"] / days)}
        for key, params in batch:
            ideas.append(Hypothesis(
                key=key, family=row.family, kind="rich_condor", params=params,
                forward_from=start, origin="GRID", preregistration=prereg,
                rationale=f"Neighbour of {row.key} ({parent['trades']} shadow trades), "
                          f"overfit probability {overfit:.2f}",
                baseline_key=row.baseline_key))
            if len(ideas) >= MAX_GRID_A_NIGHT:
                return ideas
    return ideas


_SCHEMA = {
    "type": "object",
    "properties": {"ideas": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "rationale": {"type": "string"},
            "short_delta": {"type": "number"},
            "threshold": {"type": "number"},
            "entry_time": {"type": "string"},
            "hold_overnight": {"type": "boolean"},
            "into_expiry": {"type": "boolean"},
            "filters": {"type": "array", "items": {
                "type": "object",
                "properties": {"feature": {"type": "string"}, "min": {"type": "number"},
                               "max": {"type": "number"}, "equals": {"type": "boolean"}},
                "required": ["feature"], "additionalProperties": False}},
            "expected_mean": {"type": "number"},
            "expected_sd": {"type": "number"},
            "trades_per_session": {"type": "number"},
        },
        "required": ["name", "rationale", "short_delta", "threshold", "entry_time",
                     "hold_overnight", "into_expiry", "filters", "expected_mean", "expected_sd",
                     "trades_per_session"],
        "additionalProperties": False}}},
    "required": ["ideas"],
    "additionalProperties": False,
}

_PROMPT = """You propose research hypotheses for a simulated NIFTY options paper-trading study.
Nothing you propose is traded with real money; each idea is pre-registered, screened on a backtest
that can only reject it, and judged on forward paper results under a strict error budget.

The one strategy family is an iron condor (one lot, 300-point wings) sold when the at-the-money
straddle is "rich": its implied move over a forecast from recent realised movement. Parameters:
- short_delta: delta of the sold strikes, {delta_low}-{delta_high}
- threshold: minimum richness to trade, {threshold_low}-{threshold_high}
- entry_time: "HH:MM" IST in 5-minute steps, {first}-{last}
- hold_overnight: hold to the next session's 15:10 instead of closing the same day
- into_expiry: allow holding into expiry day
- filters: conditions known before entry, each {{"feature", and "min"/"max" or "equals"}} over:
  realised_vol (annualised, last 5 sessions), vix (previous close), term_gap (weekly over next
  expiry implied vol), skew (2% put over 2% call price), event_today, event_next (booleans).

Current rules and their forward shadow results (simulated, rupees per trade):
{summary}

Propose up to {count} new, distinct hypotheses with a clear economic rationale each, not small
tweaks of an existing rule. Give honest pre-registered effects: expected_mean and expected_sd in
rupees per trade, and trades_per_session (0-1). Overstating the effect only wastes the budget."""


def _summary(session: Session) -> str:
    lines = []
    for row in session.scalars(select(LearningHypothesis).order_by(LearningHypothesis.key)):
        check = row.latest_check or {}
        lines.append(f"- {row.key} [{row.stage}] {json.dumps(row.params, sort_keys=True)}: "
                     f"{check.get('trades', 0)} trades, average {check.get('average')}")
    return "\n".join(lines) or "- none yet"


def _anthropic_client() -> Any | None:
    """The SDK client when installed and configured; LLM ideas stay off otherwise."""
    try:
        import anthropic
    except ImportError:
        return None
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return None
    return anthropic.Anthropic()


def llm_ideas(session: Session, today: date, taken: set[str],
              client_factory: Callable[[], Any | None] = _anthropic_client) -> list[Hypothesis]:
    client = client_factory()
    if client is None:
        logger.info("LLM ideas are off: no Anthropic SDK or credentials")
        return []
    prompt = _PROMPT.format(
        delta_low=RANGES["short_delta"][0], delta_high=RANGES["short_delta"][1],
        threshold_low=RANGES["threshold"][0], threshold_high=RANGES["threshold"][1],
        first=FIRST_ENTRY, last=LAST_ENTRY, summary=_summary(session), count=MAX_LLM_A_WEEK)
    response = client.beta.messages.create(
        model=LLM_MODEL, max_tokens=16000, betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": _SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason != "end_turn":
        logger.warning("LLM ideas skipped: stop reason %s", response.stop_reason)
        return []
    text = next(block.text for block in response.content if block.type == "text")
    ideas = []
    start = today + timedelta(days=1)
    for item in json.loads(text)["ideas"][:MAX_LLM_A_WEEK]:
        params = {name: item[name] for name in ("short_delta", "threshold", "entry_time",
                                                "hold_overnight", "into_expiry")}
        if item["filters"]:
            params["filters"] = item["filters"]
        slug = "".join(char if char.isalnum() else "_" for char in item["name"].lower())[:40]
        key = f"llm_{today:%Y%m%d}_{slug}"
        if key in taken or not in_range(params):
            logger.warning("LLM idea %s refused: out of range or taken", key)
            continue
        ideas.append(Hypothesis(
            key=key, family="night", kind="rich_condor", params=params, forward_from=start,
            origin="LLM", rationale=item["rationale"][:2_000],
            preregistration={"expected_mean": float(item["expected_mean"]),
                             "expected_sd": float(item["expected_sd"]),
                             "max_loss": 20_000.0,
                             "trades_per_session": float(item["trades_per_session"])}))
    return ideas


def save_generated(path: Path, ideas: Sequence[Hypothesis]) -> None:
    """Append new ideas to the private machine file; earlier entries never change."""
    rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    for idea in ideas:
        row = asdict(idea)
        row["forward_from"] = idea.forward_from.isoformat()
        rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")


def generate(session: Session, market: Market, today: date, known: set[str],
             client_factory: Callable[[], Any | None] = _anthropic_client) -> list[Hypothesis]:
    taken = set(known) | set(session.scalars(select(LearningHypothesis.key)))
    ideas = grid_ideas(session, market, today, taken)
    taken |= {idea.key for idea in ideas}
    asked = any(key.startswith(f"llm_{today:%Y%m%d}") for key in taken)
    if today.weekday() == LLM_WEEKDAY and not asked:
        try:
            ideas += llm_ideas(session, today, taken, client_factory)
        except Exception as error:  # noqa: BLE001 - a failed call only costs this week's ideas
            logger.error("LLM ideas failed: %s", type(error).__name__)
    return ideas
