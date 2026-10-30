"""Simulated NIFTY futures entries and exits against the shared F&O paper account.

Provider I/O happens before the account lock; every cash mutation re-checks the
ledger, the open-trade caps and (for the worker) lease ownership under the lock.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.api.paper_schemas import PaperExpirySettlementRequest
from apps.api.services.strategy_eligibility import eligibility_refusal
from apps.api.settings import Settings
from packages.database.futures_models import PaperFuturesOrder, PaperFuturesTrade
from packages.database.models import (
    AuditEvent,
    PaperAccount,
    PaperDeployment,
    PaperTrade,
    UserNotification,
)
from packages.market_data.contracts import (
    ExchangeSession,
    MarketDataProvider,
    MarketDataProviderError,
    MarketDataUnavailable,
    Quote,
)
from packages.paper_trading.daily_risk import daily_realized_pnl, daily_risk_budget
from packages.paper_trading.exits import ExitDecision, evaluate_exit
from packages.paper_trading.futures import (
    GAP_RISK_NOTE,
    FuturesSide,
    charge_snapshot,
    entry_order_side,
    exit_order_side,
    mark_position,
    money,
    quote_evidence,
    require_futures_quote,
)
from packages.paper_trading.futures_strategies import (
    FuturesExitPolicy,
    in_rollover_window,
    parse_futures_exit_policy,
)
from packages.paper_trading.margin import MARGIN_DISCLAIMER, MARGIN_MODEL_ID, futures_margin
from packages.paper_trading.quotes import PaperQuoteError
from packages.paper_trading.session import (
    PaperSessionError,
    exchange_today,
    expiry_has_passed,
    expiry_square_off_due,
    require_derivatives_session_open,
)
from packages.paper_trading.transactions import (
    PaperLedgerMismatchError,
    assert_consistent_ledger,
    lock_account,
    open_futures_trades,
    option_cash_reserve,
)
from packages.paper_trading.worker_lease import LeaseToken, assert_owned
from packages.recorder.freshness import feed_freshness
from packages.recorder.universe import nifty_futures

EXCHANGE = "NFO"
UNDERLYING = "NIFTY"
MAX_WAITING_PAPER_STRATEGIES = 26  # Shared with armed option templates.


class FuturesError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class FuturesContractResponse(BaseModel):
    instrument_key: str
    trading_symbol: str
    expiry: date
    lot_size: int
    tick_size: Decimal | None


class FuturesQuoteResponse(BaseModel):
    contract: FuturesContractResponse
    quote: dict[str, object]
    margin_per_lot: Decimal | None
    margin_breakdown: dict[str, object] | None
    margin_model: str = MARGIN_MODEL_ID
    margin_disclaimer: str = MARGIN_DISCLAIMER


class FuturesTradeCreateRequest(BaseModel):
    instrument_key: str = Field(min_length=3, max_length=160)
    side: FuturesSide
    lots: int = Field(ge=1, le=50)
    stop_loss_amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    target_profit_amount: Decimal | None = Field(
        default=None, gt=0, max_digits=14, decimal_places=2
    )
    trailing_stop_amount: Decimal | None = Field(
        default=None, gt=0, max_digits=14, decimal_places=2
    )
    acknowledged_unbounded_risk: Literal[True]

    @model_validator(mode="after")
    def _trailing_needs_room(self) -> "FuturesTradeCreateRequest":
        if (self.trailing_stop_amount is not None
                and self.trailing_stop_amount > self.stop_loss_amount * 10):
            raise ValueError("Trailing stop is implausibly wide for this stop-loss")
        return self


def _utc(value: datetime) -> datetime:
    """SQLite returns naive UTC; clients must not read it as local time."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class FuturesTradeResponse(BaseModel):
    id: UUID
    strategy_key: str | None = None
    strategy_name: str | None = None
    intraday: bool = False
    instrument_key: str
    trading_symbol: str
    underlying: str
    expiry: date
    side: str
    lots: int
    lot_size: int
    quantity_units: int
    entry_price: Decimal
    margin_blocked: Decimal
    max_loss: Decimal
    stop_loss_amount: Decimal
    target_profit_amount: Decimal | None
    trailing_stop_amount: Decimal | None
    peak_unrealized_pnl: Decimal
    last_mark_price: Decimal | None
    last_marked_at: datetime | None
    unrealized_pnl: Decimal | None
    exit_price: Decimal | None
    realized_pnl: Decimal | None
    exit_reason: str | None
    status: str
    opened_at: datetime
    closed_at: datetime | None
    execution_warnings: list[str] = Field(default_factory=list)

    @classmethod
    def from_model(cls, trade: PaperFuturesTrade) -> "FuturesTradeResponse":
        return cls(
            id=trade.id, strategy_key=trade.strategy_key,
            strategy_name=trade.entry_snapshot.get("strategy_name"),
            intraday=(trade.entry_snapshot.get("exit_policy") or {}).get("intraday") is True,
            instrument_key=trade.instrument_key,
            trading_symbol=trade.trading_symbol, underlying=trade.underlying,
            expiry=trade.expiry, side=trade.side, lots=trade.lots, lot_size=trade.lot_size,
            quantity_units=trade.lots * trade.lot_size, entry_price=trade.entry_price,
            margin_blocked=trade.margin_blocked, max_loss=trade.max_loss,
            stop_loss_amount=trade.stop_loss_amount,
            target_profit_amount=trade.target_profit_amount,
            trailing_stop_amount=trade.trailing_stop_amount,
            peak_unrealized_pnl=trade.peak_unrealized_pnl,
            last_mark_price=trade.last_mark_price,
            last_marked_at=_utc(trade.last_marked_at) if trade.last_marked_at else None,
            unrealized_pnl=trade.unrealized_pnl, exit_price=trade.exit_price,
            realized_pnl=trade.realized_pnl, exit_reason=trade.exit_reason,
            status=trade.status, opened_at=_utc(trade.opened_at),
            closed_at=_utc(trade.closed_at) if trade.closed_at else None,
        )


