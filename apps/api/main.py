import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import desc, func, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from apps.api.auth import (
    CurrentUser,
    Role,
    SessionDep,
    authenticate,
    bearer_scheme,
    create_access_token,
    hash_password,
    require_roles,
    session_id_from_token,
    session_start_from_token,
    verify_password,
)
from apps.api.backtest_schemas import (
    OptionsBacktestRequest,
    ScheduledOptionsBacktestRequest,
)
from apps.api.capabilities import CapabilityMatrix, current_capabilities
from apps.api.equity_routes import router as equity_router
from apps.api.login_throttle import (
    LoginThrottleUnavailable,
    get_login_throttle,
    get_registration_throttle,
)
from apps.api.market_outlook import IST, nifty_benchmark
from apps.api.market_outlook import router as market_outlook_router
from apps.api.paper_schemas import (
    NotificationListResponse,
    OptionMarketConditionEvidenceResponse,
    PaperAccountResponse,
    PaperDeploymentResponse,
    PaperEntryReconciliationResponse,
    PaperExitReconciliationResponse,
    PaperExpiryIndexCloseReference,
    PaperExpirySettlementPreviewResponse,
    PaperExpirySettlementRequest,
    PaperKillSwitchRequest,
    PaperKillSwitchResponse,
    PaperLedgerReconciliationResponse,
    PaperMonitorResponse,
    PaperOfficialExpirySettlementPreviewResponse,
    PaperOfficialIndexCloseReference,
    PaperOrderResponse,
    PaperStrategyPresetCreateRequest,
    PaperStrategyPresetResponse,
    PaperTradeCreateRequest,
    PaperTradeListResponse,
    PaperTradeResponse,
    StrategyDefinitionResponse,
    StrategyLegDefinitionResponse,
    UserNotificationResponse,
)
from apps.api.research_routes import router as research_router
from apps.api.schemas import (
    ChangePasswordRequest,
    InstrumentInput,
    InstrumentResponse,
    LoginRequest,
    RegisterRequest,
    SecurityActivityResponse,
    TokenResponse,
    UserResponse,
)
from apps.api.services.paper_monitor import monitor_exits
from apps.api.settings import get_settings
from apps.api.strategy_schemas import (
    StrategyDefinitionSnapshot,
    StrategyVersionCreateRequest,
    StrategyVersionResponse,
)
from apps.api.watchlist_schemas import WatchlistAddRequest, WatchlistItemResponse
from packages.backtesting.contracts import (
    OptionsBacktestResult,
    OptionsBacktestRunRecord,
    ScheduledOptionsBacktestResult,
    ScheduledOptionsBacktestRunRecord,
)
from packages.backtesting.replay import (
    BacktestDataError,
    run_scheduled_expiry_replay,
    run_single_expiry_replay,
)
from packages.database.models import (
    AuditEvent,
    AuthSession,
    BacktestRun,
    Instrument,
    PaperAccount,
    PaperDeployment,
    PaperEntryCommand,
    PaperOrder,
    PaperStrategyPreset,
    PaperTrade,
    ResearchPolicySet,
    ResearchStudy,
    StrategyVersion,
    User,
    UserNotification,
    WatchlistItem,
    WorkerLease,
)
from packages.features.options import OptionChainFeatures, calculate_option_features
from packages.indicators.calculations import calculate_indicators
from packages.indicators.contracts import IndicatorSnapshot
from packages.instruments.service import (
    available_expiries,
    available_strikes,
    list_instruments,
    register_instrument,
)
from packages.market_data.contracts import (
    DiscoveredInstrument,
    ExpiredOptionContract,
    HistoricalBar,
    MarketDataPlanRequired,
    MarketDataProvider,
    MarketDataProviderError,
    MarketDataUnavailable,
    OptionChain,
    OptionContract,
    OptionContractQuote,
    ProviderHealth,
    Quote,
)
from packages.market_data.index_catalog import OptionIndex, option_indexes, resolve_option_index
from packages.market_data.provider import get_market_data_provider
from packages.market_data.quality import assess_option_chain
from packages.options.market_fit import (
    MarketFitAssessment,
    MarketFitRequest,
    ProviderMarketFitRequest,
    assess_nifty_market_fit,
)
from packages.options.models import (
    OptionGreeksInput,
    OptionGreeksResult,
    PayoffLeg,
    PayoffRequest,
    PayoffResult,
)
from packages.options.payoff import calculate_expiry_payoff
from packages.options.pricing import calculate_greeks
from packages.paper_trading.costs import cost_snapshot, summarize_costs
from packages.paper_trading.daily_risk import daily_realized_pnl, daily_risk_budget
from packages.paper_trading.expiry_settlement import (
    ExpirySettlement,
    calculate_expiry_settlement,
)
from packages.paper_trading.index_scope import saved_option_index
from packages.paper_trading.ledger_reconciliation import reconcile_paper_ledger
from packages.paper_trading.observed_entries import replay_observed_entries
from packages.paper_trading.observed_exits import replay_observed_exits
from packages.paper_trading.official_index_close import (
    NSE_INDEX_NAMES,
    OfficialCloseUnavailable,
    archive_url,
    fetch_index_close,
    fetch_nifty_close,
)
from packages.paper_trading.option_evidence import (
    observed_pcr_condition,
    option_policy_signature,
    qualifies_automated_option_outcome,
    trade_entry_condition,
)
from packages.paper_trading.option_policy import EXPIRY_TEST_POLICY_VERSION, OptionExitPolicy
from packages.paper_trading.quotes import PaperQuoteError, require_quote
from packages.paper_trading.rules import EntryCondition, RuleMetric, evaluate_entry_conditions
from packages.paper_trading.session import (
    PaperSessionError,
    exchange_today,
    expiry_has_passed,
    expiry_square_off_due,
    require_derivatives_session_open,
)
from packages.paper_trading.strategies import STRATEGIES, StrategyDefinition, get_strategy
from packages.paper_trading.transactions import (
    PaperLedgerMismatchError,
    assert_consistent_ledger,
    lock_account,
)
from packages.paper_trading.worker_lease import WORKER_NAME, LeaseToken, assert_owned
from packages.regime.candidate_registry import fingerprint
from packages.regime.classifier import MarketRegimeResult, classify_market_regime
from packages.regime.frozen_nifty_study import FrozenNiftyStudy
from packages.regime.research_policy_sets import current_policy_set_ids
from packages.regime.research_recommendations import (
    build_market_fit_opportunity,
    recommendation_view,
    record_recommendation,
)

app = FastAPI(title="Trading Platform API", version="0.8.0")
app.include_router(equity_router)
app.include_router(research_router)
app.include_router(market_outlook_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)
@app.middleware("http")
async def security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
    )
    return response


AdminUser = Annotated[User, Depends(require_roles(Role.ADMIN))]
_CENT = Decimal("0.01")


def _new_auth_session(
    session: SessionDep, user: User, *, started_at: datetime | None = None
) -> AuthSession:
    started = started_at or datetime.now(timezone.utc)
    auth_session = AuthSession(
        user_id=user.id, started_at=started,
        absolute_expires_at=started + timedelta(minutes=get_settings().session_max_minutes),
    )
    session.add(auth_session)
    session.flush()
    return auth_session


def _token_response(user: User, auth_session: AuthSession) -> TokenResponse:
    started = auth_session.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    access_token, expires_in = create_access_token(
        user, session_started_at=started, session_id=auth_session.id
    )
    return TokenResponse(access_token=access_token, expires_in=expires_in)


def _paper_account(session: SessionDep, user: User, *, create: bool = True) -> PaperAccount | None:
    account = session.scalar(select(PaperAccount).where(PaperAccount.user_id == user.id))
    if account is None and create:
        capital = Decimal(get_settings().initial_paper_capital)
        account = PaperAccount(user_id=user.id, initial_cash=capital, cash_balance=capital)
        session.add(account)
        try:
            session.flush()
            session.commit()
        except IntegrityError:
            session.rollback()
            account = session.scalar(select(PaperAccount).where(PaperAccount.user_id == user.id))
    return account


def _get_paper_account(session: SessionDep, user: User) -> PaperAccount:
    account = _paper_account(session, user)
    if account is None:
        raise HTTPException(status_code=500, detail="Paper account could not be initialized")
    return account


def _require_consistent_paper_ledger(session: SessionDep, account: PaperAccount) -> None:
    try:
        assert_consistent_ledger(session, account)
    except PaperLedgerMismatchError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error


def _paper_trade_response(trade: PaperTrade) -> PaperTradeResponse:
    return PaperTradeResponse.from_model(trade)


def _add_user_notification(
    session: SessionDep,
    user_id: UUID,
    event_type: str,
    title: str,
    message: str,
    details: dict[str, object],
) -> None:
    session.add(
        UserNotification(
            user_id=user_id,
            event_type=event_type,
            title=title,
            message=message,
            details=details,
        )
    )


def _strategy_contracts(
    strategy: StrategyDefinition,
    reference_strike: Decimal,
    expiry: date,
    contracts: list[OptionContract],
) -> list[tuple[OptionContract, str, int]]:
    expiry_contracts = [contract for contract in contracts if contract.expiry == expiry]
    strikes = sorted({contract.strike for contract in expiry_contracts})
    try:
        index = strikes.index(reference_strike)
    except ValueError:
        raise HTTPException(
            status_code=422, detail="Selected strike is unavailable for this expiry"
        ) from None
    selections: list[tuple[OptionContract, str, int]] = []
    for leg in strategy.legs:
        leg_index = index + leg.strike_offset
        if leg_index < 0 or leg_index >= len(strikes):
            raise HTTPException(
                status_code=422, detail="Strategy leg falls outside available strikes"
            )
        strike = strikes[leg_index]
        match = next(
            (
                contract
                for contract in contracts
                if contract.expiry == expiry
                and contract.strike == strike
                and contract.option_type == leg.option_type
            ),
            None,
        )
        if match is None:
            raise HTTPException(status_code=422, detail="A required option contract is unavailable")
        selections.append((match, leg.side, leg.quantity_ratio))
    return selections


def _require_quote(
    chain: OptionChain, instrument_key: str, side: str, quantity: int
) -> tuple[Decimal, OptionContractQuote]:
    settings = get_settings()
    try:
        return require_quote(
            chain,
            instrument_key,
            side,
            quantity,
            max_age_seconds=settings.paper_quote_max_age_seconds,
            allow_unverified=settings.paper_allow_unverified_quotes,
        )
    except PaperQuoteError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def _entry_replay(
    session: SessionDep,
    user_id: UUID,
    key: str,
    request_hash: str,
) -> PaperTradeResponse | None:
    command = session.scalar(
        select(PaperEntryCommand).where(
            PaperEntryCommand.user_id == user_id,
            PaperEntryCommand.idempotency_key == key,
        )
    )
    if command is None:
        return None
    if command.request_hash != request_hash:
        raise HTTPException(status_code=409, detail="Idempotency key belongs to a different entry")
    return PaperTradeResponse.model_validate(command.response_data)


@app.post(
    "/auth/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["authentication"],
)
def register(
    body: RegisterRequest, session: SessionDep, response: Response, request: Request
) -> TokenResponse:
    email = str(body.email).strip().lower()
    try:
        retry_after = get_registration_throttle().consume(
            email, request.client.host if request.client else "unknown"
        )
    except LoginThrottleUnavailable:
        raise HTTPException(
            status_code=503, detail="Account creation temporarily unavailable",
            headers={"Cache-Control": "no-store"},
        ) from None
    if retry_after is not None:
        raise HTTPException(
            status_code=429, detail="Too many account-creation attempts; try again later",
            headers={"Retry-After": str(retry_after), "Cache-Control": "no-store"},
        )
    user = User(email=email, password_hash=hash_password(body.password), role=Role.VIEWER.value)
    session.add(user)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Account could not be created") from None
    session.add(
        AuditEvent(
            actor_user_id=user.id,
            event_type="auth.registration.succeeded",
            details={"role": Role.VIEWER.value},
        )
    )
    auth_session = _new_auth_session(session, user)
    session.commit()
    session.refresh(user)
    response.headers["Cache-Control"] = "no-store"
    return _token_response(user, auth_session)


@app.post("/auth/login", response_model=TokenResponse, tags=["authentication"])
def login(
    body: LoginRequest, session: SessionDep, response: Response, request: Request
) -> TokenResponse:
    try:
        retry_after = get_login_throttle().consume(
            str(body.email), request.client.host if request.client else "unknown"
        )
    except LoginThrottleUnavailable:
        raise HTTPException(
            status_code=503, detail="Authentication temporarily unavailable",
            headers={"Cache-Control": "no-store"},
        ) from None
    if retry_after is not None:
        raise HTTPException(
            status_code=429, detail="Too many sign-in attempts; try again later",
            headers={"Retry-After": str(retry_after), "Cache-Control": "no-store"},
        )
    user = authenticate(session, str(body.email), body.password)
    if user is None:
        session.add(
            AuditEvent(event_type="auth.login.failed", details={"reason": "invalid_credentials"})
        )
        session.commit()
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
        )
    session.add(AuditEvent(actor_user_id=user.id, event_type="auth.login.succeeded", details={}))
    auth_session = _new_auth_session(session, user)
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return _token_response(user, auth_session)


@app.post("/auth/renew", response_model=TokenResponse, tags=["authentication"])
def renew(
    user: CurrentUser,
    session: SessionDep,
    response: Response,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> TokenResponse:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Invalid or expired credentials")
    try:
        started = session_start_from_token(credentials.credentials)
        session_id = session_id_from_token(credentials.credentials)
        if session_id is None:
            auth_session = _new_auth_session(session, user, started_at=started)
            session.add(
                AuditEvent(
                    actor_user_id=user.id, event_type="auth.session.legacy_upgraded", details={}
                )
            )
            session.commit()
        else:
            existing_session = session.scalar(
                select(AuthSession).where(
                    AuthSession.id == session_id, AuthSession.user_id == user.id
                )
            )
            if existing_session is None or existing_session.revoked_at is not None:
                raise ValueError("Session has been revoked")
            auth_session = existing_session
        renewed = _token_response(user, auth_session)
    except ValueError:
        raise HTTPException(
            status_code=401,
            detail="Session lifetime has ended; sign in again",
            headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
        ) from None
    response.headers["Cache-Control"] = "no-store"
    return renewed


@app.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, tags=["authentication"])
def logout(
    user: CurrentUser,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> Response:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Invalid or expired credentials")
    try:
        session_id = session_id_from_token(credentials.credentials)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired credentials") from None
    if session_id is not None:
        auth_session = session.scalar(
            select(AuthSession).where(
                AuthSession.id == session_id, AuthSession.user_id == user.id
            )
        )
        if auth_session is not None:
            auth_session.revoked_at = datetime.now(timezone.utc)
            session.add(
                AuditEvent(
                    actor_user_id=user.id, event_type="auth.session.revoked",
                    details={"session_id": str(session_id)},
                )
            )
            session.commit()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@app.post(
    "/auth/change-password", status_code=status.HTTP_204_NO_CONTENT,
    tags=["authentication"],
)
def change_password(
    body: ChangePasswordRequest, user: CurrentUser, session: SessionDep, request: Request
) -> Response:
    try:
        retry_after = get_login_throttle().consume(
            user.email, request.client.host if request.client else "unknown"
        )
    except LoginThrottleUnavailable:
        raise HTTPException(
            status_code=503, detail="Authentication temporarily unavailable",
            headers={"Cache-Control": "no-store"},
        ) from None
    if retry_after is not None:
        raise HTTPException(
            status_code=429, detail="Too many password attempts; try again later",
            headers={"Retry-After": str(retry_after), "Cache-Control": "no-store"},
        )
    if not verify_password(body.current_password, user.password_hash):
        session.add(AuditEvent(
            actor_user_id=user.id, event_type="auth.password_change.failed", details={}
        ))
        session.commit()
        raise HTTPException(
            status_code=401, detail="Current password is incorrect",
            headers={"Cache-Control": "no-store"},
        )
    if verify_password(body.new_password, user.password_hash):
        raise HTTPException(
            status_code=409, detail="Choose a different password",
            headers={"Cache-Control": "no-store"},
        )
    user.password_hash = hash_password(body.new_password)
    session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )
    session.add(AuditEvent(
        actor_user_id=user.id, event_type="auth.password_changed", details={}
    ))
    session.commit()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@app.get(
    "/auth/activity", response_model=list[SecurityActivityResponse],
    tags=["authentication"],
)
def security_activity(
    user: CurrentUser, session: SessionDep, response: Response,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[SecurityActivityResponse]:
    """Expose a bounded, own-account security timeline without audit metadata."""
    allowed = (
        "auth.registration.succeeded", "auth.login.succeeded",
        "auth.password_change.failed", "auth.password_changed",
        "auth.session.revoked", "auth.session.legacy_upgraded",
    )
    events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.actor_user_id == user.id, AuditEvent.event_type.in_(allowed))
        .order_by(desc(AuditEvent.created_at), desc(AuditEvent.id))
        .limit(limit)
    ).all()
    response.headers["Cache-Control"] = "no-store"
    return [
        SecurityActivityResponse(
            id=event.id, event_type=event.event_type, created_at=(
                event.created_at.replace(tzinfo=timezone.utc)
                if event.created_at.tzinfo is None else event.created_at
            ),
        )
        for event in events
    ]


@app.get("/auth/me", response_model=UserResponse, tags=["authentication"])
def me(user: CurrentUser, response: Response) -> User:
    response.headers["Cache-Control"] = "no-store"
    return user


