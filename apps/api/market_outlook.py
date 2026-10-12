"""Read-only, provider-neutral descriptive NIFTY conditions."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter

from apps.api.auth import CurrentUser
from packages.market_data.contracts import (
    DiscoveredInstrument,
    MarketDataProvider,
    MarketDataProviderError,
    MarketDataUnavailable,
)
from packages.market_data.provider import get_market_data_provider
from packages.regime.outlook import MarketOutlook, describe_market, unavailable

router = APIRouter(prefix="/analytics", tags=["analytics"])
IST = ZoneInfo("Asia/Kolkata")


async def nifty_benchmark(provider: MarketDataProvider) -> DiscoveredInstrument | None:
    candidates = await provider.search_instruments("NIFTY 50", exchange="NSE", segment="INDEX")
    return next((
        row for row in candidates
        if row.exchange == "NSE" and row.instrument_type == "INDEX"
        and (row.name or "").casefold() == "nifty 50"
    ), None)


async def latest_completed_session(provider: MarketDataProvider, now: datetime) -> date | None:
    for days_ago in range(10):
        candidate = now.astimezone(IST).date() - timedelta(days=days_ago)
        session = await provider.get_equity_session(candidate)
        if (
            session is not None and session.exchange == "NSE"
            and session.end.tzinfo is not None
            and session.end.astimezone(IST).date() == candidate
            and now >= session.end + timedelta(minutes=10)
        ):
            return candidate
    return None


@router.get("/market-outlook", response_model=MarketOutlook)
async def market_outlook(user: CurrentUser) -> MarketOutlook:
    del user
    provider = get_market_data_provider()
    provider_id = provider.provider_id
    try:
        benchmark = await nifty_benchmark(provider)
        if benchmark is None:
            return unavailable(
                provider_id, "", "NIFTY 50 is not available from this data provider."
            )
        now = datetime.now(timezone.utc)
        expected = await latest_completed_session(provider, now)
        if expected is None:
            return unavailable(
                provider_id, benchmark.instrument_key,
                "Could not verify a recent completed NSE session.",
            )
        bars = await provider.get_historical(
            benchmark.instrument_key, now - timedelta(days=300), now, "1d"
        )
        return describe_market(
            bars, provider=provider_id, instrument_key=benchmark.instrument_key,
            expected_session=expected,
        )
    except (MarketDataUnavailable, MarketDataProviderError):
        return unavailable(
            provider_id, "", "Market history or NSE session verification is unavailable."
        )
