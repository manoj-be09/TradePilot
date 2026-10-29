"""Import expired NIFTY option candles from Upstox into private local storage.

Read-only provider GETs only. For each expiry the importer selects strikes within a
percentage window of the NIFTY close about a week before expiry, fetches each contract's
candles for a bounded lookback, validates them, and stores them atomically per contract.
A contract with no candles is recorded EMPTY and one with invalid or failed data FAILED;
nothing is fabricated or forward-filled. COMPLETE and EMPTY contracts are skipped on
re-runs, so an interrupted import resumes where it stopped.
"""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from packages.database.history_models import HistoricalImportContract, HistoricalOptionBar
from packages.market_data.contracts import (
    ExpiredOptionContract,
    HistoricalBar,
    MarketDataProviderError,
    MarketDataRateLimited,
    MarketDataUnavailable,
)

SOURCE = "upstox_expired_v2"
NIFTY_INDEX_KEY = "NSE_INDEX|Nifty 50"
_IST = ZoneInfo("Asia/Kolkata")
ContractStatus = Literal["COMPLETE", "EMPTY", "FAILED"]


class HistoryProvider(Protocol):
    async def get_expired_option_expiries(self, underlying: str = "NIFTY") -> list[date]: ...

    async def get_expired_option_contracts(
        self, underlying: str, expiry: date
    ) -> list[ExpiredOptionContract]: ...

    async def get_expired_historical(
        self, instrument_key: str, start: date, end: date, interval: str
    ) -> list[HistoricalBar]: ...

    async def get_historical(
        self, instrument_token: str, start: datetime, end: datetime, interval: str
    ) -> list[HistoricalBar]: ...


class HistoryImportError(Exception):
    """An expiry cannot be planned; its contracts are left untouched."""


@dataclass(frozen=True)
class ImportSettings:
    interval: str = "5m"
    strike_window_pct: Decimal = Decimal(3)
    lookback_days: int = 10
    # About 1,800 requests per 30 minutes, under Upstox's 2,000-per-30-minute cap.
    pause_seconds: float = 1.0
    force: bool = False
    rate_limit_retries: int = 5
    rate_limit_wait_seconds: float = 60.0


@dataclass
class ExpirySummary:
    expiry: date
    reference_spot: Decimal | None = None
    planned: int = 0
    skipped: int = 0
    counts: dict[str, int] = field(default_factory=lambda: {"COMPLETE": 0, "EMPTY": 0, "FAILED": 0})
    bars: int = 0
    detail: str = ""


def _session_day(moment: datetime) -> date:
    return moment.astimezone(_IST).date()


async def reference_spot(provider: HistoryProvider, expiry: date) -> Decimal:
    """NIFTY daily close on the last session at least seven days before expiry."""
    target = expiry - timedelta(days=7)
    start = datetime.combine(target - timedelta(days=10), time.min, tzinfo=timezone.utc)
    end = datetime.combine(target, time.max, tzinfo=timezone.utc)
    bars = await provider.get_historical(NIFTY_INDEX_KEY, start, end, "1d")
    eligible = [bar for bar in bars if _session_day(bar.timestamp) <= target and bar.close > 0]
    if not eligible:
        raise HistoryImportError(f"No NIFTY daily close found before {target}")
    return max(eligible, key=lambda bar: bar.timestamp).close


def select_contracts(
    contracts: Sequence[ExpiredOptionContract], expiry: date, spot: Decimal, window_pct: Decimal,
) -> list[ExpiredOptionContract]:
    low = spot * (1 - window_pct / 100)
    high = spot * (1 + window_pct / 100)
    chosen = [
        item for item in contracts
        if item.expiry == expiry and low <= item.strike <= high
    ]
    identities = {(item.strike, item.option_type) for item in chosen}
    if len(identities) != len(chosen):
        raise HistoryImportError(f"Duplicate contracts in the {expiry} contract master")
    return sorted(chosen, key=lambda item: (item.strike, item.option_type))


def validate_bars(bars: Sequence[HistoricalBar], start: date, end: date) -> str | None:
    """Return a rejection reason, or None when every bar is usable."""
    seen: set[datetime] = set()
    for bar in bars:
        if bar.timestamp.tzinfo is None:
            return "A candle has no timezone."
        if not start <= _session_day(bar.timestamp) <= end:
            return "A candle falls outside the requested date range."
        if bar.timestamp in seen:
            return "Duplicate candle timestamps."
        seen.add(bar.timestamp)
        if not (bar.low <= min(bar.open, bar.close) and max(bar.open, bar.close) <= bar.high):
            return "A candle has inconsistent OHLC values."
    return None


