"""Arm NIFTY futures templates and let the paper worker enter them when their rules pass.

Mirrors option_deployments: provider I/O happens outside account locks, every state
change is fenced by the worker lease, and the fill itself goes through open_trade so
quote, session, margin and account risk gates stay in one place.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from apps.api.paper_schemas import PaperDeploymentResponse
from apps.api.services.futures_paper import (
    MAX_WAITING_PAPER_STRATEGIES,
    ArmedEntry,
    FuturesContractResponse,
    FuturesError,
    FuturesTradeCreateRequest,
    _open_session,
    _quote,
    _require_futures_provider,
    ensure_account,
    list_contracts,
    open_trade,
)
from apps.api.services.option_deployments import (
    _finish_waiting,
    _mark_entry_attempt,
    _record_retryable_block,
)
from apps.api.settings import Settings
from packages.database.models import AuditEvent, PaperDeployment, User
from packages.features.options import calculate_option_features
from packages.market_data.contracts import (
    MarketDataProvider,
    MarketDataProviderError,
    MarketDataUnavailable,
    OptionChain,
)
from packages.market_data.quality import assess_option_chain
from packages.paper_trading.demotion import enforce_strategy_demotion
from packages.paper_trading.futures import FuturesSide, money, quote_evidence
from packages.paper_trading.futures_strategies import (
    RESEARCH_NOTE,
    ROLLOVER_DAYS_BEFORE_EXPIRY,
    FuturesExitPolicy,
    FuturesRuleMetric,
    FuturesTemplate,
    evaluate_futures_conditions,
    front_contract_expiry,
    futures_rule_metrics,
    get_futures_template,
    list_futures_templates,
    parse_futures_exit_policy,
)
from packages.paper_trading.session import exchange_today
from packages.paper_trading.transactions import (
    PaperLedgerMismatchError,
    assert_consistent_ledger,
    lock_account,
)
from packages.paper_trading.worker_lease import LeaseToken, assert_owned

MAX_CHECKS_PER_CYCLE = 5


class FuturesDeploymentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_key: str = Field(min_length=1, max_length=64)
    expected_template_version: str = Field(min_length=1, max_length=32)
    lots: int = Field(ge=1, le=10)
    acknowledged_unbounded_risk: Literal[True]


class FuturesStrategyResponse(BaseModel):
    key: str
    name: str
    description: str
    version: str
    side: FuturesSide
    intraday: bool
    stop_points: Decimal
    target_points: Decimal
    trailing_points: Decimal
    conditions: list[dict[str, str]]
    note: str = RESEARCH_NOTE


def list_strategies() -> list[FuturesStrategyResponse]:
    return [
        FuturesStrategyResponse(
            key=row.key, name=row.name, description=row.description, version=row.version,
            side=row.side, intraday=row.intraday, stop_points=row.stop_points,
            target_points=row.target_points, trailing_points=row.trailing_points,
            conditions=[condition.as_dict() for condition in row.conditions],
        )
        for row in list_futures_templates()
    ]


async def _front_contract(
    provider: MarketDataProvider, rollover_days: int
) -> FuturesContractResponse:
    contracts = await list_contracts(provider)
    expiry = front_contract_expiry(
        [row.expiry for row in contracts], exchange_today(), rollover_days
    )
    contract = next((row for row in contracts if row.expiry == expiry), None)
    if contract is None:
        raise FuturesError(409, "No NIFTY futures contract is listed outside the rollover window")
    return contract


def _contract_snapshot(contract: FuturesContractResponse) -> dict[str, Any]:
    return {
        "instrument_key": contract.instrument_key,
        "trading_symbol": contract.trading_symbol,
        "expiry": contract.expiry.isoformat(),
        "lot_size": contract.lot_size,
    }


def _per_trade_limit(initial_cash: Decimal, settings: Settings) -> Decimal:
    return money(initial_cash * Decimal(settings.paper_max_trade_risk_pct) / Decimal(100))


async def arm_futures_deployment(
    body: FuturesDeploymentCreateRequest,
    user_id: UUID,
    session: Session,
    idempotency_key: str,
    provider: MarketDataProvider,
    settings: Settings,
) -> PaperDeploymentResponse:
    if not settings.paper_trading_enabled:
        raise FuturesError(503, "Paper trading is disabled by configuration")
    template = get_futures_template(body.strategy_key)
    if template is None:
        raise FuturesError(422, "Only registered futures templates can be armed")
    if body.expected_template_version != template.version:
        raise FuturesError(409, "Futures template changed; reload the strategy list")
    digest = hashlib.sha256(json.dumps(
        body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()

    def replay() -> PaperDeploymentResponse | None:
        previous = session.scalar(select(PaperDeployment).where(
            PaperDeployment.user_id == user_id,
            PaperDeployment.command_key == idempotency_key,
        ))
        if previous is None:
            return None
        if previous.request_hash != digest:
            raise FuturesError(409, "Command key was already used with different parameters")
        return PaperDeploymentResponse.from_model(previous)

    existing = replay()
    if existing is not None:
        return existing
    _require_futures_provider(provider)
    if not callable(getattr(provider, "get_option_chain", None)):
        raise FuturesError(503, "Futures entry rules need an options-capable market provider")
    ensure_account(session, user_id, settings)
    session.commit()  # Never hold a transaction across provider I/O.
    contract = await _front_contract(provider, ROLLOVER_DAYS_BEFORE_EXPIRY)
    stop, target, trailing = template.amounts(body.lots, contract.lot_size)

    account = lock_account(session, user_id)
    if account is None:
        session.rollback()
        raise FuturesError(409, "Paper account is not initialized; open the paper account first")
    try:
        if account.kill_switch_enabled:
            raise FuturesError(423, "Paper kill switch is active")
        try:
            assert_consistent_ledger(session, account)
        except PaperLedgerMismatchError as error:
            raise FuturesError(409, str(error)) from error
        demotion = enforce_strategy_demotion(
            session, user_id, template.key, settings.paper_demotion_limits(account.initial_cash),
        )
        if demotion is not None:
            session.commit()
            raise FuturesError(
                423, f"Strategy {template.key} is paused; reinstate it to allow new paper entries"
            )
        existing = replay()
        if existing is not None:
            session.rollback()
            return existing
        limit = _per_trade_limit(account.initial_cash, settings)
        if stop > limit:
            raise FuturesError(
                409,
                f"Stop-loss of ₹{stop} for {body.lots} lot(s) exceeds the per-trade risk cap of "
                f"₹{limit}; use fewer lots",
            )
        waiting = session.scalars(select(PaperDeployment).where(
            PaperDeployment.user_id == user_id,
            PaperDeployment.status == "WAITING_FOR_ENTRY",
        )).all()
        if len(waiting) >= MAX_WAITING_PAPER_STRATEGIES:
            raise FuturesError(409, "At most 26 paper strategies can wait for entry")
        if any(
            row.instrument_kind == "FUTURES"
            and row.request_data.get("strategy_key") == template.key
            for row in waiting
        ):
            raise FuturesError(409, "This futures strategy is already waiting for entry")
        now = datetime.now(timezone.utc)
        row = PaperDeployment(
            user_id=user_id, command_key=idempotency_key, request_hash=digest,
            instrument_kind="FUTURES",
            request_data={
                "strategy_key": template.key,
                "strategy_name": template.name,
                "strategy_definition": template.definition(),
                "side": template.side,
                "lots": body.lots,
                "stop_loss_amount": str(stop),
                "target_profit_amount": str(target),
                "trailing_stop_amount": str(trailing),
                "acknowledged_unbounded_risk": True,
                "armed_contract": _contract_snapshot(contract),
                "market_data_provider": provider.provider_id,
                "exit_policy": FuturesExitPolicy(
                    settings.paper_expiry_square_off_minutes_before_close,
                    intraday=template.intraday,
                    rollover_days_before_expiry=ROLLOVER_DAYS_BEFORE_EXPIRY,
                ).as_dict(),
            },
            status="WAITING_FOR_ENTRY",
            detail="Armed; waiting for a later qualifying open-market check",
            trade_id=None, created_at=now, updated_at=now,
        )
        session.add(row)
        session.flush()
        session.add(AuditEvent(
            actor_user_id=user_id, correlation_id=str(row.id),
            event_type="paper.deployment.armed",
            details={"deployment_id": str(row.id), "strategy_key": template.key},
        ))
        session.commit()
    except FuturesError:
        session.rollback()
        raise
    return PaperDeploymentResponse.from_model(row)


@dataclass(frozen=True)
class _MarketView:
    """One cycle's observation of the front contract, shared by all waiting strategies."""

    contract: FuturesContractResponse
    chain: OptionChain
    quality_status: str
    quality: dict[str, Any]
    metrics: dict[FuturesRuleMetric, Decimal | None]
    evidence: dict[str, Any]
    market_as_of: datetime


