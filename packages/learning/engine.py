"""The v2 learning loop: frozen forward hypotheses judged by always-valid evidence. Simulated only.

Stages: a new hypothesis passes a power check before it spends any of the error budget, then runs
in SHADOW on sessions after it was frozen. It moves to PROBATION (one paper lot in the probation
account) once its shadow evidence reaches 1/alpha for its own budget slice, with at least 20
trades, and beats its baseline (the same trade taken without the rule's filter). It is APPROVED
after ten paper trades that do not trail their own shadow results by much, and only if it beats
the family's current champion over the same sessions. Clear evidence of losing (futility), a hard
loss limit or a losing streak demotes or retires it; a demoted champion rolls back to the one it
replaced. Backtests never promote anything. Baselines are scored but never promoted and spend no
budget. Overrides are the owner's: a probation runner may trade them in paper before they earn it,
but their stage moves only on evidence.

APPROVED clears a hypothesis for paper trading; this module places nothing.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from statistics import mean
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from packages.backtesting.candidate_algorithms import CURRENT_LOT
from packages.backtesting.published_strategies import Market, TradeResult
from packages.backtesting.rich_premium import WING_POINTS, run_rich_condor
from packages.backtesting.rule_engine import EngineRule
from packages.database.models import (
    LearningEvent,
    LearningHypothesis,
    LearningObservation,
    PaperTrade,
)
from packages.learning.budget import alpha_for, remaining_after
from packages.learning.evidence import Trade, futility_evidence, growth_evidence
from packages.learning.features import (
    Context,
    Features,
    passes,
    session_features,
    validate,
)
from packages.learning.power import power_check
from packages.paper_trading.demotion import as_utc
from packages.paper_trading.night_condor import paper_name
from packages.paper_trading.session import exchange_today

POLICY_ID = "learning-v2"
MIN_TRADES = 20
# Dropping a rule that does not lose only costs a missed idea, so this bar is looser than 1/alpha.
FUTILITY_BAR = 10.0
PROBATION_TRADES = 10
# Paper may trail shadow over the same sessions by up to half of shadow's average.
PAPER_GAP_SHARE = 0.5
MAX_TRADE_LOSS = 20_000.0
MAX_DRAWDOWN = 60_000.0
DEMOTE_LOSS_STREAK = 5
# Exit spreads can cost a little more than the structure's width; known before entry.
LOSS_SLACK = 1_000.0
_CENT = Decimal("0.01")
Origin = Literal["HUMAN", "GRID", "LLM"]
Source = Literal["SHADOW", "PAPER"]


@dataclass(frozen=True)
class Hypothesis:
    key: str
    family: str
    kind: str
    params: dict[str, Any]
    forward_from: date
    origin: Origin
    rationale: str
    # expected_mean, expected_sd and max_loss in rupees per trade, trades_per_session.
    preregistration: dict[str, float]
    override: bool = False
    baseline_key: str | None = None
    baseline: bool = False


@dataclass(frozen=True)
class Kind:
    run: Callable[[dict[str, Any], Market], list[TradeResult]]
    # Worst loss and best gain of one result, from what was known when it was entered.
    bounds: Callable[[dict[str, Any], TradeResult], tuple[float, float]]


def _rich_condor(params: dict[str, Any], market: Market) -> list[TradeResult]:
    return run_rich_condor(params["short_delta"], params["threshold"], params["hold_overnight"],
                           0, time.fromisoformat(params["entry_time"]),
                           params["into_expiry"])(market)


def _condor_bounds(params: dict[str, Any], result: TradeResult) -> tuple[float, float]:
    return (WING_POINTS * CURRENT_LOT - result.credit + result.charges + LOSS_SLACK,
            max(result.credit, 1.0))


KINDS: dict[str, Kind] = {"rich_condor": Kind(_rich_condor, _condor_bounds)}


def load_hypotheses(path: Path) -> list[Hypothesis]:
    """Read the private hypothesis file; expected effects stay out of the public repository."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    found = []
    for row in rows:
        if row["kind"] not in KINDS or row["origin"] not in ("HUMAN", "GRID", "LLM"):
            raise ValueError(f"{row.get('key')}: unknown kind or origin")
        validate(row["params"].get("filters", []))
        found.append(Hypothesis(
            key=row["key"], family=row["family"], kind=row["kind"], params=row["params"],
            forward_from=date.fromisoformat(row["forward_from"]), origin=row["origin"],
            rationale=row["rationale"], preregistration=row.get("preregistration", {}),
            override=row.get("override", False), baseline_key=row.get("baseline_key"),
            baseline=row.get("baseline", False),
        ))
    if len({item.key for item in found}) != len(found):
        raise ValueError("Hypothesis keys must be unique")
    return found


