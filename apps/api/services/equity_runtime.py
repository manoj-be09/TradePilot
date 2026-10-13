"""Durable equity simulation with reserved capital and short serialized mutations."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from apps.api.settings import get_settings
from packages.database.models import (
    AuditEvent,
    EquityAccount,
    EquityCashEvent,
    EquityDeployment,
    User,
)
from packages.market_data.contracts import HistoricalBar, MarketDataProvider, Quote
from packages.paper_trading.equity import (
    VERSION,
    ZONE,
    completed_bars,
    entry_threshold,
    exit_reason,
    money,
    open_position,
    proceeds,
    quote_problem,
    risk_distance,
    signal,
    trail,
)
from packages.paper_trading.equity_cash_journal import (
    EquityCashJournalMismatch,
    ensure_equity_cash_journal,
    reconcile_equity_cash_journal,
    record_equity_cash_event,
)
from packages.paper_trading.equity_ledger import reconcile_equity_ledger
from packages.paper_trading.worker_lease import LeaseToken, assert_owned
from packages.regime.strategy_fit import describe_stock_context

ACTIVE = ("WAITING_FOR_ENTRY", "OPEN", "EXIT_REQUESTED")

ENTRY_CHECK_CODES: dict[str, tuple[str, str]] = {
    "Deployment provider is not the active provider": ("PROVIDER_MISMATCH", "DATA"),
    "Market data unavailable; no fill made": ("MARKET_DATA_UNAVAILABLE", "DATA"),
    "Unsupported strategy version; manual review required": ("VERSION_UNSUPPORTED", "CONFIG"),
    "Waiting for an exchange-timestamped bid/ask book": ("BOOK_TIME_UNVERIFIED", "DATA"),
    "Quote instrument mismatch": ("QUOTE_INSTRUMENT_MISMATCH", "DATA"),
    "Indicative paper fill needs a provider response timestamp": (
        "RESPONSE_TIME_UNVERIFIED", "DATA"
    ),
    "Waiting for a verified exchange trade within 60 seconds": ("TRADE_TIME_STALE", "DATA"),
    "Indicative paper fill needs a provider response within 5 seconds": (
        "RESPONSE_TIME_STALE", "DATA"
    ),
    "Waiting for a valid two-sided quote": ("TWO_SIDED_QUOTE_MISSING", "DATA"),
    "Waiting for spread at or below 0.5%": ("SPREAD_TOO_WIDE", "DATA"),
    "Paper fill needs enough displayed depth at the selected price": (
        "DEPTH_INSUFFICIENT", "DATA"
    ),
    "Deployed; waiting for a new completed daily candle": ("NEW_CANDLE_PENDING", "SIGNAL"),
    "Waiting for completed candle history": ("HISTORY_PENDING", "DATA"),
    "Daily history is stale; entry blocked": ("HISTORY_STALE", "DATA"),
    "Deployed; waiting for strategy entry signal": ("SIGNAL_NOT_MET", "SIGNAL"),
    "Signal present; price moved over 2% from signal close": ("PRICE_DRIFT", "PRICE"),
    "Budget cannot fund one share within risk limit": ("RISK_BUDGET", "RISK"),
}


def held_quantity(state: dict[str, Any]) -> int:
    position = state.get("position")
    if not isinstance(position, dict):
        return 0
    try:
        return int(position.get("quantity", 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def record_waiting_check(
    session: Session, row: EquityDeployment, state: dict[str, Any], checked_at: datetime,
) -> None:
    """Keep the latest check, and audit each distinct reason once per NSE session."""
    reason = state["detail"]
    code, category = ENTRY_CHECK_CODES.get(reason, ("OTHER", "UNKNOWN"))
    session_date = checked_at.astimezone(ZONE).date().isoformat()
    previous = state.get("entry_check_events")
    if not isinstance(previous, dict) or previous.get("session_date") != session_date:
        previous = {"session_date": session_date, "reasons": []}
    reasons = previous.get("reasons")
    if not isinstance(reasons, list):
        reasons = []
    if code not in reasons and len(reasons) < 16:
        session.add(AuditEvent(
            actor_user_id=row.user_id,
            correlation_id=str(row.id),
            event_type="equity.deployment.entry_checked",
            details={
                "deployment_id": str(row.id),
                "strategy": row.strategy,
                "strategy_version": row.version,
                "provider": row.provider,
                "quote_mode": state.get("quote_mode", "VERIFIED"),
                "session_date": session_date,
                "code": code,
                "category": category,
                "reason": reason,
            },
        ))
        reasons.append(code)
    state["entry_check_events"] = {"session_date": session_date, "reasons": reasons}
    state["last_entry_check"] = {"at": checked_at.isoformat(), "detail": reason}


def lock_wallet(session: Session, user_id: UUID) -> EquityAccount:
    session.rollback()
    if session.get_bind().dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))
    # Lock a row that exists before creating the wallet, including on PostgreSQL.
    session.execute(select(User.id).where(User.id == user_id).with_for_update()).scalar_one()
    account = session.get(EquityAccount, user_id, populate_existing=True)
    created = account is None
    if account is None:
        account = EquityAccount(
            user_id=user_id,
            initial_cash=Decimal(get_settings().initial_equity_paper_capital),
            cash=Decimal(get_settings().initial_equity_paper_capital),
        )
        session.add(account)
        session.flush()
    ensure_equity_cash_journal(session, account, created=created)
    events = list(session.scalars(
        select(EquityCashEvent).where(EquityCashEvent.user_id == user_id)
        .order_by(EquityCashEvent.id)
    ))
    deployments = list(session.scalars(
        select(EquityDeployment).where(EquityDeployment.user_id == user_id)
    ))
    journal = reconcile_equity_cash_journal(
        user_id=user_id, initial_cash=account.initial_cash,
        recorded_cash=account.cash, events=events, deployments=deployments,
    )
    ledger = reconcile_equity_ledger(
        user_id=user_id, initial_cash=account.initial_cash,
        recorded_cash=account.cash, deployments=deployments, audit_events=[],
    )
    if journal.status in {"MISMATCH", "NOT_STARTED"} or ledger.status == "MISMATCH":
        raise EquityCashJournalMismatch("Equity paper wallet journal needs review")
    return account


def serialize(row: EquityDeployment) -> dict[str, Any]:
    result = {
        name: getattr(row, name)
        for name in (
            "id",
            "symbol",
            "strategy",
            "version",
            "provider",
            "instrument_key",
            "budget",
            "risk_budget",
            "status",
            "state",
            "created_at",
            "updated_at",
        )
    }
    result["created_at"] = utc(row.created_at)
    result["updated_at"] = utc(row.updated_at)
    return result


async def history(provider: MarketDataProvider, key: str, now: datetime) -> list[HistoricalBar]:
    bars = completed_bars(
        await provider.get_historical(key, now - timedelta(days=365), now, "1d"), now
    )
    if len(bars) < 51:
        raise ValueError("At least 51 completed daily candles are required")
    return bars


async def process_equities(
    factory: sessionmaker[Session], provider: MarketDataProvider, token: LeaseToken
) -> tuple[int, int, list[str]]:
    with factory() as session:
        ids = list(
            session.execute(
                select(EquityDeployment.id, EquityDeployment.user_id).where(
                    EquityDeployment.status.in_(ACTIVE)
                )
            )
        )
    if not ids:
        return 0, 0, []
    now = datetime.now(timezone.utc)
    try:
        market = await provider.get_equity_session(now.astimezone(ZONE).date())
        market_open = (
            market is not None and market.exchange == "NSE" and (market.start <= now < market.end)
        )
    except Exception:
        market_open = False
    evaluated = closed = 0
    warnings: list[str] = []
    cached_bars: dict[str, list[HistoricalBar]] = {}
    cached_quotes: dict[str, Quote] = {}
    for deployment_id, user_id in ids:
        with factory() as session:
            snapshot = session.get(EquityDeployment, deployment_id)
            if snapshot is None:
                continue
            key, source, status = snapshot.instrument_key, snapshot.provider, snapshot.status
            indicative = snapshot.state.get("quote_mode") == "INDICATIVE"
            size = held_quantity(snapshot.state) if status != "WAITING_FOR_ENTRY" else 1
            missing_reference = status == "WAITING_FOR_ENTRY" and not snapshot.state.get(
                "entry_threshold_price"
            )
        quote: Quote | None = None
        bars: list[HistoricalBar] = []
        issue = None
        if not market_open:
            issue = "Waiting for a verified open NSE session"
        elif source != provider.provider_id:
            issue = "Deployment provider is not the active provider"
        else:
            try:
                if key not in cached_quotes:
                    cached_quotes[key] = await provider.get_quote(key)
                quote = cached_quotes[key].model_copy(deep=True)
                issue = quote_problem(
                    quote, key, datetime.now(timezone.utc), indicative=indicative,
                    side="BUY" if status == "WAITING_FOR_ENTRY" else "SELL",
                    quantity=size,
                )
            except Exception:
                issue = "Market data unavailable; no fill made"
        # A daily reference remains useful outside market hours, including for
        # deployments saved before Apply started recording it.
        if status == "WAITING_FOR_ENTRY" and source == provider.provider_id and (
            market_open or missing_reference
        ):
            try:
                if key not in cached_bars:
                    cached_bars[key] = await history(provider, key, now)
                bars = cached_bars[key]
            except Exception:
                if market_open and issue is None:
                    issue = "Daily history unavailable; no fill made"
        with factory() as session:
            try:
                wallet = lock_wallet(session, user_id)
            except EquityCashJournalMismatch:
                warnings.append("Equity wallet journal mismatch; no paper fill made")
                continue
            if not assert_owned(session, token):
                session.rollback()
                return evaluated, closed, ["Equity worker lease expired"]
            row = session.get(EquityDeployment, deployment_id, populate_existing=True)
            if row is None or row.status not in ACTIVE:
                session.rollback()
                continue
            now = datetime.now(timezone.utc)
            state = dict(row.state)
            row.updated_at = now
            if quote is not None:
                state.update(
                    current_price=str(quote.last_price),
                    current_price_at=quote.timestamp.isoformat(),
                    current_price_basis=quote.timestamp_basis,
                )
            if row.status == "WAITING_FOR_ENTRY" and bars:
                threshold = entry_threshold(row.strategy, bars)
                if threshold is not None:
                    state["entry_threshold_price"] = str(money(threshold))
                day = bars[-1].timestamp.astimezone(ZONE).date()
                state["latest_daily_signal_date"] = day.isoformat()
                state["latest_daily_signal_met"] = signal(row.strategy, bars)
                state["latest_daily_signal_after_apply"] = (
                    day > datetime.fromisoformat(state["signal_after"]).date()
                )
            if row.version != VERSION:
                issue = "Unsupported strategy version; manual review required"
            if row.status == "WAITING_FOR_ENTRY" and now >= utc(row.created_at) + timedelta(days=7):
                row.status = "EXPIRED"
                wallet.cash += row.budget
                record_equity_cash_event(
                    session, wallet, row.id, kind="RELEASE", delta=row.budget,
                )
                state["detail"] = "No entry within seven days; reserved cash released"
                session.add(AuditEvent(
                    actor_user_id=user_id,
                    correlation_id=str(deployment_id),
                    event_type="equity.deployment.expired",
                    details={
                        "deployment_id": str(deployment_id),
                        "strategy": row.strategy,
                        "symbol": row.symbol,
                        "reserved_cash_released": str(row.budget),
                        "reason": state["detail"],
                    },
                ))
            elif issue:
                state["detail"] = issue
                if market_open:
                    warnings.append("An equity deployment is waiting for valid data")
            elif quote is not None:
                # Revalidate after I/O and lock acquisition, including market-session end.
                indicative = state.get("quote_mode") == "INDICATIVE"
                size = held_quantity(state) if row.status != "WAITING_FOR_ENTRY" else 1
                problem = quote_problem(
                    quote, key, now, indicative=indicative,
                    side="BUY" if row.status == "WAITING_FOR_ENTRY" else "SELL",
                    quantity=size,
                )
                if market is None or not market.start <= now < market.end:
                    problem = "Waiting for a verified open NSE session"
                if problem:
                    state["detail"] = problem
                elif row.status == "WAITING_FOR_ENTRY":
                    assert quote.ask is not None
                    if not bars:
                        state["detail"] = "Waiting for completed candle history"
                    else:
                        state["entry_reference_price"] = str(bars[-1].close)
                        state["planned_entry_price"] = str(quote.ask)
                        day = bars[-1].timestamp.astimezone(ZONE).date()
                        baseline = datetime.fromisoformat(state["signal_after"]).date()
                        if day <= baseline:
                            state["detail"] = "Deployed; waiting for a new completed daily candle"
                        elif (now.astimezone(ZONE).date() - day).days > 4:
                            state["detail"] = "Daily history is stale; entry blocked"
                        elif not signal(row.strategy, bars):
                            state["detail"] = "Deployed; waiting for strategy entry signal"
                        elif abs(quote.ask / bars[-1].close - 1) > Decimal("0.02"):
                            state["detail"] = (
                                "Signal present; price moved over 2% from signal close"
                            )
                        else:
                            position = open_position(
                                quote.ask, risk_distance(bars), row.budget, row.risk_budget
                            )
                            if position is None:
                                state["detail"] = "Budget cannot fund one share within risk limit"
                            else:
                                size_problem = quote_problem(
                                    quote, key, now, indicative=indicative,
                                    side="BUY", quantity=int(position["quantity"]),
                                )
                                if size_problem:
                                    state["detail"] = size_problem
                                    record_waiting_check(session, row, state, now)
                                    row.state = state
                                    session.commit()
                                    continue
                                assert quote.last_trade_time is not None
                                book_at = (
                                    quote.book_as_of.isoformat()
                                    if quote.book_timestamp_basis == "exchange" and quote.book_as_of
                                    else None
                                )
                                quote_basis = (
                                    quote.book_timestamp_basis
                                    if book_at is not None else quote.timestamp_basis
                                )
                                entry_context = describe_stock_context(bars)
                                entry_context["as_of"] = entry_context["as_of"].isoformat()
                                row.status = "OPEN"
                                state.update(
                                    position=position,
                                    entry_at=now.isoformat(),
                                    entry_quote_at=quote.last_trade_time.isoformat(),
                                    entry_quote_basis=quote_basis,
                                    entry_book_as_of=book_at,
                                    entry_response_at=quote.timestamp.isoformat(),
                                    entry_ask=str(quote.ask),
                                    entry_signal_date=day.isoformat(),
                                    entry_market_context=entry_context,
                                    initial_stop=position["stop"],
                                    detail=(
                                        "Indicative paper position open; stop and target monitored"
                                        if indicative else "Position open; stops monitored"
                                    ),
                                )
                                session.add(AuditEvent(
                                    actor_user_id=user_id,
                                    correlation_id=str(deployment_id),
                                    event_type="equity.deployment.entered",
                                    details={
                                        "deployment_id": str(deployment_id),
                                        "strategy": row.strategy,
                                        "strategy_version": row.version,
                                        "provider": row.provider,
                                        "instrument_key": row.instrument_key,
                                        "signal_date": day.isoformat(),
                                        "quote_at": quote.last_trade_time.isoformat(),
                                        "quote_basis": quote_basis,
                                        "book_as_of": book_at,
                                        "quote_mode": state.get("quote_mode", "VERIFIED"),
                                        "response_at": quote.timestamp.isoformat(),
                                        "ask": str(quote.ask),
                                        "entry_price": position["entry_price"],
                                        "exit_policy_version": position["exit_policy_version"],
                                        "quantity": position["quantity"],
                                        "initial_stop": position["stop"],
                                        "target": position["target"],
                                        "planned_risk": str(row.risk_budget),
                                    },
                                ))
                else:
                    assert (
                        quote.bid is not None
                        and quote.last_trade_time is not None
                    )
                    book_at = (
                        quote.book_as_of.isoformat()
                        if quote.book_timestamp_basis == "exchange" and quote.book_as_of
                        else None
                    )
                    quote_basis = (
                        quote.book_timestamp_basis if book_at is not None
                        else quote.timestamp_basis
                    )
                    position = dict(state["position"])
                    reason = (
                        "MANUAL_EXIT"
                        if row.status == "EXIT_REQUESTED"
                        else exit_reason(position, quote.bid)
                    )
                    evaluated += 1
                    if reason:
                        fill, net = proceeds(position, quote.bid)
                        pnl = money(net - Decimal(position["cost"]))
                        returned_cash = row.budget + pnl
                        wallet.cash += returned_cash
                        record_equity_cash_event(
                            session, wallet, row.id, kind="CLOSE", delta=returned_cash,
                        )
                        row.status = "CLOSED"
                        state.update(
                            exit_at=now.isoformat(),
                            exit_price=str(fill),
                            realized_pnl=str(pnl),
                            exit_reason=reason,
                            outcome_evidence={
                                "version": 2,
                                "strategy_version": row.version,
                                "calculation_version": position.get("calculation_version"),
                                "exit_policy_version": position.get(
                                    "exit_policy_version", "equity-exit-v1"
                                ),
                                "entry_at": state.get("entry_at"),
                                "entry_quote_at": state.get("entry_quote_at"),
                                "entry_quote_basis": state.get("entry_quote_basis"),
                                "entry_book_as_of": state.get("entry_book_as_of"),
                                "entry_ask": state.get("entry_ask"),
                                "entry_signal_date": state.get("entry_signal_date"),
                                "entry_market_context": state.get("entry_market_context"),
                                "entry_price": position["entry_price"],
                                "initial_stop": state.get("initial_stop", str(
                                    Decimal(position["entry_price"]) - Decimal(position["distance"])
                                )),
                                "final_stop": position["stop"],
                                "initial_risk": str(-Decimal(position["stop_scenario_net"])),
                                "exit_at": now.isoformat(),
                                "exit_quote_at": quote.last_trade_time.isoformat(),
                                "exit_quote_basis": quote_basis,
                                "exit_book_as_of": book_at,
                                "exit_response_at": quote.timestamp.isoformat(),
                                "exit_bid": str(quote.bid),
                                "exit_price": str(fill),
                                "exit_reason": reason,
                                "net_pnl": str(pnl),
                            },
                            detail="Closed; net proceeds returned to equity simulation balance",
                        )
                        session.add(AuditEvent(
                            actor_user_id=user_id,
                            correlation_id=str(deployment_id),
                            event_type="equity.deployment.exited",
                            details={
                                "deployment_id": str(deployment_id),
                                "strategy": row.strategy,
                                "quote_at": quote.last_trade_time.isoformat(),
                                "quote_basis": quote_basis,
                                "book_as_of": book_at,
                                "quote_mode": state.get("quote_mode", "VERIFIED"),
                                "response_at": quote.timestamp.isoformat(),
                                "bid": str(quote.bid),
                                "exit_reason": reason,
                                "exit_price": str(fill),
                                "net_pnl": str(pnl),
                                "final_stop": position["stop"],
                                "high_water": position["high_water"],
                            },
                        ))
                        closed += 1
                    else:
                        updated_position = trail(position, quote.bid)
                        state["position"] = updated_position
                        if Decimal(updated_position["stop"]) > Decimal(position["stop"]):
                            session.add(AuditEvent(
                                actor_user_id=user_id,
                                correlation_id=str(deployment_id),
                                event_type="equity.deployment.stop_raised",
                                details={
                                    "deployment_id": str(deployment_id),
                                    "strategy": row.strategy,
                                    "quote_at": quote.last_trade_time.isoformat(),
                                    "old_stop": position["stop"],
                                    "new_stop": updated_position["stop"],
                                    "high_water": updated_position["high_water"],
                                },
                            ))
                        state["detail"] = (
                            "Indicative paper position open; stop, target and trail monitored"
                            if indicative else "Position open; stop, target and trail monitored"
                        )
                    _, marked = proceeds(position, quote.bid)
                    state["marked_pnl"] = str(money(marked - Decimal(position["cost"])))
                    state["quote_at"] = quote.last_trade_time.isoformat()
            if (row.status == "WAITING_FOR_ENTRY" and market_open and market is not None
                    and market.start <= now < market.end):
                record_waiting_check(session, row, state, now)
            row.state = state
            session.commit()
    return evaluated, closed, warnings
