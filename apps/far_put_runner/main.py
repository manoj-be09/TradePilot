"""Run with python -m apps.far_put_runner.main; paper trades the hedged far put spread.

The owner chose on 15 November 2026 to paper trade `far_put_spread_5d_300` forward before it has
earned it, on its own account (`--setup` once). The rule is the research function
`run_far_put_spread(0.05, 300, 3)`: on the first session within three days of expiry, at 09:20, sell
the put nearest 5 delta and buy the put 300 points below, then hold to the expiry-day square-off at
15:05. No stop or target. It opens through the same guarded function as the web app, so the kill
switch, quote checks and risk limits apply. Read-only market data; it places no broker orders.
Simulated and unproven; not investment advice.
"""

import argparse
import asyncio
import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from apps.api.paper_schemas import PaperTradeCreateRequest, PaperTradeCustomLegRequest
from apps.night_runner.main import OpenTrade, setup
from packages.backtesting.global_strategies import strike_at_delta
from packages.database.models import User
from packages.database.session import build_session_factory
from packages.market_data.contracts import (
    MarketDataProvider,
    MarketDataProviderError,
    MarketDataUnavailable,
    OptionChain,
)
from packages.paper_trading.night_condor import chain_book, paper_name
from packages.paper_trading.option_policy import OptionExitPolicy
from packages.paper_trading.session import exchange_today

logger = logging.getLogger("far_put_runner")
IST = ZoneInfo("Asia/Kolkata")
CONFIG = Path("work/far-put-paper.json")
RULE_KEY = "far_put_spread_5d_300"
SHORT_DELTA, WING_POINTS, DAYS_BEFORE = 0.05, Decimal(300), 3
ENTRY = time(9, 20)
# The research decision bar plus its three 5-minute retries, with room for a slow cycle.
ENTRY_WINDOW = timedelta(minutes=20)
EXIT_POLICY = OptionExitPolicy(25)  # 15:05 on expiry day, the research square-off bar
NAME = paper_name(RULE_KEY, 1)


def choose_strikes(chain: OptionChain, decided_at: datetime) -> tuple[Decimal, Decimal] | None:
    """The sold 5-delta put and the bought put 300 below; None means no trade, never a naked put."""
    if chain.underlying_spot is None:
        return None
    book = chain_book(chain, decided_at, (decided_at.date(), chain.expiry))
    sold = strike_at_delta(book, "PE", decided_at, float(chain.underlying_spot), SHORT_DELTA)
    if sold is None or (sold - WING_POINTS, "PE") not in book.bars:
        return None
    return sold, sold - WING_POINTS


def spread_request(expiry: date, strikes: tuple[Decimal, Decimal]) -> PaperTradeCreateRequest:
    sold, bought = strikes
    return PaperTradeCreateRequest(strategy_key="custom", expiry=expiry, lots=1, custom_legs=[
        PaperTradeCustomLegRequest(option_type="PE", side="SELL", strike=sold),
        PaperTradeCustomLegRequest(option_type="PE", side="BUY", strike=bought),
    ])


class FarPutRunner:
    def __init__(self, factory: sessionmaker[Session], provider: MarketDataProvider,
                 user_email: str, open_trade: OpenTrade) -> None:
        self.factory, self.provider, self.open_trade = factory, provider, open_trade
        self.user_email = user_email.strip().lower()
        self.finished: date | None = None

    async def _entry_day(self, expiry: date, today: date) -> bool:
        """`_entry_session` live: today is the first session on or after expiry minus 3 days."""
        first = expiry - timedelta(days=DAYS_BEFORE)
        if not first <= today < expiry:
            return False
        day = first
        while day < today:
            if await self.provider.get_nfo_session(day) is not None:
                return False
            day += timedelta(days=1)
        return True

    async def cycle(self, now: datetime) -> str:
        today = exchange_today(now)
        if self.finished == today:
            return "DONE"
        market = await self.provider.get_nfo_session(today)
        if market is None:
            return "NO_SESSION"
        start = datetime.combine(today, ENTRY, IST)
        if now < start:
            return "WAITING" if market.start <= now else "CLOSED"
        if now > start + ENTRY_WINDOW or now > market.end:
            self.finished = today
            return "WINDOW_PASSED"
        contracts = await self.provider.get_option_contracts("NIFTY")
        expiry = min((item.expiry for item in contracts if item.expiry > today), default=None)
        if expiry is None or not await self._entry_day(expiry, today):
            self.finished = today
            return "NOT_ENTRY_DAY"
        chain = await self.provider.get_option_chain("NIFTY", expiry)
        if chain.expiry != expiry or (chain.quality and chain.quality.status == "FAIL"):
            return "NO_DATA"
        strikes = choose_strikes(chain, now)
        if strikes is None:
            return "NO_STRIKES"  # retried each cycle until the window closes
        return await self._open(expiry, strikes, today)

    async def _open(self, expiry: date, strikes: tuple[Decimal, Decimal], today: date) -> str:
        with self.factory() as session:
            user = session.scalar(select(User).where(User.email == self.user_email))
            if user is None or not user.active:
                logger.error("The far put account is missing or inactive")
                return "NO_ACCOUNT"
            try:
                await self.open_trade(spread_request(expiry, strikes), user, session,
                                      f"far_put_v1_{expiry:%Y%m%d}", self.provider,
                                      exit_policy=EXIT_POLICY, custom_name=NAME)
            except HTTPException as error:
                session.rollback()
                logger.warning("Far put spread not opened: %s %s", error.status_code,
                               error.detail)
                return "BLOCKED"  # the idempotency key makes a retry safe
        self.finished = today
        logger.warning("Far put spread opened (paper, not promoted): sell %s, buy %s PE, %s",
                       *strikes, expiry)
        return "OPENED"


async def serve(config: Path, interval_seconds: float, once: bool) -> None:
    from apps.api.main import _open_paper_trade_with_provider
    from packages.market_data.provider import get_market_data_provider

    if not config.exists():
        logger.info("No %s; the far put spread is off", config)
        return
    data: dict[str, Any] = json.loads(config.read_text(encoding="utf-8"))
    runner = FarPutRunner(build_session_factory(), get_market_data_provider(),
                          data["user_email"], _open_paper_trade_with_provider)
    while True:
        try:
            status = await runner.cycle(datetime.now(timezone.utc))
        except (MarketDataUnavailable, MarketDataProviderError) as error:
            status = f"NO_DATA ({type(error).__name__})"
        logger.info("Cycle: %s", status)
        if once:
            return
        await asyncio.sleep(interval_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--setup", action="store_true",
                        help="Create the separate paper account's user, save the config, and exit")
    parser.add_argument("--user-email")
    args = parser.parse_args()
    if args.setup:
        if not args.user_email:
            parser.error("--setup needs --user-email")
        created = setup(build_session_factory(), args.config, args.user_email)
        logger.info("Far put account %s; config saved to %s",
                    "created" if created else "already existed", args.config)
        return
    asyncio.run(serve(args.config, args.interval, args.once))


if __name__ == "__main__":
    main()
