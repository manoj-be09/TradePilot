"""Autonomous, idempotent entry for armed options paper strategies."""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from apps.api.paper_schemas import PaperTradeCreateRequest
from apps.api.settings import Settings
from packages.database.models import AuditEvent, PaperDeployment, User
from packages.market_data.contracts import (
    ExchangeSession,
    MarketDataProvider,
    MarketDataProviderError,
    MarketDataUnavailable,
)
from packages.paper_trading.option_policy import parse_option_exit_policy
from packages.paper_trading.session import (
    PaperSessionError,
    exchange_today,
    expiry_entry_window_closed,
    expiry_has_passed,
)
from packages.paper_trading.transactions import lock_account
from packages.paper_trading.worker_lease import LeaseToken, assert_owned


def _finish_waiting(
    factory: sessionmaker[Session], token: LeaseToken, deployment_id: UUID,
    user_id: UUID, status: str, detail: str,
) -> bool:
    with factory() as session:
        lock_account(session, user_id)
        if not assert_owned(session, token):
            session.rollback()
            return False
        row = session.scalar(select(PaperDeployment).where(
            PaperDeployment.id == deployment_id,
            PaperDeployment.user_id == user_id,
        ).with_for_update().execution_options(populate_existing=True))
        if row is None or row.status != "WAITING_FOR_ENTRY":
            session.rollback()
            return False
        row.status = status
        row.detail = detail
        row.updated_at = datetime.now(timezone.utc)
        session.add(AuditEvent(
            actor_user_id=user_id,
            correlation_id=str(deployment_id),
            event_type=f"paper.deployment.{status.lower()}",
            details={"deployment_id": str(deployment_id), "reason": detail},
        ))
        session.commit()
        return True


def _record_retryable_block(
    factory: sessionmaker[Session], token: LeaseToken, deployment_id: UUID,
    user_id: UUID, status_code: int, reason: str,
) -> bool:
    """Record a changed entry gate without filling or ending the armed deployment."""
    with factory() as session:
        lock_account(session, user_id)
        if not assert_owned(session, token):
            session.rollback()
            return False
        row = session.scalar(select(PaperDeployment).where(
            PaperDeployment.id == deployment_id,
            PaperDeployment.user_id == user_id,
            PaperDeployment.status == "WAITING_FOR_ENTRY",
        ).with_for_update())
        if row is None:
            session.rollback()
            return False
        previous = session.scalar(select(AuditEvent).where(
            AuditEvent.actor_user_id == user_id,
            AuditEvent.correlation_id == str(deployment_id),
            AuditEvent.event_type == "paper.deployment.entry_blocked",
        ).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(1))
        if previous is not None and (
            previous.details.get("status_code") == status_code
            and previous.details.get("reason") == reason
        ):
            if row.detail == reason[:256]:
                session.rollback()
                return False
            row.detail = reason[:256]
            row.updated_at = datetime.now(timezone.utc)
            session.commit()
            return True
        row.detail = reason[:256]
        row.updated_at = datetime.now(timezone.utc)
        session.add(AuditEvent(
            actor_user_id=user_id,
            correlation_id=str(deployment_id),
            event_type="paper.deployment.entry_blocked",
            details={
                "deployment_id": str(deployment_id),
                "status_code": status_code,
                "reason": reason,
            },
        ))
        session.commit()
        return True


def _mark_entry_attempt(
    factory: sessionmaker[Session], token: LeaseToken, deployment_id: UUID,
    user_id: UUID,
) -> bool:
    """Rotate checked deployments without adding duplicate decision events."""
    with factory() as session:
        lock_account(session, user_id)
        if not assert_owned(session, token):
            session.rollback()
            return False
        row = session.scalar(select(PaperDeployment).where(
            PaperDeployment.id == deployment_id,
            PaperDeployment.user_id == user_id,
            PaperDeployment.status == "WAITING_FOR_ENTRY",
        ).with_for_update())
        if row is None:
            session.rollback()
            return False
        row.updated_at = datetime.now(timezone.utc)
        session.commit()
        return True


