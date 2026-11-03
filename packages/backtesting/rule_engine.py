"""Research rule engine: forward-shadow evidence, promotion, demotion and rollback. Simulated.

Each frozen rule version is run every night over imported sessions from its `forward_from`
date, which is after it was frozen, so only data nobody had seen when it was fixed counts.
Those simulated trades go to an append-only shadow ledger. Backtests never count.

Stages: SHADOW -> PAPER_APPROVED (promoted) -> DEMOTED. A newer version that is promoted
replaces the current one, which becomes SUPERSEDED; if the newer one is later demoted the
engine rolls back to the latest superseded version. A version that breaks a hard loss limit in
shadow is RETIRED. Every change is appended to research_rule_events with its evidence.

Promotion needs a strict bar: a t value above the one-sided Bonferroni line for every rule
tried on this history, plus a minimum number of trades and days and the study loss limits.
The same bar is checked every night, which a fixed-sample test does not allow for; the
Bonferroni line is far stricter than that repetition needs.

PAPER_APPROVED only clears a version for paper trading in a separate paper account. This module
places no order, arms nothing and never touches a paper account. Not investment advice.
"""

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from math import sqrt
from statistics import NormalDist, mean, stdev
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.backtesting.published_strategies import Market, TradeResult
from packages.backtesting.rich_premium import run_rich_condor
from packages.database.models import ResearchRuleEvent, ResearchRuleVersion, ResearchShadowTrade

ENGINE_POLICY_ID = "research-rule-engine-v1"
# Every rule tried on this history, rounded up: about 12,400 option rules, 36 trend rules and a
# from-scratch idea on 3 November 2026, plus the 45 rules of the 1 November studies.
TRIALS_TESTED = 12_500
FAMILY_ALPHA = 0.05
T_BAR = NormalDist().inv_cdf(1 - FAMILY_ALPHA / TRIALS_TESTED)
MIN_TRADES = 20
MIN_SHADOW_DAYS = 90
# The per-trade and drawdown limits of the studies the rules came from.
MAX_TRADE_LOSS = 20_000.0
MAX_DRAWDOWN = 60_000.0
DEMOTE_LOSS_STREAK = 5
DRIFT_MIN_TRADES = 10
DRIFT_T = -2.0
_CENT = Decimal("0.01")


@dataclass(frozen=True)
class EngineRule:
    key: str
    version: int
    forward_from: date
    params: dict[str, Any]
    # Traded before it earned it, on the owner's word; its paper trades say "not promoted".
    override: bool = True

    def runner(self) -> Callable[[Market], list[TradeResult]]:
        p = self.params
        return run_rich_condor(
            p["short_delta"], p["threshold"], p["hold_overnight"], 0,
            time.fromisoformat(p["entry_time"]), p["into_expiry"],
        )


# Version 1 of each is `night_rules()` in rich_premium.py, frozen on 1 November 2026.
ENGINE_RULES = (
    EngineRule("very_rich_night_condor", 1, date(2026, 11, 2), {
        "short_delta": 0.15, "threshold": 1.1, "hold_overnight": True, "entry_time": "09:30",
        "into_expiry": True,
    }),
    EngineRule("very_rich_night_condor_920", 1, date(2026, 11, 2), {
        "short_delta": 0.2, "threshold": 1.1, "hold_overnight": True, "entry_time": "09:20",
        "into_expiry": True,
    }),
)


@dataclass(frozen=True)
class Evidence:
    trades: int
    average: float
    t_value: float | None
    worst: float
    drawdown: float
    loss_streak: int
    days: int


def evidence(values: Sequence[float], days: int = 0) -> Evidence:
    """Summary of simulated net P&L per trade, in session order."""
    if not values:
        return Evidence(0, 0.0, None, 0.0, 0.0, 0, days)
    spread = stdev(values) if len(values) > 1 else 0.0
    t_value = mean(values) / (spread / sqrt(len(values))) if spread > 0 else None
    running = peak = drawdown = 0.0
    streak = longest = 0
    for value in values:
        running += value
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
        streak = streak + 1 if value < 0 else 0
        longest = max(longest, streak)
    return Evidence(len(values), mean(values), t_value, min(values), drawdown, longest, days)


def hard_limit_breach(found: Evidence) -> str | None:
    if found.worst < -MAX_TRADE_LOSS:
        return "WORST_TRADE"
    if found.drawdown > MAX_DRAWDOWN:
        return "DRAWDOWN"
    return None