@dataclass(frozen=True)
class ArmedEntry:
    """Worker-only context binding one fill to its armed deployment and lease."""

    deployment_id: UUID
    strategy_key: str
    strategy_name: str
    lease: LeaseToken
    exit_policy: FuturesExitPolicy
    entry_rules: dict[str, object]


def _notify(session: Session, user_id: UUID, event_type: str, title: str, message: str,
            details: dict[str, object]) -> None:
    session.add(UserNotification(
        user_id=user_id, event_type=event_type, title=title, message=message, details=details,
    ))


def _require_futures_provider(provider: MarketDataProvider) -> None:
    if provider.provider_id == "unconfigured" or not callable(
        getattr(provider, "search_instruments", None)
    ):
        raise FuturesError(503, "Futures paper trading requires a configured market provider")


async def list_contracts(provider: MarketDataProvider) -> list[FuturesContractResponse]:
    """Current and later NIFTY futures, nearest expiry first."""
    _require_futures_provider(provider)
    try:
        rows = await provider.search_instruments("NIFTY FUT", exchange="NSE", segment="FO")
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        raise FuturesError(503, str(error)) from error
    return [
        FuturesContractResponse(
            instrument_key=spec.instrument_key, trading_symbol=spec.trading_symbol or "",
            expiry=spec.expiry, lot_size=spec.lot_size, tick_size=spec.tick_size,
        )
        for spec in nifty_futures(rows, exchange_today())
        if spec.expiry is not None and spec.lot_size is not None
    ]


async def _contract(provider: MarketDataProvider, instrument_key: str) -> FuturesContractResponse:
    contract = next(
        (row for row in await list_contracts(provider) if row.instrument_key == instrument_key),
        None,
    )
    if contract is None:
        raise FuturesError(422, "Selected NIFTY futures contract is not listed or has expired")
    return contract


async def _quote(provider: MarketDataProvider, instrument_key: str) -> Quote:
    try:
        quote = await provider.get_quote(instrument_key)
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        raise FuturesError(503, str(error)) from error
    if quote.instrument_token != instrument_key:
        raise FuturesError(502, "Futures quote identity differs from the selected contract")
    return quote


async def _open_session(provider: MarketDataProvider) -> ExchangeSession:
    try:
        nfo = await provider.get_nfo_session(exchange_today())
        require_derivatives_session_open(nfo, EXCHANGE)
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        raise FuturesError(503, str(error)) from error
    except PaperSessionError as error:
        raise FuturesError(409, str(error)) from error
    if nfo is None:
        raise FuturesError(409, "No verified NFO session is available")
    return nfo