async def process_option_deployments(
    factory: sessionmaker[Session], provider: MarketDataProvider, token: LeaseToken,
    settings: Settings,
) -> tuple[int, int, list[str]]:
    """Check up to five waiting entries per cycle; never hold account locks during I/O."""
    with factory() as session:
        waiting = list(session.scalars(
            select(PaperDeployment)
            .where(PaperDeployment.status == "WAITING_FOR_ENTRY")
            .order_by(PaperDeployment.updated_at, PaperDeployment.created_at, PaperDeployment.id)
            .limit(20)
        ))
        rows = [(row.id, row.user_id, row.request_data) for row in waiting]
    checked = filled = 0
    warnings: list[str] = []
    today = exchange_today()
    expiry_session: ExchangeSession | None = None
    expiry_session_loaded = False
    expiry_session_failed = False
    for deployment_id, user_id, request_data in rows:
        try:
            body = PaperTradeCreateRequest.model_validate(request_data)
        except ValueError:
            _finish_waiting(
                factory, token, deployment_id, user_id, "REJECTED",
                "Saved entry rules are invalid; arm a new strategy",
            )
            continue
        try:
            exit_policy = parse_option_exit_policy(
                request_data.get("exit_policy"),
                legacy_cutoff=settings.paper_expiry_square_off_minutes_before_close,
            )
        except ValueError:
            _finish_waiting(
                factory, token, deployment_id, user_id, "REJECTED",
                "Saved exit policy is invalid; arm a new strategy",
            )
            continue
        if expiry_has_passed(body.expiry):
            _finish_waiting(
                factory, token, deployment_id, user_id, "EXPIRED",
                "Option expiry passed before an entry signal; no trade opened",
            )
            continue
        if checked >= 5:
            continue
        if not _mark_entry_attempt(factory, token, deployment_id, user_id):
            continue
        checked += 1
        armed_provider = request_data.get("market_data_provider", "upstox")
        if provider.provider_id != armed_provider:
            reason = "Armed option strategy provider differs from the active provider"
            _record_retryable_block(factory, token, deployment_id, user_id, 503, reason)
            warnings.append(reason)
            continue
        if not callable(getattr(provider, "get_option_contracts", None)):
            reason = "Active provider has no option-contract capability"
            _record_retryable_block(factory, token, deployment_id, user_id, 503, reason)
            warnings.append(reason)
            continue
        if body.expiry == today:
            if not expiry_session_loaded:
                expiry_session_loaded = True
                try:
                    expiry_session = await provider.get_nfo_session(today)
                except (MarketDataUnavailable, MarketDataProviderError):
                    expiry_session_failed = True
            if expiry_session_failed:
                reason = "NFO session unavailable; expiry-day entries remain waiting"
                _record_retryable_block(factory, token, deployment_id, user_id, 503, reason)
                warnings.append(reason)
                continue
            try:
                cutoff_passed = expiry_entry_window_closed(
                    body.expiry,
                    expiry_session,
                    minutes_before_close=exit_policy.square_off_minutes_before_close,
                )
            except PaperSessionError:
                reason = "NFO session invalid; expiry-day entries remain waiting"
                _record_retryable_block(factory, token, deployment_id, user_id, 503, reason)
                warnings.append(reason)
                continue
            if cutoff_passed:
                _finish_waiting(
                    factory, token, deployment_id, user_id, "EXPIRED",
                    "Expiry-day entry window ended before a signal; no paper trade opened",
                )
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
                # Import here: the HTTP route and worker share one guarded order path.
                from apps.api.main import _open_paper_trade_with_provider

                await _open_paper_trade_with_provider(
                    body, user, session, f"auto_{deployment_id.hex}", provider,
                    lease=token, deployment_id=deployment_id, exit_policy=exit_policy,
                )
                filled += 1
            except HTTPException as error:
                session.rollback()
                if error.status_code in {404, 422}:
                    _finish_waiting(
                        factory, token, deployment_id, user_id, "REJECTED",
                        "Saved strategy or contract is no longer valid; arm a new strategy",
                    )
                elif error.status_code >= 500:
                    warnings.append("NIFTY options data unavailable; armed entry was not filled")
                # Rule/quality failures already have a detailed checked snapshot. Record
                # other retryable gates once per changed reason, without logging provider data.
                if error.status_code in {409, 423, 503}:
                    reason = (
                        error.detail if isinstance(error.detail, str)
                        and error.status_code in {409, 423}
                        else "Paper entry could not be evaluated; waiting for a retry"
                    )
                    if isinstance(error.detail, str) or error.status_code == 503:
                        _record_retryable_block(
                            factory, token, deployment_id, user_id,
                            error.status_code, reason,
                        )
            except Exception:
                session.rollback()
                warnings.append("Armed options entry failed; no paper fill was confirmed")
    return checked, filled, warnings