def _record(
    session: Session, contract: ExpiredOptionContract, settings: ImportSettings,
    start: date, end: date, status: ContractStatus, bars: Sequence[HistoricalBar], detail: str,
    now: datetime,
) -> None:
    session.execute(delete(HistoricalOptionBar).where(
        HistoricalOptionBar.instrument_key == contract.instrument_key,
        HistoricalOptionBar.interval == settings.interval,
    ))
    if status == "COMPLETE":
        session.add_all([
            HistoricalOptionBar(
                instrument_key=contract.instrument_key, interval=settings.interval,
                bar_start=bar.timestamp, expiry=contract.expiry, strike=contract.strike,
                option_type=contract.option_type, open=bar.open, high=bar.high, low=bar.low,
                close=bar.close, volume=bar.volume, open_interest=bar.open_interest,
            )
            for bar in bars
        ])
    session.merge(HistoricalImportContract(
        instrument_key=contract.instrument_key, interval=settings.interval, source=SOURCE,
        trading_symbol=contract.trading_symbol, expiry=contract.expiry,
        strike=contract.strike, option_type=contract.option_type,
        lot_size=contract.lot_size, requested_from=start, requested_to=end, status=status,
        bar_count=len(bars) if status == "COMPLETE" else 0,
        first_bar=bars[0].timestamp if status == "COMPLETE" and bars else None,
        last_bar=bars[-1].timestamp if status == "COMPLETE" and bars else None,
        detail=detail[:300], imported_at=now,
    ))


async def _fetch_with_backoff(
    provider: HistoryProvider, contract: ExpiredOptionContract, start: date, end: date,
    settings: ImportSettings, sleep: Callable[[float], Any],
) -> list[HistoricalBar]:
    """Retry throttled requests after doubling waits; other errors fail at once."""
    wait = settings.rate_limit_wait_seconds
    for attempt in range(settings.rate_limit_retries + 1):
        try:
            return await provider.get_expired_historical(
                contract.instrument_key, start, end, settings.interval
            )
        except MarketDataRateLimited:
            if attempt == settings.rate_limit_retries:
                raise
            await sleep(wait)
            wait = min(wait * 2, 900.0)
    raise AssertionError("unreachable")


async def import_expiry(
    provider: HistoryProvider,
    factory: sessionmaker[Session],
    expiry: date,
    settings: ImportSettings,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> ExpirySummary:
    summary = ExpirySummary(expiry=expiry)
    try:
        spot = await reference_spot(provider, expiry)
        listed = await provider.get_expired_option_contracts("NIFTY", expiry)
        contracts = select_contracts(listed, expiry, spot, settings.strike_window_pct)
    except (HistoryImportError, MarketDataUnavailable, MarketDataProviderError) as error:
        summary.detail = f"Expiry not planned: {type(error).__name__}: {error}"[:300]
        return summary
    summary.reference_spot = spot
    summary.planned = len(contracts)
    with factory() as session:
        done = set() if settings.force else set(session.scalars(
            select(HistoricalImportContract.instrument_key).where(
                HistoricalImportContract.expiry == expiry,
                HistoricalImportContract.interval == settings.interval,
                HistoricalImportContract.status.in_(("COMPLETE", "EMPTY")),
            )
        ))
    start, end = expiry - timedelta(days=settings.lookback_days), expiry
    for contract in contracts:
        if contract.instrument_key in done:
            summary.skipped += 1
            continue
        status: ContractStatus
        bars: list[HistoricalBar] = []
        try:
            bars = sorted(
                await _fetch_with_backoff(provider, contract, start, end, settings, sleep),
                key=lambda bar: bar.timestamp,
            )
            reason = validate_bars(bars, start, end)
            if reason:
                status, detail = "FAILED", reason
            elif not bars:
                status, detail = "EMPTY", "Provider returned no candles for this range."
            else:
                status, detail = "COMPLETE", f"{len(bars)} candles"
        except (MarketDataUnavailable, MarketDataProviderError) as error:
            # Provider text is kept short; it never contains credentials.
            status, detail = "FAILED", f"{type(error).__name__}: {error}"
        with factory() as session:
            _record(session, contract, settings, start, end, status, bars, detail, clock())
            session.commit()
        summary.counts[status] += 1
        if status == "COMPLETE":
            summary.bars += len(bars)
        await sleep(settings.pause_seconds)
    return summary


async def list_expiries(
    provider: HistoryProvider, start: date, end: date, today: date,
) -> list[date]:
    expiries = await provider.get_expired_option_expiries("NIFTY")
    return sorted({item for item in expiries if start <= item <= end and item < today})
