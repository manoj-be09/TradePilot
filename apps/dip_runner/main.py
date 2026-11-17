"""Run with python -m apps.dip_runner.main; paper trades the buy-the-dip call debit spread.

The owner chose on 17 November 2026 to paper trade this rule forward on its own account (`--setup`
once) although its one confirmation run failed (2023-26: 29 trades, t 0.46). The rule is the private
study's `dip(-0.025, spread=True)`: when NIFTY is at least 2.5% below its close five sessions ago,
buy near the close the ATM call and sell the call 2% higher (both rounded to 50) of the first expiry
at least five sessions away, then hold to the expiry-day square-off. One trade per expiry. It opens
through the same guarded function as the web app, so the kill switch, quote checks and risk limits
apply. Read-only market data; it places no broker orders. Simulated and unproven; not advice.
"""

import argparse
import asyncio
import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from apps.api.paper_schemas import PaperTradeCreateRequest, PaperTradeCustomLegRequest
from apps.night_runner.main import OpenTrade, setup
from packages.database.models import User
from packages.database.session import build_session_factory
from packages.market_data.contracts import (
    MarketDataProvider,
    MarketDataProviderError,
    MarketDataUnavailable,
    OptionChain,
)
from packages.paper_trading.night_condor import paper_name
from packages.paper_trading.option_policy import OptionExitPolicy
from packages.paper_trading.session import exchange_today

logger = logging.getLogger("dip_runner")
IST = ZoneInfo("Asia/Kolkata")
CONFIG = Path("work/dip-spread-paper.json")
RULE_KEY = "dip_call_spread_2_5"
NIFTY_INDEX_KEY = "NSE_INDEX|Nifty 50"
FALL, LOOKBACK, MIN_SESSIONS, WIDTH = Decimal("-0.025"), 5, 5, Decimal("1.02")
ENTRY = time(15, 15)  # the research entered at the close; this is the last calm bar before it
ENTRY_WINDOW = timedelta(minutes=10)
EXIT_POLICY = OptionExitPolicy(10)  # 15:20 on expiry day, near the research expiry close
NAME = paper_name(RULE_KEY, 1)


def strike_near(value: Decimal) -> Decimal:
    return (value / 50).quantize(Decimal(1), rounding=ROUND_HALF_EVEN) * 50


def fell_enough(spot: Decimal, closes: list[Decimal]) -> bool:
    """`closes`: completed sessions before today, oldest first; the spot stands for today."""
    return len(closes) >= LOOKBACK and spot / closes[-LOOKBACK] - 1 <= FALL


def choose_strikes(chain: OptionChain) -> tuple[Decimal, Decimal] | None:
    """The bought ATM call and the sold call 2% higher; None means no trade, never a naked call."""
    if chain.underlying_spot is None:
        return None
    bought, sold = strike_near(chain.underlying_spot), strike_near(chain.underlying_spot * WIDTH)
    calls = {item.strike for item in chain.contracts if item.option_type == "CE"}
    if bought not in calls or sold not in calls or sold <= bought:
        return None
    return bought, sold


def spread_request(expiry: date, strikes: tuple[Decimal, Decimal]) -> PaperTradeCreateRequest:
    bought, sold = strikes
    return PaperTradeCreateRequest(strategy_key="custom", expiry=expiry, lots=1, custom_legs=[
        PaperTradeCustomLegRequest(option_type="CE", side="BUY", strike=bought),
        PaperTradeCustomLegRequest(option_type="CE", side="SELL", strike=sold),
    ])


class DipRunner:
    def __init__(self, factory: sessionmaker[Session], provider: MarketDataProvider,
                 user_email: str, open_trade: OpenTrade) -> None:
        self.factory, self.provider, self.open_trade = factory, provider, open_trade
        self.user_email = user_email.strip().lower()
        self.finished: date | None = None

    async def _closes(self, today: date) -> list[Decimal]:
        start = datetime.combine(today - timedelta(days=20), time(0), IST)
        end = datetime.combine(today - timedelta(days=1), time(23, 59), IST)
        bars = await self.provider.get_historical(NIFTY_INDEX_KEY, start, end, "1d")
        bars = sorted((bar for bar in bars if bar.timestamp.astimezone(IST).date() < today),
                      key=lambda bar: bar.timestamp)
        return [bar.close for bar in bars]

    async def _sessions_after(self, today: date, expiry: date) -> int:
        count, day = 0, today + timedelta(days=1)
        while day <= expiry:
            if await self.provider.get_nfo_session(day) is not None:
                count += 1
            day += timedelta(days=1)
        return count

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
        expiry = None
        for candidate in sorted({item.expiry for item in contracts if item.expiry > today}):
            if await self._sessions_after(today, candidate) >= MIN_SESSIONS:
                expiry = candidate
                break
        if expiry is None:
            return "NO_DATA"
        chain = await self.provider.get_option_chain("NIFTY", expiry)
        if chain.expiry != expiry or chain.underlying_spot is None or (
                chain.quality and chain.quality.status == "FAIL"):
            return "NO_DATA"
        closes = await self._closes(today)
        if len(closes) < LOOKBACK:
            return "NO_DATA"
        if not fell_enough(chain.underlying_spot, closes):
            self.finished = today
            return "NO_SIGNAL"
        strikes = choose_strikes(chain)
        if strikes is None:
            return "NO_STRIKES"  # retried each cycle until the window closes
        return await self._open(expiry, strikes, today)

    async def _open(self, expiry: date, strikes: tuple[Decimal, Decimal], today: date) -> str:
        with self.factory() as session:
            user = session.scalar(select(User).where(User.email == self.user_email))
            if user is None or not user.active:
                logger.error("The dip spread account is missing or inactive")
                return "NO_ACCOUNT"
            try:
                # One trade per expiry: the key repeats for every signal day aimed at it.
                await self.open_trade(spread_request(expiry, strikes), user, session,
                                      f"dip_v1_{expiry:%Y%m%d}", self.provider,
                                      exit_policy=EXIT_POLICY, custom_name=NAME)
            except HTTPException as error:
                session.rollback()
                logger.warning("Dip spread not opened: %s %s", error.status_code, error.detail)
                return "BLOCKED"  # the idempotency key makes a retry safe
        self.finished = today
        logger.warning("Dip call spread opened (paper, not promoted): buy %s, sell %s CE, %s",
                       *strikes, expiry)
        return "OPENED"


async def serve(config: Path, interval_seconds: float, once: bool) -> None:
    from apps.api.main import _open_paper_trade_with_provider
    from packages.market_data.provider import get_market_data_provider

    if not config.exists():
        logger.info("No %s; the dip spread is off", config)
        return
    data: dict[str, Any] = json.loads(config.read_text(encoding="utf-8"))
    runner = DipRunner(build_session_factory(), get_market_data_provider(),
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
        logger.info("Dip spread account %s; config saved to %s",
                    "created" if created else "already existed", args.config)
        return
    asyncio.run(serve(args.config, args.interval, args.once))


if __name__ == "__main__":
    main()