async def _regime_bias(
    provider: MarketDataProvider, chain: OptionChain, settings: Settings
) -> str | None:
    # Import here: the HTTP app owns the regime window and imports this package's routes.
    from apps.api.main import _completed_nifty_regime

    regime, _, _, _ = await _completed_nifty_regime(
        chain, accept_timestamp_warnings=settings.paper_allow_unverified_quotes,
        provider=provider,
    )
    return regime.directional_bias if regime is not None else None


async def _market_view(provider: MarketDataProvider, settings: Settings) -> _MarketView:
    await _open_session(provider)
    contract = await _front_contract(provider, ROLLOVER_DAYS_BEFORE_EXPIRY)
    quote = await _quote(provider, contract.instrument_key)
    try:
        # NIFTY options share the monthly future's expiry, so this chain is always listed.
        chain = await provider.get_option_chain("NIFTY", contract.expiry)
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        raise FuturesError(503, str(error)) from error
    if chain.underlying != "NIFTY" or chain.expiry != contract.expiry:
        raise FuturesError(503, "Option-chain identity differs from the futures contract")
    assessed = assess_option_chain(chain)
    chain = chain.model_copy(update={"quality": assessed})
    features = calculate_option_features(chain) if assessed.status != "FAIL" else None
    bias = await _regime_bias(provider, chain, settings) if features is not None else None
    metrics = futures_rule_metrics(
        quote=quote, spot=chain.underlying_spot, regime_bias=bias,
        put_call_ratio_oi=features.put_call_ratio_oi if features is not None else None,
    )
    return _MarketView(
        contract=contract, chain=chain, quality_status=assessed.status,
        quality=assessed.model_dump(mode="json", exclude={"checked_at"}),
        metrics=metrics, evidence=quote_evidence(quote), market_as_of=chain.as_of,
    )