async def quote_contract(
    provider: MarketDataProvider, instrument_key: str, settings: Settings
) -> FuturesQuoteResponse:
    contract = await _contract(provider, instrument_key)
    quote = await _quote(provider, instrument_key)
    reference = quote.ask or quote.last_price
    margin = (
        futures_margin(reference, contract.lot_size, settings.paper_margin_parameters)
        if reference > 0 else None
    )
    return FuturesQuoteResponse(
        contract=contract, quote=quote_evidence(quote),
        margin_per_lot=margin.total if margin else None,
        margin_breakdown=margin.snapshot() if margin else None,
    )


def ensure_account(session: Session, user_id: UUID, settings: Settings) -> None:
    """Create the shared F&O paper account on first use, as the options flow does."""
    if session.scalar(select(PaperAccount.id).where(PaperAccount.user_id == user_id)):
        return
    capital = Decimal(settings.initial_paper_capital)
    session.add(PaperAccount(user_id=user_id, initial_cash=capital, cash_balance=capital))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()


def _request_hash(body: FuturesTradeCreateRequest) -> str:
    return hashlib.sha256(json.dumps(
        body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


def _replay(session: Session, user_id: UUID, key: str, request_hash: str
            ) -> FuturesTradeResponse | None:
    existing = session.scalar(select(PaperFuturesTrade).where(
        PaperFuturesTrade.user_id == user_id, PaperFuturesTrade.idempotency_key == key,
    ))
    if existing is None:
        return None
    if existing.request_hash != request_hash:
        raise FuturesError(409, "Idempotency key was already used for a different futures order")
    return FuturesTradeResponse.from_model(existing)


async def open_trade(
    body: FuturesTradeCreateRequest,
    user_id: UUID,
    session: Session,
    idempotency_key: str,
    provider: MarketDataProvider,
    settings: Settings,
    armed: ArmedEntry | None = None,
) -> FuturesTradeResponse:
    request_hash = _request_hash(body)
    replay = _replay(session, user_id, idempotency_key, request_hash)
    if replay is not None:
        return replay
    if not settings.paper_trading_enabled:
        raise FuturesError(503, "Paper trading is disabled by configuration")
    ensure_account(session, user_id, settings)
    session.commit()  # Never hold a transaction across provider I/O.
    contract = await _contract(provider, body.instrument_key)
    if expiry_has_passed(contract.expiry):
        raise FuturesError(409, "Selected futures contract has expired")
    nfo = await _open_session(provider)
    now = datetime.now(timezone.utc)
    if expiry_square_off_due(
        contract.expiry, nfo, now=now,
        minutes_before_close=settings.paper_expiry_square_off_minutes_before_close,
        expected_exchange=EXCHANGE,
    ):
        raise FuturesError(409, "Expiry-day futures entry window is closed before square-off")
    if armed is not None and armed.exit_policy.intraday and expiry_square_off_due(
        exchange_today(now), nfo, now=now,
        minutes_before_close=armed.exit_policy.square_off_minutes_before_close,
        expected_exchange=EXCHANGE,
    ):
        raise FuturesError(409, "Intraday futures entries stop before the daily square-off")
    quote = await _quote(provider, body.instrument_key)
    units = body.lots * contract.lot_size
    order_side = entry_order_side(body.side)
    try:
        price = require_futures_quote(
            quote, order_side, units,
            max_age_seconds=settings.paper_quote_max_age_seconds,
            allow_unverified=settings.paper_allow_unverified_quotes,
        )
    except PaperQuoteError as error:
        raise FuturesError(409, str(error)) from error
    margin_estimate = futures_margin(price, units, settings.paper_margin_parameters)
    margin = margin_estimate.total
    max_loss = money(body.stop_loss_amount)

    account = lock_account(session, user_id)
    if account is None:
        session.rollback()
        raise FuturesError(409, "Paper account is not initialized; open the paper account first")
    try:
        replay = _replay(session, user_id, idempotency_key, request_hash)
        if replay is not None:
            session.rollback()
            return replay
        try:
            assert_consistent_ledger(session, account)
        except PaperLedgerMismatchError as error:
            raise FuturesError(409, str(error)) from error
        if account.kill_switch_enabled:
            raise FuturesError(423, "Paper kill switch is active; new trades are paused")
        deployment = None
        if armed is not None:
            deployment = _require_armed_for_fill(
                session, user_id, account, armed, body.lots, settings,
            )
        open_options = list(session.scalars(select(PaperTrade).where(
            PaperTrade.account_id == account.id, PaperTrade.status == "OPEN",
        )))
        open_futures = open_futures_trades(session, account.id)
        if len(open_options) + len(open_futures) >= settings.paper_max_open_trades:
            raise FuturesError(409, "Maximum number of simultaneous paper trades reached")
        per_trade_limit = money(
            account.initial_cash * Decimal(settings.paper_max_trade_risk_pct) / Decimal(100)
        )
        portfolio_limit = money(
            account.initial_cash * Decimal(settings.paper_max_portfolio_risk_pct) / Decimal(100)
        )
        current_risk = sum(
            (trade.max_loss for trade in open_options), Decimal(0)
        ) + sum((trade.max_loss for trade in open_futures), Decimal(0))
        if max_loss > per_trade_limit:
            raise FuturesError(
                409, f"Stop-loss risk exceeds the per-trade risk cap of ₹{per_trade_limit}"
            )
        if current_risk + max_loss > portfolio_limit:
            raise FuturesError(
                409, f"Open-trade risk would exceed the portfolio cap of ₹{portfolio_limit}"
            )
        budget = daily_risk_budget(
            initial_cash=account.initial_cash,
            limit_pct=settings.paper_daily_loss_limit_pct,
            realized_pnl=daily_realized_pnl(session, account.id, exchange_today()),
            open_max_loss=current_risk,
        )
        if max_loss > budget.remaining_for_new_risk:
            raise FuturesError(
                409, "Daily paper loss budget would be exceeded; "
                f"₹{budget.remaining_for_new_risk} remains for new risk",
            )
        projected_cash = money(account.cash_balance - margin)
        if projected_cash < 0:
            raise FuturesError(409, f"Paper cash cannot cover the ₹{margin} futures margin")
        # Losses come out of the blocked margin first, then free cash. Open option trades
        # hold the larger of their maximum loss and their recorded margin estimate.
        held = option_cash_reserve(session, open_options) + sum(
            (trade.max_loss for trade in open_futures), Decimal(0)
        )
        if held + max_loss > projected_cash + margin:
            raise FuturesError(
                409, "Available paper cash cannot cover maximum loss across open positions"
            )
        entry_snapshot = {
            "version": "nifty-futures-paper-v1",
            "provider": provider.provider_id,
            "exchange": EXCHANGE,
            "session": {"start": nfo.start.isoformat(), "end": nfo.end.isoformat()},
            "contract": contract.model_dump(mode="json"),
            "quote": quote_evidence(quote),
            "margin": {**margin_estimate.snapshot(), "amount": str(margin)},
            "risk": {"basis": "STOP_LOSS_AMOUNT", "note": GAP_RISK_NOTE},
            "exit_policy": {
                "square_off_minutes_before_close":
                    settings.paper_expiry_square_off_minutes_before_close,
            },
        }
        if armed is not None:
            entry_snapshot.update({
                "strategy_key": armed.strategy_key,
                "strategy_name": armed.strategy_name,
                "exit_policy": armed.exit_policy.as_dict(),
                "entry_rules": armed.entry_rules,
            })
        trade = PaperFuturesTrade(
            account_id=account.id, user_id=user_id, idempotency_key=idempotency_key,
            request_hash=request_hash, provider=provider.provider_id,
            strategy_key=armed.strategy_key if armed else None,
            deployment_id=armed.deployment_id if armed else None,
            instrument_key=contract.instrument_key, trading_symbol=contract.trading_symbol,
            underlying=UNDERLYING, expiry=contract.expiry, side=body.side, lots=body.lots,
            lot_size=contract.lot_size, entry_price=price, margin_blocked=margin,
            entry_cash_flow=-margin, max_loss=max_loss, stop_loss_amount=max_loss,
            target_profit_amount=body.target_profit_amount,
            trailing_stop_amount=body.trailing_stop_amount, peak_unrealized_pnl=Decimal(0),
            status="OPEN", entry_snapshot=entry_snapshot, opened_at=now,
        )
        account.cash_balance = projected_cash
        account.updated_at = now
        session.add(trade)
        session.flush()
        session.add(PaperFuturesOrder(
            account_id=account.id, trade_id=trade.id, user_id=user_id, action="OPEN",
            status="EXECUTED", net_cash_flow=-margin, created_at=now,
            fill_snapshot={
                "paper_only": True,
                "side": order_side,
                "fill_price": str(price),
                "quantity_units": units,
                "quote": quote_evidence(quote),
                "feed": feed_freshness(session, body.instrument_key, now),
                "modeled_charges": charge_snapshot(order_side, price, units, exchange_today()),
                "method": "Immediate simulated fill at the touch: buys at ask, sells at bid.",
            },
        ))
        session.add(AuditEvent(
            actor_user_id=user_id, correlation_id=str(trade.id),
            event_type="paper.futures.opened",
            details={"trade_id": str(trade.id), "side": body.side, "lots": body.lots,
                     "entry_price": str(price), "margin": str(margin)},
        ))
        _notify(session, user_id, "paper.futures.opened",
                f"Paper futures {body.side.lower()} opened: {contract.trading_symbol}",
                f"{body.lots} lot(s) at ₹{price} · margin blocked ₹{margin}",
                {"trade_id": str(trade.id)})
        if deployment is not None:
            deployment.status = "FILLED"
            deployment.detail = "Entry signal passed; paper futures position opened"
            deployment.updated_at = now
            session.add(AuditEvent(
                actor_user_id=user_id, correlation_id=str(deployment.id),
                event_type="paper.deployment.filled",
                details={"deployment_id": str(deployment.id), "trade_id": str(trade.id)},
            ))
        session.commit()
    except FuturesError:
        session.rollback()
        raise
    except IntegrityError:
        # The account lock already serializes entries; this covers a racing
        # duplicate key that still reaches the unique constraint.
        session.rollback()
        replay = _replay(session, user_id, idempotency_key, request_hash)
        if replay is None:
            raise
        return replay
    session.refresh(trade)
    response = FuturesTradeResponse.from_model(trade)
    if quote.timestamp_basis != "exchange" or quote.book_timestamp_basis != "exchange":
        response.execution_warnings.append(
            "Futures quote timestamps are not exchange-verified; this fill relies on the "
            "server's unverified-quote override."
        )
    return response


def _require_armed_for_fill(
    session: Session, user_id: UUID, account: PaperAccount, armed: ArmedEntry, lots: int,
    settings: Settings,
) -> PaperDeployment:
    """Fence the fill to the lease holder, a still-armed row and an eligible strategy."""
    if not assert_owned(session, armed.lease):
        raise FuturesError(409, "Paper worker lease expired; no futures fill was recorded")
    deployment = session.scalar(select(PaperDeployment).where(
        PaperDeployment.id == armed.deployment_id,
        PaperDeployment.user_id == user_id,
        PaperDeployment.instrument_kind == "FUTURES",
    ).with_for_update().execution_options(populate_existing=True))
    if deployment is None or deployment.status != "WAITING_FOR_ENTRY":
        raise FuturesError(409, "Paper deployment is no longer armed")
    refusal = eligibility_refusal(
        session, user_id, armed.strategy_key, account.initial_cash, lots, settings,
    )
    if refusal is not None:
        raise FuturesError(423, refusal)
    return deployment


def list_trades(session: Session, user_id: UUID) -> list[FuturesTradeResponse]:
    rows = session.scalars(
        select(PaperFuturesTrade).where(PaperFuturesTrade.user_id == user_id)
        .order_by(PaperFuturesTrade.opened_at.desc(), PaperFuturesTrade.id)
        .limit(200)
    )
    return [FuturesTradeResponse.from_model(row) for row in rows]


def _close_locked(
    session: Session, trade: PaperFuturesTrade, price: Decimal, quote: Quote | None, reason: str,
    settlement_reference: dict[str, str] | None = None,
) -> Decimal:
    """Stage the close of a locked open trade; the caller commits."""
    units = trade.lots * trade.lot_size
    side = cast(FuturesSide, trade.side)
    mark = mark_position(
        side=side,
        entry_price=trade.entry_price, exit_price=price, quantity_units=units,
        margin_blocked=trade.margin_blocked,
    )
    now = datetime.now(timezone.utc)
    trade.status = "CLOSED"
    trade.exit_price = price
    trade.realized_pnl = mark.pnl
    trade.unrealized_pnl = None
    trade.last_mark_price = price
    trade.last_marked_at = now
    trade.exit_reason = reason
    trade.closed_at = now
    order_side = exit_order_side(side)
    session.add(PaperFuturesOrder(
        account_id=trade.account_id, trade_id=trade.id, user_id=trade.user_id,
        action="CLOSE", status="EXECUTED", net_cash_flow=mark.close_cash_flow, created_at=now,
        fill_snapshot={
            "paper_only": True,
            "side": order_side,
            "fill_price": str(price),
            "quantity_units": units,
            "exit_reason": reason,
            "margin_released": str(trade.margin_blocked),
            "price_pnl": str(mark.pnl),
            **(
                {
                    "quote": quote_evidence(quote),
                    "feed": feed_freshness(session, trade.instrument_key, now),
                    "modeled_charges": charge_snapshot(
                        order_side, price, units, exchange_today()
                    ),
                    "method": "Simulated closing fill at the touch: sells at bid, buys at ask.",
                } if quote is not None else {
                    "settlement_reference": settlement_reference,
                    "modeled_charges": {
                        "coverage": "UNAVAILABLE",
                        "reason": "Expiry settlement charges are not modeled",
                    },
                    "method": "Operator-recorded final settlement at the index close; no fill.",
                }
            ),
        },
    ))
    session.add(AuditEvent(
        actor_user_id=trade.user_id, correlation_id=str(trade.id),
        event_type="paper.futures.closed",
        details={"trade_id": str(trade.id), "exit_reason": reason,
                 "exit_price": str(price), "realized_pnl": str(mark.pnl)},
    ))
    _notify(session, trade.user_id, "paper.futures.closed",
            f"Paper futures closed: {reason.replace('_', ' ').lower()}",
            f"{trade.trading_symbol} · realized P&L ₹{mark.pnl}",
            {"trade_id": str(trade.id), "exit_reason": reason, "realized_pnl": str(mark.pnl)})
    return mark.close_cash_flow


def _lock_open_trade(session: Session, user_id: UUID, trade_id: UUID) -> PaperFuturesTrade | None:
    return session.scalar(
        select(PaperFuturesTrade)
        .where(PaperFuturesTrade.id == trade_id, PaperFuturesTrade.user_id == user_id)
        .with_for_update().execution_options(populate_existing=True)
    )


async def close_trade(
    trade_id: UUID, user_id: UUID, session: Session, provider: MarketDataProvider,
    settings: Settings,
) -> FuturesTradeResponse:
    """Manual paper exit at the current touch; same quote rules as automated exits."""
    trade = session.scalar(select(PaperFuturesTrade).where(
        PaperFuturesTrade.id == trade_id, PaperFuturesTrade.user_id == user_id,
    ))
    if trade is None:
        raise FuturesError(404, "Futures paper trade not found")
    if trade.status != "OPEN":
        raise FuturesError(409, "Futures paper trade is already closed")
    if trade.provider != provider.provider_id:
        raise FuturesError(409, "Trade belongs to another market provider; no exit recorded")
    instrument_key, units = trade.instrument_key, trade.lots * trade.lot_size
    side = cast(FuturesSide, trade.side)
    session.commit()
    await _open_session(provider)
    quote = await _quote(provider, instrument_key)
    try:
        price = require_futures_quote(
            quote, exit_order_side(side), units,
            max_age_seconds=settings.paper_quote_max_age_seconds,
            allow_unverified=settings.paper_allow_unverified_quotes,
        )
    except PaperQuoteError as error:
        raise FuturesError(409, str(error)) from error
    account = lock_account(session, user_id)
    locked = _lock_open_trade(session, user_id, trade_id)
    if account is None or locked is None or locked.status != "OPEN":
        session.rollback()
        raise FuturesError(409, "Futures paper trade is already closed")
    try:
        assert_consistent_ledger(session, account)
    except PaperLedgerMismatchError as error:
        session.rollback()
        raise FuturesError(409, str(error)) from error
    close_flow = _close_locked(session, locked, price, quote, "MANUAL")
    account.cash_balance = money(account.cash_balance + close_flow)
    account.updated_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(locked)
    return FuturesTradeResponse.from_model(locked)


def _settlement_reference(body: PaperExpirySettlementRequest) -> dict[str, str]:
    return {
        "settlement_date": body.settlement_date.isoformat(),
        "final_index_value": str(money(body.final_index_value)),
        "source_reference": body.source_reference,
        **({"archive_sha256": body.archive_sha256} if body.archive_sha256 else {}),
    }


def settle_expired_trade(
    trade_id: UUID, body: PaperExpirySettlementRequest, user_id: UUID, session: Session,
) -> FuturesTradeResponse:
    """Close a position left open past expiry at a reviewed final index value.

    Only reachable when the expiry-day square-off could not run; nothing settles by itself.
    """
    if body.confirmed_final is not True:
        raise FuturesError(422, "Confirm the final index value after reviewing the amount")
    account = lock_account(session, user_id)
    trade = _lock_open_trade(session, user_id, trade_id)
    if account is None or trade is None:
        session.rollback()
        raise FuturesError(404, "Futures paper trade not found")
    reference = _settlement_reference(body)
    if trade.status == "CLOSED":
        order = session.scalar(select(PaperFuturesOrder).where(
            PaperFuturesOrder.trade_id == trade.id, PaperFuturesOrder.action == "CLOSE",
        ))
        recorded = order.fill_snapshot.get("settlement_reference") if order else None
        session.rollback()
        if trade.exit_reason == "EXPIRY_SETTLEMENT" and recorded == reference:
            return FuturesTradeResponse.from_model(trade)
        raise FuturesError(409, "Futures trade was already closed with different data")
    if not expiry_has_passed(trade.expiry):
        session.rollback()
        raise FuturesError(409, "Only a position past its expiry can be settled")
    if body.settlement_date != trade.expiry:
        session.rollback()
        raise FuturesError(422, "Settlement date must match the contract expiry")
    try:
        assert_consistent_ledger(session, account)
    except PaperLedgerMismatchError as error:
        session.rollback()
        raise FuturesError(409, str(error)) from error
    close_flow = _close_locked(
        session, trade, money(body.final_index_value), None, "EXPIRY_SETTLEMENT", reference,
    )
    account.cash_balance = money(account.cash_balance + close_flow)
    account.updated_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(trade)
    return FuturesTradeResponse.from_model(trade)


def _rearm_after_rollover(session: Session, trade: PaperFuturesTrade, now: datetime) -> bool:
    """Stage a fresh wait for the next contract; never reopen without rules and risk checks."""
    if trade.deployment_id is None:
        return False
    origin = session.get(PaperDeployment, trade.deployment_id)
    if origin is None:
        return False
    waiting = list(session.scalars(select(PaperDeployment).where(
        PaperDeployment.user_id == trade.user_id,
        PaperDeployment.status == "WAITING_FOR_ENTRY",
    )))
    if len(waiting) >= MAX_WAITING_PAPER_STRATEGIES or any(
        row.instrument_kind == "FUTURES"
        and row.request_data.get("strategy_key") == trade.strategy_key
        for row in waiting
    ):
        return False
    row = PaperDeployment(
        user_id=trade.user_id, command_key=f"roll_{trade.id.hex}",
        request_hash=origin.request_hash, request_data=dict(origin.request_data),
        instrument_kind="FUTURES", status="WAITING_FOR_ENTRY",
        detail="Rolled after closing the expiring contract; waiting for entry rules",
        trade_id=None, created_at=now, updated_at=now,
    )
    session.add(row)
    session.flush()
    session.add(AuditEvent(
        actor_user_id=trade.user_id, correlation_id=str(row.id),
        event_type="paper.deployment.armed",
        details={"deployment_id": str(row.id), "strategy_key": trade.strategy_key,
                 "rolled_from_trade_id": str(trade.id)},
    ))
    return True


def _saved_exit_policy(trade: PaperFuturesTrade) -> FuturesExitPolicy | None:
    """Armed trades carry their policy; a malformed one must not change exit timing."""
    if trade.deployment_id is None:
        return None
    try:
        return parse_futures_exit_policy(trade.entry_snapshot.get("exit_policy"))
    except ValueError:
        return None


async def monitor_futures_exits(
    user_id: UUID,
    session: Session,
    provider: MarketDataProvider,
    settings: Settings,
    lease: LeaseToken | None = None,
) -> tuple[int, list[UUID], list[str]]:
    """Mark open futures; close any at a stop, target, trail, square-off or rollover."""
    snapshots = list(session.scalars(select(PaperFuturesTrade).where(
        PaperFuturesTrade.user_id == user_id, PaperFuturesTrade.status == "OPEN",
    ).order_by(PaperFuturesTrade.opened_at)))
    targets = [
        (trade.id, trade.instrument_key, trade.provider, trade.expiry,
         cast(FuturesSide, trade.side), trade.lots * trade.lot_size)
        for trade in snapshots
    ]
    session.commit()
    if not targets:
        return 0, [], []
    warnings: list[str] = []
    try:
        nfo = await _open_session(provider)
    except FuturesError as error:
        return 0, [], [f"Futures exit checks skipped: {error.detail}"]
    evaluated = 0
    closed: list[UUID] = []
    for trade_id, instrument_key, trade_provider, expiry, side, units in targets:
        if trade_provider != provider.provider_id:
            warnings.append(f"Futures trade {trade_id} belongs to another market provider.")
            continue
        if expiry_has_passed(expiry):
            warnings.append(
                f"Futures trade {trade_id} is open after expiry; review its settlement on "
                "Trade. No synthetic exit was recorded."
            )
            continue
        try:
            quote = await _quote(provider, instrument_key)
            price = require_futures_quote(
                quote, exit_order_side(side), units,
                max_age_seconds=settings.paper_quote_max_age_seconds,
                allow_unverified=settings.paper_allow_unverified_quotes,
            )
        except (FuturesError, PaperQuoteError) as error:
            warnings.append(f"Futures exit check for {trade_id} skipped: {error}.")
            continue
        account = lock_account(session, user_id)
        if lease is not None and not assert_owned(session, lease):
            session.rollback()
            warnings.append("Worker ownership expired; futures exit mutation was fenced off.")
            break
        trade = _lock_open_trade(session, user_id, trade_id)
        if account is None or trade is None or trade.status != "OPEN":
            session.rollback()
            continue
        try:
            assert_consistent_ledger(session, account)
        except PaperLedgerMismatchError as error:
            session.rollback()
            warnings.append(str(error))
            continue
        evaluated += 1
        mark = mark_position(
            side=side, entry_price=trade.entry_price, exit_price=price,
            quantity_units=units, margin_blocked=trade.margin_blocked,
        )
        decision = evaluate_exit(
            pnl=mark.pnl, peak_pnl=trade.peak_unrealized_pnl or Decimal(0),
            stop_loss_amount=trade.stop_loss_amount,
            target_profit_amount=trade.target_profit_amount,
            trailing_stop_amount=trade.trailing_stop_amount,
        )
        now = datetime.now(timezone.utc)
        if decision.reason is None and expiry_square_off_due(
            expiry, nfo, now=now,
            minutes_before_close=settings.paper_expiry_square_off_minutes_before_close,
            expected_exchange=EXCHANGE,
        ):
            decision = ExitDecision(decision.peak_pnl, "EXPIRY_SQUARE_OFF")
        policy = _saved_exit_policy(trade)
        if policy is None and trade.deployment_id is not None:
            warnings.append(
                f"Futures trade {trade_id} has an unreadable exit policy; only stop, target, "
                "trail and expiry square-off apply."
            )
        if decision.reason is None and policy is not None:
            # A missed square-off (outage, worker down) must not carry into another day.
            if policy.intraday and (
                exchange_today(_utc(trade.opened_at)) < exchange_today(now)
                or expiry_square_off_due(
                    exchange_today(now), nfo, now=now,
                    minutes_before_close=policy.square_off_minutes_before_close,
                    expected_exchange=EXCHANGE,
                )
            ):
                decision = ExitDecision(decision.peak_pnl, "SESSION_SQUARE_OFF")
            elif not policy.intraday and in_rollover_window(
                expiry, exchange_today(now), policy.rollover_days_before_expiry
            ):
                decision = ExitDecision(decision.peak_pnl, "ROLLOVER")
        trade.peak_unrealized_pnl = decision.peak_pnl
        if decision.reason is None:
            trade.last_mark_price = price
            trade.last_marked_at = now
            trade.unrealized_pnl = mark.pnl
            session.commit()
            continue
        close_flow = _close_locked(session, trade, price, quote, decision.reason)
        account.cash_balance = money(account.cash_balance + close_flow)
        account.updated_at = now
        if decision.reason == "ROLLOVER":
            rearmed = _rearm_after_rollover(session, trade, now)
            _notify(session, user_id, "paper.futures.rollover",
                    "Paper futures rollover",
                    f"{trade.trading_symbol} closed before expiry. " + (
                        "The strategy is waiting again on the next contract; it re-enters only "
                        "if its rules and risk checks pass." if rearmed else
                        "The strategy was not re-armed; start it again to re-enter."
                    ),
                    {"trade_id": str(trade_id), "rearmed": rearmed})
        session.commit()
        closed.append(trade_id)
    return evaluated, closed, warnings