def promotion_needs(found: Evidence) -> list[str]:
    """What is still missing before a shadow version may be promoted; empty means it passes."""
    needs = []
    if found.trades < MIN_TRADES:
        needs.append(f"{MIN_TRADES - found.trades} more shadow trade(s)")
    if found.days < MIN_SHADOW_DAYS:
        needs.append(f"{MIN_SHADOW_DAYS - found.days} more day(s) in shadow")
    if found.average <= 0:
        needs.append("a positive average after charges")
    if found.t_value is None or found.t_value < T_BAR:
        needs.append(f"a t value of at least {T_BAR:.2f} ({TRIALS_TESTED:,} rules tried)")
    return needs


def demotion_reason(before: Sequence[float], after: Sequence[float]) -> str | None:
    """Why a promoted version must be demoted, judged on trades since it was promoted."""
    found = evidence(after)
    breach = hard_limit_breach(found)
    if breach:
        return breach
    if found.loss_streak >= DEMOTE_LOSS_STREAK:
        return "LOSS_STREAK"
    if len(after) >= DRIFT_MIN_TRADES and before:
        spread = stdev(after)
        if spread > 0 and (mean(after) - mean(before)) / (spread / sqrt(len(after))) <= DRIFT_T:
            return "DRIFT"
    return None


@dataclass
class _State:
    row: ResearchRuleVersion
    trades: list[ResearchShadowTrade] = field(default_factory=list)

    def values(self, since: date | None = None, until: date | None = None) -> list[float]:
        return [float(trade.net_pnl) for trade in self.trades
                if (since is None or trade.session > since)
                and (until is None or trade.session <= until)]


def _money(value: float) -> Decimal:
    return Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)


def _register(session: Session, rule: EngineRule, now: datetime) -> ResearchRuleVersion:
    row = session.scalar(select(ResearchRuleVersion).where(
        ResearchRuleVersion.rule_key == rule.key, ResearchRuleVersion.version == rule.version,
    ))
    if row is not None:
        if row.params != rule.params or row.forward_from != rule.forward_from:
            # A frozen version cannot change; a changed rule needs a new version number.
            raise ValueError(f"{rule.key} v{rule.version} differs from its frozen record")
        return row
    row = ResearchRuleVersion(
        rule_key=rule.key, version=rule.version, params=rule.params,
        forward_from=rule.forward_from, stage="SHADOW", stage_since=now, latest_check={},
        created_at=now,
    )
    session.add(row)
    session.flush()
    _event(session, row, "REGISTERED", "FROZEN",
           f"Shadow test starts with sessions from {rule.forward_from.isoformat()}",
           {"params": rule.params}, now)
    return row


def _event(
    session: Session, row: ResearchRuleVersion, event_type: str, reason: str, summary: str,
    details: dict[str, Any], now: datetime,
) -> None:
    session.add(ResearchRuleEvent(
        rule_version_id=row.id, rule_key=row.rule_key, version=row.version,
        event_type=event_type, reason=reason, summary=summary[:256],
        details={"policy_id": ENGINE_POLICY_ID, **details}, occurred_at=_stamp(session, now),
    ))


def _stamp(session: Session, now: datetime) -> datetime:
    """`now`, or a microsecond after the last event of this session, so the trail never ties."""
    last = session.info.get("rule_engine_last_event")
    stamp = now if last is None or now > last else last + timedelta(microseconds=1)
    session.info["rule_engine_last_event"] = stamp
    return stamp


def _move(
    session: Session, state: _State, stage: str, event_type: str, reason: str, summary: str,
    details: dict[str, Any], now: datetime,
) -> None:
    state.row.stage, state.row.stage_since = stage, now
    _event(session, state.row, event_type, reason, summary, details, now)