def _money(value: float) -> Decimal:
    return Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)


def _stamp(session: Session, now: datetime) -> datetime:
    """`now`, or a microsecond after this session's last event, so the trail never ties."""
    last = session.info.get("learning_last_event")
    stamp = now if last is None or now > last else last + timedelta(microseconds=1)
    session.info["learning_last_event"] = stamp
    return stamp


def _event(session: Session, row: LearningHypothesis, event_type: str, reason: str,
           summary: str, details: dict[str, Any], now: datetime) -> None:
    session.add(LearningEvent(
        hypothesis_id=row.id, key=row.key, event_type=event_type, reason=reason,
        summary=summary[:256], details={"policy_id": POLICY_ID, **details},
        occurred_at=_stamp(session, now),
    ))


def _move(session: Session, row: LearningHypothesis, stage: str, event_type: str, reason: str,
          summary: str, details: dict[str, Any], now: datetime) -> str:
    row.stage, row.stage_since = stage, now
    _event(session, row, event_type, reason, summary, details, now)
    return f"{row.key}: {event_type.lower().replace('_', ' ')} ({reason})"


def _frozen(hypothesis: Hypothesis) -> dict[str, Any]:
    return {"family": hypothesis.family, "kind": hypothesis.kind, "params": hypothesis.params,
            "forward_from": hypothesis.forward_from.isoformat(),
            "baseline_key": hypothesis.baseline_key}


def register(session: Session, hypothesis: Hypothesis, now: datetime) -> LearningHypothesis:
    row = session.scalar(select(LearningHypothesis).where(
        LearningHypothesis.key == hypothesis.key))
    if row is not None:
        saved = {"family": row.family, "kind": row.kind, "params": row.params,
                 "forward_from": row.forward_from.isoformat(), "baseline_key": row.baseline_key}
        if saved != _frozen(hypothesis):
            # A frozen hypothesis cannot change; a changed rule needs a new key.
            raise ValueError(f"{hypothesis.key} differs from its frozen record")
        row.override = hypothesis.override
        return row
    row = LearningHypothesis(
        key=hypothesis.key, family=hypothesis.family, kind=hypothesis.kind,
        params=hypothesis.params, forward_from=hypothesis.forward_from, origin=hypothesis.origin,
        override=hypothesis.override, baseline_key=hypothesis.baseline_key,
        rationale=hypothesis.rationale, preregistration=hypothesis.preregistration,
        stage="BASELINE", stage_since=now, latest_check={}, created_at=now,
    )
    session.add(row)
    session.flush()
    if hypothesis.baseline:
        _event(session, row, "REGISTERED", "BASELINE", "Scored for comparison only", {}, now)
        return row
    spent = session.scalar(select(func.max(LearningHypothesis.budget_sequence))) or 0
    alpha = alpha_for(spent + 1)
    prereg = hypothesis.preregistration
    try:
        power = power_check(prereg["expected_mean"], prereg["expected_sd"], prereg["max_loss"],
                            prereg["trades_per_session"], alpha)
    except (KeyError, ValueError) as error:
        row.stage = "UNDERPOWERED"
        _event(session, row, "REFUSED", "NO_PREREGISTRATION",
               f"No usable pre-registration: {error}", {}, now)
        return row
    details = {"trades_needed": round(power.trades_needed, 1),
               "sessions_needed": round(power.sessions_needed, 1), "alpha": alpha}
    if not power.feasible and not hypothesis.override:
        row.stage = "UNDERPOWERED"
        _event(session, row, "REFUSED", "UNDERPOWERED",
               f"Needs about {power.sessions_needed:,.0f} sessions to show its effect", details,
               now)
        return row
    row.stage, row.budget_sequence, row.alpha = "SHADOW", spent + 1, alpha
    _event(session, row, "REGISTERED", "FROZEN",
           f"Shadow from {hypothesis.forward_from.isoformat()}, budget slice {alpha:.2e}",
           details, now)
    return row


