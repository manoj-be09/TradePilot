"""Run with python -m apps.night_runner.main; paper trades the night condors under the override.

The owner chose on 3 November 2026 to trade `very_rich_night_condor` and
`very_rich_night_condor_920` before the rule engine's promotion bar is met. Trades go to a separate
account (`--setup` once), so the frozen 26-template test keeps its risk budget, and each is named
"not promoted". A version the engine demotes, retires or replaces is not traded.

At each rule's entry time it reads richness from the live chain and past 5-minute candles, then
opens the condor through the same guarded function as the web app: the kill switch, quote checks,
and the 2% per-trade and 10% daily limits all apply. The paper worker exits it at 15:10 on the next
session, or on expiry day. After the close it imports newly expired expiries into a private cache
that only this runner reads. Read-only provider calls; it places no broker orders. Simulated
results are not investment advice.
"""

import argparse
import asyncio
import json
import logging
import secrets
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from apps.api.paper_schemas import PaperTradeCreateRequest, PaperTradeCustomLegRequest
from packages.backtesting.book_loader import load_books
from packages.backtesting.published_strategies import Bar, Book, Kind, Market
from packages.backtesting.rich_premium import SessionMove, session_moves
from packages.backtesting.rule_engine import ENGINE_RULES, EngineRule
from packages.database.base import Base
from packages.database.history_models import HistoricalImportContract, HistoricalOptionBar
from packages.database.models import (
    LearningHypothesis,
    ResearchRuleVersion,
    User,
    UserNotification,
)
from packages.database.session import build_session_factory
from packages.history.importer import (
    HistoryProvider,
    ImportSettings,
    import_expiry,
    list_expiries,
)
from packages.learning.engine import tradable_rules
from packages.learning.features import Context, features_at, passes
from packages.market_data.contracts import (
    MarketDataProvider,
    MarketDataProviderError,
    MarketDataUnavailable,
    OptionChain,
    OptionContract,
)
from packages.paper_trading.night_condor import (
    NIGHT_EXIT_POLICY,
    STOPPED_STAGES,
    NightDecision,
    chain_book,
    decide,
    paper_name,
)
from packages.paper_trading.session import exchange_today

logger = logging.getLogger("night_runner")
IST = ZoneInfo("Asia/Kolkata")
DEFAULT_CONFIG = Path("work/night-override.json")
CACHE_DATABASE = Path("work/night-history.db")
WIDE_HISTORY = Path("work/history-wide.db")
HYPOTHESES = Path("work/learning/hypotheses.json")
# The decision bar plus the research's three retries, with a little room for a slow cycle.
ENTRY_WINDOW = timedelta(minutes=20)
IMPORT_AFTER = time(15, 45)
LOOKBACK = timedelta(days=50)
ACTIVE_LOOKBACK = timedelta(days=14)
STRIKE_WINDOW = Decimal("0.03")
PAUSE_SECONDS = 0.3
_OPEN, _CLOSE = time(9, 15), time(15, 30)
OpenTrade = Callable[..., Awaitable[Any]]


@dataclass
class DayState:
    day: date
    moves: list[SessionMove] | None = None
    sessions: dict[date, bool] = field(default_factory=dict)
    decisions: dict[str, NightDecision] = field(default_factory=dict)
    finished: set[str] = field(default_factory=set)
    imported: bool = False


def custom_name(rule: EngineRule) -> str:
    return paper_name(rule.key, rule.version, rule.override)


def quote_evidence(chain: OptionChain, strikes: Sequence[Decimal]) -> list[str]:
    """Last price, mid and quote age of each leg, to measure how far live drifts from research."""
    kinds = ("CE", "PE", "CE", "PE")
    found = {(item.strike, item.option_type): item for item in chain.contracts}
    lines = []
    for strike, kind in zip(strikes, kinds, strict=True):
        item = found.get((strike, kind))
        if item is None:
            lines.append(f"{kind}{strike} missing")
            continue
        quote = item.quote
        mid = (quote.bid + quote.ask) / 2 if quote.bid and quote.ask else None
        traded = quote.last_trade_time
        age = (chain.as_of - traded).total_seconds() if traded else None
        lines.append(f"{kind}{strike} ltp {quote.last_price} mid {mid} age "
                     f"{'?' if age is None else f'{age:.0f}s'}")
    return lines


