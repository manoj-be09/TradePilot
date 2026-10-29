import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.database import history_models  # noqa: F401
from packages.database.base import Base
from packages.database.history_models import HistoricalImportContract, HistoricalOptionBar
from packages.history.importer import (
    HistoryImportError,
    ImportSettings,
    import_expiry,
    list_expiries,
    select_contracts,
    validate_bars,
)
from packages.market_data.contracts import (
    ExpiredOptionContract,
    HistoricalBar,
    MarketDataProviderError,
    MarketDataRateLimited,
)

EXPIRY = date(2025, 4, 17)
SETTINGS = ImportSettings(interval="5m", strike_window_pct=Decimal(2), lookback_days=3,
                          pause_seconds=0)


def _contract(strike: int, option_type: str, expiry: date = EXPIRY) -> ExpiredOptionContract:
    return ExpiredOptionContract(
        instrument_key=f"NSE_FO|{strike}{option_type}|{expiry:%d-%m-%Y}",
        trading_symbol=f"NIFTY {strike} {option_type}", expiry=expiry, strike=strike,
        option_type=option_type, lot_size=75, tick_size=Decimal("0.05"),
    )


def _bar(day: date, minute: int, price: str = "100") -> HistoricalBar:
    moment = datetime(day.year, day.month, day.day, 4, 0, tzinfo=timezone.utc)
    value = Decimal(price)
    return HistoricalBar(
        timestamp=moment + timedelta(minutes=5 * minute), open=value, high=value + 2,
        low=value - 2, close=value, volume=10, open_interest=500,
    )


class Provider:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.empty: set[str] = set()
        self.broken: set[str] = set()
        self.error: set[str] = set()
        self.throttled: dict[str, int] = {}

    async def get_expired_option_expiries(self, underlying="NIFTY"):
        return [date(2025, 4, 3), date(2025, 4, 9), EXPIRY, date(2030, 1, 1)]

    async def get_historical(self, instrument_token, start, end, interval):
        assert instrument_token == "NSE_INDEX|Nifty 50" and interval == "1d"
        return [HistoricalBar(
            timestamp=datetime(2025, 4, 9, 18, 30, tzinfo=timezone.utc),
            open=23000, high=23100, low=22900, close=23000, volume=0,
        )]

    async def get_expired_option_contracts(self, underlying, expiry):
        return [_contract(strike, kind) for strike in (22000, 22550, 23000, 23450, 24000)
                for kind in ("CE", "PE")]

    async def get_expired_historical(self, instrument_key, start, end, interval):
        self.calls.append(instrument_key)
        if self.throttled.get(instrument_key, 0) > 0:
            self.throttled[instrument_key] -= 1
            raise MarketDataRateLimited("Upstox rate limit reached; wait before retrying")
        if instrument_key in self.error:
            raise MarketDataProviderError("Upstox rejected the request")
        if instrument_key in self.empty:
            return []
        if instrument_key in self.broken:
            return [_bar(start, 0), _bar(start, 0)]
        return [_bar(EXPIRY, 1), _bar(EXPIRY - timedelta(days=1), 0), _bar(EXPIRY, 0)]


@pytest.fixture
def factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        HistoricalOptionBar.__table__, HistoricalImportContract.__table__,
    ])
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _statuses(factory) -> dict[str, str]:
    with factory() as session:
        return {row.instrument_key: row.status
                for row in session.scalars(select(HistoricalImportContract))}


def test_import_selects_strike_window_and_stores_sorted_candles(factory):
    provider = Provider()
    summary = asyncio.run(import_expiry(provider, factory, EXPIRY, SETTINGS))
    # 2% of 23000 keeps 22550, 23000 and 23450: three strikes, both option types.
    assert summary.reference_spot == 23000
    assert summary.planned == 6 and summary.counts["COMPLETE"] == 6
    assert summary.bars == 18
    with factory() as session:
        rows = list(session.scalars(
            select(HistoricalOptionBar).where(
                HistoricalOptionBar.instrument_key == _contract(23000, "CE").instrument_key
            ).order_by(HistoricalOptionBar.bar_start)
        ))
        record = session.get(
            HistoricalImportContract, (_contract(23000, "CE").instrument_key, "5m")
        )
    assert [row.bar_start for row in rows] == sorted(row.bar_start for row in rows)
    assert record.status == "COMPLETE" and record.bar_count == 3 and record.lot_size == 75
    assert record.requested_from == EXPIRY - timedelta(days=3)