def record_observation(
    session: Session, row: LearningHypothesis, day: date, source: Source, net_pnl: float,
    max_loss: float, max_gain: float, now: datetime,
) -> bool:
    """Add one forward trade unless that session is already recorded; True when added."""
    known = session.scalar(select(LearningObservation.id).where(
        LearningObservation.hypothesis_id == row.id, LearningObservation.session == day,
        LearningObservation.source == source))
    if known is not None:
        return False
    # A result past its bound would void the test, so widen it; this should never happen.
    max_loss, max_gain = max(max_loss, -net_pnl + 1.0), max(max_gain, net_pnl + 1.0)
    session.add(LearningObservation(
        hypothesis_id=row.id, session=day, source=source, net_pnl=_money(net_pnl),
        max_loss=_money(max_loss), max_gain=_money(max_gain), recorded_at=now,
    ))
    return True


def link_paper_trades(session: Session, now: datetime, version: int = 1) -> int:
    """Record each closed paper trade of a hypothesis as a PAPER observation.

    The night runner names its trades with `paper_name`; a hypothesis key equals the rule key it
    traded, under either name in case the owner's override was added or lifted. Returns how many
    were added.
    """
    added = 0
    for row in session.scalars(select(LearningHypothesis)):
        names = [paper_name(row.key, version, override) for override in (True, False)]
        trades = session.scalars(select(PaperTrade).where(
            PaperTrade.strategy_name.in_(names),
            PaperTrade.status == "CLOSED", PaperTrade.realized_pnl.is_not(None)))
        for trade in trades:
            credit = max(float(trade.entry_cash_flow), 1.0)
            added += record_observation(
                session, row, exchange_today(as_utc(trade.opened_at)), "PAPER",
                float(trade.realized_pnl or 0), float(trade.max_loss) + LOSS_SLACK, credit, now)
    session.flush()
    return added


@dataclass
class _State:
    row: LearningHypothesis
    shadow: list[LearningObservation] = field(default_factory=list)
    paper: list[LearningObservation] = field(default_factory=list)


def _trades(observations: Sequence[LearningObservation]) -> list[Trade]:
    return [Trade(float(item.net_pnl), float(item.max_loss), float(item.max_gain))
            for item in observations]


def _values(observations: Sequence[LearningObservation]) -> list[float]:
    return [float(item.net_pnl) for item in observations]


def breach(values: Sequence[float]) -> str | None:
    running = peak = 0.0
    for value in values:
        if value < -MAX_TRADE_LOSS:
            return "WORST_TRADE"
        running += value
        peak = max(peak, running)
        if peak - running > MAX_DRAWDOWN:
            return "DRAWDOWN"
    return None


def _streak(values: Sequence[float]) -> int:
    longest = current = 0
    for value in values:
        current = current + 1 if value < 0 else 0
        longest = max(longest, current)
    return longest