def condor_request(expiry: date, strikes: Sequence[Decimal]) -> PaperTradeCreateRequest:
    call, put, far_call, far_put = strikes
    legs = [("CE", "SELL", call), ("PE", "SELL", put), ("CE", "BUY", far_call),
            ("PE", "BUY", far_put)]
    return PaperTradeCreateRequest(
        strategy_key="custom", expiry=expiry, lots=1,
        custom_legs=[PaperTradeCustomLegRequest(option_type=kind, side=side, strike=strike)
                     for kind, side, strike in legs],
    )


class NightRunner:
    def __init__(
        self, factory: sessionmaker[Session], provider: MarketDataProvider, user_email: str,
        database_urls: Sequence[str], cache: sessionmaker[Session], open_trade: OpenTrade,
        rules: Sequence[EngineRule] = ENGINE_RULES,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        alert_email: str | None = None,
        rules_source: Callable[[], Sequence[EngineRule]] | None = None,
    ) -> None:
        self.factory, self.provider, self.user_email = factory, provider, user_email
        self.database_urls, self.cache, self.open_trade = list(database_urls), cache, open_trade
        self.rules, self.sleep, self.alert_email = list(rules), sleep, alert_email
        # With v2 applied, the learning ledger names the rules each morning.
        self.rules_source = rules_source
        self.context = Context()
        self.state = DayState(date.min)
        self.alerted: set[tuple[date, str]] = set()

    def alert(self, topic: str, title: str, message: str) -> None:
        """One phone alert per topic per day, through the relay's account; logged regardless."""
        logger.warning("%s: %s", title, message)
        key = (self.state.day, topic)
        if self.alert_email is None or key in self.alerted:
            return
        self.alerted.add(key)
        with self.factory() as session:
            user_id = session.scalar(select(User.id).where(
                User.email == self.alert_email.strip().lower()))
            if user_id is None:
                return
            session.add(UserNotification(user_id=user_id, event_type="night_runner." + topic,
                                         title=title[:160], message=message[:512], details={}))
            session.commit()

    async def _trades(self, day: date) -> bool:
        if day not in self.state.sessions:
            self.state.sessions[day] = await self.provider.get_nfo_session(day) is not None
        return self.state.sessions[day]

    async def _neighbour(self, day: date, step: int) -> date | None:
        for offset in range(1, 11):
            candidate = day + timedelta(days=step * offset)
            if await self._trades(candidate):
                return candidate
        return None

    async def _past_moves(
        self, today: date, spot: Decimal, contracts: Sequence[OptionContract],
    ) -> list[SessionMove]:
        """Expired expiries from local history; the two nearest live ones fetched from Upstox."""
        bars: dict[date, dict[tuple[Decimal, Kind], dict[datetime, Bar]]] = {}
        loaded = load_books(self.database_urls[0], date.max, self.database_urls[1:],
                            regular_hours_only=True, first_expiry=today - LOOKBACK)
        for book in loaded:
            bars[book.expiry] = book.bars
        active = sorted({item.expiry for item in contracts if item.expiry >= today})[:2]
        start = datetime.combine(today - ACTIVE_LOOKBACK, time.min, IST)
        end = datetime.combine(today - timedelta(days=1), time.max, IST)
        for item in sorted(contracts, key=lambda c: (c.expiry, c.strike, c.option_type)):
            if (item.expiry not in active or item.option_type not in ("CE", "PE")
                    or abs(item.strike - spot) > spot * STRIKE_WINDOW):
                continue
            kind: Kind = "CE" if item.option_type == "CE" else "PE"
            series = bars.setdefault(item.expiry, {}).setdefault((item.strike, kind), {})
            for bar in await self.provider.get_historical(item.instrument_key, start, end, "5m"):
                moment = bar.timestamp.astimezone(IST)
                if moment.date() < today and _OPEN <= moment.time() < _CLOSE:
                    series[moment] = Bar(float(bar.open), float(bar.high), float(bar.low),
                                         float(bar.close))
            await self.sleep(PAUSE_SECONDS)
        market = Market([Book(expiry, by_key) for expiry, by_key in bars.items()], lambda _: 0.0)
        return [move for move in session_moves(market) if move.session < today]

    def _stopped(self, rule: EngineRule) -> str | None:
        """Stopped by either loop: the v1 rule engine or, once applied, the v2 ledger."""
        with self.factory() as session:
            stages = [session.scalar(select(ResearchRuleVersion.stage).where(
                ResearchRuleVersion.rule_key == rule.key,
                ResearchRuleVersion.version == rule.version))]
            if self.rules_source is not None:
                stages.append(session.scalar(select(LearningHypothesis.stage).where(
                    LearningHypothesis.key == rule.key)))
        return next((stage for stage in stages if stage in STOPPED_STAGES), None)

    async def _decide(
        self, rule: EngineRule, chain: OptionChain, decided_at: datetime,
    ) -> NightDecision:
        today = decided_at.date()
        sessions = [today + timedelta(days=offset)
                    for offset in range((chain.expiry - today).days + 1)]
        sessions = [day for day in sessions if day == today or await self._trades(day)]
        previous = await self._neighbour(today, -1)
        following = await self._neighbour(today, 1)
        if previous is None or self.state.moves is None:
            return NightDecision(None, None, "No previous session found; no trade")
        book = chain_book(chain, decided_at, sessions)
        filters = rule.params.get("filters", [])
        if filters:
            values = features_at(book, await self._following_book(chain, decided_at), decided_at,
                                 self.state.moves, following, self.context)
            if not passes(filters, values):
                shown = {name: values.get(name) for name in {item["feature"] for item in filters}}
                # No richness means a data gap, which alerts; an unmet filter does not.
                gap = any(value is None for value in shown.values())
                return NightDecision(None, None if gap else 0.0,
                                     f"Filters not met {shown}; no trade")
        return decide(rule, self.state.moves, book, decided_at, previous, following)

    async def _following_book(self, chain: OptionChain, decided_at: datetime) -> Book | None:
        """The next expiry's chain as bars, for the term-structure feature; None if unreadable."""
        contracts = await self.provider.get_option_contracts("NIFTY")
        later = sorted({item.expiry for item in contracts if item.expiry > chain.expiry})
        if not later:
            return None
        second = await self.provider.get_option_chain("NIFTY", later[0])
        today = decided_at.date()
        days = [today + timedelta(days=offset) for offset in range((later[0] - today).days + 1)]
        days = [day for day in days if day == today or await self._trades(day)]
        return chain_book(second, decided_at, days)

    async def _open(
        self, rule: EngineRule, expiry: date, decision: NightDecision, today: date,
    ) -> str:
        if decision.strikes is None:
            return "NO_TRADE"
        key = f"night_{rule.key}_v{rule.version}_{today:%Y%m%d}"
        with self.factory() as session:
            user = session.scalar(select(User).where(User.email == self.user_email))
            if user is None or not user.active:
                logger.error("The override account is missing or inactive")
                return "NO_ACCOUNT"
            try:
                await self.open_trade(
                    condor_request(expiry, decision.strikes), user, session, key, self.provider,
                    exit_policy=NIGHT_EXIT_POLICY, custom_name=custom_name(rule),
                )
            except HTTPException as error:
                session.rollback()
                if error.status_code in {404, 422}:
                    self.state.finished.add(rule.key)
                self.alert(f"blocked.{rule.key}", "Night condor not opened",
                           f"{rule.key}: {error.status_code} {error.detail}")
                return "BLOCKED"
        self.state.finished.add(rule.key)
        self.alert(f"opened.{rule.key}", "Night condor opened (paper, not promoted)",
                   f"{rule.key}: strikes {', '.join(map(str, decision.strikes))}; "
                   f"{decision.reason}")
        return "OPENED"

    async def import_expired(self, today: date) -> None:
        engine = self.cache.kw["bind"]
        if isinstance(engine, Engine) and engine.dialect.name == "sqlite":
            Base.metadata.create_all(engine, tables=[
                Base.metadata.tables[model.__tablename__]
                for model in (HistoricalOptionBar, HistoricalImportContract)
            ])
        history = cast(HistoryProvider, self.provider)
        for expiry in await list_expiries(history, today - LOOKBACK, today, today):
            summary = await import_expiry(history, self.cache, expiry, ImportSettings())
            logger.info("Cache %s: %s planned, %s already done, %s", expiry, summary.planned,
                        summary.skipped, summary.detail or "ok")

    async def cycle(self, now: datetime) -> str:
        today = exchange_today(now)
        if self.state.day != today:
            self.state = DayState(today)
            if self.rules_source is not None:
                self.rules = list(self.rules_source())
                self.context = Context.load(self.database_urls[0])
                logger.info("Rules today: %s", ", ".join(rule.key for rule in self.rules)
                            or "none")
        market = await self.provider.get_nfo_session(today)
        if market is None:
            return "NO_SESSION"
        if now.astimezone(IST).time() >= IMPORT_AFTER:
            if not self.state.imported:
                self.state.imported = True
                try:
                    await self.import_expired(today)
                except Exception as error:  # noqa: BLE001 - tomorrow still needs an alert
                    self.alert("import", "Night runner cache import failed",
                               f"{type(error).__name__}; tomorrow may lack past sessions")
            return "AFTER_CLOSE"
        if not market.start <= now <= market.end:
            return "CLOSED"
        pending = [
            (rule, decided_at) for rule in self.rules
            if rule.key not in self.state.finished and now < (decided_at := datetime.combine(
                today, time.fromisoformat(rule.params["entry_time"]), IST)) + ENTRY_WINDOW
        ]
        if not pending:
            return "DONE"
        due = [(rule, decided_at) for rule, decided_at in pending if decided_at <= now]
        if not due and self.state.moves is not None:
            return "WAITING"
        contracts = await self.provider.get_option_contracts("NIFTY")
        expiries = sorted({item.expiry for item in contracts if item.expiry >= today})
        if not expiries:
            return "NO_EXPIRY"
        chain = await self.provider.get_option_chain("NIFTY", expiries[0])
        if chain.underlying_spot is None or (chain.quality and chain.quality.status == "FAIL"):
            return "NO_DATA"
        if self.state.moves is None:
            # Fetched once a day at the open, before the first entry time.
            self.state.moves = await self._past_moves(today, chain.underlying_spot, contracts)
        results = []
        for rule, decided_at in due:
            stage = self._stopped(rule)
            if stage is not None:
                self.state.finished.add(rule.key)
                logger.warning("%s is %s by the rule engine; not traded", rule.key, stage)
                continue
            decision = self.state.decisions.get(rule.key)
            if decision is None:
                decision = self.state.decisions[rule.key] = await self._decide(
                    rule, chain, decided_at)
                logger.warning("%s: %s", rule.key, decision.reason)
                if decision.strikes is not None:
                    logger.info("%s quotes: %s", rule.key,
                                "; ".join(quote_evidence(chain, decision.strikes)))
                elif decision.richness is None:
                    # Gaps, not a quiet market: no trade for a data reason deserves a look.
                    self.alert(f"data.{rule.key}", "Night condor skipped for missing data",
                               f"{rule.key}: {decision.reason}")
            if decision.strikes is None:
                self.state.finished.add(rule.key)
                continue
            results.append(await self._open(rule, chain.expiry, decision, today))
        return ", ".join(results) or ("DECIDED" if due else "READY")


