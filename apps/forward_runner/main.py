"""Run with python -m apps.forward_runner.main; keeps a frozen forward paper period armed.

Each cycle in market hours it arms every frozen strategy version once for the nearest
expiry after today, at the strike nearest the index. Arming goes through the same endpoint
function as the web app, so the kill switch, ledger check, demotion, waiting limit and
frozen-rule comparison all still apply; the worker rechecks entry rules and risk limits
before any simulated fill. It places no broker orders. Freeze a period once with --freeze.
"""

import argparse
import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from apps.api.paper_schemas import PaperTradeCreateRequest
from apps.api.services.strategy_eligibility import StrategyNotEligible
from packages.database.models import PaperDeployment, StrategyVersion, User
from packages.database.session import build_session_factory
from packages.market_data.contracts import (
    MarketDataProvider,
    MarketDataProviderError,
    MarketDataUnavailable,
)
from packages.paper_trading.forward_plan import (
    ForwardPlan,
    load_plan,
    nearest_strike,
    next_expiry,
    save_new_plan,
)
from packages.paper_trading.session import exchange_today

logger = logging.getLogger("forward_runner")
DEFAULT_PLAN = Path("work/forward-paper.json")
FROZEN_NAME_PREFIX = "Batch study · "
Arm = Callable[[PaperTradeCreateRequest, User, Session, str], Awaitable[Any]]


def freeze(session: Session, user_email: str, start: date, weeks: int) -> ForwardPlan:
    """Pin the latest 1-lot batch version of each template for this user."""
    user = session.scalar(select(User).where(User.email == user_email))
    if user is None:
        raise ValueError("No account has that email")
    latest: dict[str, StrategyVersion] = {}
    versions = session.scalars(select(StrategyVersion).where(
        StrategyVersion.user_id == user.id,
    ).order_by(StrategyVersion.version_number))
    for version in versions:
        if version.name.startswith(FROZEN_NAME_PREFIX) and version.definition.get("lots") == 1:
            latest[version.definition["template_key"]] = version
    if not latest:
        raise ValueError("No saved 1-lot batch strategy versions to freeze")
    return ForwardPlan(
        user_email=user_email,
        version_ids=tuple(latest[key].id for key in sorted(latest)),
        start=start,
        end=start + timedelta(weeks=weeks),
    )


async def run_cycle(
    factory: sessionmaker[Session],
    provider: MarketDataProvider,
    plan: ForwardPlan,
    arm: Arm,
    now: datetime | None = None,
) -> str:
    moment = now or datetime.now(timezone.utc)
    today = exchange_today(moment)
    if today > plan.end:
        return "FINISHED"
    try:
        market = await provider.get_nfo_session(today)
        if market is None or not market.start <= moment <= market.end:
            return "CLOSED"
        contracts = await provider.get_option_contracts("NIFTY")
        expiry = next_expiry(sorted({item.expiry for item in contracts}), today)
        if expiry is None:
            return "NO_EXPIRY"
        chain = await provider.get_option_chain("NIFTY", expiry)
    except (MarketDataUnavailable, MarketDataProviderError) as error:
        logger.warning("Market data unavailable: %s", type(error).__name__)
        return "NO_DATA"
    if chain.quality is not None and chain.quality.status == "FAIL":
        return "NO_DATA"
    if chain.underlying_spot is None:
        return "NO_DATA"
    strike = nearest_strike(
        sorted({item.strike for item in chain.contracts}), chain.underlying_spot
    )
    if strike is None:
        return "NO_DATA"

    armed = blocked = 0
    with factory() as session:
        user = session.scalar(select(User).where(User.email == plan.user_email))
        if user is None:
            logger.error("The frozen plan's account no longer exists")
            return "NO_ACCOUNT"
        # One attempt per strategy per expiry, whatever became of it: a cancelled or rejected
        # one stays that way, so the period's sample is not padded by retries.
        attempted = {
            row.request_data.get("strategy_key")
            for row in session.scalars(select(PaperDeployment).where(
                PaperDeployment.user_id == user.id,
            ))
            if row.request_data.get("expiry") == expiry.isoformat()
        }
        for version_id in plan.version_ids:
            version = session.get(StrategyVersion, version_id)
            if version is None or version.user_id != user.id:
                logger.error("Frozen strategy version %s is missing", version_id)
                blocked += 1
                continue
            definition = version.definition
            if definition["template_key"] in attempted:
                continue
            try:
                body = PaperTradeCreateRequest(
                    strategy_key=definition["template_key"],
                    strategy_version_id=version.id,
                    expected_template_version=definition["template_version"],
                    expiry=expiry,
                    reference_strike=strike,
                    lots=definition["lots"],
                    entry_conditions=definition["entry_conditions"],
                    stop_loss_amount=definition["stop_loss_amount"],
                    target_profit_amount=definition["target_profit_amount"],
                    trailing_stop_amount=definition["trailing_stop_amount"],
                )
            except ValueError:
                logger.error("Frozen version %s has invalid rules", version_id)
                blocked += 1
                continue
            key = f"fwd_{version.id.hex}_{expiry:%Y%m%d}"
            try:
                await arm(body, user, session, key)
                armed += 1
            except StrategyNotEligible as error:
                # Only this strategy is paused or on probation; the others are still armed.
                session.rollback()
                logger.info("%s not armed: %s", definition["template_key"], error.detail)
                blocked += 1
            except HTTPException as error:
                session.rollback()
                if error.status_code == 423:
                    logger.info("Entries are paused; nothing more is armed this cycle")
                    return "PAUSED"
                # The waiting limit or changed rules: fail closed, retry later.
                logger.warning("%s not armed: %s", definition["template_key"], error.detail)
                blocked += 1
    logger.info("Expiry %s: armed %d, blocked %d", expiry, armed, blocked)
    return "OK"


async def serve(plan_path: Path, interval_seconds: float, once: bool) -> None:
    from apps.api.main import arm_paper_deployment
    from packages.market_data.provider import get_market_data_provider

    async def arm(body: PaperTradeCreateRequest, user: User, session: Session, key: str) -> Any:
        return await arm_paper_deployment(body, user, session, key)

    plan = load_plan(plan_path)
    factory = build_session_factory()
    provider = get_market_data_provider()
    while True:
        status = await run_cycle(factory, provider, plan, arm)
        logger.info("Cycle: %s", status)
        if once or status == "FINISHED":
            return
        await asyncio.sleep(interval_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--interval", type=float, default=300.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--freeze", action="store_true", help="Freeze a new period and exit")
    parser.add_argument("--user-email")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--weeks", type=int, default=6)
    args = parser.parse_args()
    if args.freeze:
        if not args.user_email:
            parser.error("--freeze needs --user-email")
        with build_session_factory()() as session:
            plan = freeze(session, args.user_email, args.start or exchange_today(), args.weeks)
        save_new_plan(args.plan, plan)
        logger.info("Frozen %d versions, %s to %s", len(plan.version_ids), plan.start, plan.end)
        return
    asyncio.run(serve(args.plan, args.interval, args.once))


if __name__ == "__main__":
    main()