def run_learning(
    session: Session, market: Market, now: datetime, hypotheses: Sequence[Hypothesis],
    context: Context | None = None,
) -> list[str]:
    """Record new shadow trades and apply every due decision. The caller commits. Rerunnable."""
    states: dict[str, _State] = {}
    features: dict[str, dict[date, Features]] = {}
    for hypothesis in hypotheses:
        row = register(session, hypothesis, now)
        if row.stage != "UNDERPOWERED":
            kind = KINDS[row.kind]
            filters = row.params.get("filters", [])
            entry = row.params.get("entry_time", "09:20")
            if filters and entry not in features:
                features[entry] = session_features(market, time.fromisoformat(entry),
                                                   context or Context())
            for result in kind.run(row.params, market):
                if filters and not passes(filters, features[entry].get(result.session, {})):
                    continue
                if result.session >= row.forward_from:
                    loss, gain = kind.bounds(row.params, result)
                    record_observation(session, row, result.session, "SHADOW", result.net_pnl,
                                       loss, gain, now)
        states[row.key] = _State(row)
    session.flush()
    for state in states.values():
        observed = session.scalars(select(LearningObservation).where(
            LearningObservation.hypothesis_id == state.row.id,
        ).order_by(LearningObservation.session)).all()
        state.shadow = [item for item in observed if item.source == "SHADOW"]
        state.paper = [item for item in observed if item.source == "PAPER"]
    decisions: list[str] = []
    families = sorted({state.row.family for state in states.values()})
    for family in families:
        members = [state for state in states.values() if state.row.family == family]
        decisions += _decide(session, members, states, now)
    session.flush()
    return decisions


def _same_sessions(observations: Sequence[LearningObservation], days: set[date]) -> list[float]:
    return [float(item.net_pnl) for item in observations if item.session in days]


def _decide(session: Session, members: list[_State], states: dict[str, _State],
            now: datetime) -> list[str]:
    decisions: list[str] = []

    def champion() -> _State | None:
        return next((state for state in members if state.row.stage == "APPROVED"), None)

    current = champion()
    if current is not None:
        since = current.row.stage_since.date()
        after = [item for item in (current.paper or current.shadow) if item.session > since]
        values = _values(after)
        reason = (breach(values) or ("LOSS_STREAK" if _streak(values) >= DEMOTE_LOSS_STREAK
                  else None) or ("FUTILITY" if futility_evidence(_trades(after)) >= FUTILITY_BAR
                  else None))
        if reason:
            decisions.append(_move(session, current.row, "DEMOTED", "DEMOTED", reason,
                                   f"{reason.replace('_', ' ').lower()}, {len(after)} trade(s) "
                                   "since approval", {"trades": len(after)}, now))
            fallback = max((state for state in members if state.row.stage == "SUPERSEDED"),
                           key=lambda state: state.row.stage_since, default=None)
            if fallback is not None:
                decisions.append(_move(session, fallback.row, "APPROVED", "ROLLED_BACK",
                                       "NEWER_DEMOTED", f"Back after {current.row.key} was "
                                       "demoted", {"demoted": current.row.key}, now))
    for state in members:
        row = state.row
        if row.stage in ("SHADOW", "PROBATION"):
            decisions += _judge(session, state, states, champion(), now)
    return decisions