def save_config(path: Path, user_email: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"user_email": user_email}, indent=2) + "\n", encoding="utf-8")


def setup(factory: sessionmaker[Session], config: Path, user_email: str) -> bool:
    """Create the separate paper account's user if needed; True when it was created."""
    from apps.api.auth import Role, hash_password

    email = user_email.strip().lower()
    with factory() as session:
        created = session.scalar(select(User.id).where(User.email == email)) is None
        if created:
            # Nobody knows this password; set one with apps.api.reset_password to sign in.
            session.add(User(email=email, password_hash=hash_password(secrets.token_urlsafe(32)),
                             role=Role.TRADER.value))
            session.commit()
    save_config(config, email)
    return created


async def serve(config: Path, interval_seconds: float, once: bool) -> None:
    from apps.api.main import _open_paper_trade_with_provider
    from apps.api.settings import get_settings
    from packages.market_data.provider import get_market_data_provider

    if not config.exists():
        logger.info("No %s; the night condor override is off", config)
        return
    user_email = json.loads(config.read_text(encoding="utf-8"))["user_email"]
    urls = [get_settings().database_url] + [
        f"sqlite:///{path.as_posix()}" for path in (WIDE_HISTORY, CACHE_DATABASE) if path.exists()
    ]
    factory = build_session_factory()

    def ledger_rules() -> list[EngineRule]:
        with factory() as session:
            return tradable_rules(session)

    runner = NightRunner(
        factory, get_market_data_provider(), user_email, urls,
        build_session_factory(f"sqlite:///{CACHE_DATABASE.as_posix()}"),
        _open_paper_trade_with_provider, alert_email=get_settings().telegram_alert_user_email,
        # v2 applied: once the hypothesis file exists, the ledger replaces the v1 override list.
        rules_source=ledger_rules if HYPOTHESES.exists() else None,
    )
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
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--setup", action="store_true",
                        help="Create the separate paper account's user, save the config, and exit")
    parser.add_argument("--user-email")
    args = parser.parse_args()
    if args.setup:
        if not args.user_email:
            parser.error("--setup needs --user-email")
        created = setup(build_session_factory(), args.config, args.user_email)
        logger.info("Override account %s; config saved to %s",
                    "created" if created else "already existed", args.config)
        return
    asyncio.run(serve(args.config, args.interval, args.once))


if __name__ == "__main__":
    main()