def _entry_rules(
    template: FuturesTemplate, view: _MarketView
) -> tuple[dict[str, object], bool]:
    results = evaluate_futures_conditions(template.conditions, view.metrics)
    return (
        {
            "conditions": [row.as_dict() for row in template.conditions],
            "results": [row.as_dict() for row in results],
            "market_as_of": view.market_as_of.isoformat(),
            "quality_status": view.quality_status,
            "contract": _contract_snapshot(view.contract),
        },
        all(row.passed is True for row in results),
    )


def _record_entry_check(
    factory: sessionmaker[Session], token: LeaseToken, deployment_id: UUID, user_id: UUID,
    provider_id: str, request_hash: str, strategy_key: str, view: _MarketView,
    decision: str, rules: dict[str, object] | None,
) -> bool:
    """Save the decision using the same audit shape as armed option checks."""
    # Import here: the HTTP app owns the dedupe and digest rules for entry-check events.
    from apps.api.main import _record_armed_entry_check

    with factory() as session:
        lock_account(session, user_id)
        try:
            _record_armed_entry_check(session, user_id, deployment_id, token, {
                "version": 1,
                "instrument_kind": "FUTURES",
                "provider": provider_id,
                "request_hash": request_hash,
                "strategy_key": strategy_key,
                "expiry": view.contract.expiry.isoformat(),
                "market_as_of": view.market_as_of.isoformat(),
                "timestamp_basis": view.chain.timestamp_basis,
                "decision": decision,
                "quality": view.quality,
                "futures_quote": view.evidence,
                "rule_metrics": {
                    name: str(value) if value is not None else None
                    for name, value in view.metrics.items()
                },
                **({"entry_rule_snapshot": rules} if rules is not None else {}),
            })
        except HTTPException:
            session.rollback()
            return False
        session.commit()
    return True


def _sync_armed_contract(
    factory: sessionmaker[Session], token: LeaseToken, deployment_id: UUID, user_id: UUID,
    contract: FuturesContractResponse,
) -> None:
    """Show which contract a waiting strategy now targets after the front month rolls."""
    with factory() as session:
        lock_account(session, user_id)
        if not assert_owned(session, token):
            session.rollback()
            return
        row = session.scalar(select(PaperDeployment).where(
            PaperDeployment.id == deployment_id,
            PaperDeployment.user_id == user_id,
            PaperDeployment.status == "WAITING_FOR_ENTRY",
        ).with_for_update())
        if row is None:
            session.rollback()
            return
        previous = row.request_data.get("armed_contract")
        row.request_data = {**row.request_data, "armed_contract": _contract_snapshot(contract)}
        row.updated_at = datetime.now(timezone.utc)
        session.add(AuditEvent(
            actor_user_id=user_id, correlation_id=str(deployment_id),
            event_type="paper.deployment.contract_rolled",
            details={"deployment_id": str(deployment_id), "from": previous,
                     "to": _contract_snapshot(contract)},
        ))
        session.commit()


