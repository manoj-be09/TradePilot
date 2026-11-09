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
import secrets
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from apps.api.paper_schemas import PaperTradeCreateRequest
from apps.api.services.strategy_eligibility import StrategyNotEligible
from apps.api.strategy_schemas import StrategyVersionCreateRequest
from packages.database.models import PaperDeployment, StrategyVersion, User
from packages.database.session import build_session_factory
from packages.market_data.contracts import (
    MarketDataProvider,
    MarketDataProviderError,
    MarketDataUnavailable,
)
from packages.options.models import PayoffLeg, PayoffRequest
from packages.options.payoff import calculate_expiry_payoff
from packages.paper_trading.forward_plan import (
    ForwardPlan,
    load_plan,
    nearest_strike,
    next_expiry,
    save_new_plan,
)
from packages.paper_trading.session import exchange_today
from packages.paper_trading.strategies import STRATEGIES, STRATEGY_REGISTRY, StrategyDefinition
from packages.paper_trading.template_policy import TemplatePolicy, template_policy

logger = logging.getLogger("forward_runner")
DEFAULT_PLAN = Path("work/forward-paper.json")
FROZEN_NAME_PREFIX = "Forward · "
Arm = Callable[[PaperTradeCreateRequest, User, Session, str], Awaitable[Any]]


def freeze(session: Session, user_email: str, start: date, weeks: int) -> ForwardPlan:
    """Pin the latest 1-lot forward version of each template for this user."""
    user = session.scalar(select(User).where(User.email == user_email))
    if user is None:
        raise ValueError("No account has that email")
    latest: dict[str, StrategyVersion] = {}
    versions = session.scalars(select(StrategyVersion).where(
        StrategyVersion.user_id == user.id,
    ).order_by(StrategyVersion.created_at, StrategyVersion.version_number))
    for version in versions:
        if version.name.startswith(FROZEN_NAME_PREFIX) and version.definition.get("lots") == 1:
            latest[version.definition["template_key"]] = version
    if not latest:
        raise ValueError("No saved 1-lot forward versions to freeze; run --propose first")
    return ForwardPlan(
        user_email=user_email,
        version_ids=tuple(latest[key].id for key in sorted(latest)),
        start=start,
        end=start + timedelta(weeks=weeks),
    )


CreateVersion = Callable[[StrategyVersionCreateRequest, User, Session], Awaitable[Any]]