def _judge(session: Session, state: _State, states: dict[str, _State],
           incumbent: _State | None, now: datetime) -> list[str]:
    row = state.row
    shadow, values = _trades(state.shadow), _values(state.shadow)
    growth, futility = growth_evidence(shadow), futility_evidence(shadow)
    bar = 1 / row.alpha if row.alpha else float("inf")
    check: dict[str, Any] = {
        "policy_id": POLICY_ID, "trades": len(values),
        "average": round(mean(values), 2) if values else None,
        "evidence": round(growth, 3), "bar": round(bar, 1), "futility": round(futility, 3),
        "paper_trades": len(state.paper), "needs": [],
    }
    if state.paper:
        # The gap between simulated and filled results on the same sessions.
        twin_days = {item.session for item in state.paper}
        twin = _same_sessions(state.shadow, twin_days)
        check["paper_vs_shadow"] = {
            "sessions": len(twin_days), "paper_average": round(mean(_values(state.paper)), 2),
            "shadow_average": round(mean(twin), 2) if twin else None,
        }
    row.latest_check, row.checked_at = check, now
    limit = breach(values)
    if limit or futility >= FUTILITY_BAR:
        reason = limit or "FUTILITY"
        stage = "RETIRED" if row.stage == "SHADOW" else "DEMOTED"
        return [_move(session, row, stage, stage if stage == "RETIRED" else "DEMOTED", reason,
                      f"{reason.replace('_', ' ').lower()} after {len(values)} shadow trade(s)",
                      check, now)]
    if row.stage == "SHADOW":
        needs = []
        if len(values) < MIN_TRADES:
            needs.append(f"{MIN_TRADES - len(values)} more shadow trade(s)")
        if growth < bar:
            needs.append(f"evidence {growth:,.1f} of {bar:,.0f}")
        if row.baseline_key:
            base = states.get(row.baseline_key)
            rival = _values(base.shadow) if base else []
            if not rival:
                needs.append("baseline results")
            elif values and mean(values) <= mean(rival):
                needs.append("a higher average than its baseline")
        check["needs"] = needs
        if needs:
            return []
        return [_move(session, row, "PROBATION", "TO_PROBATION", "FORWARD_EVIDENCE",
                      f"{len(values)} shadow trades, evidence {growth:,.0f} of {bar:,.0f}",
                      check, now)]
    paper = _values(state.paper)
    paper_limit = breach(paper) or ("FUTILITY" if futility_evidence(_trades(state.paper))
                                    >= FUTILITY_BAR else None)
    if paper_limit:
        return [_move(session, row, "DEMOTED", "DEMOTED", paper_limit,
                      f"{paper_limit.replace('_', ' ').lower()} in paper probation", check, now)]
    if len(paper) < PROBATION_TRADES:
        check["needs"] = [f"{PROBATION_TRADES - len(paper)} more paper trade(s)"]
        return []
    days = {item.session for item in state.paper}
    twin = _same_sessions(state.shadow, days)
    expected = mean(twin) if twin else 0.0
    if mean(paper) <= 0 or mean(paper) < expected - PAPER_GAP_SHARE * abs(expected):
        return [_move(session, row, "DEMOTED", "DEMOTED", "PAPER_GAP",
                      f"Paper average ₹{mean(paper):,.0f} against shadow ₹{expected:,.0f}",
                      check, now)]
    if incumbent is not None:
        rival = _same_sessions(incumbent.shadow, {item.session for item in state.shadow})
        if rival and mean(rival) >= mean(values):
            check["needs"] = [f"a higher average than {incumbent.row.key} over the same sessions"]
            return []
        _move(session, incumbent.row, "SUPERSEDED", "SUPERSEDED", "CHALLENGER_APPROVED",
              f"Replaced by {row.key}", {"by": row.key}, now)
    return [_move(session, row, "APPROVED", "APPROVED", "PAPER_CONFIRMED",
                  f"{len(paper)} paper trades averaging ₹{mean(paper):,.0f}", check, now)]


TRADED_STAGES = frozenset({"PROBATION", "APPROVED"})


def tradable_rules(session: Session) -> list[EngineRule]:
    """What the probation runner may paper trade: hypotheses in probation or approved, and the
    owner's overrides until evidence stops them. Only rules the night runner can exit (held to the
    next session) qualify; anything else fails closed."""
    rules = []
    for row in session.scalars(select(LearningHypothesis).where(
            LearningHypothesis.kind == "rich_condor").order_by(LearningHypothesis.key)):
        if not (row.stage in TRADED_STAGES or (row.override and row.stage == "SHADOW")):
            continue
        if row.params.get("hold_overnight") and row.params.get("into_expiry"):
            rules.append(EngineRule(row.key, 1, row.forward_from, row.params, row.override))
    return rules


def budget_summary(session: Session) -> dict[str, Any]:
    spent = session.scalar(select(func.max(LearningHypothesis.budget_sequence))) or 0
    return {"hypotheses_tested": spent, "remaining_alpha": remaining_after(spent),
            "next_slice": alpha_for(spent + 1)}


def hypotheses(session: Session) -> list[LearningHypothesis]:
    return list(session.scalars(select(LearningHypothesis).order_by(
        LearningHypothesis.family, LearningHypothesis.created_at)))


def events(session: Session, limit: int = 100) -> list[LearningEvent]:
    return list(session.scalars(select(LearningEvent).order_by(
        LearningEvent.occurred_at.desc(), LearningEvent.id.desc()).limit(limit)))
