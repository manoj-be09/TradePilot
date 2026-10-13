"""Authenticated equity research and simulation; broker credentials never leave the server."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from apps.api.auth import CurrentUser, SessionDep
from apps.api.market_outlook import IST, latest_completed_session, nifty_benchmark
from apps.api.services.equity_runtime import ACTIVE, history, lock_wallet, serialize, utc
from apps.api.settings import get_settings
from packages.backtesting.performance import PerformanceTrade, summarize
from packages.database.models import (
    AuditEvent,
    EquityAccount,
    EquityCashEvent,
    EquityDeployment,
    EquityPolicyTrial,
    EquityValidationStudy,
    StrategyCatalogEntry,
    WatchlistItem,
)
from packages.market_data.contracts import HistoricalBar
from packages.market_data.provider import get_market_data_provider
from packages.paper_trading.equity import (
    CALCULATION_VERSION,
    DEFAULT_EXIT_POLICY,
    ENTRY_CRITERIA,
    EXIT_CRITERIA,
    FEE,
    MINIMUM_BARS,
    SLIPPAGE,
    STOP_CRITERIA,
    STRATEGIES,
    VERSION,
    ZONE,
    average_true_range,
    completed_bars,
    entry_threshold,
    money,
    open_position,
    proceeds,
    quote_problem,
    replay,
    risk_distance,
    signal,
)
from packages.paper_trading.equity_cash_journal import (
    EquityCashJournalMismatch,
    reconcile_equity_cash_journal,
    record_equity_cash_event,
)
from packages.paper_trading.equity_ledger import reconcile_equity_ledger
from packages.regime.chronological_validation import (
    ChronologicalValidation,
    review_fixed_equity_rules,
    unavailable_validation,
)
from packages.regime.equity_policy_trials import (
    POLICIES,
    REGISTRY_VERSION,
    PolicyComparison,
    compare_exit_policies,
)
from packages.regime.outlook import describe_market
from packages.regime.research_identity import research_code_digest
from packages.regime.shadow_fit import ShadowFitResult, compare_shadow_fit, unavailable_fit
from packages.regime.strategy_fit import describe_stock_context, fit_for_strategy
from packages.regime.strategy_ranking import RANKING_METHOD, RANKING_VERSION, profit_review

router = APIRouter(prefix="/equity", tags=["equity paper trading"])


class EquityCashJournalResponse(BaseModel):
    status: Literal["MATCHED", "LEGACY_CHECKPOINT", "NOT_STARTED", "MISMATCH"]
    entry_count: int = Field(ge=0)
    checkpoint_cash: Decimal | None
    recorded_cash: Decimal
    journal_cash: Decimal | None
    issue_count: int = Field(ge=0)
    issues: list[str]


class EquityLedgerReconciliationResponse(BaseModel):
    status: Literal["MATCHED", "MISMATCH"]
    initial_cash: Decimal
    recorded_cash: Decimal
    expected_cash: Decimal
    cash_difference: Decimal
    deployment_count: int = Field(ge=0)
    reserved_count: int = Field(ge=0)
    closed_count: int = Field(ge=0)
    issue_count: int = Field(ge=0)
    issues: list[str]
    audit_coverage: Literal["COMPLETE", "INCOMPLETE"]
    audit_issue_count: int = Field(ge=0)
    audit_issues: list[str]
    cash_journal: EquityCashJournalResponse


def _canonical_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _frozen_study(study: EquityValidationStudy) -> ChronologicalValidation:
    if (
        study.dataset_manifest is not None
        and _canonical_hash(study.dataset_manifest) != study.dataset_digest
    ):
        raise HTTPException(409, "Frozen validation dataset failed integrity review")
    if study.dataset_manifest is not None:
        try:
            manifest_matches = (
                study.dataset_manifest.get("provider") == study.provider
                and study.dataset_manifest.get("instrument_key") == study.instrument_key
                and study.dataset_manifest.get("rule_version") == study.rule_version
                and Decimal(study.dataset_manifest["allocation"]) == study.allocation
                and Decimal(study.dataset_manifest["planned_risk"]) == study.risk_budget
            )
        except (KeyError, TypeError, ValueError, InvalidOperation):
            manifest_matches = False
        if not manifest_matches:
            raise HTTPException(409, "Frozen validation dataset does not match its study")
    if _canonical_hash(study.report) != study.report_digest:
        raise HTTPException(409, "Frozen validation evidence failed integrity review")
    try:
        result = ChronologicalValidation.model_validate(study.report)
    except ValueError as error:
        raise HTTPException(409, "Frozen validation evidence is invalid") from error
    if (
        result.study_id != study.id
        or result.generation != study.generation
        or result.parent_study_id != study.parent_study_id
        or not result.frozen
        or result.dataset_digest != study.dataset_digest
        or result.rule_version != study.rule_version
        or result.allocation != study.allocation
        or result.planned_risk != study.risk_budget
    ):
        raise HTTPException(409, "Frozen validation evidence does not match its manifest")
    return result


def _saved_policy_trial(
    trial: EquityPolicyTrial, study: EquityValidationStudy,
) -> PolicyComparison:
    if (
        _canonical_hash(trial.trial_manifest) != trial.manifest_digest
        or _canonical_hash(trial.result) != trial.result_digest
    ):
        raise HTTPException(409, "Frozen policy comparison failed integrity review")
    try:
        result = PolicyComparison.model_validate(trial.result)
    except ValueError as error:
        raise HTTPException(409, "Frozen policy comparison is invalid") from error
    if study.dataset_manifest is not None:
        for field in ("fee_per_side", "slippage_per_side", "calculation_source_digest"):
            if (
                field in study.dataset_manifest
                and trial.trial_manifest.get(field) != study.dataset_manifest[field]
            ):
                raise HTTPException(409, "Frozen policy comparison does not match its study")
    if (
        trial.user_id != study.user_id or trial.study_id != study.id
        or trial.registry_version != REGISTRY_VERSION
        or trial.trial_manifest.get("dataset_digest") != study.dataset_digest
        or trial.trial_manifest.get("study_report_digest") != study.report_digest
        or trial.trial_manifest.get("study_id") != str(study.id)
        or trial.trial_manifest.get("strategy") != trial.strategy
        or result.study_id != str(study.id)
        or result.strategy != trial.strategy
        or result.dataset_digest != study.dataset_digest
        or result.trial_id != trial.id
        or result.registry_version != trial.registry_version
        or result.holdout_evaluated or result.parameters_changed
        or result.ranking_changed
    ):
        raise HTTPException(409, "Frozen policy comparison does not match its study")
    return result.model_copy(update={"comparison_digest": trial.result_digest})


@router.get("/strategy-catalog")
def strategy_catalog(
    user: CurrentUser, session: SessionDep, response: Response
) -> list[dict[str, Any]]:
    """Curated source metadata; research-only methods cannot be deployed."""
    response.headers["Cache-Control"] = "no-store"
    entries = session.scalars(
        select(StrategyCatalogEntry).order_by(StrategyCatalogEntry.sort_order)
    )
    return [
        {
            "key": entry.key,
            "name": entry.name,
            "status": entry.status,
            "implementation_key": entry.implementation_key,
            "catalog_version": entry.catalog_version,
            **entry.details,
            **({
                "entry": ENTRY_CRITERIA[entry.implementation_key],
                "exit": EXIT_CRITERIA,
                "stop_loss": STOP_CRITERIA,
            } if entry.implementation_key in ENTRY_CRITERIA else {}),
        }
        for entry in entries
    ]


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    watchlist_id: UUID
    budget: Decimal = Field(default=Decimal("10000"), ge=100, le=25000, decimal_places=2)
    risk_budget: Decimal = Field(default=Decimal("200"), ge=10, le=1000, decimal_places=2)

    @model_validator(mode="after")
    def bounded_risk(self) -> "ResearchRequest":
        if self.risk_budget > self.budget * Decimal("0.1"):
            raise ValueError("Planned risk must not exceed 10% of the allocation")
        return self


class DeployRequest(ResearchRequest):
    strategy: str
    command_key: UUID
    indicative_quotes: bool = False


class PolicyTrialRequest(BaseModel):
    study_id: UUID | None = None
    model_config = ConfigDict(extra="forbid")
    watchlist_id: UUID
    strategy: str


def instrument(body: ResearchRequest, user_id: UUID, session: SessionDep) -> WatchlistItem:
    item = session.get(WatchlistItem, body.watchlist_id)
    if item is None or item.user_id != user_id:
        raise HTTPException(404, "Watchlist instrument not found")
    if item.exchange != "NSE" or item.instrument_type not in {"EQ", "EQUITY", "ETF"}:
        raise HTTPException(422, "This release supports NSE cash equities and ETFs only")
    if item.expiry is not None or item.segment not in {"EQ", "NSE_EQ"}:
        raise HTTPException(422, "Only cash equity instruments are supported")
    return item


def _automated_outcome_audit_matches(
    session: SessionDep, row: EquityDeployment, snapshot: dict[str, Any],
    position: dict[str, Any],
) -> bool:
    """Require the independent entry/close events to agree with paper feedback."""
    events = list(session.scalars(select(AuditEvent).where(
        AuditEvent.actor_user_id == row.user_id,
        AuditEvent.correlation_id == str(row.id),
        AuditEvent.event_type.in_((
            "equity.deployment.entered", "equity.deployment.exited",
        )),
    )))
    if len(events) != 2:
        return False
    by_type = {event.event_type: event.details for event in events}
    entered = by_type.get("equity.deployment.entered")
    exited = by_type.get("equity.deployment.exited")
    if not isinstance(entered, dict) or not isinstance(exited, dict):
        return False
    return (
        entered.get("deployment_id") == exited.get("deployment_id") == str(row.id)
        and entered.get("strategy") == exited.get("strategy") == row.strategy
        and entered.get("strategy_version") == row.version
        and entered.get("provider") == row.provider
        and entered.get("instrument_key") == row.instrument_key
        and entered.get("quote_basis") == exited.get("quote_basis") == "exchange"
        and entered.get("book_as_of") == snapshot.get("entry_book_as_of")
        and exited.get("book_as_of") == snapshot.get("exit_book_as_of")
        and entered.get("ask") == snapshot.get("entry_ask")
        and exited.get("bid") == snapshot.get("exit_bid")
        and entered.get("entry_price") == position.get("entry_price")
        and exited.get("exit_price") == snapshot.get("exit_price")
        and exited.get("exit_reason") == snapshot.get("exit_reason")
        and exited.get("net_pnl") == snapshot.get("net_pnl")
    )


def _paper_performance(
    session: SessionDep, user_id: UUID, provider: str, instrument_key: str, strategy: str
) -> dict[str, Any]:
    rows = list(session.scalars(
        select(EquityDeployment).where(
            EquityDeployment.user_id == user_id,
            EquityDeployment.provider == provider,
            EquityDeployment.instrument_key == instrument_key,
            EquityDeployment.strategy == strategy,
            EquityDeployment.status.in_(["CLOSED", "OPEN", "EXIT_REQUESTED"]),
        ).order_by(EquityDeployment.updated_at, EquityDeployment.id)
    ))
    completed: list[PerformanceTrade] = []
    verified: list[PerformanceTrade] = []
    excluded = 0
    fallback_dates = 0
    versions: set[str] = set()
    for row in rows:
        if row.status != "CLOSED":
            continue
        try:
            pnl = Decimal(str(row.state["realized_pnl"]))
            if not pnl.is_finite():
                raise ValueError("Invalid P&L")
        except (KeyError, ValueError, ArithmeticError):
            excluded += 1
            continue
        try:
            stamp = datetime.fromisoformat(row.state["exit_at"])
            if stamp.tzinfo is None:
                raise ValueError("Missing timezone")
        except (KeyError, ValueError, TypeError):
            stamp = row.updated_at.replace(tzinfo=timezone.utc) if (
                row.updated_at.tzinfo is None
            ) else row.updated_at
            fallback_dates += 1
        # This original entry scenario survives subsequent trailing-stop updates.
        # Never reconstruct initial risk from the moved stop or today's allocation.
        initial_risk = None
        try:
            position = row.state.get("position", {})
            if position.get("calculation_version") == CALCULATION_VERSION:
                initial_risk = -Decimal(str(position["stop_scenario_net"]))
        except (KeyError, ValueError, ArithmeticError, TypeError):
            pass
        trade = PerformanceTrade(pnl, initial_risk, stamp)
        completed.append(trade)
        snapshot = row.state.get("outcome_evidence")
        try:
            entry_book_at = (
                datetime.fromisoformat(snapshot["entry_book_as_of"])
                if isinstance(snapshot, dict) else None
            )
            exit_book_at = (
                datetime.fromisoformat(snapshot["exit_book_as_of"])
                if isinstance(snapshot, dict) else None
            )
            if (
                isinstance(snapshot, dict)
                and row.state.get("quote_mode") != "INDICATIVE"
                and snapshot.get("version") == 2
                and snapshot.get("strategy_version") == row.version == VERSION
                and snapshot.get(
                    "exit_policy_version", DEFAULT_EXIT_POLICY.version
                ) == position.get("exit_policy_version", DEFAULT_EXIT_POLICY.version)
                == DEFAULT_EXIT_POLICY.version
                and snapshot.get("exit_at") == row.state.get("exit_at")
                and snapshot.get("exit_quote_at")
                and snapshot.get("entry_quote_at")
                and snapshot.get("entry_quote_basis") == "exchange"
                and snapshot.get("exit_quote_basis") == "exchange"
                and entry_book_at is not None and entry_book_at.tzinfo is not None
                and exit_book_at is not None and exit_book_at.tzinfo is not None
                and exit_book_at > entry_book_at
                and snapshot.get("entry_book_as_of") == row.state.get("entry_book_as_of")
                and snapshot.get("exit_reason") == row.state.get("exit_reason")
                and snapshot.get("exit_reason") in {
                    "STOP_LOSS", "TRAILING_STOP", "PROFIT_TARGET",
                }
                and Decimal(str(snapshot["entry_ask"]))
                == Decimal(str(position["reference_price"]))
                and Decimal(str(snapshot["exit_bid"])) > 0
                and Decimal(str(snapshot["exit_price"]))
                == proceeds(position, Decimal(str(snapshot["exit_bid"])))[0]
                and money(
                    proceeds(position, Decimal(str(snapshot["exit_bid"])))[1]
                    - Decimal(str(position["cost"]))
                ) == pnl
                and Decimal(str(snapshot["net_pnl"])) == pnl
                and initial_risk is not None
                and initial_risk > 0
                and Decimal(str(snapshot["initial_risk"])) == initial_risk
                and _automated_outcome_audit_matches(session, row, snapshot, position)
            ):
                verified.append(trade)
        except (KeyError, ValueError, ArithmeticError, TypeError):
            pass
        versions.add(row.version)
    completed.sort(key=lambda trade: trade.exit_at or datetime.min.replace(tzinfo=timezone.utc))
    verified.sort(key=lambda trade: trade.exit_at or datetime.min.replace(tzinfo=timezone.utc))
    return {
        **summarize(completed),
        "feedback": summarize(verified),
        "outcomes_without_verified_snapshot": len(completed) - len(verified),
        "open_trades_excluded": sum(row.status != "CLOSED" for row in rows),
        "invalid_records_excluded": excluded,
        "fallback_exit_dates": fallback_dates,
        "strategy_versions": sorted(versions),
    }


@router.post("/strategies")
async def strategies(
    body: ResearchRequest, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    item = instrument(body, user.id, session)
    provider = get_market_data_provider()
    if item.provider != provider.provider_id:
        raise HTTPException(409, "Select an instrument from the active data provider")
    now = datetime.now(timezone.utc)
    try:
        bars = await history(provider, item.instrument_key, now)
    except Exception as error:
        raise HTTPException(
            503, "Daily history unavailable or invalid; no estimates generated"
        ) from error
    live = False
    live_detail = "Live quote/session unavailable; ranking uses completed daily history only"
    quote = None
    try:
        quote = await provider.get_quote(item.instrument_key)
        market = await provider.get_equity_session(now.astimezone(ZONE).date())
        quote_issue = quote_problem(quote, item.instrument_key, datetime.now(timezone.utc))
        live = bool(
            market
            and market.exchange == "NSE"
            and market.start <= now < market.end
            and quote_issue is None
        )
        if live:
            live_detail = "Verified fresh quote during NSE session"
        elif quote_issue:
            live_detail = f"{quote_issue}; ranking uses completed daily history only"
    except Exception:
        pass  # Historical evidence is still useful; it must not be presented as live.
    distance = risk_distance(bars)
    price = quote.ask if live and quote is not None else bars[-1].close
    assert price is not None
    proposed = open_position(price, distance, body.budget, body.risk_budget)
    context = describe_stock_context(bars)
    rows = []
    stale = (now.astimezone(ZONE).date() - bars[-1].timestamp.astimezone(ZONE).date()).days > 4
    for key, (name, rule) in STRATEGIES.items():
        history_ready = len(bars) >= MINIMUM_BARS[key]
        historical = replay(key, bars, body.budget, body.risk_budget)
        paper = _paper_performance(session, user.id, item.provider, item.instrument_key, key)
        # Legacy top-level fields now always describe replay; never pool sources.
        evidence = {
            **historical,
            "source": "historical_replay",
            "replay_closed_trades": historical["closed_trades"],
            "paper_closed_trades": paper["closed_trades"],
            "paper_net_pnl": paper["net_pnl"],
        }
        fit = signal(key, bars)
        entry_ready = (
            live and not stale and fit and proposed is not None
            and abs(price / bars[-1].close - 1) <= Decimal("0.02")
        )
        fit_score, fit_reasons = fit_for_strategy(key, context)
        review = profit_review(evidence, fit_score, float(body.risk_budget), paper["feedback"])
        rows.append(
            {
                "key": key,
                "name": name,
                "rule": rule,
                "entry_criteria": ENTRY_CRITERIA[key],
                "history_ready": history_ready,
                "minimum_bars": MINIMUM_BARS[key],
                "signal": fit,
                "entry_ready": entry_ready,
                "score": review["score"],
                "profit_review": review,
                "evidence": evidence,
                "performance": {
                    "historical": historical,
                    "paper": paper,
                    "ranking_source": "historical_replay_with_separate_paper_feedback",
                },
                "proposed": proposed if history_ready else None,
                "provisional": evidence["closed_trades"] < 20,
                "market_fit_score": fit_score,
                "market_fit_reasons": fit_reasons,
            }
        )
    rows.sort(key=lambda row: (-row["profit_review"]["tier"], -row["score"], row["key"]))
    shortlist = rows[:3]
    qualified = [row["key"] for row in shortlist if (
        row["entry_ready"] and row["market_fit_score"] >= 2
        and row["proposed"] is not None and row["profit_review"]["tier"] == 3
        and context["volume"] != "unknown" and context["volatility"] != "extreme"
        and not stale
    )]
    positive = any(row["profit_review"]["tier"] >= 2 for row in shortlist)
    return {
        "calculation_version": CALCULATION_VERSION,
        "exit_policy_version": DEFAULT_EXIT_POLICY.version,
        "shared_scenario": {
            "position": proposed,
            "exit_policy_version": DEFAULT_EXIT_POLICY.version,
            "basis": "live_ask" if live else "last_completed_close",
            "reference_at": quote.timestamp if live and quote is not None else bars[-1].timestamp,
            "reference_price": str(price),
            "signal_close": str(bars[-1].close),
            "atr_14_sma": str(average_true_range(bars)),
            "allocation": str(body.budget),
            "planned_risk": str(body.risk_budget),
            "fee_pct_per_side": str(FEE * 100),
            "slippage_pct_per_side": str(SLIPPAGE * 100),
            "exit_rule": EXIT_CRITERIA,
            "stop_rule": STOP_CRITERIA,
        },
        "strategies": rows,
        "top_three": [row["key"] for row in shortlist],
        "qualified_now": qualified,
        "ranking_version": RANKING_VERSION,
        "has_positive_replay": positive,
        "market_context": context,
        "recommendation": (
            "A top candidate has positive historical results and passes current setup checks. "
            "Apply starts monitoring for a new signal."
            if qualified
            else "Top candidates include positive historical results, but evidence or current "
            "setup checks are incomplete. Review the sample and risk before paper testing."
            if positive
            else "These are the top three available comparisons. None currently demonstrates "
            "positive average net replay profit; waiting is a valid choice."
        ),
        "version": VERSION,
        "provider": provider.provider_id,
        "symbol": item.trading_symbol,
        "as_of": now,
        "history_from": bars[0].timestamp,
        "history_to": bars[-1].timestamp,
        "bars": len(bars),
        "live": live,
        "live_detail": live_detail,
        "stale": stale,
        "ranking": RANKING_METHOD,
    }


@router.post("/strategy-fit-shadow", response_model=ShadowFitResult)
async def strategy_fit_shadow(
    body: ResearchRequest, user: CurrentUser, session: SessionDep, response: Response
) -> ShadowFitResult:
    """On-demand historical comparison; never changes ranks or paper deployments."""
    response.headers["Cache-Control"] = "no-store"
    item = instrument(body, user.id, session)
    provider = get_market_data_provider()
    if item.provider != provider.provider_id:
        raise HTTPException(409, "Select an instrument from the active data provider")
    provider_id = provider.provider_id
    symbol = item.trading_symbol
    now = datetime.now(timezone.utc)
    try:
        benchmark = await nifty_benchmark(provider)
        if benchmark is None:
            return unavailable_fit(
                provider_id, symbol, "NIFTY 50 is unavailable from this provider."
            )
        expected = await latest_completed_session(provider, now)
        if expected is None:
            return unavailable_fit(
                provider_id, symbol, "A recent completed NSE session could not be verified."
            )
        start = now - timedelta(days=900)
        nifty_raw = await provider.get_historical(benchmark.instrument_key, start, now, "1d")
        current = describe_market(
            nifty_raw,
            provider=provider_id,
            instrument_key=benchmark.instrument_key,
            expected_session=expected,
        )
        if current.status != "ready":
            return unavailable_fit(
                provider_id, symbol, current.reason or "NIFTY history is unavailable."
            )
        nifty_bars = [bar for bar in nifty_raw if bar.timestamp.astimezone(ZONE).date() <= expected]
        stock_raw = await provider.get_historical(item.instrument_key, start, now, "1d")
        stock_bars = completed_bars(stock_raw, now)
        return compare_shadow_fit(
            stock_bars,
            nifty_bars,
            current,
            provider=provider_id,
            symbol=symbol,
            nifty_key=benchmark.instrument_key,
            budget=body.budget,
            risk_budget=body.risk_budget,
        )
    except Exception:
        # Provider errors and malformed history must not expose credentials or become evidence.
        return unavailable_fit(
            provider_id, symbol, "Verified daily history is unavailable or invalid."
        )


@router.post("/chronological-validation", response_model=ChronologicalValidation)
async def chronological_validation(
    body: ResearchRequest, user: CurrentUser, session: SessionDep, response: Response
) -> ChronologicalValidation:
    result = await _chronological_validation(body, user, session, response)
    # Keep unsuccessful diagnostics visible without making them frozen evidence.
    session.add(AuditEvent(
        actor_user_id=user.id, event_type="equity.validation.attempt",
        correlation_id=str(body.watchlist_id),
        details={
            "provider": result.provider, "symbol": result.symbol,
            "requested_allocation": str(body.budget),
            "requested_risk": str(body.risk_budget),
            "served_allocation": str(result.allocation) if result.allocation is not None else None,
            "served_risk": str(result.planned_risk) if result.planned_risk is not None else None,
            "status": result.status, "reason": result.reason,
            "frozen": result.frozen,
            "study_id": str(result.study_id) if result.study_id else None,
            "dataset_digest": result.dataset_digest,
            "result_digest": _canonical_hash(result.model_dump(mode="json")),
            "rule_version": result.rule_version,
            "validation_version": result.version,
            "bars": result.bars,
            "reviewable_strategies": sum(row.status == "reviewable" for row in result.rows),
            "strategies_reviewed": len(result.rows),
        },
    ))
    session.commit()
    return result


@router.get("/validation-attempts")
def validation_attempts(
    user: CurrentUser, session: SessionDep, response: Response,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    """Account-scoped request history; attempts are not independent frozen studies."""
    response.headers["Cache-Control"] = "no-store"
    events = session.scalars(select(AuditEvent).where(
        AuditEvent.actor_user_id == user.id,
        AuditEvent.event_type == "equity.validation.attempt",
    ).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(limit))
    return [{"id": str(event.id), "created_at": event.created_at,
             "watchlist_id": event.correlation_id, **event.details} for event in events]


async def _chronological_validation(
    body: ResearchRequest, user: CurrentUser, session: SessionDep, response: Response
) -> ChronologicalValidation:
    """Review fixed equity rules on earlier windows without opening the reserved holdout."""
    response.headers["Cache-Control"] = "no-store"
    item = instrument(body, user.id, session)
    existing = session.scalar(select(EquityValidationStudy).where(
        EquityValidationStudy.generation == 1,
        EquityValidationStudy.user_id == user.id,
        EquityValidationStudy.provider == item.provider,
        EquityValidationStudy.instrument_key == item.instrument_key,
    ))
    if existing is not None:
        return _frozen_study(existing)
    provider = get_market_data_provider()
    if item.provider != provider.provider_id:
        raise HTTPException(409, "Select an instrument from the active data provider")
    now = datetime.now(timezone.utc)
    try:
        expected = await latest_completed_session(provider, now)
        if expected is None:
            return unavailable_validation(
                provider.provider_id, item.trading_symbol,
                "A recent completed NSE session could not be verified.",
            )
        raw = await provider.get_historical(
            item.instrument_key, now - timedelta(days=900), now, "1d"
        )
        bars = completed_bars(raw, now)
        if not bars or bars[-1].timestamp.astimezone(ZONE).date() != expected:
            return unavailable_validation(
                provider.provider_id, item.trading_symbol,
                "The latest completed NSE session is missing from daily history.",
            )
        report = review_fixed_equity_rules(
            bars, provider=provider.provider_id, symbol=item.trading_symbol,
            budget=body.budget, risk_budget=body.risk_budget,
        )
    except Exception:
        return unavailable_validation(
            provider.provider_id, item.trading_symbol,
            "Verified daily history is unavailable or invalid.",
        )
    if report.status != "ready":
        return report
    try:
        code_digest = research_code_digest()
    except OSError:
        return unavailable_validation(
            provider.provider_id, item.trading_symbol,
            "Calculation source identity could not be verified.",
        )
    manifest = {
        "provider": item.provider,
        "instrument_key": item.instrument_key,
        "rule_version": VERSION,
        "calculation_version": CALCULATION_VERSION,
        "exit_policy_version": DEFAULT_EXIT_POLICY.version,
        "allocation": str(body.budget),
        "fee_per_side": str(FEE),
        "slippage_per_side": str(SLIPPAGE),
        "planned_risk": str(body.risk_budget),
        "calculation_source_digest": code_digest,
        "bars": [
            [bar.timestamp.isoformat(), str(bar.open), str(bar.high),
             str(bar.low), str(bar.close), bar.volume]
            for bar in bars
        ],
    }
    digest = _canonical_hash(manifest)
    report = report.model_copy(update={"dataset_digest": digest})
    if not any(row.status == "reviewable" for row in report.rows):
        return report  # Insufficient diagnostics are not frozen as parameter evidence.
    study_id = uuid4()
    frozen_at = datetime.now(timezone.utc)
    report = report.model_copy(update={
        "study_id": study_id, "frozen_at": frozen_at, "frozen": True,
    })
    report_data = report.model_dump(mode="json")
    session.add(EquityValidationStudy(
        id=study_id, user_id=user.id, provider=item.provider,
        instrument_key=item.instrument_key, symbol=item.trading_symbol,
        rule_version=VERSION, allocation=body.budget, risk_budget=body.risk_budget,
        dataset_digest=digest, dataset_manifest=manifest,
        report_digest=_canonical_hash(report_data),
        report=report_data, frozen_at=frozen_at,
    ))
    session.add(AuditEvent(
        actor_user_id=user.id, event_type="equity.validation.study_frozen",
        details={
            "study_id": str(study_id), "provider": item.provider,
            "instrument_key": item.instrument_key, "rule_version": VERSION,
            "dataset_digest": digest, "reserved_from": str(report.reserved_from),
        },
    ))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        winner = session.scalar(select(EquityValidationStudy).where(
            EquityValidationStudy.generation == 1,
            EquityValidationStudy.user_id == user.id,
            EquityValidationStudy.provider == item.provider,
            EquityValidationStudy.instrument_key == item.instrument_key,
        ))
        if winner is None:
            raise HTTPException(409, "Validation study could not be frozen") from None
        return _frozen_study(winner)
    return report


class SuccessorRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    reason: str = Field(min_length=8, max_length=200)


@router.get("/validation-studies/{study_id}", response_model=ChronologicalValidation)
def read_validation_study(
    study_id: UUID, user: CurrentUser, session: SessionDep, response: Response,
) -> ChronologicalValidation:
    response.headers["Cache-Control"] = "no-store"
    study = session.scalar(select(EquityValidationStudy).where(
        EquityValidationStudy.id == study_id, EquityValidationStudy.user_id == user.id,
    ))
    if study is None:
        raise HTTPException(404, "Validation study not found")
    return _frozen_study(study)


@router.post("/validation-studies/{study_id}/successor", response_model=ChronologicalValidation)
def create_validation_successor(
    study_id: UUID, body: SuccessorRequest, user: CurrentUser,
    session: SessionDep, response: Response,
) -> ChronologicalValidation:
    """Explicit source-update review on the parent's exact candles and reserved window."""
    response.headers["Cache-Control"] = "no-store"
    parent = session.scalar(select(EquityValidationStudy).where(
        EquityValidationStudy.id == study_id, EquityValidationStudy.user_id == user.id,
    ))
    if parent is None:
        raise HTTPException(404, "Validation study not found")
    baseline = _frozen_study(parent)
    child = session.scalar(select(EquityValidationStudy).where(
        EquityValidationStudy.parent_study_id == parent.id,
        EquityValidationStudy.user_id == user.id,
    ))
    if child is not None:
        return _frozen_study(child)
    manifest = parent.dataset_manifest
    if manifest is None or any(field not in manifest for field in (
        "fee_per_side", "slippage_per_side", "calculation_version", "exit_policy_version",
    )):
        raise HTTPException(409, "Parent study lacks explicit calculation assumptions")
    if (
        manifest["fee_per_side"] != str(FEE)
        or manifest["slippage_per_side"] != str(SLIPPAGE)
        or manifest["exit_policy_version"] != DEFAULT_EXIT_POLICY.version
        or manifest["calculation_version"] != CALCULATION_VERSION
    ):
        raise HTTPException(409, "Successor must preserve the parent's cost and exit assumptions")
    try:
        code_digest = research_code_digest()
        bars = [HistoricalBar(
            timestamp=row[0], open=row[1], high=row[2], low=row[3], close=row[4], volume=row[5],
        ) for row in manifest["bars"]]
    except (OSError, KeyError, TypeError, ValueError, IndexError):
        raise HTTPException(409, "Parent candles or calculation source are unavailable") from None
    if manifest.get("calculation_source_digest") == code_digest:
        raise HTTPException(409, "Calculation source is unchanged; reuse the original study")
    report = review_fixed_equity_rules(
        bars, provider=parent.provider, symbol=parent.symbol,
        budget=parent.allocation, risk_budget=parent.risk_budget,
    )
    boundaries_changed = report.status == "ready" and any(
        getattr(report, field) != getattr(baseline, field) for field in (
            "bars", "training_from", "training_to", "validation_from", "validation_to",
            "reserved_from", "reserved_to", "reserved_bars",
        )
    )
    report = report.model_copy(update={
        "generation": parent.generation + 1, "parent_study_id": parent.id,
    })
    session.add(AuditEvent(
        actor_user_id=user.id, event_type="equity.validation.successor_attempt",
        correlation_id=str(parent.id), details={
            "parent_study_id": str(parent.id), "reason": body.reason,
            "calculation_source_digest": code_digest,
            "status": "boundary_rejected" if boundaries_changed else report.status,
            "report_digest": _canonical_hash(report.model_dump(mode="json")),
        },
    ))
    if boundaries_changed:
        session.commit()
        raise HTTPException(409, "Successor would change the parent's research boundaries")
    if report.status != "ready" or not any(row.status == "reviewable" for row in report.rows):
        session.commit()
        return report
    successor_id, frozen_at = uuid4(), datetime.now(timezone.utc)
    successor_manifest = {
        **manifest, "calculation_source_digest": code_digest, "rule_version": report.rule_version,
    }
    digest = _canonical_hash(successor_manifest)
    report = report.model_copy(update={
        "study_id": successor_id, "generation": parent.generation + 1,
        "parent_study_id": parent.id, "dataset_digest": digest,
        "frozen": True, "frozen_at": frozen_at,
    })
    report_data = report.model_dump(mode="json")
    session.add(EquityValidationStudy(
        id=successor_id, user_id=user.id, provider=parent.provider,
        instrument_key=parent.instrument_key, symbol=parent.symbol,
        rule_version=report.rule_version, allocation=parent.allocation,
        risk_budget=parent.risk_budget,
        generation=parent.generation + 1, parent_study_id=parent.id,
        dataset_digest=digest, dataset_manifest=successor_manifest,
        report_digest=_canonical_hash(report_data), report=report_data, frozen_at=frozen_at,
    ))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        child = session.scalar(select(EquityValidationStudy).where(
            EquityValidationStudy.parent_study_id == study_id,
            EquityValidationStudy.user_id == user.id,
        ))
        if child is None:
            raise HTTPException(409, "Successor could not be frozen") from None
        return _frozen_study(child)
    return report