async def propose(
    session: Session, user: User, provider: MarketDataProvider, create: CreateVersion,
    today: date, templates: frozenset[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Save a 1-lot version of each template, with exits sized from the next expiry's quotes.

    Returns the templates saved and those skipped because quotes or a finite loss are missing.
    Saving arms nothing. `templates` limits it to those keys, for a separate account.
    """
    contracts = await provider.get_option_contracts("NIFTY")
    expiry = next_expiry(sorted({item.expiry for item in contracts}), today)
    if expiry is None:
        raise ValueError("No upcoming expiry is listed")
    chain = await provider.get_option_chain("NIFTY", expiry)
    if chain.underlying_spot is None or (chain.quality and chain.quality.status == "FAIL"):
        raise ValueError("The option chain has no usable index price or failed quality checks")
    quotes = {(item.strike, item.option_type): item.quote for item in chain.contracts}
    strikes = sorted({item.strike for item in chain.contracts})
    reference = nearest_strike(strikes, chain.underlying_spot)
    lot_size = next((item.lot_size for item in contracts if item.expiry == expiry), None)
    if reference is None or lot_size is None:
        raise ValueError("The option chain has no strikes or lot size")
    index = strikes.index(reference)
    later = sorted({item.expiry for item in contracts if item.expiry > expiry})
    later_quotes: dict[tuple[Decimal, str], Any] | None = None
    saved: list[str] = []
    skipped: list[str] = []
    # Calendars are proposed only when named: the main period's set stays as it was frozen.
    candidates = STRATEGIES + (STRATEGY_REGISTRY.calendars() if templates else ())
    for strategy in candidates:
        if templates is not None and strategy.key not in templates:
            continue
        if strategy.expiry_scope == "MULTI_EXPIRY" and later_quotes is None and later:
            later_chain = await provider.get_option_chain("NIFTY", later[0])
            later_quotes = {(item.strike, item.option_type): item.quote
                            for item in later_chain.contracts}
        legs: list[PayoffLeg] = []
        debit = Decimal(0)
        spread_cost = Decimal(0)
        for leg in strategy.legs:
            position = index + leg.strike_offset
            strike = strikes[position] if 0 <= position < len(strikes) else None
            source = (later_quotes or {}) if leg.expiry_offset else quotes
            quote = source.get((strike, leg.option_type)) if strike is not None else None
            if strike is None or quote is None or not quote.bid or not quote.ask:
                break
            premium = quote.ask if leg.side == "BUY" else quote.bid
            quantity = leg.quantity_ratio if leg.side == "BUY" else -leg.quantity_ratio
            legs.append(PayoffLeg(option_type=leg.option_type, strike=strike,
                                  quantity=quantity, entry_premium=premium))
            debit += premium * quantity * lot_size
            spread_cost += (quote.ask - quote.bid) * leg.quantity_ratio * lot_size
        if len(legs) != len(strategy.legs):
            skipped.append(strategy.key)
            continue
        if strategy.expiry_scope == "MULTI_EXPIRY":
            # A long call calendar loses at most its debit; its best case has no closed form.
            policy = (template_policy(strategy.key, debit, None, spread_cost)
                      if debit > 0 else None)
            if policy is None:
                skipped.append(strategy.key)
                continue
            await create(_version_request(strategy, policy), user, session)
            saved.append(strategy.key)
            continue
        payoff = calculate_expiry_payoff(PayoffRequest(
            underlying="NIFTY", multiplier=Decimal(lot_size), price_min=Decimal(0),
            price_max=max(chain.underlying_spot, *(leg.strike for leg in legs)) * 2,
            grid_points=2, legs=legs,
        ))
        policy = None if payoff.maximum_loss_unbounded or payoff.maximum_loss is None else (
            template_policy(strategy.key, payoff.maximum_loss, None
                            if payoff.maximum_profit_unbounded else payoff.maximum_profit,
                            spread_cost)
        )
        if policy is None:
            skipped.append(strategy.key)
            continue
        await create(_version_request(strategy, policy), user, session)
        saved.append(strategy.key)
    return saved, skipped


def _version_request(strategy: StrategyDefinition, policy: TemplatePolicy,
                     ) -> StrategyVersionCreateRequest:
    return StrategyVersionCreateRequest(
        name=f"{FROZEN_NAME_PREFIX}{strategy.name} · {strategy.version} · 1L",
        strategy_key=strategy.key, expected_template_version=strategy.version, lots=1,
        entry_conditions=policy.entry_conditions, stop_loss_amount=policy.stop_loss,
        target_profit_amount=policy.target_profit, trailing_stop_amount=policy.trailing_stop,
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
    if today < plan.start:
        return "NOT_STARTED"
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
        frozen = {str(item) for item in plan.version_ids}
        attempted = {
            row.request_data.get("strategy_key")
            for row in session.scalars(select(PaperDeployment).where(
                PaperDeployment.user_id == user.id,
            ))
            if row.request_data.get("expiry") == expiry.isoformat()
            and row.request_data.get("strategy_version_id") in frozen
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


async def _propose_command(user_email: str, templates: frozenset[str] | None,
                           create_user: bool) -> None:
    from apps.api.auth import Role, hash_password
    from apps.api.main import create_strategy_version
    from packages.market_data.provider import get_market_data_provider

    email = user_email.strip().lower()
    with build_session_factory()() as session:
        user = session.scalar(select(User).where(User.email == email))
        if user is None and create_user:
            # Nobody knows this password; set one with apps.api.reset_password to sign in.
            user = User(email=email, password_hash=hash_password(secrets.token_urlsafe(32)),
                        role=Role.TRADER.value)
            session.add(user)
            session.commit()
        if user is None:
            raise ValueError("No account has that email")
        saved, skipped = await propose(
            session, user, get_market_data_provider(), create_strategy_version, exchange_today(),
            templates,
        )
    logger.info("Saved %d forward versions; skipped %s", len(saved), skipped or "none")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--interval", type=float, default=300.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--propose", action="store_true",
        help="Save 1-lot forward versions from current quotes, arm nothing, and exit",
    )
    parser.add_argument("--freeze", action="store_true", help="Freeze a new period and exit")
    parser.add_argument("--user-email")
    parser.add_argument("--templates", help="With --propose: comma-separated template keys only")
    parser.add_argument("--create-user", action="store_true",
                        help="With --propose: create the account if it does not exist")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--weeks", type=int, default=6)
    args = parser.parse_args()
    if args.propose:
        if not args.user_email:
            parser.error("--propose needs --user-email")
        templates = frozenset(args.templates.split(",")) if args.templates else None
        known = {item.key for item in STRATEGIES + STRATEGY_REGISTRY.calendars()}
        unknown = templates - known if templates else set()
        if unknown:
            parser.error(f"Unknown templates: {sorted(unknown)}")
        asyncio.run(_propose_command(args.user_email, templates, args.create_user))
        return
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
