"""Record strategy demotions, probation steps and promotions as soon as they are due."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, union
from sqlalchemy.orm import Session, sessionmaker

from apps.api.settings import Settings
from packages.database.models import PaperTrade, StrategyDemotion
from packages.paper_trading.demotion import apply_strategy_eligibility
from packages.paper_trading.transactions import lock_account
from packages.paper_trading.worker_lease import LeaseToken, assert_owned


class StrategyNotEligible(HTTPException):
    """One strategy may not take this entry; unlike the kill switch, others still may."""

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=423, detail=detail)


def sweep_strategy_eligibility(
    factory: sessionmaker[Session], token: LeaseToken, settings: Settings
) -> list[str]:
    """Apply the eligibility rules for every traded strategy, one account lock at a time.

    Exits never depend on this. A failure only leaves a warning: the entry path applies the
    same rules under its own lock, so a missed sweep cannot let a paused strategy enter.
    """
    with factory() as session:
        pairs = session.execute(union(
            select(PaperTrade.user_id, PaperTrade.strategy_key),
            select(StrategyDemotion.user_id, StrategyDemotion.strategy_key),
        )).all()
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