async def process_futures_deployments(
    factory: sessionmaker[Session], provider: MarketDataProvider, token: LeaseToken,
    settings: Settings,
) -> tuple[int, int, list[str]]:
    """Check up to five waiting futures templates per cycle against one market view."""
    with factory() as session:
        rows = [
            (row.id, row.user_id, row.request_hash, row.request_data)
            for row in session.scalars(
                select(PaperDeployment)
                .where(
                    PaperDeployment.status == "WAITING_FOR_ENTRY",
                    PaperDeployment.instrument_kind == "FUTURES",
                )
                .order_by(
                    PaperDeployment.updated_at, PaperDeployment.created_at, PaperDeployment.id,
                )
                .limit(20)
            )
        ]
    checked = filled = 0
    warnings: list[str] = []
    view: _MarketView | None = None
    view_error: FuturesError | None = None
    for deployment_id, user_id, request_hash, data in rows:
        template = get_futures_template(str(data.get("strategy_key")))
        try:
            exit_policy = parse_futures_exit_policy(data.get("exit_policy"))
            if template is None or data.get("strategy_definition") != template.definition():
                raise ValueError("Template changed")
            body = FuturesTradeCreateRequest(
                instrument_key="pending-contract", side=data["side"], lots=data["lots"],
                stop_loss_amount=data["stop_loss_amount"],
                target_profit_amount=data["target_profit_amount"],
                trailing_stop_amount=data["trailing_stop_amount"],
                acknowledged_unbounded_risk=True,
            )
            armed_lot_size = int(data["armed_contract"]["lot_size"])
        except (KeyError, TypeError, ValueError):
            _finish_waiting(
                factory, token, deployment_id, user_id, "REJECTED",
                "Saved futures rules or template changed; arm a new strategy",
            )
            continue
        if checked >= MAX_CHECKS_PER_CYCLE:
            continue
        if not _mark_entry_attempt(factory, token, deployment_id, user_id):
            continue
        checked += 1
        if provider.provider_id != data.get("market_data_provider"):
            reason = "Armed futures strategy provider differs from the active provider"
            _record_retryable_block(factory, token, deployment_id, user_id, 503, reason)
            warnings.append(reason)
            continue
        if view is None and view_error is None:
            try:
                view = await _market_view(provider, settings)
            except FuturesError as error:
                view_error = error
                if error.status_code >= 500:
                    warnings.append("Futures market data unavailable; armed entries not filled")
        if view is None:
            detail = view_error.detail if view_error else "Market view unavailable"
            code = view_error.status_code if view_error else 503
            _record_retryable_block(
                factory, token, deployment_id, user_id, code,
                detail if code < 500 else "Futures entry could not be evaluated; retrying",
            )
            continue
        if view.contract.lot_size != armed_lot_size:
            _finish_waiting(
                factory, token, deployment_id, user_id, "REJECTED",
                "Contract lot size changed since arming; stop amounts no longer apply. Arm again.",
            )
            continue
        if data["armed_contract"].get("instrument_key") != view.contract.instrument_key:
            _sync_armed_contract(factory, token, deployment_id, user_id, view.contract)
        if template is None:  # Already rejected above; keeps the type narrowed.
            continue
        if view.quality_status == "FAIL":
            _record_entry_check(
                factory, token, deployment_id, user_id, provider.provider_id, request_hash,
                template.key, view, "QUALITY_FAIL", None,
            )
            continue
        rules, passed = _entry_rules(template, view)
        rules["market_data_provider"] = provider.provider_id
        if not _record_entry_check(
            factory, token, deployment_id, user_id, provider.provider_id, request_hash,
            template.key, view, "RULES_PASS" if passed else "RULES_NOT_MET", rules,
        ) or not passed:
            continue
        with factory() as session:
            user = session.get(User, user_id)
            if user is None or not user.active:
                session.rollback()
                _finish_waiting(
                    factory, token, deployment_id, user_id, "REJECTED",
                    "Account is inactive; no paper entry opened",
                )
                continue
            try:
                await open_trade(
                    body.model_copy(update={"instrument_key": view.contract.instrument_key}),
                    user_id, session, f"auto_{deployment_id.hex}", provider, settings,
                    armed=ArmedEntry(
                        deployment_id=deployment_id, strategy_key=template.key,
                        strategy_name=template.name, lease=token,
                        exit_policy=exit_policy, entry_rules=rules,
                    ),
                )
                filled += 1
            except FuturesError as error:
                session.rollback()
                if error.status_code in {404, 422}:
                    _finish_waiting(
                        factory, token, deployment_id, user_id, "REJECTED",
                        "Saved strategy or contract is no longer valid; arm a new strategy",
                    )
                    continue
                if error.status_code >= 500:
                    warnings.append("Futures market data unavailable; armed entry was not filled")
                _record_retryable_block(
                    factory, token, deployment_id, user_id, error.status_code,
                    error.detail if error.status_code < 500
                    else "Futures entry could not be evaluated; retrying",
                )
            except Exception:
                session.rollback()
                warnings.append("Armed futures entry failed; no paper fill was confirmed")
    return checked, filled, warnings
