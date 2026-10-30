"""Run with python -m apps.paper_worker.main; no browser or app JWT is required."""

import argparse
import asyncio
import logging
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from apps.api.services.futures_deployments import process_futures_deployments
from apps.api.services.futures_paper import monitor_futures_exits
from apps.api.services.option_deployments import process_option_deployments
from apps.api.services.paper_monitor import monitor_exits
from apps.api.services.strategy_eligibility import sweep_strategy_eligibility
from apps.api.settings import Settings, get_settings
from packages.database.futures_models import PaperFuturesTrade
from packages.database.models import PaperTrade
from packages.database.session import build_session_factory
from packages.market_data.contracts import MarketDataProvider
from packages.market_data.provider import get_market_data_provider
from packages.paper_trading.worker_lease import acquire, finish

logger = logging.getLogger("paper_worker")


async def run_cycle(
    factory: sessionmaker[Session],
    provider: MarketDataProvider,
    settings: Settings,
    owner: str | None = None,
) -> str:
    with factory() as session:
        token = acquire(session, owner or str(uuid4()), settings.paper_worker_lease_seconds)
    if token is None:
        return "BUSY"
    if not settings.paper_trading_enabled:
        with factory() as session:
            finish(session, token, "DISABLED", "Paper trading is disabled by configuration")
        return "DISABLED"

    async def process() -> tuple[int, int, int, list[str]]:
        with factory() as session:
            users = list(
                session.scalars(
                    select(PaperTrade.user_id)
                    .where(
                        PaperTrade.status == "OPEN",
                    )
                    .distinct()
                )
            )
        evaluated = closed = 0
        warnings: list[str] = []
        for user_id in users:
            with factory() as session:
                result = await monitor_exits(user_id, session, provider, settings, token)
                evaluated += result.evaluated_count
                closed += len(result.closed_trade_ids)
                warnings.extend(result.warnings)
        with factory() as session:
            futures_users = list(session.scalars(
                select(PaperFuturesTrade.user_id)
                .where(PaperFuturesTrade.status == "OPEN")
                .distinct()
            ))
        for user_id in futures_users:
            with factory() as session:
                futures_checked, futures_closed, futures_warnings = await monitor_futures_exits(
                    user_id, session, provider, settings, token
                )
                evaluated += futures_checked
                closed += len(futures_closed)
                warnings.extend(futures_warnings)
        # After exits, so a close that breaches a limit blocks this cycle's armed entries.
        warnings.extend(sweep_strategy_eligibility(factory, token, settings))
        option_checked, option_filled, option_warnings = await process_option_deployments(
            factory, provider, token, settings
        )
        evaluated += option_checked
        warnings.extend(option_warnings)
        futures_checked, futures_filled, futures_warnings = await process_futures_deployments(
            factory, provider, token, settings
        )
        evaluated += futures_checked
        warnings.extend(futures_warnings)
        return evaluated, closed, option_filled + futures_filled, warnings

    try:
        evaluated, closed, opened, warnings = await asyncio.wait_for(
            process(),
            timeout=settings.paper_worker_lease_seconds - 5,
        )
        status = "DEGRADED" if warnings else "HEALTHY"
        detail = (
            f"Evaluated {evaluated}; opened {opened}; closed {closed}; "
            f"warnings {len(warnings)}"
        )
        if warnings:
            # Monitor warnings are deliberately credential-free and safe to expose to
            # the authenticated owner. Keep one concise reason within the lease limit.
            detail += f". Reason: {warnings[0]}"
    except Exception as error:
        # Do not persist/log provider exceptions, credentials, or account identifiers.
        status, detail = "ERROR", f"Cycle failed ({type(error).__name__}); inspect worker setup"
        logger.error("%s", detail)
    with factory() as session:
        finish(session, token, status, detail)
    return status


async def serve(once: bool = False) -> None:
    settings = get_settings()
    factory = build_session_factory()
    owner = str(uuid4())
    while True:
        status = await run_cycle(factory, get_market_data_provider(), settings, owner)
        logger.info("Paper exit cycle: %s", status)
        if once:
            return
        await asyncio.sleep(settings.paper_worker_interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Independent paper exit worker (never live orders)"
    )
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(serve(args.once))
    except KeyboardInterrupt:
        logger.info("Worker stopped; dashboard health will become stale")


if __name__ == "__main__":
    main()
