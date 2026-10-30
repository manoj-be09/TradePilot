"""Record strategy demotions, probation steps and promotions as soon as they are due."""

from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, union
from sqlalchemy.orm import Session, sessionmaker

from apps.api.settings import Settings
from packages.database.futures_models import PaperFuturesTrade
from packages.database.models import PaperDeployment, PaperTrade, StrategyDemotion
from packages.paper_trading.demotion import PROBATION_MAX_LOTS, apply_strategy_eligibility
from packages.paper_trading.transactions import lock_account
from packages.paper_trading.worker_lease import LeaseToken, assert_owned


class StrategyNotEligible(HTTPException):
    """One strategy may not take this entry; unlike the kill switch, others still may."""

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=423, detail=detail)


def eligibility_refusal(
    session: Session, user_id: UUID, strategy_key: str, initial_cash: Decimal, lots: int,
    settings: Settings, *, arming: bool = False,
) -> str | None:
    """Why demotion or probation forbids this option or futures entry, if they do.

    Call holding the account lock. A refusal commits first, so a newly staged step and its
    audit event survive the rejected entry.
    """
    eligibility = apply_strategy_eligibility(
        session, user_id, strategy_key, settings.paper_demotion_limits(initial_cash),
        settings.paper_promotion_policy,
    )
    refusal: str | None = None
    if eligibility.stage == "DEMOTED":
        refusal = (
            f"Strategy {strategy_key}: {eligibility.block_reason} "
            "Existing positions keep their exits."
        )
    elif eligibility.stage == "PROBATION":
        if lots > PROBATION_MAX_LOTS:
            refusal = f"Strategy {strategy_key} is on probation: at most 1 lot at a time."
        elif _has_open_position(session, user_id, strategy_key) or (arming and any(
            row.request_data.get("strategy_key") == strategy_key
            for row in session.scalars(select(PaperDeployment).where(
                PaperDeployment.user_id == user_id,
                PaperDeployment.status == "WAITING_FOR_ENTRY",
            ))
        )):
            refusal = (
                f"Strategy {strategy_key} is on probation: one position or armed entry at a time."
            )
    if refusal is not None:
        session.commit()
    return refusal


def _has_open_position(session: Session, user_id: UUID, strategy_key: str) -> bool:
    return any(
        session.scalar(select(model.id).where(
            model.user_id == user_id, model.strategy_key == strategy_key, model.status == "OPEN",
        ).limit(1)) is not None
        for model in (PaperTrade, PaperFuturesTrade)
    )


def sweep_strategy_eligibility(
    factory: sessionmaker[Session], token: LeaseToken, settings: Settings
) -> list[str]:
    """Apply the eligibility rules for every traded strategy, one account lock at a time.

    Exits never depend on this. A failure only leaves a warning: the entry path applies the
    same rules under its own lock, so a missed sweep cannot let a paused strategy enter.
    """
    with factory() as session:
        pairs: list[Any] = list(session.execute(union(
            select(PaperTrade.user_id, PaperTrade.strategy_key),
            select(PaperFuturesTrade.user_id, PaperFuturesTrade.strategy_key).where(
                PaperFuturesTrade.strategy_key.is_not(None)
            ),
            select(StrategyDemotion.user_id, StrategyDemotion.strategy_key),
        )))
    by_user: dict[UUID, list[str]] = {}
    for user_id, strategy_key in pairs:
        by_user.setdefault(user_id, []).append(strategy_key)
    warnings: list[str] = []
    for user_id, keys in by_user.items():
        with factory() as session:
            try:
                account = lock_account(session, user_id)
                if account is None or not assert_owned(session, token):
                    session.rollback()
                    continue
                limits = settings.paper_demotion_limits(account.initial_cash)
                for strategy_key in keys:
                    apply_strategy_eligibility(
                        session, user_id, strategy_key, limits, settings.paper_promotion_policy,
                    )
                session.commit()
            except Exception:
                session.rollback()
                warnings.append("Strategy eligibility check failed; entries re-check it")
    return warnings
