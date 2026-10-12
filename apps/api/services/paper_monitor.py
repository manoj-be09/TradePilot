import hashlib
import json
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.paper_schemas import PaperMonitorResponse
from apps.api.settings import Settings
from packages.database.models import (
    AuditEvent,
    PaperAccount,
    PaperOrder,
    PaperTrade,
    UserNotification,
)
from packages.market_data.contracts import (
    MarketDataProvider,
    MarketDataProviderError,
    MarketDataUnavailable,
    OptionChain,
)
from packages.market_data.quality import assess_option_chain
from packages.paper_trading.exits import ExitDecision, evaluate_exit
from packages.paper_trading.option_policy import parse_option_exit_policy
from packages.paper_trading.quotes import PaperQuoteError, require_quote
from packages.paper_trading.session import (
    PaperSessionError,
    exchange_today,
    expiry_has_passed,
    expiry_square_off_due,
    require_nfo_session_open,
)
from packages.paper_trading.transactions import (
    PaperLedgerMismatchError,
    assert_consistent_ledger,
    lock_account,
)
from packages.paper_trading.worker_lease import LeaseToken, assert_owned

_CENT = Decimal("0.01")


def _record_exit_observation(
    session: Session, user_id: UUID, trade_id: UUID, snapshot: dict[str, object]
) -> None:
    """Save a distinct eligible or skipped exit check with its trade state."""
    digest = hashlib.sha256(json.dumps(
        snapshot, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    comparable = {key: value for key, value in snapshot.items() if key != "evaluated_at"}
    dedup_digest = hashlib.sha256(json.dumps(
        comparable, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    previous = session.scalar(select(AuditEvent).where(
        AuditEvent.actor_user_id == user_id,
        AuditEvent.correlation_id == str(trade_id),
        AuditEvent.event_type == "paper.trade.exit_checked",
    ).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(1))
    if previous is not None:
        prior = previous.details.get("snapshot")
        prior_comparable = (
            {key: value for key, value in prior.items() if key != "evaluated_at"}
            if isinstance(prior, dict) else None
        )
        if previous.details.get("dedup_digest") == dedup_digest or prior_comparable == comparable:
            return
    session.add(AuditEvent(
        actor_user_id=user_id,
        correlation_id=str(trade_id),
        event_type="paper.trade.exit_checked",
        details={
            "trade_id": str(trade_id), "snapshot_digest": digest,
            "dedup_digest": dedup_digest, "snapshot": snapshot,
        },
    ))


def _raw_exit_leg_quotes(trade: PaperTrade, chain: OptionChain | None) -> list[dict[str, object]]:
    """Keep source observations even when they cannot support a paper fill."""
    if chain is None:
        return []
    by_key = {contract.instrument_token: contract for contract in chain.contracts}
    result: list[dict[str, object]] = []
    for leg in trade.legs["items"]:
        exit_side = "SELL" if leg["side"] == "BUY" else "BUY"
        contract = by_key.get(str(leg["instrument_key"]))
        quote = contract.quote if contract is not None else None
        side_quote = (quote.bid if exit_side == "SELL" else quote.ask) if quote else None
        result.append({
            "instrument_key": str(leg["instrument_key"]),
            "option_type": str(leg["option_type"]),
            "strike": str(leg["strike"]),
            "side": exit_side,
            "quantity_units": int(leg["quantity_units"]),
            "bid": str(quote.bid) if quote and quote.bid is not None else None,
            "ask": str(quote.ask) if quote and quote.ask is not None else None,
            "bid_quantity": contract.bid_quantity if contract else None,
            "ask_quantity": contract.ask_quantity if contract else None,
            "selected_side_quote": str(side_quote) if side_quote is not None else None,
            "quote_timestamp": quote.timestamp.isoformat() if quote else None,
            "timestamp_basis": quote.timestamp_basis if quote else None,
            "book_as_of": quote.book_as_of.isoformat()
            if quote and quote.book_as_of else None,
            "book_timestamp_basis": quote.book_timestamp_basis if quote else None,
        })
    return result


def _record_skipped_exit_checks(
    session: Session,
    user_id: UUID,
    trade_ids: list[UUID],
    provider: MarketDataProvider,
    lease: LeaseToken | None,
    reason_code: str,
    reason: str,
    chain: OptionChain | None = None,
    quality_status: str | None = None,
) -> bool:
    """Persist a fail-closed gap only while the trade and worker ownership are current."""
    if not trade_ids:
        return True
    account = lock_account(session, user_id)
    if account is None or (lease is not None and not assert_owned(session, lease)):
        session.rollback()
        return False
    trades = session.scalars(select(PaperTrade).where(
        PaperTrade.id.in_(trade_ids),
        PaperTrade.user_id == user_id,
        PaperTrade.account_id == account.id,
        PaperTrade.status == "OPEN",
    ).with_for_update().execution_options(populate_existing=True)).all()
    for trade in trades:
        _record_exit_observation(session, user_id, trade.id, {
            "version": 1,
            "provider": provider.provider_id,
            "market_as_of": chain.as_of.isoformat() if chain else None,
            "timestamp_basis": chain.timestamp_basis if chain else None,
            "quality_status": quality_status,
            "strategy_key": trade.strategy_key,
            "decision": "SKIPPED",
            "reason_code": reason_code,
            "reason": reason,
            "unrealized_pnl": None,
            "leg_quotes": _raw_exit_leg_quotes(trade, chain),
        })
    session.commit()
    return True


def _add_user_notification(
    session: Session,
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


async def monitor_exits(
    user_id: UUID,
    session: Session,
    provider: MarketDataProvider,
    settings: Settings,
    lease: LeaseToken | None = None,
) -> PaperMonitorResponse:
    """Evaluate configured paper exits against fresh quotes for this user's open trades."""
    account = session.scalar(select(PaperAccount).where(PaperAccount.user_id == user_id))
    checked_at = datetime.now(timezone.utc)
    if account is None:
        return PaperMonitorResponse(
            checked_at=checked_at,
            evaluated_count=0,
            closed_trade_ids=[],
            unrealized_pnl={},
            warnings=[],
        )
    open_trades = session.scalars(
        select(PaperTrade)
        .where(PaperTrade.account_id == account.id, PaperTrade.status == "OPEN")
        .order_by(PaperTrade.opened_at)
    ).all()
    session.commit()
    if not open_trades:
        return PaperMonitorResponse(
            checked_at=checked_at,
            evaluated_count=0,
            closed_trade_ids=[],
            unrealized_pnl={},
            warnings=[],
        )

    # Instrument keys are scoped to the source that issued them. Older trades
    # predate this field and were created only by the Upstox adapter.
    matching_trades = [
        trade for trade in open_trades
        if provider.provider_id == "unconfigured" or (
            trade.entry_rule_snapshot.get("market_data_provider", "upstox")
            if isinstance(trade.entry_rule_snapshot, dict) else "upstox")
        == provider.provider_id
    ]
    matching_ids = {trade.id for trade in matching_trades}
    mismatched_ids = [trade.id for trade in open_trades if trade.id not in matching_ids]
    if mismatched_ids:
        if not _record_skipped_exit_checks(
            session, user_id, mismatched_ids, provider, lease,
            "PROVIDER_MISMATCH", "Trade instrument keys belong to another market provider",
        ):
            return PaperMonitorResponse(
                checked_at=checked_at, evaluated_count=0, closed_trade_ids=[],
                unrealized_pnl={}, warnings=["Worker ownership expired; exits were not evaluated."],
            )

    chains: dict[date, OptionChain] = {}
    warnings: list[str] = (
        ["Open option trades belong to another market provider; exits need the original provider."]
        if mismatched_ids else []
    )
    active_expiries: list[date] = []
    for expiry in sorted({trade.expiry for trade in matching_trades}):
        if expiry_has_passed(expiry):
            warnings.append(
                f"Exit checks for {expiry} stopped: open positions are unresolved after expiry; "
                "settlement requires manual reconciliation. No synthetic exit was recorded."
            )
        else:
            active_expiries.append(expiry)
    active_trade_ids = [
        trade.id for trade in matching_trades if not expiry_has_passed(trade.expiry)
    ]

    nfo_session = None
    if active_expiries:
        try:
            nfo_session = await provider.get_nfo_session(exchange_today())
            require_nfo_session_open(nfo_session)
        except (MarketDataUnavailable, MarketDataProviderError, PaperSessionError):
            warnings.append(
                "NFO scheduled session is closed or unverified; paper exit checks were skipped."
            )
            if not _record_skipped_exit_checks(
                session, user_id, active_trade_ids, provider, lease,
                "SESSION_UNAVAILABLE", "NFO session closed or unverified",
            ):
                warnings.append("Worker ownership expired; exit observation was fenced off.")
            return PaperMonitorResponse(
                checked_at=checked_at, evaluated_count=0, closed_trade_ids=[],
                unrealized_pnl={}, warnings=warnings,
            )
    for expiry in active_expiries:
        expiry_trade_ids = [trade.id for trade in matching_trades if trade.expiry == expiry]
        try:
            chain = await provider.get_option_chain("NIFTY", expiry)
            quality = assess_option_chain(chain)
            if quality.status == "FAIL":
                warnings.append(f"Exit checks for {expiry} skipped: option-chain quality is FAIL.")
                if not _record_skipped_exit_checks(
                    session, user_id, expiry_trade_ids, provider, lease,
                    "CHAIN_QUALITY_FAIL", "Option-chain quality failed",
                    chain, quality.status,
                ):
                    warnings.append("Worker ownership expired; exit observation was fenced off.")
                    break
                continue
            chains[expiry] = chain
        except (MarketDataUnavailable, MarketDataProviderError):
            warnings.append(f"Exit checks for {expiry} skipped: market data is unavailable.")
            if not _record_skipped_exit_checks(
                session, user_id, expiry_trade_ids, provider, lease,
                "MARKET_DATA_UNAVAILABLE", "Option-chain data unavailable",
            ):
                warnings.append("Worker ownership expired; exit observation was fenced off.")
                break

    unrealized: dict[UUID, Decimal] = {}
    closed_trade_ids: list[UUID] = []
    evaluated_count = 0
    for snapshot in matching_trades:
        if expiry_has_passed(snapshot.expiry):
            continue
        trade_chain = chains.get(snapshot.expiry)
        if trade_chain is None:
            continue
        close_flow = Decimal(0)
        fills: list[dict[str, object]] = []
        observed_quotes: list[dict[str, object]] = []
        try:
            for leg in snapshot.legs["items"]:
                exit_side = "SELL" if leg["side"] == "BUY" else "BUY"
                exit_price, market_contract = require_quote(
                    trade_chain,
                    str(leg["instrument_key"]),
                    exit_side,
                    int(leg["quantity_units"]),
                    max_age_seconds=settings.paper_quote_max_age_seconds,
                    allow_unverified=settings.paper_allow_unverified_quotes,
                )
                units = int(leg["quantity_units"])
                amount = (exit_price * units).quantize(_CENT, rounding=ROUND_HALF_UP)
                leg_cash_flow = amount if exit_side == "SELL" else -amount
                close_flow += leg_cash_flow
                fills.append(
                    {
                        "instrument_key": leg["instrument_key"],
                        "trading_symbol": leg["trading_symbol"],
                        "option_type": leg["option_type"],
                        "side": exit_side,
                        "strike": leg["strike"],
                        "quantity_units": units,
                        "fill_price": str(exit_price),
                        "cash_flow": str(leg_cash_flow),
                        "quote_timestamp": market_contract.quote.timestamp.isoformat(),
                        "book_as_of": market_contract.quote.book_as_of.isoformat()
                        if market_contract.quote.book_as_of else None,
                        "book_timestamp_basis": market_contract.quote.book_timestamp_basis,
                    }
                )
                observed_quotes.append({
                    "instrument_key": str(leg["instrument_key"]),
                    "option_type": str(leg["option_type"]),
                    "strike": str(leg["strike"]),
                    "side": exit_side,
                    "quantity_units": units,
                    "bid": str(market_contract.quote.bid),
                    "ask": str(market_contract.quote.ask),
                    "bid_quantity": market_contract.bid_quantity,
                    "ask_quantity": market_contract.ask_quantity,
                    "selected_side_quote": str(exit_price),
                    "quote_timestamp": market_contract.quote.timestamp.isoformat(),
                    "timestamp_basis": market_contract.quote.timestamp_basis,
                    "book_as_of": market_contract.quote.book_as_of.isoformat()
                    if market_contract.quote.book_as_of else None,
                    "book_timestamp_basis": market_contract.quote.book_timestamp_basis,
                })
        except PaperQuoteError as error:
            warnings.append(f"Exit check for trade {snapshot.id} skipped: {error}.")
            if not _record_skipped_exit_checks(
                session, user_id, [snapshot.id], provider, lease,
                "QUOTE_REJECTED", str(error), trade_chain,
                assess_option_chain(trade_chain).status,
            ):
                warnings.append("Worker ownership expired; exit observation was fenced off.")
                break
            continue
        close_flow = close_flow.quantize(_CENT, rounding=ROUND_HALF_UP)
        pnl = (snapshot.entry_cash_flow + close_flow).quantize(_CENT, rounding=ROUND_HALF_UP)
        account_locked = lock_account(session, user_id)
        if lease is not None and not assert_owned(session, lease):
            session.rollback()
            warnings.append("Worker ownership expired; exit mutation was fenced off.")
            break
        if account_locked is not None:
            try:
                assert_consistent_ledger(session, account_locked)
            except PaperLedgerMismatchError as error:
                session.rollback()
                warnings.append(str(error))
                continue
        try:
            for fill in fills:
                require_quote(
                    trade_chain,
                    str(fill["instrument_key"]),
                    str(fill["side"]),
                    int(str(fill["quantity_units"])),
                    max_age_seconds=settings.paper_quote_max_age_seconds,
                    allow_unverified=settings.paper_allow_unverified_quotes,
                )
        except PaperQuoteError as error:
            session.rollback()
            warnings.append(str(error))
            if not _record_skipped_exit_checks(
                session, user_id, [snapshot.id], provider, lease,
                "QUOTE_RECHECK_REJECTED", str(error), trade_chain,
                assess_option_chain(trade_chain).status,
            ):
                warnings.append("Worker ownership expired; exit observation was fenced off.")
                break
            continue
        trade = session.scalar(
            select(PaperTrade)
            .where(PaperTrade.id == snapshot.id, PaperTrade.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if account_locked is None or trade is None or trade.status != "OPEN":
            session.rollback()
            continue
        if expiry_has_passed(trade.expiry):
            session.rollback()
            warnings.append(
                f"Exit check for trade {trade.id} stopped as expiry passed during evaluation; "
                "settlement requires manual reconciliation."
            )
            _record_skipped_exit_checks(
                session, user_id, [snapshot.id], provider, lease,
                "EXPIRY_PASSED", "Expiry passed during exit evaluation",
                trade_chain, assess_option_chain(trade_chain).status,
            )
            continue
        try:
            saved_policy = (
                trade.entry_rule_snapshot.get("exit_policy")
                if isinstance(trade.entry_rule_snapshot, dict) else None
            )
            exit_policy = parse_option_exit_policy(
                saved_policy,
                legacy_cutoff=settings.paper_expiry_square_off_minutes_before_close,
            )
        except ValueError:
            session.rollback()
            warnings.append(f"Exit policy for trade {trade.id} is invalid; no paper exit recorded.")
            _record_skipped_exit_checks(
                session, user_id, [snapshot.id], provider, lease,
                "EXIT_POLICY_INVALID", "Saved paper exit policy is invalid",
                trade_chain, assess_option_chain(trade_chain).status,
            )
            continue
        try:
            evaluation_instant = datetime.now(timezone.utc)
            require_nfo_session_open(nfo_session, evaluation_instant)
        except PaperSessionError as error:
            session.rollback()
            warnings.append(str(error))
            if not _record_skipped_exit_checks(
                session, user_id, [snapshot.id], provider, lease,
                "SESSION_CHANGED", "NFO session closed during exit evaluation",
                trade_chain, assess_option_chain(trade_chain).status,
            ):
                warnings.append("Worker ownership expired; exit observation was fenced off.")
                break
            continue
        evaluated_count += 1
        assert nfo_session is not None
        decision = evaluate_exit(
            pnl=pnl,
            peak_pnl=trade.peak_unrealized_pnl or Decimal(0),
            stop_loss_amount=trade.stop_loss_amount,
            target_profit_amount=trade.target_profit_amount,
            trailing_stop_amount=trade.trailing_stop_amount,
        )
        if decision.reason is None and expiry_square_off_due(
            trade.expiry,
            nfo_session,
            now=evaluation_instant,
            minutes_before_close=exit_policy.square_off_minutes_before_close,
        ):
            decision = ExitDecision(decision.peak_pnl, "EXPIRY_SQUARE_OFF")
        _record_exit_observation(session, user_id, trade.id, {
            "version": 1,
            "provider": provider.provider_id,
            "market_as_of": trade_chain.as_of.isoformat(),
            "evaluated_at": evaluation_instant.isoformat(),
            "nfo_session": {
                "exchange": nfo_session.exchange,
                "start": nfo_session.start.isoformat(),
                "end": nfo_session.end.isoformat(),
            },
            "square_off_minutes_before_close": (
                exit_policy.square_off_minutes_before_close
            ),
            "exit_policy_version": exit_policy.version,
            "timestamp_basis": trade_chain.timestamp_basis,
            "quality_status": assess_option_chain(trade_chain).status,
            "strategy_key": trade.strategy_key,
            "entry_cash_flow": str(trade.entry_cash_flow),
            "mark_close_cash_flow": str(close_flow),
            "unrealized_pnl": str(pnl),
            "peak_unrealized_pnl": str(decision.peak_pnl),
            "stop_loss_amount": str(trade.stop_loss_amount)
            if trade.stop_loss_amount is not None else None,
            "target_profit_amount": str(trade.target_profit_amount)
            if trade.target_profit_amount is not None else None,
            "trailing_stop_amount": str(trade.trailing_stop_amount)
            if trade.trailing_stop_amount is not None else None,
            "decision": decision.reason or "HOLD",
            "leg_quotes": observed_quotes,
        })
        trade.peak_unrealized_pnl = decision.peak_pnl
        if decision.reason is None:
            unrealized[trade.id] = pnl
            session.commit()
            continue

        trade.realized_pnl = pnl
        trade.status = "CLOSED"
        trade.closed_at = datetime.now(timezone.utc)
        trade.exit_reason = decision.reason
        account_locked.cash_balance = (account_locked.cash_balance + close_flow).quantize(
            _CENT, rounding=ROUND_HALF_UP
        )
        account_locked.updated_at = datetime.now(timezone.utc)
        session.add(
            PaperOrder(
                account_id=account_locked.id,
                trade_id=trade.id,
                user_id=user_id,
                action="CLOSE",
                status="EXECUTED",
                net_cash_flow=close_flow,
                created_at=datetime.now(timezone.utc),
                fill_snapshot={
                    "paper_only": True,
                    "market_as_of": trade_chain.as_of.isoformat(),
                    "quality_status": assess_option_chain(trade_chain).status,
                    "exit_reason": decision.reason,
                    "fills": fills,
                    "method": (
                        "Paper risk-rule exit; simulated closing sells at bid and buys at ask; "
                        "no queue or slippage model."
                    ),
                },
            )
        )
        session.add(
            AuditEvent(
                actor_user_id=user_id,
                event_type="paper.trade.exit_rule_triggered",
                details={
                    "trade_id": str(trade.id),
                    "exit_reason": decision.reason,
                    "realized_pnl": str(pnl),
                },
            )
        )
        _add_user_notification(
            session,
            user_id,
            "paper.trade.exit_rule_triggered",
            f"Paper position closed: {decision.reason.replace('_', ' ').lower()}",
            f"{trade.strategy_name} · realized P&L ₹{pnl}",
            {
                "trade_id": str(trade.id),
                "exit_reason": decision.reason,
                "realized_pnl": str(pnl),
            },
        )
        closed_trade_ids.append(trade.id)
        session.commit()

    return PaperMonitorResponse(
        checked_at=checked_at,
        evaluated_count=evaluated_count,
        closed_trade_ids=closed_trade_ids,
        unrealized_pnl=unrealized,
        warnings=warnings,
    )