def test_rerun_resumes_and_retries_only_failed_contracts(factory):
    provider = Provider()
    failing = _contract(22550, "PE").instrument_key
    provider.error.add(failing)
    asyncio.run(import_expiry(provider, factory, EXPIRY, SETTINGS))
    assert _statuses(factory)[failing] == "FAILED"
    provider.error.clear()
    provider.calls.clear()
    summary = asyncio.run(import_expiry(provider, factory, EXPIRY, SETTINGS))
    assert provider.calls == [failing]
    assert summary.skipped == 5 and summary.counts["COMPLETE"] == 1
    assert _statuses(factory)[failing] == "COMPLETE"


def test_empty_and_invalid_contracts_store_no_candles(factory):
    provider = Provider()
    empty, broken = _contract(23000, "PE").instrument_key, _contract(23450, "CE").instrument_key
    provider.empty.add(empty)
    provider.broken.add(broken)
    asyncio.run(import_expiry(provider, factory, EXPIRY, SETTINGS))
    statuses = _statuses(factory)
    assert statuses[empty] == "EMPTY" and statuses[broken] == "FAILED"
    with factory() as session:
        stored = session.scalar(select(func.count()).select_from(HistoricalOptionBar).where(
            HistoricalOptionBar.instrument_key.in_([empty, broken])
        ))
        detail = session.get(HistoricalImportContract, (broken, "5m")).detail
    assert stored == 0
    assert "Duplicate" in detail


def test_force_reimport_replaces_contract_rows_without_duplicates(factory):
    provider = Provider()
    asyncio.run(import_expiry(provider, factory, EXPIRY, SETTINGS))
    forced = ImportSettings(interval="5m", strike_window_pct=Decimal(2), lookback_days=3,
                            pause_seconds=0, force=True)
    asyncio.run(import_expiry(provider, factory, EXPIRY, forced))
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(HistoricalOptionBar)) == 18


def test_unplannable_expiry_leaves_storage_untouched(factory):
    class NoSpot(Provider):
        async def get_historical(self, *args):
            return []

    summary = asyncio.run(import_expiry(NoSpot(), factory, EXPIRY, SETTINGS))
    assert summary.planned == 0 and "not planned" in summary.detail
    assert _statuses(factory) == {}


def test_validate_bars_rejects_out_of_range_and_bad_ohlc():
    start, end = EXPIRY - timedelta(days=3), EXPIRY
    assert validate_bars([_bar(EXPIRY + timedelta(days=1), 0)], start, end)
    bad = _bar(EXPIRY, 0).model_copy(update={"high": Decimal(1)})
    assert "OHLC" in (validate_bars([bad], start, end) or "")
    assert validate_bars([_bar(EXPIRY, 0)], start, end) is None


def test_select_contracts_rejects_duplicate_master_rows():
    duplicate = [_contract(23000, "CE"), _contract(23000, "CE")]
    with pytest.raises(HistoryImportError):
        select_contracts(duplicate, EXPIRY, Decimal(23000), Decimal(2))


def test_list_expiries_excludes_future_and_out_of_range():
    expiries = asyncio.run(list_expiries(Provider(), date(2025, 4, 5), date(2031, 1, 1),
                                         date(2026, 10, 28)))
    assert expiries == [date(2025, 4, 9), EXPIRY]


def test_rate_limits_back_off_and_retry_before_failing(factory):
    provider = Provider()
    recovers = _contract(23000, "CE").instrument_key
    gives_up = _contract(23000, "PE").instrument_key
    provider.throttled = {recovers: 2, gives_up: 99}
    waits: list[float] = []

    async def record_sleep(seconds):
        waits.append(seconds)

    settings = ImportSettings(interval="5m", strike_window_pct=Decimal(2), lookback_days=3,
                              pause_seconds=0, rate_limit_retries=3, rate_limit_wait_seconds=10)
    asyncio.run(import_expiry(provider, factory, EXPIRY, settings, sleep=record_sleep))
    statuses = _statuses(factory)
    assert statuses[recovers] == "COMPLETE"
    assert statuses[gives_up] == "FAILED"
    assert [wait for wait in waits if wait] == [10, 20, 10, 20, 40]
    assert provider.calls.count(gives_up) == 4