@router.post("/exit-policy-comparison", response_model=PolicyComparison)
def exit_policy_comparison(
    body: PolicyTrialRequest, user: CurrentUser, session: SessionDep, response: Response,
) -> PolicyComparison:
    """Freeze one predeclared, research-only comparison per strategy and study."""
    response.headers["Cache-Control"] = "no-store"
    item = instrument(ResearchRequest(watchlist_id=body.watchlist_id), user.id, session)
    if body.strategy not in STRATEGIES:
        raise HTTPException(422, "Unknown paper strategy")
    study = session.scalar(select(EquityValidationStudy).where(
        (EquityValidationStudy.id == body.study_id if body.study_id is not None
         else EquityValidationStudy.generation == 1),
        EquityValidationStudy.user_id == user.id,
        EquityValidationStudy.provider == item.provider,
        EquityValidationStudy.instrument_key == item.instrument_key,
    ))
    if study is None:
        if body.study_id is not None:
            raise HTTPException(404, "Validation study not found for this instrument")
        return PolicyComparison(
            status="unavailable", provider=item.provider, symbol=item.trading_symbol,
            strategy=body.strategy,
            reason="No eligible frozen chronological study exists for this instrument.",
        )
    baseline = _frozen_study(study)
    existing = session.scalar(select(EquityPolicyTrial).where(
        EquityPolicyTrial.study_id == study.id,
        EquityPolicyTrial.strategy == body.strategy,
        EquityPolicyTrial.registry_version == REGISTRY_VERSION,
    ))
    if existing is not None:
        return _saved_policy_trial(existing, study)
    manifest = study.dataset_manifest
    if manifest is None:
        return PolicyComparison(
            status="unavailable", provider=item.provider, symbol=item.trading_symbol,
            strategy=body.strategy,
            reason="This older study has no preserved candles for a policy comparison.",
        )
    if "fee_per_side" not in manifest or "slippage_per_side" not in manifest:
        return PolicyComparison(
            status="unavailable", provider=item.provider, symbol=item.trading_symbol,
            strategy=body.strategy,
            reason="This older study has no preserved fee and slippage assumptions.",
        )
    if "calculation_source_digest" not in manifest:
        return PolicyComparison(
            status="unavailable", provider=item.provider, symbol=item.trading_symbol,
            strategy=body.strategy,
            reason="This older study has no preserved calculation source identity.",
        )
    try:
        code_digest = research_code_digest()
    except OSError:
        raise HTTPException(409, "Calculation source identity could not be verified") from None
    if manifest["calculation_source_digest"] != code_digest:
        raise HTTPException(409, "Frozen study uses different calculation source")
    if (
        manifest.get("fee_per_side") != str(FEE)
        or manifest.get("slippage_per_side") != str(SLIPPAGE)
        or manifest.get("calculation_version") != CALCULATION_VERSION
        or manifest.get("exit_policy_version", DEFAULT_EXIT_POLICY.version)
        != DEFAULT_EXIT_POLICY.version
    ):
        raise HTTPException(409, "Frozen study uses an unsupported calculation policy")
    try:
        bars = [HistoricalBar(
            timestamp=row[0], open=row[1], high=row[2], low=row[3],
            close=row[4], volume=row[5],
        ) for row in manifest["bars"]]
        dates = [bar.timestamp.astimezone(ZONE).date() for bar in bars]
        if (
            len(bars) != baseline.bars or len(bars) < 400
            or any(bar.timestamp.tzinfo is None for bar in bars)
            or any(left >= right for left, right in zip(dates, dates[1:], strict=False))
            or any(not 0 < bar.low <= min(bar.open, bar.close)
                   <= max(bar.open, bar.close) <= bar.high for bar in bars)
            or dates[len(bars) * 3 // 5 - 1] != baseline.training_to
            or dates[len(bars) * 4 // 5 - 1] != baseline.validation_to
            or dates[len(bars) * 4 // 5] != baseline.reserved_from
        ):
            raise ValueError("Frozen study candle boundaries do not match")
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as error:
        raise HTTPException(409, "Frozen study candles are invalid") from error
    trial_manifest = {
        "calculation_source_digest": code_digest,
        "study_id": str(study.id),
        "dataset_digest": study.dataset_digest,
        "study_report_digest": study.report_digest,
        "strategy": body.strategy,
        "registry_version": REGISTRY_VERSION,
        "rule_version": study.rule_version,
        "calculation_version": CALCULATION_VERSION,
        "fee_per_side": str(FEE),
        "slippage_per_side": str(SLIPPAGE),
        "allocation": str(study.allocation),
        "planned_risk": str(study.risk_budget),
        "policies": [
            {"version": policy.version, "atr_multiplier": str(policy.atr_multiplier),
             "minimum_distance_fraction": str(policy.minimum_distance_fraction),
             "target_r": str(policy.target_r),
             "trail_activation_r": str(policy.trail_activation_r),
             "trail_distance_r": str(policy.trail_distance_r)}
            for policy in POLICIES
        ],
    }
    trial_id = uuid4()
    frozen_at = datetime.now(timezone.utc)
    result = compare_exit_policies(
        bars, provider=item.provider, symbol=item.trading_symbol,
        strategy=body.strategy, study_id=str(study.id),
        dataset_digest=study.dataset_digest,
        budget=study.allocation, risk_budget=study.risk_budget,
    ).model_copy(update={"trial_id": trial_id, "frozen_at": frozen_at})
    result_data = result.model_dump(mode="json")
    result_digest = _canonical_hash(result_data)
    session.add(EquityPolicyTrial(
        id=trial_id, user_id=user.id, study_id=study.id,
        strategy=body.strategy, registry_version=REGISTRY_VERSION,
        trial_manifest=trial_manifest, manifest_digest=_canonical_hash(trial_manifest),
        result=result_data, result_digest=result_digest, created_at=frozen_at,
    ))
    session.add(AuditEvent(
        actor_user_id=user.id, event_type="equity.policy.trial_frozen",
        details={"trial_id": str(trial_id), "study_id": str(study.id),
                 "strategy": body.strategy, "dataset_digest": study.dataset_digest,
                 "registry_version": REGISTRY_VERSION,
                 "reserved_from": str(baseline.reserved_from)},
    ))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        winner = session.scalar(select(EquityPolicyTrial).where(
            EquityPolicyTrial.study_id == study.id,
            EquityPolicyTrial.strategy == body.strategy,
            EquityPolicyTrial.registry_version == REGISTRY_VERSION,
        ))
        if winner is None:
            raise HTTPException(409, "Policy comparison could not be frozen") from None
        return _saved_policy_trial(winner, study)
    return result.model_copy(update={"comparison_digest": result_digest})


@router.get("/deployments")
def deployments(user: CurrentUser, session: SessionDep, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    account = session.get(EquityAccount, user.id)
    initial = account.initial_cash if account else Decimal(
        get_settings().initial_equity_paper_capital
    )
    rows = list(
        session.scalars(
            select(EquityDeployment)
            .where(EquityDeployment.user_id == user.id)
            .order_by(EquityDeployment.created_at.desc())
            .limit(200)
        )
    )
    return {
        "initial_cash": str(initial.quantize(Decimal("0.01"))),
        "cash": str(account.cash if account else initial),
        "deployments": [serialize(row) for row in rows],
    }


@router.get(
    "/account/ledger-reconciliation",
    response_model=EquityLedgerReconciliationResponse,
)
def equity_account_ledger_reconciliation(
    user: CurrentUser, session: SessionDep, response: Response
) -> EquityLedgerReconciliationResponse:
    """Check every saved equity deployment against the simulated wallet."""
    response.headers["Cache-Control"] = "no-store"
    account = session.get(EquityAccount, user.id)
    initial = account.initial_cash if account else Decimal(
        get_settings().initial_equity_paper_capital
    )
    rows = list(session.scalars(select(EquityDeployment).where(
        EquityDeployment.user_id == user.id,
    )))
    lifecycle_events = list(session.scalars(select(AuditEvent).where(
        AuditEvent.actor_user_id == user.id,
        AuditEvent.event_type.in_((
            "equity.deployment.applied", "equity.deployment.entered",
            "equity.deployment.exit_requested", "equity.deployment.exited",
            "equity.deployment.cancelled", "equity.deployment.expired",
        )),
    )))
    cash_events = list(session.scalars(
        select(EquityCashEvent).where(EquityCashEvent.user_id == user.id)
        .order_by(EquityCashEvent.id)
    ))
    result = reconcile_equity_ledger(
        user_id=user.id, initial_cash=initial,
        recorded_cash=account.cash if account else initial,
        deployments=rows, audit_events=lifecycle_events,
    )
    cash_journal = reconcile_equity_cash_journal(
        user_id=user.id, initial_cash=initial,
        recorded_cash=account.cash if account else initial, events=cash_events,
        deployments=rows,
    )
    return EquityLedgerReconciliationResponse(
        **vars(result), cash_journal=EquityCashJournalResponse(**vars(cash_journal))
    )


@router.get("/dashboard")
def equity_dashboard(user: CurrentUser, session: SessionDep, response: Response) -> dict[str, Any]:
    """All-time account-scoped paper totals, independent of the recent-deployments limit."""
    response.headers["Cache-Control"] = "no-store"

    def empty(strategy: str = "") -> dict[str, Any]:
        return {
            "strategy": strategy, "gross_profit": Decimal(0), "gross_loss": Decimal(0),
            "closed_trades": 0, "winning_trades": 0, "closed_unpriced": 0,
            "active_strategies": 0,
        }

    total = empty()
    by_strategy: dict[str, dict[str, Any]] = {}
    daily: dict[tuple[str, str], dict[str, Any]] = {}
    recent_start = datetime.now(timezone.utc).astimezone(IST).date() - timedelta(days=13)
    rows = session.execute(
        select(
            EquityDeployment.strategy, EquityDeployment.status,
            EquityDeployment.state, EquityDeployment.updated_at,
        )
        .where(EquityDeployment.user_id == user.id)
        .execution_options(yield_per=500)
    )
    for strategy, status, state, updated_at in rows:
        strategy_name = str(strategy)
        group = by_strategy.setdefault(strategy_name, empty(strategy_name))
        for item in (total, group):
            if status in ACTIVE:
                item["active_strategies"] += 1
            if status != "CLOSED":
                continue
            raw_pnl = state.get("realized_pnl") if isinstance(state, dict) else None
            try:
                pnl = Decimal(str(raw_pnl)) if raw_pnl is not None else None
            except InvalidOperation:
                pnl = None
            if pnl is None or not pnl.is_finite():
                item["closed_unpriced"] += 1
            else:
                item["closed_trades"] += 1
                if pnl > 0:
                    item["winning_trades"] += 1
                item["gross_profit" if pnl >= 0 else "gross_loss"] += pnl
        if status == "CLOSED" and pnl is not None and pnl.is_finite():
            recorded_at = cast(datetime, updated_at)
            close_time = (
                recorded_at.replace(tzinfo=timezone.utc)
                if recorded_at.tzinfo is None else recorded_at
            )
            local_day = close_time.astimezone(IST).date()
            if local_day >= recent_start:
                day = local_day.isoformat()
                bucket = daily.setdefault((day, strategy_name), {
                    "date": day, "strategy": strategy_name,
                    "closed_trades": 0, "realized_net": Decimal(0),
                })
                bucket["closed_trades"] += 1
                bucket["realized_net"] += pnl

    def present(item: dict[str, Any]) -> dict[str, Any]:
        profit, loss = item["gross_profit"], item["gross_loss"]
        return {
            "strategy": item["strategy"], "gross_profit": str(profit),
            "gross_loss": str(loss), "realized_net": str(profit + loss),
            "closed_trades": item["closed_trades"],
            "winning_trades": item["winning_trades"],
            "closed_unpriced": item["closed_unpriced"],
            "active_strategies": item["active_strategies"],
        }

    return {
        "scope": "all_time", "recent_activity_days": 14, "totals": present(total),
        "strategies": [present(by_strategy[key]) for key in sorted(by_strategy)],
        "daily": [
            {**daily[key], "realized_net": str(daily[key]["realized_net"])}
            for key in sorted(daily)
        ],
    }


@router.get("/deployments/{deployment_id}/decisions")
def deployment_decisions(
    deployment_id: UUID, user: CurrentUser, session: SessionDep, response: Response
) -> list[dict[str, Any]]:
    """Account-scoped material paper decisions; no broker actions or market-data calls."""
    response.headers["Cache-Control"] = "no-store"
    deployment = session.scalar(select(EquityDeployment.id).where(
        EquityDeployment.id == deployment_id,
        EquityDeployment.user_id == user.id,
    ))
    if deployment is None:
        raise HTTPException(404, "Equity deployment not found")
    events = session.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.actor_user_id == user.id,
            AuditEvent.correlation_id == str(deployment_id),
            AuditEvent.event_type.like("equity.deployment.%"),
        )
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(200)
    )
    return [{
        "id": str(event.id),
        "event_type": event.event_type,
        "created_at": utc(event.created_at),
        "details": event.details,
    } for event in events]


@router.post("/deployments", status_code=201)
async def deploy(body: DeployRequest, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    if not get_settings().paper_trading_enabled:
        raise HTTPException(403, "Paper trading is disabled")
    if body.strategy not in STRATEGIES:
        raise HTTPException(422, "Unknown equity strategy")
    uid = user.id
    digest = hashlib.sha256(body.model_dump_json(exclude={"command_key"}).encode()).hexdigest()
    # Resolve completed commands before provider I/O so a retry survives a data outage.
    previous = session.scalar(
        select(EquityDeployment).where(
            EquityDeployment.user_id == uid, EquityDeployment.command_key == str(body.command_key)
        )
    )
    if previous:
        if previous.request_hash != digest:
            raise HTTPException(409, "Command key was already used with different parameters")
        return serialize(previous)
    item = instrument(body, uid, session)
    source, key, symbol = item.provider, item.instrument_key, item.trading_symbol
    provider = get_market_data_provider()
    if source != provider.provider_id:
        raise HTTPException(409, "Select an instrument from the active data provider")
    now = datetime.now(timezone.utc)
    try:
        bars = await history(provider, key, now)
    except Exception as error:
        raise HTTPException(503, "Cannot deploy without valid completed daily history") from error
    baseline = bars[-1].timestamp.astimezone(ZONE).date()
    if len(bars) < MINIMUM_BARS[body.strategy]:
        raise HTTPException(
            422, f"This strategy needs {MINIMUM_BARS[body.strategy]} completed bars"
        )
    if (now.astimezone(ZONE).date() - baseline).days > 4:
        raise HTTPException(422, "Daily history is stale; deployment blocked")
    threshold = entry_threshold(body.strategy, bars)
    initial_state = {
        "signal_after": baseline.isoformat(),
        "quote_mode": "INDICATIVE" if body.indicative_quotes else "VERIFIED",
        "detail": "Deployed; waiting for a new completed daily candle",
        "latest_daily_signal_date": baseline.isoformat(),
        "latest_daily_signal_met": signal(body.strategy, bars),
        "latest_daily_signal_after_apply": False,
    }
    if threshold is not None:
        initial_state["entry_threshold_price"] = str(money(threshold))
    try:
        wallet = lock_wallet(session, uid)
    except EquityCashJournalMismatch as error:
        raise HTTPException(409, str(error)) from error
    previous = session.scalar(
        select(EquityDeployment).where(
            EquityDeployment.user_id == uid, EquityDeployment.command_key == str(body.command_key)
        )
    )
    if previous:
        if previous.request_hash != digest:
            raise HTTPException(409, "Command key was already used with different parameters")
        return serialize(previous)
    active = list(
        session.scalars(
            select(EquityDeployment).where(
                EquityDeployment.user_id == uid, EquityDeployment.status.in_(ACTIVE)
            )
        )
    )
    if any(row.provider == source and row.instrument_key == key for row in active):
        raise HTTPException(409, "This symbol already has an active equity deployment")
    if len(active) >= 5 or wallet.cash < body.budget:
        raise HTTPException(
            422, "Insufficient unreserved cash or five active deployment limit reached"
        )
    row = EquityDeployment(
        user_id=uid,
        command_key=str(body.command_key),
        request_hash=digest,
        provider=source,
        instrument_key=key,
        symbol=symbol,
        strategy=body.strategy,
        version=VERSION,
        budget=body.budget,
        risk_budget=body.risk_budget,
        status="WAITING_FOR_ENTRY",
        created_at=now,
        updated_at=now,
        state=initial_state,
    )
    wallet.cash -= body.budget
    session.add(row)
    session.flush()
    record_equity_cash_event(
        session, wallet, row.id, kind="RESERVE", delta=-body.budget,
    )
    session.add(AuditEvent(
        actor_user_id=uid,
        correlation_id=str(row.id),
        event_type="equity.deployment.applied",
        details={
            "deployment_id": str(row.id),
            "strategy": row.strategy,
            "strategy_version": row.version,
            "provider": source,
            "instrument_key": key,
            "symbol": symbol,
            "allocation": str(body.budget),
            "planned_risk": str(body.risk_budget),
            "quote_mode": "INDICATIVE" if body.indicative_quotes else "VERIFIED",
            "signal_after": baseline.isoformat(),
        },
    ))
    session.commit()
    return serialize(row)


@router.post("/deployments/{deployment_id}/stop")
def stop(deployment_id: UUID, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    uid = user.id
    try:
        wallet = lock_wallet(session, uid)
    except EquityCashJournalMismatch as error:
        raise HTTPException(409, str(error)) from error
    row = session.get(EquityDeployment, deployment_id, populate_existing=True)
    if row is None or row.user_id != uid:
        raise HTTPException(404, "Deployment not found")
    if row.status == "WAITING_FOR_ENTRY":
        row.status = "CANCELLED"
        wallet.cash += row.budget
        record_equity_cash_event(
            session, wallet, row.id, kind="RELEASE", delta=row.budget,
        )
        row.state = {**row.state, "detail": "Cancelled before entry; reserved cash released"}
        session.add(AuditEvent(
            actor_user_id=uid,
            correlation_id=str(row.id),
            event_type="equity.deployment.cancelled",
            details={
                "deployment_id": str(row.id),
                "strategy": row.strategy,
                "reserved_cash_released": str(row.budget),
            },
        ))
    elif row.status == "OPEN":
        row.status = "EXIT_REQUESTED"
        row.state = {**row.state, "detail": "Exit requested; waiting for a valid open-market quote"}
        session.add(AuditEvent(
            actor_user_id=uid,
            correlation_id=str(row.id),
            event_type="equity.deployment.exit_requested",
            details={"deployment_id": str(row.id), "strategy": row.strategy},
        ))
    row.updated_at = datetime.now(timezone.utc)
    session.commit()
    return serialize(row)