def run_engine(
    session: Session, market: Market, now: datetime,
    rules: Sequence[EngineRule] = ENGINE_RULES,
    runner: Callable[[EngineRule, Market], list[TradeResult]] | None = None,
) -> list[str]:
    """Record new shadow trades and apply every due decision. The caller commits.

    Returns one line per decision. Safe to rerun: trades are keyed by version and session.
    """
    run = runner or (lambda rule, data: rule.runner()(data))
    states: dict[str, list[_State]] = {}
    for rule in rules:
        row = _register(session, rule, now)
        known = {trade.session: trade for trade in session.scalars(select(ResearchShadowTrade)
                 .where(ResearchShadowTrade.rule_version_id == row.id))}
        for result in run(rule, market):
            if result.session < rule.forward_from or result.session in known:
                continue
            trade = ResearchShadowTrade(
                rule_version_id=row.id, session=result.session, expiry=result.expiry,
                gross_pnl=_money(result.gross_pnl), charges=_money(result.charges),
                net_pnl=_money(result.net_pnl), recorded_at=now,
            )
            session.add(trade)
            known[result.session] = trade
        trades = sorted(known.values(), key=lambda trade: trade.session)
        states.setdefault(rule.key, []).append(_State(row, trades))
    decisions: list[str] = []
    for versions in states.values():
        decisions += _decide(session, sorted(versions, key=lambda s: s.row.version), now)
    session.flush()
    return decisions


def _decide(session: Session, versions: list[_State], now: datetime) -> list[str]:
    decisions: list[str] = []
    today = now.date()

    def champion() -> _State | None:
        return next((s for s in versions if s.row.stage == "PAPER_APPROVED"), None)

    current = champion()
    if current is not None:
        promoted_on = current.row.stage_since.date()
        reason = demotion_reason(current.values(until=promoted_on),
                                 current.values(since=promoted_on))
        if reason:
            found = evidence(current.values(since=promoted_on))
            _move(session, current, "DEMOTED", "DEMOTED", reason,
                  f"{reason.replace('_', ' ').lower()} after promotion, "
                  f"{found.trades} trade(s) since", asdict(found), now)
            decisions.append(f"{current.row.rule_key} v{current.row.version} demoted: {reason}")
            fallback = next((s for s in reversed(versions) if s.row.stage == "SUPERSEDED"), None)
            if fallback is not None:
                _move(session, fallback, "PAPER_APPROVED", "ROLLED_BACK", "NEWER_DEMOTED",
                      f"Back to v{fallback.row.version} after v{current.row.version} was demoted",
                      {"demoted_version": current.row.version}, now)
                decisions.append(f"{fallback.row.rule_key} rolled back to v{fallback.row.version}")
    for state in versions:
        row = state.row
        found = evidence(state.values(), max(0, (today - row.forward_from).days))
        check: dict[str, Any] = {"policy_id": ENGINE_POLICY_ID, **asdict(found),
                                 "t_bar": round(T_BAR, 2), "needs": []}
        breach = hard_limit_breach(found)
        if row.stage in ("SHADOW", "SUPERSEDED") and breach:
            _move(session, state, "RETIRED", "RETIRED", breach,
                  f"{breach.replace('_', ' ').lower()} past the study limit in shadow",
                  asdict(found), now)
            decisions.append(f"{row.rule_key} v{row.version} retired: {breach}")
        elif row.stage == "SHADOW":
            needs = promotion_needs(found)
            incumbent = champion()
            if not needs and incumbent is not None:
                rival = [float(t.net_pnl) for t in incumbent.trades
                         if t.session >= row.forward_from]
                if rival and mean(rival) >= found.average:
                    needs.append(f"a higher average than v{incumbent.row.version} "
                                 "over the same sessions")
            check["needs"] = needs
            if not needs:
                if incumbent is not None:
                    _move(session, incumbent, "SUPERSEDED", "SUPERSEDED", "CHALLENGER_PROMOTED",
                          f"Replaced by v{row.version}", {"by_version": row.version}, now)
                _move(session, state, "PAPER_APPROVED", "PROMOTED", "FORWARD_BAR_PASSED",
                      f"{found.trades} shadow trades, average ₹{found.average:,.0f}, "
                      f"t {found.t_value:.2f} against {T_BAR:.2f}", asdict(found), now)
                decisions.append(f"{row.rule_key} v{row.version} promoted")
        row.latest_check, row.checked_at = check, now
    return decisions


def rule_versions(session: Session) -> list[ResearchRuleVersion]:
    return list(session.scalars(select(ResearchRuleVersion).order_by(
        ResearchRuleVersion.rule_key, ResearchRuleVersion.version)))


def rule_events(session: Session, limit: int = 100) -> list[ResearchRuleEvent]:
    return list(session.scalars(select(ResearchRuleEvent).order_by(
        ResearchRuleEvent.occurred_at.desc(), ResearchRuleEvent.id.desc()).limit(limit)))