@app.get("/instruments", response_model=list[InstrumentResponse], tags=["instruments"])
def instruments(
    user: CurrentUser,
    session: SessionDep,
    underlying: str | None = Query(default=None, max_length=32),
    expiry: date | None = None,
    exchange: str | None = Query(default=None, max_length=16),
    instrument_type: Literal["INDEX", "EQUITY", "ETF", "FUTURE", "OPTION"] | None = None,
    search: str | None = Query(default=None, min_length=1, max_length=64),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
) -> list[Instrument]:
    del user
    return list_instruments(
        session, underlying, expiry,
        exchange=exchange, instrument_type=instrument_type,
        search=search, limit=limit, offset=offset,
    )


@app.post("/instruments", response_model=InstrumentResponse, status_code=201, tags=["instruments"])
def create_instrument(body: InstrumentInput, session: SessionDep, user: AdminUser) -> Instrument:
    try:
        instrument = register_instrument(session, body.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    session.add(
        AuditEvent(
            actor_user_id=user.id,
            event_type="instrument.created",
            details={
                "exchange": body.exchange,
                "instrument_token": body.instrument_token,
                "provider": body.provider,
            },
        )
    )
    session.commit()
    session.refresh(instrument)
    return instrument


@app.get("/watchlist", response_model=list[WatchlistItemResponse], tags=["watchlist"])
def watchlist_items(
    user: CurrentUser, session: SessionDep, response: Response
) -> list[WatchlistItem]:
    response.headers["Cache-Control"] = "no-store"
    return list(session.scalars(
        select(WatchlistItem).where(WatchlistItem.user_id == user.id)
        .order_by(WatchlistItem.created_at, WatchlistItem.id).limit(100)
    ))


@app.post("/watchlist", response_model=WatchlistItemResponse, tags=["watchlist"])
async def add_watchlist_item(
    body: WatchlistAddRequest, user: CurrentUser, session: SessionDep, response: Response
) -> WatchlistItem:
    response.headers["Cache-Control"] = "no-store"
    provider = get_market_data_provider()
    if body.provider_id is not None and body.provider_id != provider.provider_id:
        raise HTTPException(status_code=409, detail="Requested market-data source is not active")
    existing = session.scalar(select(WatchlistItem).where(
        WatchlistItem.user_id == user.id,
        WatchlistItem.provider == provider.provider_id,
        WatchlistItem.instrument_key == body.instrument_key,
    ))
    if existing is not None:
        response.status_code = 200
        return existing
    query = body.query.strip()
    if len(query) < 3 or any(ord(character) < 32 for character in query):
        raise HTTPException(
            status_code=422, detail="Search requires at least three visible characters"
        )
    try:
        discovered = await provider.search_instruments(
            query, exchange=body.exchange, segment=body.segment
        )
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        raise _market_data_http_error(error) from error
    match = next((row for row in discovered if row.instrument_key == body.instrument_key), None)
    if match is None:
        raise HTTPException(status_code=422, detail="Instrument was not found in provider search")
    session.execute(select(User.id).where(User.id == user.id).with_for_update())
    count = session.scalar(select(func.count()).select_from(WatchlistItem).where(
        WatchlistItem.user_id == user.id
    )) or 0
    if count >= 100:
        raise HTTPException(status_code=409, detail="Watchlist is full")
    item = WatchlistItem(
        user_id=user.id, provider=provider.provider_id, instrument_key=match.instrument_key,
        trading_symbol=match.trading_symbol, name=match.name, exchange=match.exchange,
        segment=match.segment, instrument_type=match.instrument_type, expiry=match.expiry,
    )
    session.add(item)
    session.add(AuditEvent(
        actor_user_id=user.id, event_type="watchlist.item_added",
        details={"provider": provider.provider_id, "instrument_key": match.instrument_key},
    ))
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        existing = session.scalar(select(WatchlistItem).where(
            WatchlistItem.user_id == user.id,
            WatchlistItem.provider == provider.provider_id,
            WatchlistItem.instrument_key == body.instrument_key,
        ))
        if existing is None:
            raise HTTPException(status_code=409, detail="Could not save watchlist item") from error
        response.status_code = 200
        return existing
    session.refresh(item)
    response.status_code = 201
    return item


@app.delete("/watchlist/{item_id}", status_code=204, tags=["watchlist"])
def remove_watchlist_item(
    item_id: UUID, user: CurrentUser, session: SessionDep, response: Response
) -> None:
    response.headers["Cache-Control"] = "no-store"
    item = session.scalar(select(WatchlistItem).where(
        WatchlistItem.id == item_id, WatchlistItem.user_id == user.id,
    ))
    if item is None:
        raise HTTPException(status_code=404, detail="Watchlist item not found")
    session.add(AuditEvent(
        actor_user_id=user.id, event_type="watchlist.item_removed",
        details={"provider": item.provider, "instrument_key": item.instrument_key},
    ))
    session.delete(item)
    session.commit()


@app.get("/instruments/expiries", response_model=list[date], tags=["instruments"])
def instrument_expiries(
    user: CurrentUser, session: SessionDep, underlying: str = "NIFTY"
) -> list[date]:
    del user
    return available_expiries(session, underlying)


@app.get("/instruments/strikes", response_model=list[str], tags=["instruments"])
def instrument_strikes(
    user: CurrentUser, session: SessionDep, underlying: str, expiry: date
) -> list[str]:
    del user
    return [str(value) for value in available_strikes(session, underlying, expiry)]


def _market_data_http_error(
    error: MarketDataUnavailable | MarketDataProviderError,
) -> HTTPException:
    if isinstance(error, MarketDataUnavailable):
        return HTTPException(status_code=503, detail=str(error))
    if isinstance(error, MarketDataPlanRequired):
        return HTTPException(status_code=403, detail=str(error))
    return HTTPException(status_code=502, detail=str(error))


@app.get("/market-data/status", response_model=ProviderHealth, tags=["market-data"])
async def market_data_status(user: CurrentUser, response: Response) -> ProviderHealth:
    del user
    response.headers["Cache-Control"] = "no-store"
    return await get_market_data_provider().health_check()


@app.get("/market-data/capabilities", response_model=CapabilityMatrix, tags=["market-data"])
def market_data_capabilities(user: CurrentUser, response: Response) -> CapabilityMatrix:
    del user
    response.headers["Cache-Control"] = "no-store"
    return current_capabilities(get_settings(), get_market_data_provider())


@app.get(
    "/market-data/instrument-search",
    response_model=list[DiscoveredInstrument],
    tags=["market-data"],
)
async def market_data_instrument_search(
    user: CurrentUser,
    query: Annotated[str, Query(min_length=3, max_length=50)],
    exchange: Literal["NSE", "BSE", "MCX"] = "NSE",
    segment: Literal["EQ", "FO", "FUT", "OPT", "INDEX"] = "EQ",
    provider_id: str | None = None,
) -> list[DiscoveredInstrument]:
    del user
    query = query.strip()
    if len(query) < 3 or any(ord(character) < 32 for character in query):
        raise HTTPException(
            status_code=422, detail="Search requires at least three visible characters"
        )
    provider = get_market_data_provider()
    if provider_id is not None and provider_id != provider.provider_id:
        raise HTTPException(status_code=409, detail="Requested market-data source is not active")
    try:
        return await provider.search_instruments(query, exchange=exchange, segment=segment)
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        raise _market_data_http_error(error) from error


@app.get(
    "/market-data/option-contracts",
    response_model=list[OptionContract],
    tags=["market-data"],
)
async def market_data_option_contracts(
    user: CurrentUser, expiry_date: date | None = None, underlying: str = "NIFTY"
) -> list[OptionContract]:
    del user
    provider = get_market_data_provider()
    if provider.provider_id == "unconfigured" or not callable(
        getattr(provider, "get_option_contracts", None)
    ):
        raise _market_data_http_error(
            MarketDataUnavailable("No market-data provider is configured")
        )
    try:
        return await provider.get_option_contracts(underlying=underlying, expiry=expiry_date)
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        raise _market_data_http_error(error) from error


@app.get("/market-data/option-indexes", response_model=list[OptionIndex], tags=["market-data"])
async def market_data_option_indexes(user: CurrentUser) -> list[OptionIndex]:
    del user
    try:
        return await option_indexes()
    except MarketDataProviderError as error:
        raise _market_data_http_error(error) from error


@app.get(
    "/market-data/expired-option-expiries",
    response_model=list[date],
    tags=["market-data"],
)
async def market_data_expired_option_expiries(user: CurrentUser) -> list[date]:
    del user
    try:
        return await get_market_data_provider().get_expired_option_expiries("NIFTY")
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        raise _market_data_http_error(error) from error


@app.get(
    "/market-data/expired-option-contracts",
    response_model=list[ExpiredOptionContract],
    tags=["market-data"],
)
async def market_data_expired_option_contracts(
    user: CurrentUser,
    expiry_date: Annotated[date, Query(description="Historical NIFTY expiry date")],
) -> list[ExpiredOptionContract]:
    del user
    try:
        return await get_market_data_provider().get_expired_option_contracts("NIFTY", expiry_date)
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        raise _market_data_http_error(error) from error


@app.get(
    "/market-data/expired-option-candles",
    response_model=list[HistoricalBar],
    tags=["market-data"],
)
async def market_data_expired_option_candles(
    user: CurrentUser,
    instrument_key: Annotated[str, Query(min_length=3, max_length=160)],
    start: date,
    end: date,
    interval: Annotated[str, Query(pattern="^(1m|3m|5m|15m|30m|1d)$")] = "5m",
) -> list[HistoricalBar]:
    del user
    if start > end:
        raise HTTPException(status_code=422, detail="Historical start must be before end")
    try:
        return await get_market_data_provider().get_expired_historical(
            instrument_key, start, end, interval
        )
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        raise _market_data_http_error(error) from error


def _replay_strategy_version(
    body: OptionsBacktestRequest | ScheduledOptionsBacktestRequest,
    user: User,
    session: SessionDep,
    strategy: StrategyDefinition,
) -> StrategyVersion | None:
    if body.strategy_version_id is None:
        return None
    version = session.scalar(
        select(StrategyVersion).where(
            StrategyVersion.id == body.strategy_version_id,
            StrategyVersion.user_id == user.id,
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Strategy version not found")
    if _strategy_version_hash(version.definition) != version.definition_hash:
        raise HTTPException(
            status_code=409, detail="Saved strategy definition failed integrity check"
        )
    definition = StrategyDefinitionSnapshot.model_validate(version.definition)
    if definition.option_index is not None and definition.option_index.symbol != "NIFTY":
        raise HTTPException(409, "Index-specific strategy replay is not enabled yet")
    current_legs = [
        StrategyLegDefinitionResponse(
            option_type=leg.option_type,
            side=leg.side,
            strike_offset=leg.strike_offset,
            quantity_ratio=leg.quantity_ratio,
        )
        for leg in strategy.legs
    ]
    if (
        definition.template_key != body.strategy_key
        or definition.template_version != strategy.version
        or definition.template_legs != current_legs
        or definition.lots != body.lots
    ):
        raise HTTPException(
            status_code=409,
            detail="Replay inputs differ from the saved strategy version",
        )
    if (
        definition.entry_conditions
        or definition.stop_loss_amount is not None
        or definition.target_profit_amount is not None
        or definition.trailing_stop_amount is not None
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "This candle replay cannot evaluate saved entry conditions or "
                "stop, target, and trailing exits"
            ),
        )
    return version


@app.post(
    "/backtests/options/run",
    response_model=OptionsBacktestResult,
    tags=["backtesting"],
)
async def run_options_backtest(
    body: OptionsBacktestRequest, user: CurrentUser, session: SessionDep
) -> OptionsBacktestResult:
    if body.expiry >= date.today():
        raise HTTPException(status_code=422, detail="Select an option expiry that has passed")
    strategy = get_strategy(body.strategy_key)
    if strategy is None:
        raise HTTPException(
            status_code=422,
            detail="Historical replay currently supports the nine registered strategy templates",
        )
    version = _replay_strategy_version(body, user, session, strategy)
    try:
        result = await run_single_expiry_replay(
            get_market_data_provider(),
            strategy,
            expiry=body.expiry,
            entry_date=body.entry_date,
            exit_date=body.exit_date,
            lots=body.lots,
            fee_per_order=body.fee_per_order,
            slippage_per_unit_per_side=body.slippage_per_unit_per_side,
        )
        if version is not None:
            result = result.model_copy(update={
                "strategy_version_id": version.id,
                "strategy_definition_hash": version.definition_hash,
            })
        session.add(
            BacktestRun(
                id=result.run_id,
                user_id=user.id,
                result_data=result.model_dump(mode="json"),
            )
        )
        session.add(
            AuditEvent(
                actor_user_id=user.id,
                event_type="backtesting.options.replay",
                details={
                    "run_id": str(result.run_id),
                    "strategy_key": result.strategy_key,
                    "expiry": result.expiry.isoformat(),
                    "entry_date": result.entry_date.isoformat(),
                    "exit_date": result.exit_date.isoformat(),
                    "strategy_version_id": str(version.id) if version else None,
                    "strategy_definition_hash": version.definition_hash if version else None,
                    "observed_input_digest": result.observed_input_digest,
                },
            )
        )
        session.commit()
        return result
    except BacktestDataError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        raise _market_data_http_error(error) from error


@app.post(
    "/backtests/options/scheduled-run",
    response_model=ScheduledOptionsBacktestResult,
    tags=["backtesting"],
)
async def run_scheduled_options_backtest(
    body: ScheduledOptionsBacktestRequest, user: CurrentUser, session: SessionDep
) -> ScheduledOptionsBacktestResult:
    strategy = get_strategy(body.strategy_key)
    if strategy is None:
        raise HTTPException(status_code=422, detail="Select a registered strategy template")
    version = _replay_strategy_version(body, user, session, strategy)
    try:
        result = await run_scheduled_expiry_replay(
            get_market_data_provider(), strategy, start_date=body.start_date,
            end_date=body.end_date,
            entry_sessions_before_expiry=body.entry_sessions_before_expiry,
            lots=body.lots, fee_per_order=body.fee_per_order,
            slippage_per_unit_per_side=body.slippage_per_unit_per_side,
        )
        if version is not None:
            result = result.model_copy(update={
                "strategy_version_id": version.id,
                "strategy_definition_hash": version.definition_hash,
            })
        session.add(
            BacktestRun(
                id=result.run_id, user_id=user.id,
                result_data=result.model_dump(mode="json"),
            )
        )
        session.add(
            AuditEvent(
                actor_user_id=user.id, event_type="backtesting.options.scheduled_replay",
                details={
                    "run_id": str(result.run_id), "strategy_key": result.strategy_key,
                    "strategy_version": result.strategy_version,
                    "start_date": result.start_date.isoformat(),
                    "end_date": result.end_date.isoformat(),
                    "trade_count": result.trade_count,
                    "observed_input_digest": result.observed_input_digest,
                    "strategy_version_id": str(version.id) if version else None,
                    "strategy_definition_hash": version.definition_hash if version else None,
                },
            )
        )
        session.commit()
        return result
    except BacktestDataError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        raise _market_data_http_error(error) from error


@app.get(
    "/backtests/options/scheduled-runs",
    response_model=list[ScheduledOptionsBacktestRunRecord],
    tags=["backtesting"],
)
def list_scheduled_options_backtest_runs(
    user: CurrentUser, session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[ScheduledOptionsBacktestRunRecord]:
    records = session.scalars(
        select(BacktestRun)
        .where(
            BacktestRun.user_id == user.id,
            BacktestRun.result_data["mode"].as_string()
            == "historical_scheduled_expiry_replay",
        )
        .order_by(desc(BacktestRun.created_at), desc(BacktestRun.id))
        .limit(limit)
    ).all()
    return [
        ScheduledOptionsBacktestRunRecord(
            created_at=record.created_at,
            result=ScheduledOptionsBacktestResult.model_validate(record.result_data),
        )
        for record in records
    ]


@app.get(
    "/backtests/options/runs",
    response_model=list[OptionsBacktestRunRecord],
    tags=["backtesting"],
)
def list_options_backtest_runs(
    user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[OptionsBacktestRunRecord]:
    records = session.scalars(
        select(BacktestRun)
        .where(
            BacktestRun.user_id == user.id,
            BacktestRun.result_data["mode"].as_string()
            == "historical_single_expiry_replay",
        )
        .order_by(desc(BacktestRun.created_at), desc(BacktestRun.id))
        .limit(limit)
    ).all()
    return [
        OptionsBacktestRunRecord(
            created_at=record.created_at,
            result=OptionsBacktestResult.model_validate(record.result_data),
        )
        for record in records
    ]


@app.get("/market-data/option-chain", response_model=OptionChain, tags=["market-data"])
async def market_data_option_chain(
    user: CurrentUser,
    expiry_date: Annotated[date, Query(description="NIFTY expiry date in YYYY-MM-DD format")],
    underlying: Annotated[str, Query(min_length=1, max_length=40)] = "NIFTY",
) -> OptionChain:
    del user
    try:
        chain = await get_market_data_provider().get_option_chain(underlying, expiry_date)
        return chain.model_copy(update={"quality": assess_option_chain(chain)})
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        raise _market_data_http_error(error) from error


@app.get("/market-data/quote", response_model=Quote, tags=["market-data"])
async def market_data_quote(
    user: CurrentUser,
    instrument_key: Annotated[str, Query(min_length=3, max_length=128)],
    provider_id: str | None = None,
) -> Quote:
    del user
    provider = get_market_data_provider()
    if provider_id is not None and provider_id != provider.provider_id:
        raise HTTPException(status_code=409, detail="Requested market-data source is not active")
    try:
        return await provider.get_quote(instrument_key)
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        raise _market_data_http_error(error) from error


@app.get("/market-data/historical", response_model=list[HistoricalBar], tags=["market-data"])
async def market_data_historical(
    user: CurrentUser,
    instrument_key: Annotated[str, Query(min_length=3, max_length=128)],
    start: datetime,
    end: datetime,
    interval: Annotated[str, Query(pattern="^(1m|5m|15m|30m|1h|1d)$")] = "5m",
    provider_id: str | None = None,
) -> list[HistoricalBar]:
    del user
    provider = get_market_data_provider()
    if provider_id is not None and provider_id != provider.provider_id:
        raise HTTPException(status_code=409, detail="Requested market-data source is not active")
    try:
        return await provider.get_historical(instrument_key, start, end, interval)
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        raise _market_data_http_error(error) from error


@app.get("/market-data/intraday", response_model=list[HistoricalBar], tags=["market-data"])
async def market_data_intraday(
    user: CurrentUser,
    instrument_key: Annotated[str, Query(min_length=3, max_length=128)],
    interval: Annotated[str, Query(pattern="^(1m|5m|15m|30m|1h|1d)$")] = "5m",
    provider_id: str | None = None,
) -> list[HistoricalBar]:
    del user
    provider = get_market_data_provider()
    if provider_id is not None and provider_id != provider.provider_id:
        raise HTTPException(status_code=409, detail="Requested market-data source is not active")
    try:
        return await provider.get_intraday(instrument_key, interval)
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        raise _market_data_http_error(error) from error


@app.get("/analytics/options", response_model=OptionChainFeatures, tags=["analytics"])
async def analytics_options(
    user: CurrentUser,
    expiry_date: Annotated[date, Query(description="NIFTY expiry date in YYYY-MM-DD format")],
) -> OptionChainFeatures:
    del user
    try:
        chain = await get_market_data_provider().get_option_chain("NIFTY", expiry_date)
        return calculate_option_features(chain)
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        raise _market_data_http_error(error) from error


@app.post("/analytics/options/summary", response_model=OptionChainFeatures, tags=["analytics"])
def analytics_options_summary(user: CurrentUser, body: OptionChain) -> OptionChainFeatures:
    del user
    return calculate_option_features(body)


@app.post("/analytics/options/market-fit", response_model=MarketFitAssessment, tags=["analytics"])
async def analytics_options_market_fit(
    user: CurrentUser, body: MarketFitRequest,
) -> MarketFitAssessment:
    """Research-only comparison; a submitted chain never authorizes a paper entry."""
    del user
    chain = body.chain.model_copy(update={"quality": assess_option_chain(body.chain)})
    regime: MarketRegimeResult | None = None
    bar_digest: str | None = None
    bar_received_at: datetime | None = None
    now = datetime.now(timezone.utc)
    if (
        chain.underlying == "NIFTY"
        and chain.as_of.tzinfo is not None
        and timedelta(0) <= now - chain.as_of <= timedelta(minutes=5)
        and chain.quality is not None
        and chain.quality.status == "PASS"
    ):
        provider = get_market_data_provider()
        try:
            bars = await provider.get_historical(
                "NSE_INDEX|Nifty 50", chain.as_of - timedelta(days=14), chain.as_of, "5m"
            )
            received_at = datetime.now(timezone.utc)
            complete = [
                bar for bar in bars
                if bar.timestamp.tzinfo is not None
                and bar.timestamp + timedelta(minutes=5) <= chain.as_of
            ]
            if (
                len(complete) >= 250
                and timedelta(0) <= chain.as_of - complete[-1].timestamp <= timedelta(minutes=15)
            ):
                selected_bars = complete[-250:]
                regime = classify_market_regime(selected_bars, "5m")
                bar_digest = fingerprint({
                    "instrument": "NSE_INDEX|Nifty 50", "interval": "5m",
                    "bars": [bar.model_dump(mode="json") for bar in selected_bars],
                })
                bar_received_at = received_at
        except (MarketDataUnavailable, MarketDataProviderError, ValueError):
            pass
    assessment = assess_nifty_market_fit(chain, body.reference_strike, regime)
    assessment.chain_digest = fingerprint(body.chain.model_dump(mode="json"))
    if bar_digest is not None and assessment.regime_as_of is not None:
        assessment.bar_source = "PROVIDER_HISTORICAL_HASH_ONLY"
        assessment.bar_digest = bar_digest
        assessment.bar_count = 250
        assessment.bar_received_at = bar_received_at
    return assessment


@app.post("/research/studies/{study_id}/opportunities/market-fit", tags=["research registry"])
async def record_nifty_market_fit_opportunity(
    study_id: UUID, body: MarketFitRequest, user: CurrentUser, session: SessionDep,
) -> dict[str, Any]:
    return await _record_nifty_market_fit_opportunity(
        study_id, body, user, session, chain_provider_id=None,
    )


async def _record_nifty_market_fit_opportunity(
    study_id: UUID, body: MarketFitRequest, user: CurrentUser, session: SessionDep,
    *, chain_provider_id: str | None, chain_received_at: datetime | None = None,
) -> dict[str, Any]:
    """Retain a current research abstention; never create a paper order."""
    study_row = session.scalar(select(ResearchStudy).where(
        ResearchStudy.id == study_id, ResearchStudy.user_id == user.id,
    ))
    if study_row is None:
        raise HTTPException(404, "Study not found")
    now = datetime.now(timezone.utc)
    if (body.chain.as_of.tzinfo is None
            or not timedelta(0) <= now - body.chain.as_of <= timedelta(minutes=5)):
        raise HTTPException(422, "Opportunity chain must be current and time-ordered")
    try:
        frozen = FrozenNiftyStudy.model_validate(study_row.manifest)
        if frozen.manifest_digest != study_row.manifest_digest:
            raise ValueError("Study manifest integrity failed")
        policy_set = session.scalar(select(ResearchPolicySet).where(
            ResearchPolicySet.study_id == study_id, ResearchPolicySet.user_id == user.id,
        ))
        if policy_set is None:
            raise ValueError("Policies must be frozen before opportunity observations")
        if (
            fingerprint(policy_set.manifest) != policy_set.manifest_digest
            or policy_set.manifest.get("study_manifest_digest") != frozen.manifest_digest
        ):
            raise ValueError("Frozen policy set integrity failed")
        policy_ids = current_policy_set_ids(policy_set)
        assessment = await analytics_options_market_fit(user, body)
        if chain_provider_id is not None:
            assessment = assessment.model_copy(update={
                "chain_source": "SERVER_FETCHED_OPTION_CHAIN",
                "chain_provider_id": chain_provider_id,
                "chain_received_at": chain_received_at,
            })
        snapshot = build_market_fit_opportunity(
            frozen, body.chain, assessment,
            policy_set_digest=policy_set.manifest_digest,
            frozen_policy_ids=policy_ids,
        )
    except ValueError:
        raise HTTPException(409, "Opportunity is outside the frozen NIFTY study") from None
    return recommendation_view(record_recommendation(session, user.id, study_id, snapshot))


@app.post(
    "/research/studies/{study_id}/opportunities/market-fit/provider",
    tags=["research registry"],
)
async def record_provider_nifty_market_fit_opportunity(
    study_id: UUID, body: ProviderMarketFitRequest,
    user: CurrentUser, session: SessionDep,
) -> dict[str, Any]:
    """Fetch the chain server-side; retain an unqualified research abstention."""
    if session.scalar(select(ResearchStudy.id).where(
        ResearchStudy.id == study_id, ResearchStudy.user_id == user.id,
    )) is None:
        raise HTTPException(404, "Study not found")
    if session.scalar(select(ResearchPolicySet.id).where(
        ResearchPolicySet.study_id == study_id,
        ResearchPolicySet.user_id == user.id,
    )) is None:
        raise HTTPException(409, "Policies must be frozen before opportunity observations")
    provider = get_market_data_provider()
    try:
        chain = await provider.get_option_chain("NIFTY", body.expiry)
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        raise _market_data_http_error(error) from error
    received_at = datetime.now(timezone.utc)
    if chain.underlying != "NIFTY" or chain.expiry != body.expiry:
        raise HTTPException(409, "Provider chain differs from the requested NIFTY expiry")
    return await _record_nifty_market_fit_opportunity(
        study_id, MarketFitRequest(chain=chain, reference_strike=body.reference_strike),
        user, session, chain_provider_id=provider.provider_id,
        chain_received_at=received_at,
    )


@app.get("/analytics/indicators", response_model=IndicatorSnapshot, tags=["analytics"])
async def analytics_indicators(
    user: CurrentUser,
    instrument_key: Annotated[str, Query(min_length=3, max_length=128)],
    start: datetime,
    end: datetime,
    interval: Annotated[str, Query(pattern="^(1m|5m|15m|30m|1h|1d)$")] = "5m",
) -> IndicatorSnapshot:
    del user
    try:
        bars = await get_market_data_provider().get_historical(instrument_key, start, end, interval)
        return calculate_indicators(bars, interval)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        raise _market_data_http_error(error) from error


@app.get("/analytics/regime", response_model=MarketRegimeResult, tags=["analytics"])
async def analytics_regime(
    user: CurrentUser,
    instrument_key: Annotated[str, Query(min_length=3, max_length=128)],
    start: datetime,
    end: datetime,
    interval: Annotated[str, Query(pattern="^(1m|5m|15m|30m|1h|1d)$")] = "5m",
) -> MarketRegimeResult:
    del user
    try:
        bars = await get_market_data_provider().get_historical(instrument_key, start, end, interval)
        return classify_market_regime(bars, interval)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        raise _market_data_http_error(error) from error


@app.post("/analytics/greeks", response_model=OptionGreeksResult, tags=["analytics"])
def analytics_greeks(user: CurrentUser, body: OptionGreeksInput) -> OptionGreeksResult:
    del user
    return calculate_greeks(body)


@app.post("/analytics/payoff", response_model=PayoffResult, tags=["analytics"])
def analytics_payoff(user: CurrentUser, body: PayoffRequest) -> PayoffResult:
    del user
    return calculate_expiry_payoff(body)


@app.get(
    "/paper/strategies",
    response_model=list[StrategyDefinitionResponse],
    tags=["paper trading"],
)
def paper_strategies(
    user: CurrentUser, session: SessionDep,
    underlying: Annotated[str, Query(min_length=1, max_length=32, pattern=r"^[A-Z0-9_]+$")]
    = "NIFTY",
) -> list[StrategyDefinitionResponse]:
    closed_trades = list(session.scalars(
        select(PaperTrade).where(
            PaperTrade.user_id == user.id,
            PaperTrade.status == "CLOSED",
            PaperTrade.underlying == underlying,
            PaperTrade.realized_pnl.is_not(None),
        )
    ))
    performance: dict[str, tuple[int, int, Decimal]] = {}
    qualified: dict[str, tuple[int, int, Decimal]] = {}
    qualified_policy_variants: dict[str, set[str]] = {}
    qualified_conditions: dict[tuple[str, str], tuple[int, int, Decimal, Decimal]] = {}
    trade_ids = [trade.id for trade in closed_trades]
    filled_deployments = list(session.scalars(select(PaperDeployment).where(
        PaperDeployment.user_id == user.id,
        PaperDeployment.status == "FILLED",
        PaperDeployment.trade_id.in_(trade_ids),
    ))) if trade_ids else []
    deployment_by_trade = {row.trade_id: row for row in filled_deployments}
    filled_ids = set(deployment_by_trade)
    orders_by_trade: dict[UUID, list[PaperOrder]] = {}
    observations_by_trade: dict[str, list[dict[str, Any]]] = {}
    entry_observations_by_deployment: dict[str, list[dict[str, Any]]] = {}
    if filled_ids:
        for order in session.scalars(select(PaperOrder).where(
            PaperOrder.user_id == user.id,
            PaperOrder.trade_id.in_(filled_ids),
        )):
            orders_by_trade.setdefault(order.trade_id, []).append(order)
        for event in session.scalars(select(AuditEvent).where(
            AuditEvent.actor_user_id == user.id,
            AuditEvent.event_type == "paper.trade.exit_checked",
            AuditEvent.correlation_id.in_([str(trade_id) for trade_id in filled_ids]),
        ).order_by(AuditEvent.created_at, AuditEvent.id)):
            observations_by_trade.setdefault(event.correlation_id or "", []).append(
                event.details
            )
        for event in session.scalars(select(AuditEvent).where(
            AuditEvent.actor_user_id == user.id,
            AuditEvent.event_type == "paper.deployment.entry_checked",
            AuditEvent.correlation_id.in_([str(row.id) for row in filled_deployments]),
        ).order_by(AuditEvent.created_at, AuditEvent.id)):
            entry_observations_by_deployment.setdefault(
                event.correlation_id or "", []
            ).append(event.details)
    for trade in closed_trades:
        strategy_key, realized_pnl = trade.strategy_key, trade.realized_pnl
        if realized_pnl is None:
            continue
        total, wins, net = performance.get(strategy_key, (0, 0, Decimal(0)))
        performance[strategy_key] = (total + 1, wins + int(realized_pnl > 0), net + realized_pnl)
        if trade.id in filled_ids and qualifies_automated_option_outcome(
            trade, orders_by_trade.get(trade.id, [])
        ) and replay_observed_entries(
            deployment_by_trade[trade.id], trade,
            entry_observations_by_deployment.get(
                str(deployment_by_trade[trade.id].id), []
            ),
        ).status == "MATCHED" and replay_observed_exits(
            trade, observations_by_trade.get(str(trade.id), [])
        ).status == "MATCHED":
            count, qualified_wins, qualified_net = qualified.get(
                strategy_key, (0, 0, Decimal(0))
            )
            qualified[strategy_key] = (
                count + 1, qualified_wins + int(realized_pnl > 0), qualified_net + realized_pnl
            )
            qualified_policy_variants.setdefault(strategy_key, set()).add(
                option_policy_signature(trade)
            )
            condition = trade_entry_condition(trade)
            if condition != "UNKNOWN":
                condition_key = (strategy_key, condition)
                n, condition_wins, condition_net, condition_r = qualified_conditions.get(
                    condition_key, (0, 0, Decimal(0), Decimal(0))
                )
                qualified_conditions[condition_key] = (
                    n + 1, condition_wins + int(realized_pnl > 0),
                    condition_net + realized_pnl, condition_r + realized_pnl / trade.max_loss,
                )
    return [
        StrategyDefinitionResponse(
            key=strategy.key,
            name=strategy.name,
            description=strategy.description,
            version=strategy.version,
            risk_profile="DEFINED_RISK",
            lifecycle_status="PAPER_TEMPLATE",
            expiry_scope=strategy.expiry_scope,
            legs=[
                StrategyLegDefinitionResponse(
                    option_type=leg.option_type,
                    side=leg.side,
                    strike_offset=leg.strike_offset,
                    quantity_ratio=leg.quantity_ratio,
                )
                for leg in strategy.legs
            ],
            closed_trade_count=performance.get(strategy.key, (0, 0, Decimal(0)))[0],
            winning_trade_count=performance.get(strategy.key, (0, 0, Decimal(0)))[1],
            qualified_automated_trade_count=qualified.get(
                strategy.key, (0, 0, Decimal(0))
            )[0],
            qualified_automated_wins=qualified.get(strategy.key, (0, 0, Decimal(0)))[1],
            qualified_policy_variant_count=len(qualified_policy_variants.get(strategy.key, set())),
            market_condition_evidence=[
                OptionMarketConditionEvidenceResponse(
                    condition=condition,
                    trade_count=qualified_conditions.get(
                        (strategy.key, condition), (0, 0, Decimal(0), Decimal(0))
                    )[0],
                    winning_trade_count=qualified_conditions.get(
                        (strategy.key, condition), (0, 0, Decimal(0), Decimal(0))
                    )[1],
                    average_net_pnl=(
                        qualified_conditions[(strategy.key, condition)][2]
                        / qualified_conditions[(strategy.key, condition)][0]
                    ).quantize(Decimal("0.01"))
                    if qualified_conditions.get(
                        (strategy.key, condition), (0, 0, Decimal(0), Decimal(0))
                    )[0] >= 20
                    and len(qualified_policy_variants.get(strategy.key, set())) == 1
                    else None,
                    average_r=(
                        qualified_conditions[(strategy.key, condition)][3]
                        / qualified_conditions[(strategy.key, condition)][0]
                    ).quantize(Decimal("0.0001"))
                    if qualified_conditions.get(
                        (strategy.key, condition), (0, 0, Decimal(0), Decimal(0))
                    )[0] >= 20
                    and len(qualified_policy_variants.get(strategy.key, set())) == 1
                    else None,
                )
                for condition in ("BULLISH", "BEARISH", "NEUTRAL")
            ],
            observed_win_rate_pct=(
                Decimal(performance[strategy.key][1] * 100)
                / Decimal(performance[strategy.key][0])
            ).quantize(Decimal("0.01"))
            if performance.get(strategy.key, (0, 0, Decimal(0)))[0]
            else None,
            estimated_win_probability_pct=(
                Decimal((qualified[strategy.key][1] + 2) * 100)
                / Decimal(qualified[strategy.key][0] + 4)
            ).quantize(Decimal("0.01"))
            if qualified.get(strategy.key, (0, 0, Decimal(0)))[0] >= 20
            and len(qualified_policy_variants.get(strategy.key, set())) == 1
            else None,
            average_net_pnl=(
                performance[strategy.key][2] / Decimal(performance[strategy.key][0])
            ).quantize(Decimal("0.01"))
            if performance.get(strategy.key, (0, 0, Decimal(0)))[0]
            else None,
            expected_net_pnl=(
                qualified[strategy.key][2] / Decimal(qualified[strategy.key][0])
            ).quantize(Decimal("0.01"))
            if qualified.get(strategy.key, (0, 0, Decimal(0)))[0] >= 20
            and len(qualified_policy_variants.get(strategy.key, set())) == 1
            else None,
        )
        for strategy in STRATEGIES
    ]


def _strategy_version_hash(definition: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _strategy_version_definition(
    body: StrategyVersionCreateRequest | PaperTradeCreateRequest,
    strategy: StrategyDefinition,
    option_index: OptionIndex | None = None,
) -> dict[str, object]:
    return StrategyDefinitionSnapshot(
        option_index=option_index,
        template_key=strategy.key,
        template_version=strategy.version,
        template_legs=[
            StrategyLegDefinitionResponse(
                option_type=leg.option_type,
                side=leg.side,
                strike_offset=leg.strike_offset,
                quantity_ratio=leg.quantity_ratio,
            )
            for leg in strategy.legs
        ],
        lots=body.lots,
        entry_conditions=body.entry_conditions,
        stop_loss_amount=body.stop_loss_amount,
        target_profit_amount=body.target_profit_amount,
        trailing_stop_amount=body.trailing_stop_amount,
    ).model_dump(mode="json", exclude={"option_index"} if option_index is None else set())


def _require_template_acknowledgement(
    body: PaperTradeCreateRequest, strategy: StrategyDefinition | None,
) -> None:
    if body.expected_template_version is not None:
        if strategy is None or body.expected_template_version != strategy.version:
            raise HTTPException(409, "Strategy template changed; refresh and review before entry")
    elif strategy is not None and strategy.version != "1.0.0" and body.strategy_version_id is None:
        raise HTTPException(
            409, "Revised strategy template requires a current version; refresh and review it",
        )


def _require_armed_strategy_identity(
    deployment: PaperDeployment, body: PaperTradeCreateRequest,
    strategy: StrategyDefinition | None, index: OptionIndex, request_hash: str,
) -> None:
    """Do not resolve an armed request against different template economics."""
    try:
        saved = PaperTradeCreateRequest.model_validate(deployment.request_data)
    except ValueError as error:
        raise HTTPException(
            422, "Saved entry rules are invalid; cancel and arm a new strategy",
        ) from error
    if (
        deployment.request_hash != request_hash
        or saved.model_dump(mode="json") != body.model_dump(mode="json")
        or strategy is None
    ):
        raise HTTPException(422, "Armed strategy request changed; cancel and arm a new strategy")
    if "strategy_definition" not in deployment.request_data:
        # Legacy unchanged v1 templates retain their existing behavior. Never infer
        # that an old unpinned request consented to revised template economics.
        if strategy.version != "1.0.0":
            raise HTTPException(
                422, "Legacy armed template is unpinned and revised; cancel and arm a new strategy",
            )
        return
    expected = _strategy_version_definition(
        body, strategy, index if index.symbol != "NIFTY" else None,
    )
    if deployment.request_data["strategy_definition"] != expected:
        raise HTTPException(422, "Armed template identity changed; cancel and arm a new strategy")


@app.get(
    "/strategies/versions", response_model=list[StrategyVersionResponse], tags=["strategies"]
)
def list_strategy_versions(
    user: CurrentUser, session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> list[StrategyVersionResponse]:
    versions = session.scalars(
        select(StrategyVersion)
        .where(StrategyVersion.user_id == user.id)
        .order_by(desc(StrategyVersion.created_at), desc(StrategyVersion.id))
        .limit(limit)
    ).all()
    return [StrategyVersionResponse.from_model(version) for version in versions]


@app.get(
    "/strategies/versions/{version_id}",
    response_model=StrategyVersionResponse, tags=["strategies"],
)
def get_strategy_version(
    version_id: UUID, user: CurrentUser, session: SessionDep
) -> StrategyVersionResponse:
    version = session.scalar(
        select(StrategyVersion).where(
            StrategyVersion.id == version_id, StrategyVersion.user_id == user.id
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Strategy version not found")
    return StrategyVersionResponse.from_model(version)


@app.post(
    "/strategies/versions", response_model=StrategyVersionResponse,
    status_code=status.HTTP_201_CREATED, tags=["strategies"],
)
async def create_strategy_version(
    body: StrategyVersionCreateRequest, user: CurrentUser, session: SessionDep
) -> StrategyVersionResponse:
    strategy = get_strategy(body.strategy_key)
    if strategy is None:
        raise HTTPException(status_code=422, detail="A registered strategy template is required")
    index = None
    if body.underlying is not None:
        try:
            selected = await resolve_option_index(body.underlying)
        except MarketDataProviderError as error:
            raise _market_data_http_error(error) from error
        if selected.symbol != "NIFTY":
            index = selected
    definition = _strategy_version_definition(body, strategy, index)
    definition_hash = _strategy_version_hash(definition)
    latest = session.scalar(
        select(StrategyVersion)
        .where(StrategyVersion.user_id == user.id, StrategyVersion.name == body.name)
        .order_by(desc(StrategyVersion.version_number))
        .limit(1)
    )
    if latest is None:
        if body.base_version_id is not None:
            raise HTTPException(status_code=409, detail="No base version exists for this name")
        version_number = 1
    else:
        if body.base_version_id != latest.id:
            raise HTTPException(
                status_code=409, detail="Select the latest version before saving a revision"
            )
        if definition_hash == latest.definition_hash:
            raise HTTPException(status_code=409, detail="This definition is unchanged")
        version_number = latest.version_number + 1
    version = StrategyVersion(
        user_id=user.id, name=body.name, version_number=version_number,
        definition=definition, definition_hash=definition_hash,
    )
    session.add(version)
    try:
        session.flush()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="A new version was saved concurrently; refresh and retry"
        ) from error
    session.add(
        AuditEvent(
            actor_user_id=user.id, event_type="strategy.version.created",
            details={"version_id": str(version.id), "name": body.name,
                     "version_number": version_number, "definition_hash": definition_hash},
        )
    )
    session.commit()
    session.refresh(version)
    return StrategyVersionResponse.from_model(version)


@app.get(
    "/paper/strategy-presets",
    response_model=list[PaperStrategyPresetResponse],
    tags=["paper trading"],
)
def list_paper_strategy_presets(
    user: CurrentUser, session: SessionDep
) -> list[PaperStrategyPresetResponse]:
    presets = session.scalars(
        select(PaperStrategyPreset)
        .where(PaperStrategyPreset.user_id == user.id)
        .order_by(PaperStrategyPreset.name, PaperStrategyPreset.created_at)
    ).all()
    return [PaperStrategyPresetResponse.from_model(preset) for preset in presets]


@app.post(
    "/paper/strategy-presets",
    response_model=PaperStrategyPresetResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["paper trading"],
)
def create_paper_strategy_preset(
    body: PaperStrategyPresetCreateRequest,
    user: CurrentUser,
    session: SessionDep,
) -> PaperStrategyPresetResponse:
    if body.strategy_key == "custom" or get_strategy(body.strategy_key) is None:
        raise HTTPException(
            status_code=422,
            detail="Saved presets currently support the nine registered strategy templates",
        )
    preset = PaperStrategyPreset(
        user_id=user.id,
        name=body.name,
        strategy_key=body.strategy_key,
        entry_conditions=[condition.model_dump(mode="json") for condition in body.entry_conditions],
    )
    session.add(preset)
    try:
        session.flush()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="A preset with this name already exists"
        ) from error
    session.add(
        AuditEvent(
            actor_user_id=user.id,
            event_type="paper.strategy_preset.created",
            details={"preset_id": str(preset.id), "strategy_key": preset.strategy_key},
        )
    )
    session.commit()
    session.refresh(preset)
    return PaperStrategyPresetResponse.from_model(preset)


@app.delete(
    "/paper/strategy-presets/{preset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["paper trading"],
)
def delete_paper_strategy_preset(preset_id: UUID, user: CurrentUser, session: SessionDep) -> None:
    preset = session.scalar(
        select(PaperStrategyPreset).where(
            PaperStrategyPreset.id == preset_id,
            PaperStrategyPreset.user_id == user.id,
        )
    )
    if preset is None:
        raise HTTPException(status_code=404, detail="Strategy preset not found")
    session.delete(preset)
    session.add(
        AuditEvent(
            actor_user_id=user.id,
            event_type="paper.strategy_preset.deleted",
            details={"preset_id": str(preset_id)},
        )
    )
    session.commit()


@app.get("/notifications", response_model=NotificationListResponse, tags=["notifications"])
def list_user_notifications(
    user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    unread_only: bool = False,
) -> NotificationListResponse:
    conditions = [UserNotification.user_id == user.id]
    if unread_only:
        conditions.append(UserNotification.read_at.is_(None))
    notifications = session.scalars(
        select(UserNotification)
        .where(*conditions)
        .order_by(desc(UserNotification.created_at))
        .limit(limit)
    ).all()
    unread_count = session.scalar(
        select(func.count())
        .select_from(UserNotification)
        .where(UserNotification.user_id == user.id, UserNotification.read_at.is_(None))
    )
    return NotificationListResponse(
        notifications=[UserNotificationResponse.from_model(item) for item in notifications],
        unread_count=unread_count or 0,
    )


@app.post(
    "/notifications/{notification_id}/read",
    response_model=UserNotificationResponse,
    tags=["notifications"],
)
def mark_notification_read(
    notification_id: UUID, user: CurrentUser, session: SessionDep
) -> UserNotificationResponse:
    notification = session.scalar(
        select(UserNotification)
        .where(UserNotification.id == notification_id, UserNotification.user_id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(notification)
    return UserNotificationResponse.from_model(notification)


@app.get("/paper/account", response_model=PaperAccountResponse, tags=["paper trading"])
def paper_account(user: CurrentUser, session: SessionDep) -> PaperAccountResponse:
    account = _get_paper_account(session, user)
    open_trades = session.scalars(
        select(PaperTrade).where(PaperTrade.account_id == account.id, PaperTrade.status == "OPEN")
    ).all()
    open_risk = sum((trade.max_loss for trade in open_trades), Decimal(0))
    budget = daily_risk_budget(
        initial_cash=account.initial_cash,
        limit_pct=get_settings().paper_daily_loss_limit_pct,
        realized_pnl=daily_realized_pnl(session, account.id, exchange_today()),
        open_max_loss=open_risk,
    )
    return PaperAccountResponse(
        initial_cash=account.initial_cash,
        cash_balance=account.cash_balance,
        kill_switch_enabled=account.kill_switch_enabled,
        open_trade_count=len(open_trades),
        open_max_loss=open_risk,
        daily_realized_pnl=budget.realized_pnl,
        daily_loss_limit=budget.loss_limit,
        daily_risk_remaining=budget.remaining_for_new_risk,
    )


@app.get(
    "/paper/account/ledger-reconciliation",
    response_model=PaperLedgerReconciliationResponse,
    tags=["paper trading"],
)
def paper_account_ledger_reconciliation(
    user: CurrentUser, session: SessionDep, response: Response
) -> PaperLedgerReconciliationResponse:
    """Recompute saved F&O paper cash and trade P&L without changing the ledger."""
    response.headers["Cache-Control"] = "no-store"
    account = _get_paper_account(session, user)
    trades = list(session.scalars(select(PaperTrade).where(
        PaperTrade.account_id == account.id, PaperTrade.user_id == user.id,
    )))
    orders = list(session.scalars(select(PaperOrder).where(
        PaperOrder.account_id == account.id, PaperOrder.user_id == user.id,
    )))
    result = reconcile_paper_ledger(account, trades, orders)
    return PaperLedgerReconciliationResponse(**vars(result))


@app.put(
    "/paper/account/kill-switch",
    response_model=PaperKillSwitchResponse,
    tags=["paper trading"],
)
def set_paper_kill_switch(
    body: PaperKillSwitchRequest, user: CurrentUser, session: SessionDep
) -> PaperKillSwitchResponse:
    _get_paper_account(session, user)
    account = lock_account(session, user.id)
    if account is None:
        raise HTTPException(status_code=409, detail="Paper account unavailable")
    previous_state = account.kill_switch_enabled
    account.kill_switch_enabled = body.enabled
    account.updated_at = datetime.now(timezone.utc)
    if previous_state != body.enabled:
        _add_user_notification(
            session,
            user.id,
            "paper.kill_switch.changed",
            "Paper entries paused" if body.enabled else "Paper entries resumed",
            (
                "New paper positions are paused. Existing positions can still be closed."
                if body.enabled
                else "New paper positions can be opened again."
            ),
            {"enabled": body.enabled},
        )
    session.add(
        AuditEvent(
            actor_user_id=user.id,
            event_type="paper.kill_switch.changed",
            details={"enabled": body.enabled},
        )
    )
    session.commit()
    updated_at = account.updated_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return PaperKillSwitchResponse(enabled=account.kill_switch_enabled, updated_at=updated_at)


@app.get(
    "/paper/deployments",
    response_model=list[PaperDeploymentResponse],
    tags=["paper trading"],
)
def paper_deployments(
    user: CurrentUser, session: SessionDep, response: Response
) -> list[PaperDeploymentResponse]:
    response.headers["Cache-Control"] = "no-store"
    rows = session.scalars(
        select(PaperDeployment)
        .where(PaperDeployment.user_id == user.id)
        .order_by(desc(PaperDeployment.created_at))
        .limit(100)
    ).all()
    return [PaperDeploymentResponse.from_model(row) for row in rows]


@app.get(
    "/paper/deployments/{deployment_id}/decisions",
    tags=["paper trading"],
)
def paper_deployment_decisions(
    deployment_id: UUID, user: CurrentUser, session: SessionDep, response: Response
) -> list[dict[str, object]]:
    """Return only this account's stored option-entry evaluations and lifecycle events."""
    response.headers["Cache-Control"] = "no-store"
    row = session.scalar(select(PaperDeployment.id).where(
        PaperDeployment.id == deployment_id,
        PaperDeployment.user_id == user.id,
    ))
    if row is None:
        raise HTTPException(404, "Paper deployment not found")
    events = session.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.actor_user_id == user.id,
            AuditEvent.correlation_id == str(deployment_id),
            AuditEvent.event_type.like("paper.deployment.%"),
        )
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(200)
    )
    return [{
        "id": str(event.id),
        "event_type": event.event_type,
        "created_at": event.created_at.replace(tzinfo=timezone.utc)
        if event.created_at.tzinfo is None else event.created_at,
        "details": event.details,
    } for event in events]


@app.post(
    "/paper/deployments",
    response_model=PaperDeploymentResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["paper trading"],
)
async def arm_paper_deployment(
    body: PaperTradeCreateRequest,
    user: CurrentUser,
    session: SessionDep,
    idempotency_key: Annotated[
        str, Header(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    ],
) -> PaperDeploymentResponse:
    if body.expiry_test_only:
        raise HTTPException(422, "Expiry test positions require explicit direct entry")
    if not get_settings().paper_trading_enabled:
        raise HTTPException(503, "Paper trading is disabled")
    if body.underlying != "NIFTY" and not get_settings().paper_index_execution_enabled:
        raise HTTPException(409, "Index-specific automatic arming is not enabled yet")
    try:
        index = await resolve_option_index(body.underlying)
    except MarketDataProviderError as error:
        raise _market_data_http_error(error) from error
    body.underlying = index.symbol
    if body.strategy_key == "custom" or get_strategy(body.strategy_key) is None:
        raise HTTPException(422, "Only registered defined-risk templates can be armed")
    if (
        not body.entry_conditions
        or body.stop_loss_amount is None
        or body.trailing_stop_amount is None
    ):
        raise HTTPException(422, "Armed paper strategies require entry, stop and moving-stop rules")
    digest = hashlib.sha256(
        json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    previous = session.scalar(select(PaperDeployment).where(
        PaperDeployment.user_id == user.id,
        PaperDeployment.command_key == idempotency_key,
    ))
    if previous:
        if previous.request_hash != digest:
            raise HTTPException(409, "Command key was already used with different parameters")
        return PaperDeploymentResponse.from_model(previous)
    if expiry_has_passed(body.expiry):
        raise HTTPException(409, "Option expiry has passed")
    strategy = get_strategy(body.strategy_key)
    assert strategy is not None
    _require_template_acknowledgement(body, strategy)
    if body.strategy_version_id is not None:
        version = session.scalar(select(StrategyVersion).where(
            StrategyVersion.id == body.strategy_version_id,
            StrategyVersion.user_id == user.id,
        ))
        if version is None:
            raise HTTPException(404, "Strategy version not found")
        if (
            _strategy_version_hash(version.definition) != version.definition_hash
            or _strategy_version_definition(
                body, strategy, index if index.symbol != "NIFTY" else None
            ) != version.definition
        ):
            raise HTTPException(409, "Armed rules differ from the saved strategy version")
    provider = get_market_data_provider()
    if provider.provider_id == "unconfigured" or not callable(
        getattr(provider, "get_option_contracts", None)
    ):
        raise HTTPException(503, "NIFTY options market data is unavailable")
    _get_paper_account(session, user)
    session.commit()
    account = lock_account(session, user.id)
    if account is None or account.kill_switch_enabled:
        raise HTTPException(423, "Paper kill switch is active")
    _require_consistent_paper_ledger(session, account)
    previous = session.scalar(select(PaperDeployment).where(
        PaperDeployment.user_id == user.id,
        PaperDeployment.command_key == idempotency_key,
    ))
    if previous:
        if previous.request_hash != digest:
            raise HTTPException(409, "Command key was already used with different parameters")
        return PaperDeploymentResponse.from_model(previous)
    waiting = session.scalars(select(PaperDeployment).where(
        PaperDeployment.user_id == user.id,
        PaperDeployment.status == "WAITING_FOR_ENTRY",
    )).all()
    if len(waiting) >= 5:
        raise HTTPException(409, "At most five paper strategies can wait for entry")
    if any(
        existing.request_data.get("strategy_key") == body.strategy_key
        and existing.request_data.get("underlying", "NIFTY") == index.symbol
        and existing.request_data.get("expiry") == body.expiry.isoformat()
        and existing.request_data.get("reference_strike") == str(body.reference_strike)
        for existing in waiting
    ):
        raise HTTPException(409, "This strategy and strike are already waiting for entry")
    now = datetime.now(timezone.utc)
    row = PaperDeployment(
        user_id=user.id,
        command_key=idempotency_key,
        request_hash=digest,
        request_data={
            **body.model_dump(mode="json"),
            "strategy_definition": _strategy_version_definition(
                body, strategy, index if index.symbol != "NIFTY" else None,
            ),
            **({"option_index": index.model_dump()} if index.symbol != "NIFTY" else {}),
            "market_data_provider": provider.provider_id,
            "exit_policy": OptionExitPolicy(
                get_settings().paper_expiry_square_off_minutes_before_close
            ).as_dict(),
        },
        status="WAITING_FOR_ENTRY",
        detail="Armed; waiting for a later qualifying open-market option-chain snapshot",
        trade_id=None,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    session.add(AuditEvent(
        actor_user_id=user.id,
        correlation_id=str(row.id),
        event_type="paper.deployment.armed",
        details={"deployment_id": str(row.id), "strategy_key": body.strategy_key},
    ))
    session.commit()
    return PaperDeploymentResponse.from_model(row)


@app.post(
    "/paper/deployments/{deployment_id}/cancel",
    response_model=PaperDeploymentResponse,
    tags=["paper trading"],
)
def cancel_paper_deployment(
    deployment_id: UUID, user: CurrentUser, session: SessionDep
) -> PaperDeploymentResponse:
    lock_account(session, user.id)
    row = session.scalar(select(PaperDeployment).where(
        PaperDeployment.id == deployment_id,
        PaperDeployment.user_id == user.id,
    ).with_for_update())
    if row is None:
        raise HTTPException(404, "Paper deployment not found")
    if row.status == "WAITING_FOR_ENTRY":
        row.status = "CANCELLED"
        row.detail = "Cancelled before entry"
        row.updated_at = datetime.now(timezone.utc)
        session.add(AuditEvent(
            actor_user_id=user.id,
            correlation_id=str(row.id),
            event_type="paper.deployment.cancelled",
            details={"deployment_id": str(row.id)},
        ))
    elif row.status != "CANCELLED":
        raise HTTPException(409, "This deployment has already filled or ended")
    session.commit()
    return PaperDeploymentResponse.from_model(row)


@app.post(
    "/paper/trades",
    response_model=PaperTradeResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["paper trading"],
)
async def open_paper_trade(
    body: PaperTradeCreateRequest,
    user: CurrentUser,
    session: SessionDep,
    idempotency_key: Annotated[
        str, Header(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    ],
) -> PaperTradeResponse:
    return await _open_paper_trade_with_provider(
        body, user, session, idempotency_key, get_market_data_provider()
    )


@app.get("/paper/expiry-test-policy", tags=["paper trading"])
def expiry_test_policy(user: CurrentUser) -> dict[str, object]:
    del user
    return {"version": EXPIRY_TEST_POLICY_VERSION, "maximum_loss": "10000", "lots": 1}


def _entry_check_dedupe_digest(snapshot: dict[str, object]) -> str:
    """Ignore only receipt time when deciding whether a provider snapshot is new."""
    # Receipt time belongs in the saved first observation, but a retry of the
    # same provider snapshot is not a distinct market decision.
    dedupe_snapshot = snapshot.copy()
    entry = snapshot.get("entry_rule_snapshot")
    if isinstance(entry, dict):
        context = entry.get("entry_market_context")
        if isinstance(context, dict):
            news = context.get("news")
            dedupe_snapshot["entry_rule_snapshot"] = {
                **entry,
                "entry_market_context": {
                    **{key: value for key, value in context.items()
                       if key not in {"available_at", "news"}},
                    "news": ({key: value for key, value in news.items()
                              if key != "received_at"}
                             if isinstance(news, dict) else news),
                },
            }
    return hashlib.sha256(json.dumps(
        dedupe_snapshot, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


def _record_armed_entry_check(
    session: SessionDep, user_id: UUID, deployment_id: UUID,
    lease: LeaseToken | None, snapshot: dict[str, object],
) -> None:
    """Persist each distinct evaluated provider snapshot before an unfilled check can roll back."""
    if lease is None or not assert_owned(session, lease):
        raise HTTPException(409, "Paper worker lease expired")
    deployment = session.scalar(select(PaperDeployment).where(
        PaperDeployment.id == deployment_id,
        PaperDeployment.user_id == user_id,
        PaperDeployment.status == "WAITING_FOR_ENTRY",
    ))
    if deployment is None:
        raise HTTPException(409, "Paper deployment is no longer armed")
    digest = hashlib.sha256(json.dumps(
        snapshot, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    dedupe_digest = _entry_check_dedupe_digest(snapshot)
    previous = session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.actor_user_id == user_id,
            AuditEvent.correlation_id == str(deployment_id),
            AuditEvent.event_type == "paper.deployment.entry_checked",
        )
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(1)
    )
    if previous is not None:
        previous_snapshot = previous.details.get("snapshot")
        if isinstance(previous_snapshot, dict):
            # Old events have no separate dedupe digest; compare their saved
            # snapshot without rewriting the signed full-payload digest.
            previous_digest = hashlib.sha256(json.dumps(
                previous_snapshot, sort_keys=True, separators=(",", ":")
            ).encode()).hexdigest()
            if (previous.details.get("snapshot_digest") == previous_digest
                    and _entry_check_dedupe_digest(previous_snapshot) == dedupe_digest):
                return
    decision = snapshot.get("decision")
    quality = snapshot.get("quality")
    if isinstance(quality, dict) and quality.get("status") == "FAIL":
        deployment.detail = "Latest market check: option-chain quality failed"
    elif decision == "RULES_NOT_MET":
        deployment.detail = "Latest market check: saved entry conditions are not satisfied"
    else:
        deployment.detail = "Entry rules matched; awaiting quote, session and risk checks"
    deployment.updated_at = datetime.now(timezone.utc)
    session.add(AuditEvent(
        actor_user_id=user_id,
        correlation_id=str(deployment_id),
        event_type="paper.deployment.entry_checked",
        details={
            "deployment_id": str(deployment_id),
            "snapshot_digest": digest,
            "dedupe_digest": dedupe_digest,
            "snapshot": snapshot,
        },
    ))
    session.commit()


def _armed_leg_quote_evidence(
    strategy: StrategyDefinition | None,
    reference_strike: Decimal | None,
    expiry: date,
    contracts: list[OptionContract],
    chain: OptionChain,
    lots: int,
) -> dict[str, object]:
    """Bounded raw quote observations for the selected template legs, never paper fills."""
    if strategy is None or reference_strike is None:
        return {"status": "CONTRACT_UNAVAILABLE", "legs": []}
    try:
        selected = _strategy_contracts(strategy, reference_strike, expiry, contracts)
    except HTTPException:
        return {"status": "CONTRACT_UNAVAILABLE", "legs": []}
    by_key = {leg.instrument_token: leg for leg in chain.contracts}
    legs: list[dict[str, object]] = []
    for contract, side, quantity_ratio in selected:
        observed = by_key.get(contract.instrument_key)
        quote = observed.quote if observed is not None else None
        side_quote = (quote.ask if side == "BUY" else quote.bid) if quote else None
        legs.append({
            "instrument_key": contract.instrument_key,
            "option_type": contract.option_type,
            "strike": str(contract.strike),
            "side": side,
            "quantity_units": lots * quantity_ratio * contract.lot_size,
            "quote_timestamp": quote.timestamp.isoformat() if quote else None,
            "book_as_of": quote.book_as_of.isoformat()
            if quote and quote.book_as_of else None,
            "book_timestamp_basis": quote.book_timestamp_basis if quote else None,
            "last_trade_time": quote.last_trade_time.isoformat()
            if quote and quote.last_trade_time else None,
            "timestamp_basis": quote.timestamp_basis if quote else None,
            "bid": str(quote.bid) if quote and quote.bid is not None else None,
            "ask": str(quote.ask) if quote and quote.ask is not None else None,
            "bid_quantity": observed.bid_quantity if observed else None,
            "ask_quantity": observed.ask_quantity if observed else None,
            "selected_side_quote": str(side_quote) if side_quote is not None else None,
        })
    return {
        "status": "RECORDED" if all(leg["selected_side_quote"] is not None for leg in legs)
        else "PARTIAL",
        "legs": legs,
    }


async def _open_paper_trade_with_provider(
    body: PaperTradeCreateRequest,
    user: User,
    session: SessionDep,
    idempotency_key: str,
    provider: MarketDataProvider,
    lease: LeaseToken | None = None,
    deployment_id: UUID | None = None,
    exit_policy: OptionExitPolicy | None = None,
) -> PaperTradeResponse:
    request_hash = hashlib.sha256(
        json.dumps(
            body.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    replay = _entry_replay(session, user.id, idempotency_key, request_hash)
    if replay is not None:
        return replay
    if expiry_has_passed(body.expiry):
        raise HTTPException(
            status_code=409,
            detail="Selected option expiry has passed in Asia/Kolkata; no paper entry was created",
        )
    settings = get_settings()
    policy = exit_policy or OptionExitPolicy(
        settings.paper_expiry_square_off_minutes_before_close
    )
    if body.expiry_test_only:
        if deployment_id is not None or exit_policy is not None:
            raise HTTPException(422, "Expiry test cannot inherit an automated deployment policy")
        policy = OptionExitPolicy(
            settings.paper_expiry_square_off_minutes_before_close,
            version=EXPIRY_TEST_POLICY_VERSION,
        )
    if not settings.paper_trading_enabled:
        raise HTTPException(status_code=503, detail="Paper trading is disabled by configuration")
    if body.underlying != "NIFTY" and not settings.paper_index_execution_enabled:
        raise HTTPException(409, "Index-specific paper execution is not enabled yet")
    try:
        index = await resolve_option_index(body.underlying)
    except MarketDataProviderError as error:
        raise _market_data_http_error(error) from error
    if deployment_id is not None and index.symbol != "NIFTY":
        armed = session.get(PaperDeployment, deployment_id)
        if armed is None or armed.request_data.get("option_index") != index.model_dump():
            raise HTTPException(409, "Armed index identity differs from current contract catalog")
    is_custom_strategy = body.strategy_key == "custom"
    strategy = None if is_custom_strategy else get_strategy(body.strategy_key)
    if not is_custom_strategy and strategy is None:
        raise HTTPException(status_code=422, detail="Unknown paper strategy")
    if deployment_id is not None:
        armed = session.scalar(select(PaperDeployment).where(
            PaperDeployment.id == deployment_id, PaperDeployment.user_id == user.id,
        ))
        if armed is None or armed.status != "WAITING_FOR_ENTRY":
            raise HTTPException(409, "Paper deployment is no longer armed")
        _require_armed_strategy_identity(armed, body, strategy, index, request_hash)
    else:
        _require_template_acknowledgement(body, strategy)
    strategy_version = None
    if body.strategy_version_id is not None:
        strategy_version = session.scalar(
            select(StrategyVersion).where(
                StrategyVersion.id == body.strategy_version_id,
                StrategyVersion.user_id == user.id,
            )
        )
        if strategy_version is None:
            raise HTTPException(status_code=404, detail="Strategy version not found")
        if strategy is None:
            raise HTTPException(
                status_code=409, detail="Custom structures cannot use template versions"
            )
        expected_definition = _strategy_version_definition(
            body, strategy, index if index.symbol != "NIFTY" else None
        )
        if (
            _strategy_version_hash(strategy_version.definition) != strategy_version.definition_hash
            or expected_definition != strategy_version.definition
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Paper entry differs from the saved strategy version; "
                    "reload it or clear the selection"
                ),
            )
    account = _get_paper_account(session, user)
    session.commit()
    if provider.provider_id == "unconfigured" or not callable(
        getattr(provider, "get_option_contracts", None)
    ):
        raise HTTPException(
            status_code=503, detail="Paper trading requires an options-capable market provider"
        )
    try:
        if index.symbol != "NIFTY" and not callable(
            getattr(provider, "get_index_option_session", None)
        ):
            raise MarketDataUnavailable("Index session capability unavailable")
        nfo_session = (
            await provider.get_nfo_session(exchange_today()) if index.symbol == "NIFTY"
            else await provider.get_index_option_session(index.symbol, exchange_today())
        )
        session_check_at = datetime.now(timezone.utc)
        require_derivatives_session_open(nfo_session, index.derivatives_exchange, session_check_at)
        if expiry_square_off_due(
            body.expiry, nfo_session,
            now=session_check_at,
            minutes_before_close=policy.square_off_minutes_before_close,
            expected_exchange=index.derivatives_exchange,
        ):
            raise PaperSessionError(
                "Expiry-day paper entry window is closed before the scheduled square-off"
            )
        news_observation: dict[str, Any] = {
            "source": "upstox_news_v2",
            "instrument_key": index.instrument_key,
            "status": "UNAVAILABLE",
            "coverage_reviewed": False,
            "items": [],
        }
        news_method = getattr(provider, "get_recent_news", None)
        if index.symbol == "NIFTY" and callable(news_method):
            try:
                news_observation = await asyncio.wait_for(
                    news_method(index.instrument_key), timeout=1.5
                )
            except (TimeoutError, MarketDataProviderError, MarketDataUnavailable):
                pass  # Optional context cannot prevent or reprice a paper fill.
        option_contracts = await provider.get_option_contracts(index.symbol, body.expiry)
        chain = await provider.get_option_chain(index.symbol, body.expiry)
        if chain.underlying != index.symbol or chain.expiry != body.expiry:
            raise PaperSessionError("Option-chain identity differs from the selected index")
        if index.symbol != "NIFTY":
            saved_option_index(index.symbol, {
                "option_index": index.model_dump(), "market_data_provider": provider.provider_id,
            }, [contract.instrument_key for contract in option_contracts])
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        raise _market_data_http_error(error) from error
    except PaperSessionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    quality = assess_option_chain(chain)
    leg_quotes = _armed_leg_quote_evidence(
        strategy, body.reference_strike, body.expiry, option_contracts, chain, body.lots
    ) if deployment_id is not None else None
    if quality.status == "FAIL":
        if deployment_id is not None:
            _record_armed_entry_check(session, user.id, deployment_id, lease, {
                "version": 1,
                "provider": provider.provider_id,
                "request_hash": request_hash,
                "strategy_key": body.strategy_key,
                "expiry": body.expiry.isoformat(),
                "market_as_of": chain.as_of.isoformat(),
                "timestamp_basis": chain.timestamp_basis,
                "decision": "QUALITY_FAIL",
                "quality": quality.model_dump(mode="json", exclude={"checked_at"}),
                "leg_quotes": leg_quotes,
            })
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Current option-chain quality is FAIL; no paper order was created",
                "quality": quality.model_dump(mode="json"),
            },
        )
    features = calculate_option_features(chain)
    rule_metrics: dict[RuleMetric, Decimal | None] = {
        "put_call_ratio_oi": features.put_call_ratio_oi,
        "put_call_ratio_volume": features.put_call_ratio_volume,
        "atm_iv_skew_put_minus_call_points": features.atm_iv_skew_put_minus_call_points,
        "largest_call_oi_share_pct": features.largest_call_oi_share_pct,
        "largest_put_oi_share_pct": features.largest_put_oi_share_pct,
        "median_relative_spread_pct": features.median_relative_spread_pct,
        "underlying_spot": chain.underlying_spot,
    }
    entry_conditions = [
        EntryCondition(
            metric=condition.metric,
            operator=condition.operator,
            threshold=condition.threshold,
        )
        for condition in body.entry_conditions
    ]
    condition_results = evaluate_entry_conditions(entry_conditions, rule_metrics)
    entry_rule_snapshot = {
        **({"testing_only": True, "test_purpose": "expiry_settlement"}
           if body.expiry_test_only else {}),
        **({"option_index": index.model_dump()} if index.symbol != "NIFTY" else {}),
        "market_data_provider": provider.provider_id,
        "exit_policy": policy.as_dict(),
        "conditions": [condition.model_dump(mode="json") for condition in body.entry_conditions],
        "results": [result.as_dict() for result in condition_results],
        "market_as_of": chain.as_of.isoformat(),
        "quality_status": quality.status,
        "entry_market_context": {
            "version": "nifty-option-observed-v5",
            "source": provider.provider_id,
            "availability": "SERVER_OBSERVED_AT_ENTRY",
            "available_at": datetime.now(timezone.utc).isoformat(),
            "underlying": index.symbol,
            "strategy_key": body.strategy_key,
            "reference_strike": str(body.reference_strike)
            if body.reference_strike is not None else None,
            "chain_digest": fingerprint(chain.model_dump(mode="json")),
            "timestamp_basis": chain.timestamp_basis,
            "source_timestamp_verified": quality.source_timestamp_verified,
            "quality_status": quality.status,
            "underlying_spot": str(chain.underlying_spot),
            "observed_at": chain.as_of.isoformat(),
            "expiry": body.expiry.isoformat(),
            "calendar_days_to_expiry": (body.expiry - exchange_today()).days,
            "contracts_observed": len(chain.contracts),
            "aggregate_scope": "RETURNED_OPTION_CHAIN_CONTRACTS_ONLY",
            "call_volume": features.call_volume,
            "put_volume": features.put_volume,
            "returned_chain_volume": (
                features.call_volume + features.put_volume
                if features.call_volume is not None and features.put_volume is not None
                else None
            ),
            "returned_chain_bid_quantity": sum(
                contract.bid_quantity for contract in chain.contracts
                if contract.bid_quantity is not None
            ) if any(contract.bid_quantity is not None for contract in chain.contracts) else None,
            "returned_chain_ask_quantity": sum(
                contract.ask_quantity for contract in chain.contracts
                if contract.ask_quantity is not None
            ) if any(contract.ask_quantity is not None for contract in chain.contracts) else None,
            "contracts_with_bid_quantity": sum(
                contract.bid_quantity is not None for contract in chain.contracts
            ),
            "contracts_with_ask_quantity": sum(
                contract.ask_quantity is not None for contract in chain.contracts
            ),
            "call_open_interest": features.call_open_interest,
            "put_open_interest": features.put_open_interest,
            "call_open_interest_change": features.call_open_interest_change,
            "put_open_interest_change": features.put_open_interest_change,
            "put_call_ratio_oi": str(features.put_call_ratio_oi)
            if features.put_call_ratio_oi is not None else None,
            "put_call_ratio_volume": str(features.put_call_ratio_volume)
            if features.put_call_ratio_volume is not None else None,
            "atm_call_iv_pct": str(features.atm_call_iv_pct)
            if features.atm_call_iv_pct is not None else None,
            "atm_put_iv_pct": str(features.atm_put_iv_pct)
            if features.atm_put_iv_pct is not None else None,
            "atm_strike": str(features.atm_strike) if features.atm_strike is not None else None,
            "atm_iv_skew_put_minus_call_points": (
                str(features.atm_iv_skew_put_minus_call_points)
                if features.atm_iv_skew_put_minus_call_points is not None else None
            ),
            "median_relative_spread_pct": str(features.median_relative_spread_pct)
            if features.median_relative_spread_pct is not None else None,
            "two_sided_quote_count": features.two_sided_quote_count,
            "contracts_with_delta": features.contracts_with_delta,
            "contracts_with_gamma": features.contracts_with_gamma,
            "contracts_with_theta": features.contracts_with_theta,
            "contracts_with_vega": features.contracts_with_vega,
            "quality_issues": [
                {"code": issue.code, "severity": issue.severity, "count": issue.count}
                for issue in quality.issues
            ],
            "largest_call_oi_strike": str(features.largest_call_oi_strike)
            if features.largest_call_oi_strike is not None else None,
            "largest_put_oi_strike": str(features.largest_put_oi_strike)
            if features.largest_put_oi_strike is not None else None,
            "largest_call_oi_share_pct": str(features.largest_call_oi_share_pct)
            if features.largest_call_oi_share_pct is not None else None,
            "largest_put_oi_share_pct": str(features.largest_put_oi_share_pct)
            if features.largest_put_oi_share_pct is not None else None,
            "selected_leg_books": leg_quotes,
            "buyer_count": None,
            "seller_count": None,
            "news": news_observation,
            "validated_support": None,
            "validated_resistance": None,
            "realized_volatility": None,
            "iv_rank_pct": None,
            "pcr_condition": observed_pcr_condition(features.put_call_ratio_oi),
        },
        "strategy_version_id": str(strategy_version.id) if strategy_version else None,
        "strategy_definition_hash": strategy_version.definition_hash if strategy_version else None,
        "template_version": strategy.version if strategy else None,
    }
    if deployment_id is not None:
        _record_armed_entry_check(session, user.id, deployment_id, lease, {
            "version": 1,
            "provider": provider.provider_id,
            "request_hash": request_hash,
            "strategy_key": body.strategy_key,
            "expiry": body.expiry.isoformat(),
            "market_as_of": chain.as_of.isoformat(),
            "timestamp_basis": chain.timestamp_basis,
            "decision": "RULES_PASS" if all(result.passed is True for result in condition_results)
            else "RULES_NOT_MET",
            "quality": quality.model_dump(mode="json", exclude={"checked_at"}),
            "leg_quotes": leg_quotes,
            "rule_metrics": {
                name: str(value) if value is not None else None
                for name, value in rule_metrics.items()
            },
            "entry_rule_snapshot": entry_rule_snapshot,
            "reference_strike": str(body.reference_strike),
            "lots": body.lots,
            "stop_loss_amount": str(body.stop_loss_amount),
            "target_profit_amount": str(body.target_profit_amount),
            "trailing_stop_amount": str(body.trailing_stop_amount),
        })
    if any(result.passed is not True for result in condition_results):
        if deployment_id is None:
            session.add(
                AuditEvent(
                    actor_user_id=user.id,
                    event_type="paper.trade.entry_conditions_rejected",
                    details={"entry_rule_snapshot": entry_rule_snapshot},
                )
            )
            session.commit()
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Paper entry conditions are not all satisfied by the latest snapshot",
                "conditions": entry_rule_snapshot["results"],
            },
        )
    selected: list[tuple[OptionContract, str, int]]
    if is_custom_strategy:
        custom_legs = body.custom_legs
        if not custom_legs:
            raise HTTPException(status_code=422, detail="Custom option legs are required")
        contracts_by_key = {
            (contract.strike, contract.option_type): contract
            for contract in option_contracts
            if contract.expiry == body.expiry
        }
        selected = []
        for leg in custom_legs:
            contract = contracts_by_key.get((leg.strike, leg.option_type))
            if contract is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"{leg.option_type} strike {leg.strike} is unavailable for this expiry"
                    ),
                )
            selected.append((contract, leg.side, body.lots * leg.ratio))
        strategy_key = "custom"
        strategy_name = "Custom options structure"
        reference_strike = body.reference_strike or custom_legs[0].strike
    else:
        if strategy is None or body.reference_strike is None:
            raise HTTPException(status_code=422, detail="Strategy template inputs are incomplete")
        template_selections = _strategy_contracts(
            strategy, body.reference_strike, body.expiry, option_contracts
        )
        selected = [
            (contract, side, body.lots * quantity_ratio)
            for contract, side, quantity_ratio in template_selections
        ]
        strategy_key = strategy.key
        strategy_name = strategy.name
        reference_strike = body.reference_strike

    lot_sizes = {contract.lot_size for contract, _, _ in selected}
    if len(lot_sizes) != 1:
        raise HTTPException(
            status_code=409, detail="Strategy contracts report inconsistent lot sizes"
        )
    lot_size = next(iter(lot_sizes))
    cash_flow = Decimal(0)
    fills: list[dict[str, object]] = []
    saved_legs: list[dict[str, object]] = []
    payoff_legs: list[PayoffLeg] = []
    for option_contract, side, leg_lots in selected:
        quantity_units = leg_lots * lot_size
        fill_price, market_contract = _require_quote(
            chain, option_contract.instrument_key, side, quantity_units
        )
        amount = (fill_price * quantity_units).quantize(_CENT, rounding=ROUND_HALF_UP)
        leg_cash_flow = -amount if side == "BUY" else amount
        cash_flow += leg_cash_flow
        payoff_legs.append(
            PayoffLeg(
                option_type=option_contract.option_type,
                strike=option_contract.strike,
                quantity=leg_lots if side == "BUY" else -leg_lots,
                entry_premium=fill_price,
            )
        )
        saved_leg: dict[str, object] = {
            "instrument_key": option_contract.instrument_key,
            "trading_symbol": option_contract.trading_symbol,
            "option_type": option_contract.option_type,
            "side": side,
            "strike": str(option_contract.strike),
            "quantity_units": quantity_units,
            "entry_price": str(fill_price),
            "entry_bid": str(market_contract.quote.bid),
            "entry_ask": str(market_contract.quote.ask),
        }
        saved_legs.append(saved_leg)
        fills.append(
            {
                **saved_leg,
                "fill_price": str(fill_price),
                "cash_flow": str(leg_cash_flow),
                "quote_timestamp": market_contract.quote.timestamp.isoformat(),
                "timestamp_basis": market_contract.quote.timestamp_basis,
                "last_trade_time": (
                    market_contract.quote.last_trade_time.isoformat()
                    if market_contract.quote.last_trade_time else None
                ),
                "book_as_of": market_contract.quote.book_as_of.isoformat()
                if market_contract.quote.book_as_of else None,
                "book_timestamp_basis": market_contract.quote.book_timestamp_basis,
                "displayed_bid_quantity": market_contract.bid_quantity,
                "displayed_ask_quantity": market_contract.ask_quantity,
                "traded_volume": market_contract.quote.volume,
                "open_interest": market_contract.open_interest,
                "open_interest_change": market_contract.open_interest_change,
                "implied_volatility": str(market_contract.implied_volatility)
                if market_contract.implied_volatility is not None else None,
                "greeks": {
                    name: str(value) if value is not None else None
                    for name, value in market_contract.greeks.items()
                },
            }
        )
    cash_flow = cash_flow.quantize(_CENT, rounding=ROUND_HALF_UP)
    selected_strikes = [contract.strike for contract, _, _ in selected]
    spot = chain.underlying_spot or Decimal(1)
    payoff = calculate_expiry_payoff(
        PayoffRequest(
            underlying=index.symbol,
            multiplier=Decimal(lot_size),
            price_min=Decimal(0),
            price_max=max(spot, max(selected_strikes)) * Decimal(2),
            grid_points=2,
            legs=payoff_legs,
        )
    )
    if payoff.maximum_loss_unbounded or payoff.maximum_loss is None:
        raise HTTPException(
            status_code=409,
            detail="This strategy has unbounded maximum loss and cannot be paper traded",
        )
    max_loss = payoff.maximum_loss.quantize(_CENT, rounding=ROUND_HALF_UP)
    if body.expiry_test_only and max_loss > Decimal("10000"):
        raise HTTPException(409, "Expiry test maximum premium loss must not exceed 10000 rupees")
    if max_loss <= 0:
        raise HTTPException(
            status_code=409, detail="Calculated maximum loss is invalid; trade rejected"
        )

    locked_account = lock_account(session, user.id)
    if lease is not None and not assert_owned(session, lease):
        session.rollback()
        raise HTTPException(status_code=409, detail="Worker ownership expired before paper entry")
    deployment = None
    if deployment_id is not None:
        deployment = session.scalar(select(PaperDeployment).where(
            PaperDeployment.id == deployment_id,
            PaperDeployment.user_id == user.id,
        ).with_for_update().execution_options(populate_existing=True))
        if deployment is None or deployment.status != "WAITING_FOR_ENTRY":
            session.rollback()
            raise HTTPException(status_code=409, detail="Paper deployment is no longer armed")
        _require_armed_strategy_identity(deployment, body, strategy, index, request_hash)
        if deployment.request_data.get("market_data_provider", "upstox") != provider.provider_id:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="Armed strategy belongs to another market provider; no fill recorded",
            )
        armed_at = deployment.created_at
        if armed_at.tzinfo is None:
            armed_at = armed_at.replace(tzinfo=timezone.utc)
        if chain.as_of <= armed_at:
            session.rollback()
            raise HTTPException(status_code=409, detail="Waiting for a later market snapshot")
    replay = _entry_replay(session, user.id, idempotency_key, request_hash)
    if replay is not None:
        session.rollback()
        return replay
    if expiry_has_passed(body.expiry):
        raise HTTPException(
            status_code=409,
            detail="Selected option expiry passed before the paper entry could be recorded",
        )
    try:
        session_check_at = datetime.now(timezone.utc)
        require_derivatives_session_open(nfo_session, index.derivatives_exchange, session_check_at)
        if expiry_square_off_due(
            body.expiry, nfo_session,
            now=session_check_at,
            minutes_before_close=policy.square_off_minutes_before_close,
            expected_exchange=index.derivatives_exchange,
        ):
            raise PaperSessionError(
                "Expiry-day paper entry window is closed before the scheduled square-off"
            )
    except PaperSessionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    for option_contract, side, leg_lots in selected:
        _require_quote(chain, option_contract.instrument_key, side, leg_lots * lot_size)
    if locked_account is None:
        raise HTTPException(status_code=409, detail="Paper account could not be initialized")
    account = locked_account
    _require_consistent_paper_ledger(session, account)
    if body.expiry_test_only and any(
        row.entry_rule_snapshot.get("testing_only") is True
        for row in session.scalars(select(PaperTrade).where(
            PaperTrade.user_id == user.id, PaperTrade.status == "OPEN",
        ))
    ):
        raise HTTPException(409, "Close or settle the existing expiry test before opening another")
    if account.kill_switch_enabled:
        raise HTTPException(
            status_code=423, detail="Paper kill switch is active; new trades are paused"
        )
    open_trades = session.scalars(
        select(PaperTrade).where(PaperTrade.account_id == account.id, PaperTrade.status == "OPEN")
    ).all()
    if len(open_trades) >= settings.paper_max_open_trades:
        raise HTTPException(
            status_code=409, detail="Maximum number of simultaneous paper trades reached"
        )
    per_trade_limit = (
        account.initial_cash * Decimal(settings.paper_max_trade_risk_pct) / Decimal(100)
    ).quantize(_CENT)
    portfolio_limit = (
        account.initial_cash * Decimal(settings.paper_max_portfolio_risk_pct) / Decimal(100)
    ).quantize(_CENT)
    current_risk = sum((trade.max_loss for trade in open_trades), Decimal(0))
    if max_loss > per_trade_limit:
        raise HTTPException(
            status_code=409,
            detail=f"Maximum loss exceeds the per-trade risk cap of ₹{per_trade_limit}",
        )
    if current_risk + max_loss > portfolio_limit:
        raise HTTPException(
            status_code=409,
            detail=f"Open-trade risk would exceed the portfolio cap of ₹{portfolio_limit}",
        )
    daily_budget = daily_risk_budget(
        initial_cash=account.initial_cash,
        limit_pct=settings.paper_daily_loss_limit_pct,
        realized_pnl=daily_realized_pnl(session, account.id, exchange_today()),
        open_max_loss=current_risk,
    )
    if max_loss > daily_budget.remaining_for_new_risk:
        raise HTTPException(
            status_code=409,
            detail=(
                "Daily paper loss budget would be exceeded; "
                f"₹{daily_budget.remaining_for_new_risk} remains for new risk"
            ),
        )
    projected_cash = (account.cash_balance + cash_flow).quantize(_CENT)
    if current_risk + max_loss > projected_cash:
        raise HTTPException(
            status_code=409,
            detail="Available paper cash cannot cover maximum loss across open positions",
        )

    trade = PaperTrade(
        account_id=account.id,
        user_id=user.id,
        strategy_key=strategy_key,
        strategy_name=(f"TEST ONLY · Expiry · {strategy_name}"
                       if body.expiry_test_only else strategy_name),
        underlying=index.symbol,
        expiry=body.expiry,
        reference_strike=reference_strike,
        lots=body.lots,
        lot_size=lot_size,
        max_loss=max_loss,
        entry_cash_flow=cash_flow,
        entry_rule_snapshot=entry_rule_snapshot,
        stop_loss_amount=body.stop_loss_amount,
        target_profit_amount=body.target_profit_amount,
        trailing_stop_amount=body.trailing_stop_amount,
        peak_unrealized_pnl=Decimal(0),
        exit_reason=None,
        realized_pnl=None,
        status="OPEN",
        legs={"items": saved_legs},
    )
    account.cash_balance = (account.cash_balance + cash_flow).quantize(_CENT)
    account.updated_at = datetime.now(timezone.utc)
    session.add(trade)
    session.flush()
    session.add(
        PaperOrder(
            account_id=account.id,
            trade_id=trade.id,
            user_id=user.id,
            action="OPEN",
            status="EXECUTED",
            net_cash_flow=cash_flow,
            created_at=datetime.now(timezone.utc),
            fill_snapshot={
                "paper_only": True,
                "market_as_of": chain.as_of.isoformat(),
                "quality_status": quality.status,
                "entry_rule_snapshot": entry_rule_snapshot,
                "modeled_costs": cost_snapshot(fills),
                "fills": fills,
                "method": (
                    "Immediate simulated fills: buys at ask and sells at bid; "
                    "no queue or slippage model."
                ),
            },
        )
    )
    session.add(
        AuditEvent(
            actor_user_id=user.id,
            event_type="paper.trade.opened",
            details={
                "trade_id": str(trade.id),
                "strategy_key": strategy_key,
                "lots": body.lots,
                "max_loss": str(max_loss),
                "entry_rule_snapshot": entry_rule_snapshot,
            },
        )
    )
    _add_user_notification(
        session,
        user.id,
        "paper.trade.opened",
        f"Paper position opened: {strategy_name}",
        f"{body.lots} base lot(s) · maximum expiry loss ₹{max_loss}",
        {
            "trade_id": str(trade.id),
            "strategy_key": strategy_key,
            "max_loss": str(max_loss),
            "entry_cash_flow": str(cash_flow),
        },
    )
    session.flush()
    session.refresh(trade)
    response = _paper_trade_response(trade)
    if quality.status == "WARN":
        response.execution_warnings.append(
            "Option-chain quality is WARN. Review the quality report; source timestamps may not "
            "be exchange-verified."
        )
    session.add(
        PaperEntryCommand(
            user_id=user.id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            trade_id=trade.id,
            response_data=response.model_dump(mode="json"),
        )
    )
    if deployment is not None:
        deployment.status = "FILLED"
        deployment.trade_id = trade.id
        deployment.detail = "Entry signal passed; paper trade opened"
        deployment.updated_at = datetime.now(timezone.utc)
        session.add(AuditEvent(
            actor_user_id=user.id,
            correlation_id=str(deployment.id),
            event_type="paper.deployment.filled",
            details={"deployment_id": str(deployment.id), "trade_id": str(trade.id)},
        ))
    session.commit()
    return response


@app.get("/paper/trades", response_model=PaperTradeListResponse, tags=["paper trading"])
async def paper_trades(user: CurrentUser, session: SessionDep) -> PaperTradeListResponse:
    account = _paper_account(session, user, create=False)
    if account is None:
        return PaperTradeListResponse(trades=[], warnings=[])
    trades = session.scalars(
        select(PaperTrade)
        .where(PaperTrade.account_id == account.id)
        .order_by(desc(PaperTrade.opened_at))
    ).all()
    session.commit()
    warnings: list[str] = []
    provider = get_market_data_provider()
    indexes: dict[UUID, OptionIndex] = {}
    scope_errors: dict[UUID, str] = {}
    for trade in trades:
        if trade.status != "OPEN":
            continue
        try:
            indexes[trade.id] = saved_option_index(
                trade.underlying, trade.entry_rule_snapshot,
                [str(leg["instrument_key"]) for leg in trade.legs["items"]],
            )
        except PaperSessionError:
            scope_errors[trade.id] = (
                "Index-specific valuation needs matching pinned identity; "
                "no NIFTY quote was substituted."
            )
    chains: dict[tuple[str, date], OptionChain] = {}
    open_sessions: dict[str, bool] = {}
    groups = sorted({
        (trade.underlying, trade.expiry) for trade in trades if trade.id in indexes
    })
    for underlying, expiry in groups:
        index = next(indexes[trade.id] for trade in trades
                     if trade.id in indexes and trade.underlying == underlying)
        if expiry_has_passed(expiry):
            warnings.append(
                f"Open {underlying} positions expiring {expiry} remain unresolved after expiry; "
                "settlement must be reconciled manually. No synthetic valuation was recorded."
            )
            continue
        try:
            if underlying not in open_sessions:
                open_sessions[underlying] = False
                if underlying != "NIFTY" and not callable(
                    getattr(provider, "get_index_option_session", None)
                ):
                    raise MarketDataUnavailable("Index session capability unavailable")
                scheduled = (
                    await provider.get_nfo_session(exchange_today()) if underlying == "NIFTY"
                    else await provider.get_index_option_session(underlying, exchange_today())
                )
                require_derivatives_session_open(scheduled, index.derivatives_exchange)
                open_sessions[underlying] = True
            if not open_sessions[underlying]:
                continue
            chain = await provider.get_option_chain(underlying, expiry)
            if chain.underlying != underlying or chain.expiry != expiry:
                raise MarketDataProviderError("Option-chain identity mismatch")
            quality = assess_option_chain(chain)
            if quality.status == "FAIL":
                warnings.append(
                    f"Open positions expiring {expiry} could not be valued: chain quality is FAIL."
                )
                continue
            chains[(underlying, expiry)] = chain
        except PaperSessionError:
            warnings.append(
                f"{index.derivatives_exchange} scheduled session is closed or unverified; "
                "quote-based paper marks are hidden."
            )
        except (MarketDataUnavailable, MarketDataProviderError):
            warnings.append(
                f"Open positions expiring {expiry} could not be valued because "
                "market data is unavailable."
            )
    orders_by_trade: dict[UUID, list[PaperOrder]] = {}
    for order in session.scalars(select(PaperOrder).where(
        PaperOrder.account_id == account.id, PaperOrder.user_id == user.id,
    )):
        orders_by_trade.setdefault(order.trade_id, []).append(order)
    responses: list[PaperTradeResponse] = []
    for trade in trades:
        item = _paper_trade_response(trade)
        item.modeled_costs = summarize_costs(
            trade.status, trade.realized_pnl, orders_by_trade.get(trade.id, []),
        )
        if trade.status == "OPEN":
            if trade.id in scope_errors:
                item.valuation_warning = scope_errors[trade.id]
                responses.append(item)
                continue
            trade_provider = (
                trade.entry_rule_snapshot.get("market_data_provider", "upstox")
                if isinstance(trade.entry_rule_snapshot, dict) else "upstox"
            )
            if trade_provider != provider.provider_id:
                item.valuation_warning = (
                    "This trade belongs to another market provider; its quotes and "
                    "unrealized P&L are unavailable until that provider is active."
                )
                responses.append(item)
                continue
            current_chain = chains.get((trade.underlying, trade.expiry))
            if current_chain is None:
                item.valuation_warning = (
                    "Expired position requires settlement reconciliation; "
                    "unrealized P&L is not shown."
                    if expiry_has_passed(trade.expiry)
                    else (
                        f"{indexes[trade.id].derivatives_exchange} scheduled session "
                        "is closed or unverified; "
                        "unrealized P&L is not shown."
                        if not open_sessions.get(trade.underlying, False)
                        else "Current quotes are unavailable; unrealized P&L is not shown."
                    )
                )
            else:
                close_flow = Decimal(0)
                all_quoted = True
                settings = get_settings()
                for leg in item.legs:
                    exit_side = "SELL" if leg.side == "BUY" else "BUY"
                    try:
                        exit_price, _ = require_quote(
                            current_chain,
                            leg.instrument_key,
                            exit_side,
                            leg.quantity_units,
                            max_age_seconds=settings.paper_quote_max_age_seconds,
                            allow_unverified=settings.paper_allow_unverified_quotes,
                        )
                    except PaperQuoteError:
                        all_quoted = False
                        break
                    close_flow += (
                        exit_price * leg.quantity_units * (1 if exit_side == "SELL" else -1)
                    )
                if all_quoted:
                    item.unrealized_pnl = (trade.entry_cash_flow + close_flow).quantize(
                        _CENT, rounding=ROUND_HALF_UP
                    )
                else:
                    item.valuation_warning = (
                        "One or more leg quotes are missing, stale, or unsafe; "
                        "unrealized P&L is not shown."
                    )
        responses.append(item)
    return PaperTradeListResponse(trades=responses, warnings=warnings)


@app.get("/paper/trades/{trade_id}/observations", tags=["paper trading"])
def paper_trade_observations(
    trade_id: UUID, user: CurrentUser, session: SessionDep, response: Response
) -> list[dict[str, object]]:
    """Show this account's latest evaluated option exit checks, never another user's."""
    response.headers["Cache-Control"] = "no-store"
    owned_trade = session.scalar(select(PaperTrade.id).where(
        PaperTrade.id == trade_id,
        PaperTrade.user_id == user.id,
    ))
    if owned_trade is None:
        raise HTTPException(404, "Paper trade not found")
    events = session.scalars(select(AuditEvent).where(
        AuditEvent.actor_user_id == user.id,
        AuditEvent.correlation_id == str(trade_id),
        AuditEvent.event_type == "paper.trade.exit_checked",
    ).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(200))
    return [{
        "id": str(event.id),
        "created_at": event.created_at.replace(tzinfo=timezone.utc)
        if event.created_at.tzinfo is None else event.created_at,
        "details": event.details,
    } for event in events]


@app.get(
    "/paper/trades/{trade_id}/entry-reconciliation",
    response_model=PaperEntryReconciliationResponse,
    tags=["paper trading"],
)
def paper_trade_entry_reconciliation(
    trade_id: UUID, user: CurrentUser, session: SessionDep, response: Response
) -> PaperEntryReconciliationResponse:
    """Replay this account's saved F&O entry checks without altering paper results."""
    response.headers["Cache-Control"] = "no-store"
    trade = session.scalar(select(PaperTrade).where(
        PaperTrade.id == trade_id, PaperTrade.user_id == user.id,
    ))
    if trade is None:
        raise HTTPException(404, "Paper trade not found")
    deployment = session.scalar(select(PaperDeployment).where(
        PaperDeployment.trade_id == trade_id,
        PaperDeployment.user_id == user.id,
    ))
    if deployment is None:
        return PaperEntryReconciliationResponse(
            trade_id=trade_id, status="INCOMPLETE", evaluated_count=0,
            last_decision=None, detail="This trade has no armed deployment to replay.",
        )
    details = [event.details for event in session.scalars(select(AuditEvent).where(
        AuditEvent.actor_user_id == user.id,
        AuditEvent.correlation_id == str(deployment.id),
        AuditEvent.event_type == "paper.deployment.entry_checked",
    ).order_by(AuditEvent.created_at, AuditEvent.id))]
    replay = replay_observed_entries(deployment, trade, details)
    return PaperEntryReconciliationResponse(trade_id=trade.id, **vars(replay))


@app.get(
    "/paper/trades/{trade_id}/exit-reconciliation",
    response_model=PaperExitReconciliationResponse,
    tags=["paper trading"],
)
def paper_trade_exit_reconciliation(
    trade_id: UUID, user: CurrentUser, session: SessionDep, response: Response
) -> PaperExitReconciliationResponse:
    """Replay this account's recorded F&O exit decisions without altering paper P&L."""
    response.headers["Cache-Control"] = "no-store"
    trade = session.scalar(select(PaperTrade).where(
        PaperTrade.id == trade_id, PaperTrade.user_id == user.id,
    ))
    if trade is None:
        raise HTTPException(404, "Paper trade not found")
    details = [event.details for event in session.scalars(select(AuditEvent).where(
        AuditEvent.actor_user_id == user.id,
        AuditEvent.correlation_id == str(trade_id),
        AuditEvent.event_type == "paper.trade.exit_checked",
    ).order_by(AuditEvent.created_at, AuditEvent.id))]
    replay = replay_observed_exits(trade, details)
    return PaperExitReconciliationResponse(trade_id=trade.id, **vars(replay))


@app.post("/paper/monitor", response_model=PaperMonitorResponse, tags=["paper trading"])
async def monitor_paper_exits(user: CurrentUser, session: SessionDep) -> PaperMonitorResponse:
    return await monitor_exits(user.id, session, get_market_data_provider(), get_settings())


@app.get("/paper/worker", tags=["paper trading"])
def paper_worker_status(user: CurrentUser, session: SessionDep) -> dict[str, object]:
    del user
    lease = session.get(WorkerLease, WORKER_NAME)
    if lease is None:
        return {"status": "NOT_STARTED", "heartbeat_at": None, "last_success_at": None}
    heartbeat = lease.heartbeat_at
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
    last_success = lease.last_success_at
    if last_success is not None and last_success.tzinfo is None:
        last_success = last_success.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - heartbeat).total_seconds()
    threshold = get_settings().paper_worker_interval_seconds * 3 + 10
    return {
        "status": "STALE" if age > threshold else lease.status,
        "heartbeat_at": heartbeat.isoformat(),
        "last_success_at": last_success.isoformat() if last_success else None,
        # This lease is shared by all accounts; never expose another user's trade warnings.
        "detail": (
            "Shared worker health. Reason: one or more checks need attention; "
            "review your own positions and deployment status."
            if lease.status in {"DEGRADED", "ERROR"}
            else "Shared worker health; review each deployment for its latest check."
        ),
        "unverified_quotes_allowed": get_settings().paper_allow_unverified_quotes,
    }


def _require_supported_expired_index(trade: PaperTrade) -> None:
    if trade.underlying not in NSE_INDEX_NAMES or not expiry_has_passed(trade.expiry):
        raise HTTPException(409, "Only an expired supported NSE index trade can be settled")
    if trade.underlying != "NIFTY":
        try:
            index = saved_option_index(
                trade.underlying, trade.entry_rule_snapshot,
                [str(leg["instrument_key"]) for leg in trade.legs["items"]],
            )
            if index.exchange != "NSE":
                raise PaperSessionError("Official NSE close requires a pinned NSE index")
        except PaperSessionError as error:
            raise HTTPException(409, str(error)) from error


def _expiry_settlement_preview(
    trade: PaperTrade, body: PaperExpirySettlementRequest
) -> tuple[PaperExpirySettlementPreviewResponse, ExpirySettlement]:
    _require_supported_expired_index(trade)
    if body.settlement_date != trade.expiry:
        raise HTTPException(422, "Settlement date must equal the option expiry date")
    saved_legs = trade.legs.get("items") if isinstance(trade.legs, dict) else None
    try:
        calculation = calculate_expiry_settlement(
            saved_legs, trade.entry_cash_flow, body.final_index_value
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    preview = PaperExpirySettlementPreviewResponse.model_validate({
        "trade_id": trade.id,
        "settlement_date": body.settlement_date,
        "final_index_value": body.final_index_value,
        "source_reference": body.source_reference,
        "settlement_cash_flow": calculation.settlement_cash_flow,
        "realized_pnl": calculation.realized_pnl,
        "legs": [vars(leg) for leg in calculation.legs],
    })
    return preview, calculation


@app.get(
    "/paper/trades/{trade_id}/expiry-settlement/index-close-reference",
    response_model=PaperExpiryIndexCloseReference,
    tags=["paper trading"],
)
async def paper_expiry_index_close_reference(
    trade_id: UUID, user: CurrentUser, session: SessionDep, response: Response,
) -> PaperExpiryIndexCloseReference:
    """Read-only broker candle cross-check; never an official settlement source."""
    response.headers["Cache-Control"] = "no-store"
    trade = session.scalar(select(PaperTrade).where(
        PaperTrade.id == trade_id, PaperTrade.user_id == user.id,
    ))
    if trade is None:
        raise HTTPException(404, "Paper trade not found")
    if trade.status != "OPEN" or trade.underlying != "NIFTY" or not expiry_has_passed(
        trade.expiry
    ):
        raise HTTPException(409, "Only an open expired NIFTY paper trade can be checked")

    def unavailable(reason: str) -> PaperExpiryIndexCloseReference:
        return PaperExpiryIndexCloseReference(
            status="unavailable", trade_id=trade.id, expiry=trade.expiry, reason=reason,
        )

    provider = get_market_data_provider()
    try:
        market = await provider.get_equity_session(trade.expiry)
        if (
            market is None or market.exchange != "NSE"
            or market.end.tzinfo is None
            or market.end.astimezone(IST).date() != trade.expiry
            or market.end >= datetime.now(timezone.utc)
        ):
            return unavailable("The completed NSE session on this expiry cannot be verified.")
        benchmark = await nifty_benchmark(provider)
        if benchmark is None:
            return unavailable("The provider's NIFTY 50 index instrument is unavailable.")
        start = datetime.combine(
            trade.expiry - timedelta(days=1), datetime.min.time(), tzinfo=IST
        )
        bars = await provider.get_historical(
            benchmark.instrument_key, start, start + timedelta(days=2), "1d"
        )
    except (MarketDataUnavailable, MarketDataProviderError):
        return unavailable("Provider daily index history is unavailable.")
    exact = [bar for bar in bars if bar.timestamp.tzinfo is not None
             and bar.timestamp.astimezone(IST).date() == trade.expiry]
    if len(exact) != 1 or not all(
        value.is_finite() for value in (
            exact[0].open, exact[0].high, exact[0].low, exact[0].close
        )
    ) or not (
        0 < exact[0].low <= min(exact[0].open, exact[0].close)
        <= max(exact[0].open, exact[0].close) <= exact[0].high
    ):
        return unavailable("Exactly one valid expiry-date daily index candle is required.")
    return PaperExpiryIndexCloseReference(
        status="available", trade_id=trade.id, expiry=trade.expiry,
        provider=provider.provider_id, instrument_key=benchmark.instrument_key,
        candle_date=trade.expiry, daily_close=exact[0].close,
    )


@app.get(
    "/paper/trades/{trade_id}/expiry-settlement/official-index-close-reference",
    response_model=PaperOfficialIndexCloseReference,
    tags=["paper trading"],
)
async def paper_official_index_close_reference(
    trade_id: UUID, user: CurrentUser, session: SessionDep, response: Response,
) -> PaperOfficialIndexCloseReference:
    """Read-only NSE archive reference; leaves settlement to a reviewed action."""
    response.headers["Cache-Control"] = "no-store"
    trade = session.scalar(select(PaperTrade).where(
        PaperTrade.id == trade_id, PaperTrade.user_id == user.id,
    ))
    if trade is None:
        raise HTTPException(404, "Paper trade not found")
    if trade.status != "OPEN" or trade.underlying not in NSE_INDEX_NAMES or not expiry_has_passed(
        trade.expiry
    ):
        raise HTTPException(409, "Only an open expired supported NSE index trade can be checked")
    _require_supported_expired_index(trade)
    try:
        reference = (await fetch_nifty_close(trade.expiry) if trade.underlying == "NIFTY"
                     else await fetch_index_close(trade.expiry, trade.underlying))
    except OfficialCloseUnavailable as error:
        return PaperOfficialIndexCloseReference(
            status="unavailable", trade_id=trade.id, expiry=trade.expiry,
            underlying=trade.underlying, index_name=NSE_INDEX_NAMES[trade.underlying],
            reason=str(error),
        )
    return PaperOfficialIndexCloseReference(
        status="available", trade_id=trade.id, expiry=trade.expiry,
        underlying=trade.underlying, index_name=reference.index_name,
        daily_close=reference.close, source_url=reference.source_url,
        archive_sha256=reference.sha256, retrieved_at=reference.retrieved_at,
        warning=(f"This is the NSE daily {reference.index_name} close, not an automatic "
                 "settlement. Review exchange corrections and final settlement evidence."),
    )


@app.get(
    "/paper/trades/{trade_id}/expiry-settlement/official-preview",
    response_model=PaperOfficialExpirySettlementPreviewResponse,
    tags=["paper trading"],
)
async def paper_official_expiry_settlement_preview(
    trade_id: UUID, user: CurrentUser, session: SessionDep, response: Response,
) -> PaperOfficialExpirySettlementPreviewResponse:
    """Estimate paper expiry cash from the NSE archive without closing the trade."""
    response.headers["Cache-Control"] = "no-store"
    trade = session.scalar(select(PaperTrade).where(
        PaperTrade.id == trade_id, PaperTrade.user_id == user.id,
    ))
    if trade is None:
        raise HTTPException(404, "Paper trade not found")
    if trade.status != "OPEN":
        raise HTTPException(409, "Only an open expired paper trade can be checked")
    _require_supported_expired_index(trade)
    try:
        reference = (await fetch_nifty_close(trade.expiry) if trade.underlying == "NIFTY"
                     else await fetch_index_close(trade.expiry, trade.underlying))
    except OfficialCloseUnavailable as error:
        return PaperOfficialExpirySettlementPreviewResponse(
            status="unavailable", trade_id=trade.id, expiry=trade.expiry,
            reason=str(error),
        )
    body = PaperExpirySettlementRequest(
        settlement_date=trade.expiry,
        final_index_value=reference.close,
        source_reference=reference.source_url,
    )
    preview, _ = _expiry_settlement_preview(trade, body)
    preview.warning = (
        f"NSE daily {reference.index_name} close is a candidate, "
        "not a verified final settlement value. "
        "This cash-intrinsic paper estimate excludes charges and may change after corrections."
    )
    return PaperOfficialExpirySettlementPreviewResponse(
        status="available", trade_id=trade.id, expiry=trade.expiry,
        source_url=reference.source_url, archive_sha256=reference.sha256,
        retrieved_at=reference.retrieved_at, preview=preview,
        warning=(f"Read-only estimate from {reference.index_name}; "
                 "review exchange corrections and final settlement evidence."),
    )


@app.post(
    "/paper/trades/{trade_id}/expiry-settlement/preview",
    response_model=PaperExpirySettlementPreviewResponse,
    tags=["paper trading"],
)
def preview_paper_expiry_settlement(
    trade_id: UUID, body: PaperExpirySettlementRequest,
    user: CurrentUser, session: SessionDep,
) -> PaperExpirySettlementPreviewResponse:
    trade = session.scalar(select(PaperTrade).where(
        PaperTrade.id == trade_id, PaperTrade.user_id == user.id,
    ))
    if trade is None:
        raise HTTPException(404, "Paper trade not found")
    if trade.status != "OPEN":
        raise HTTPException(409, "Paper trade is already closed")
    preview, _ = _expiry_settlement_preview(trade, body)
    return preview


@app.post(
    "/paper/trades/{trade_id}/expiry-settlement",
    response_model=PaperTradeResponse,
    tags=["paper trading"],
)
async def reconcile_paper_expiry_settlement(
    trade_id: UUID, body: PaperExpirySettlementRequest,
    user: CurrentUser, session: SessionDep,
) -> PaperTradeResponse:
    if body.confirmed_final is not True:
        raise HTTPException(422, "Confirm the final index value after reviewing the preview")
    prior = session.scalar(select(PaperTrade).where(
        PaperTrade.id == trade_id, PaperTrade.user_id == user.id,
    ))
    if prior is None:
        raise HTTPException(404, "Paper trade not found")
    if prior.status == "OPEN":
        _require_supported_expired_index(prior)
        await _check_nse_settlement_reference(body, prior.underlying)
    account = lock_account(session, user.id)
    if account is None:
        raise HTTPException(404, "Paper account not found")
    trade = session.scalar(select(PaperTrade).where(
        PaperTrade.id == trade_id, PaperTrade.user_id == user.id,
    ).with_for_update().execution_options(populate_existing=True))
    if trade is None or trade.account_id != account.id:
        raise HTTPException(404, "Paper trade not found")
    if trade.status == "CLOSED":
        recorded = session.scalar(select(PaperOrder).where(
            PaperOrder.trade_id == trade.id, PaperOrder.action == "CLOSE",
        ))
        reference = recorded.fill_snapshot.get("settlement_reference") if recorded else None
        if trade.exit_reason == "EXPIRY_SETTLEMENT" and reference == {
            "settlement_date": body.settlement_date.isoformat(),
            "final_index_value": str(body.final_index_value.quantize(_CENT)),
            "source_reference": body.source_reference,
            **({"archive_sha256": body.archive_sha256} if body.archive_sha256 else {}),
        }:
            return _paper_trade_response(trade)
        raise HTTPException(409, "Paper trade was already closed with different settlement data")
    _require_consistent_paper_ledger(session, account)
    preview, calculation = _expiry_settlement_preview(trade, body)
    reference = {
        "settlement_date": body.settlement_date.isoformat(),
        "final_index_value": str(body.final_index_value.quantize(_CENT)),
        "source_reference": body.source_reference,
        **({"archive_sha256": body.archive_sha256} if body.archive_sha256 else {}),
    }
    trade.realized_pnl = calculation.realized_pnl
    trade.status = "CLOSED"
    trade.exit_reason = "EXPIRY_SETTLEMENT"
    trade.closed_at = datetime.now(timezone.utc)
    account.cash_balance = (
        account.cash_balance + calculation.settlement_cash_flow
    ).quantize(_CENT, rounding=ROUND_HALF_UP)
    account.updated_at = datetime.now(timezone.utc)
    session.add(PaperOrder(
        account_id=account.id,
        trade_id=trade.id,
        user_id=user.id,
        action="CLOSE",
        status="EXECUTED",
        net_cash_flow=calculation.settlement_cash_flow,
        created_at=datetime.now(timezone.utc),
        fill_snapshot={
            "paper_only": True,
            "quality_status": "OPERATOR_RECORDED",
            "modeled_costs": {"coverage": "UNAVAILABLE",
                              "reason": "Expiry settlement charges are not modeled"},
            "calculation_version": ("nifty-index-cash-intrinsic-v1" if trade.underlying == "NIFTY"
                                    else "nse-index-cash-intrinsic-v1"),
            "underlying": trade.underlying,
            "settlement_reference": reference,
            "fills": [vars(leg) | {
                "strike": str(leg.strike),
                "intrinsic_value": str(leg.intrinsic_value),
                "cash_flow": str(leg.cash_flow),
            } for leg in calculation.legs],
            "method": "Operator-recorded NIFTY index-option expiry cash intrinsic; no broker fill",
        },
    ))
    session.add(AuditEvent(
        actor_user_id=user.id,
        event_type="paper.trade.expiry_settlement_recorded",
        details={"trade_id": str(trade.id), **reference,
                 "realized_pnl": str(preview.realized_pnl)},
    ))
    session.commit()
    return _paper_trade_response(trade)


async def _check_nse_settlement_reference(
    body: PaperExpirySettlementRequest, underlying: str = "NIFTY",
) -> None:
    """Recheck an archive-backed candidate before any account lock or cash mutation."""
    official_url = archive_url(body.settlement_date)
    if body.source_reference == official_url:
        if body.archive_sha256 is None:
            raise HTTPException(422, "NSE archive fingerprint is required for this source")
        try:
            current_archive = (await fetch_nifty_close(body.settlement_date)
                               if underlying == "NIFTY"
                               else await fetch_index_close(body.settlement_date, underlying))
        except OfficialCloseUnavailable as error:
            raise HTTPException(
                409, "NSE archive could not be rechecked; refresh the preview"
            ) from error
        if (
            current_archive.sha256 != body.archive_sha256
            or current_archive.close != body.final_index_value
            or current_archive.source_url != body.source_reference
        ):
            raise HTTPException(409, "NSE archive changed; refresh and review the preview")
    elif body.archive_sha256 is not None:
        raise HTTPException(422, "NSE archive fingerprint does not match the source")


@app.post(
    "/paper/trades/{trade_id}/close",
    response_model=PaperTradeResponse,
    tags=["paper trading"],
)
async def close_paper_trade(
    trade_id: UUID, user: CurrentUser, session: SessionDep
) -> PaperTradeResponse:
    account = session.scalar(select(PaperAccount).where(PaperAccount.user_id == user.id))
    trade = session.scalar(
        select(PaperTrade).where(PaperTrade.id == trade_id, PaperTrade.user_id == user.id)
    )
    if account is None or trade is None:
        raise HTTPException(status_code=404, detail="Paper trade not found")
    if trade.status != "OPEN":
        raise HTTPException(status_code=409, detail="Paper trade is already closed")
    try:
        index = saved_option_index(
            trade.underlying, trade.entry_rule_snapshot,
            [str(leg["instrument_key"]) for leg in trade.legs["items"]],
        )
    except PaperSessionError as error:
        raise HTTPException(409, str(error)) from error
    if expiry_has_passed(trade.expiry):
        raise HTTPException(
            status_code=409,
            detail="Option expiry has passed; reconcile settlement instead of using a new quote",
        )
    underlying = trade.underlying
    expiry = trade.expiry
    saved_legs = [dict(leg) for leg in trade.legs["items"]]
    entry_cash_flow = trade.entry_cash_flow
    session.commit()
    provider = get_market_data_provider()
    trade_provider = (
        trade.entry_rule_snapshot.get("market_data_provider", "upstox")
        if isinstance(trade.entry_rule_snapshot, dict) else "upstox"
    )
    if provider.provider_id != trade_provider:
        raise HTTPException(
            status_code=409,
            detail="Paper trade belongs to another market provider; no close was recorded",
        )
    if underlying != "NIFTY" and not callable(getattr(provider, "get_index_option_session", None)):
        raise HTTPException(503, "Provider cannot verify this index's derivatives session")
    try:
        nfo_session = (
            await provider.get_nfo_session(exchange_today()) if underlying == "NIFTY"
            else await provider.get_index_option_session(underlying, exchange_today())
        )
        require_derivatives_session_open(nfo_session, index.derivatives_exchange)
        chain = await provider.get_option_chain(underlying, expiry)
        if chain.underlying != underlying or chain.expiry != expiry:
            raise HTTPException(409, "Option-chain identity differs from the saved paper position")
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        raise _market_data_http_error(error) from error
    except PaperSessionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    quality = assess_option_chain(chain)
    if quality.status == "FAIL":
        raise HTTPException(
            status_code=409, detail="Current option-chain quality is FAIL; trade remains open"
        )
    close_flow = Decimal(0)
    fills: list[dict[str, object]] = []
    for leg in saved_legs:
        exit_side = "SELL" if leg["side"] == "BUY" else "BUY"
        quantity_units = int(leg["quantity_units"])
        exit_price, market_contract = _require_quote(
            chain, str(leg["instrument_key"]), exit_side, quantity_units
        )
        amount = (exit_price * quantity_units).quantize(_CENT, rounding=ROUND_HALF_UP)
        leg_cash_flow = amount if exit_side == "SELL" else -amount
        close_flow += leg_cash_flow
        fills.append(
            {
                "instrument_key": leg["instrument_key"],
                "trading_symbol": leg["trading_symbol"],
                "option_type": leg["option_type"],
                "side": exit_side,
                "strike": leg["strike"],
                "quantity_units": quantity_units,
                "fill_price": str(exit_price),
                "cash_flow": str(leg_cash_flow),
                "quote_timestamp": market_contract.quote.timestamp.isoformat(),
                "timestamp_basis": market_contract.quote.timestamp_basis,
                "last_trade_time": (
                    market_contract.quote.last_trade_time.isoformat()
                    if market_contract.quote.last_trade_time else None
                ),
                "book_as_of": market_contract.quote.book_as_of.isoformat()
                if market_contract.quote.book_as_of else None,
                "book_timestamp_basis": market_contract.quote.book_timestamp_basis,
            }
        )
    close_flow = close_flow.quantize(_CENT, rounding=ROUND_HALF_UP)
    account = lock_account(session, user.id)
    trade = session.scalar(
        select(PaperTrade)
        .where(PaperTrade.id == trade_id, PaperTrade.user_id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if account is None or trade is None:
        raise HTTPException(status_code=404, detail="Paper trade not found")
    if trade.status != "OPEN":
        raise HTTPException(status_code=409, detail="Paper trade was already closed")
    _require_consistent_paper_ledger(session, account)
    if (trade.legs["items"] != saved_legs or trade.entry_cash_flow != entry_cash_flow
            or trade.underlying != underlying):
        raise HTTPException(
            status_code=409, detail="Paper trade changed while closing; refresh and retry"
        )
    if expiry_has_passed(trade.expiry):
        raise HTTPException(
            status_code=409,
            detail="Option expiry passed before the paper close could be recorded",
        )
    try:
        require_derivatives_session_open(nfo_session, index.derivatives_exchange)
    except PaperSessionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    for leg in saved_legs:
        _require_quote(
            chain,
            str(leg["instrument_key"]),
            "SELL" if leg["side"] == "BUY" else "BUY",
            int(leg["quantity_units"]),
        )
    trade.realized_pnl = (trade.entry_cash_flow + close_flow).quantize(
        _CENT, rounding=ROUND_HALF_UP
    )
    trade.status = "CLOSED"
    trade.exit_reason = "MANUAL"
    trade.closed_at = datetime.now(timezone.utc)
    account.cash_balance = (account.cash_balance + close_flow).quantize(_CENT)
    account.updated_at = datetime.now(timezone.utc)
    session.add(
        PaperOrder(
            account_id=account.id,
            trade_id=trade.id,
            user_id=user.id,
            action="CLOSE",
            status="EXECUTED",
            net_cash_flow=close_flow,
            created_at=datetime.now(timezone.utc),
            fill_snapshot={
                "paper_only": True,
                "market_as_of": chain.as_of.isoformat(),
                "quality_status": quality.status,
                "modeled_costs": cost_snapshot(fills),
                "fills": fills,
                "method": (
                    "Immediate simulated fills: closing sells at bid and buys at ask; "
                    "no queue or slippage model."
                ),
            },
        )
    )
    session.add(
        AuditEvent(
            actor_user_id=user.id,
            event_type="paper.trade.closed",
            details={"trade_id": str(trade.id), "realized_pnl": str(trade.realized_pnl)},
        )
    )
    _add_user_notification(
        session,
        user.id,
        "paper.trade.closed",
        "Paper position closed manually",
        f"{trade.strategy_name} · realized P&L ₹{trade.realized_pnl}",
        {"trade_id": str(trade.id), "realized_pnl": str(trade.realized_pnl)},
    )
    session.commit()
    session.refresh(trade)
    response = _paper_trade_response(trade)
    response.unrealized_pnl = None
    if quality.status == "WARN":
        response.execution_warnings.append(
            "Closing fill used an option-chain snapshot with WARN quality."
        )
    return response


@app.get("/paper/orders", response_model=list[PaperOrderResponse], tags=["paper trading"])
def paper_orders(user: CurrentUser, session: SessionDep) -> list[PaperOrderResponse]:
    account = _paper_account(session, user, create=False)
    if account is None:
        return []
    orders = session.scalars(
        select(PaperOrder)
        .where(PaperOrder.account_id == account.id)
        .order_by(desc(PaperOrder.created_at))
        .limit(200)
    ).all()
    result: list[PaperOrderResponse] = []
    for order in orders:
        created_at = order.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        result.append(
            PaperOrderResponse(
                id=order.id,
                trade_id=order.trade_id,
                action=order.action,
                status=order.status,
                net_cash_flow=order.net_cash_flow,
                fill_snapshot=order.fill_snapshot,
                created_at=created_at,
            )
        )
    return result


@app.get("/health", tags=["operations"])
async def health() -> dict[str, str | bool]:
    """Liveness endpoint; doesn't claim database or market provider readiness."""
    settings = get_settings()
    return {
        "status": "ok",
        "environment": settings.environment,
        "live_trading_enabled": settings.effective_live_trading_enabled,
    }


@app.get("/ready", tags=["operations"])
def readiness(session: SessionDep, response: Response) -> dict[str, str]:
    """Report whether the API can use its database and authentication limiter."""
    response.headers["Cache-Control"] = "no-store"
    try:
        session.execute(text("SELECT 1")).scalar_one()
        get_login_throttle().check_health()
        get_registration_throttle().check_health()
    except (SQLAlchemyError, LoginThrottleUnavailable):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API dependencies unavailable",
            headers={"Cache-Control": "no-store"},
        ) from None
    return {"status": "ready"}


@app.get("/ready/paper-worker", tags=["operations"])
def paper_worker_readiness(session: SessionDep, response: Response) -> dict[str, str]:
    """Check the independent paper exit loop without exposing account details."""
    response.headers["Cache-Control"] = "no-store"
    try:
        lease = session.get(WorkerLease, WORKER_NAME)
    except SQLAlchemyError:
        lease = None
    now = datetime.now(timezone.utc)
    heartbeat = lease.heartbeat_at if lease is not None else None
    if heartbeat is not None and heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
    settings = get_settings()
    max_age = settings.paper_worker_interval_seconds * 3 + 10
    healthy_cycle = (
        lease is not None
        and heartbeat is not None
        and lease.status in {"HEALTHY", "DEGRADED"}
        and (now - heartbeat).total_seconds() <= max_age
    )
    lease_until = lease.lease_until if lease is not None else None
    if lease_until is not None and lease_until.tzinfo is None:
        lease_until = lease_until.replace(tzinfo=timezone.utc)
    active_cycle = (
        lease is not None
        and heartbeat is not None
        and lease_until is not None
        and lease.status == "RUNNING"
        and lease_until > now
        and (now - heartbeat).total_seconds() <= settings.paper_worker_lease_seconds
    )
    if not (healthy_cycle or active_cycle):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Paper exit worker unavailable",
            headers={"Cache-Control": "no-store"},
        )
    return {"status": "ready"}
