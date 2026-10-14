"""Discover listed NSE/BSE index-option underlyings from Upstox's BOD file."""

import gzip
import json
from datetime import datetime, timezone
from io import BytesIO

import httpx
from pydantic import BaseModel

from packages.market_data.contracts import MarketDataProviderError

MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"


class OptionIndex(BaseModel):
    symbol: str
    instrument_key: str
    name: str
    exchange: str
    derivatives_exchange: str


def parse_option_indexes(rows: object) -> list[OptionIndex]:
    if not isinstance(rows, list):
        raise ValueError("Instrument master must be a list")
    found: dict[str, OptionIndex] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("underlying_type") != "INDEX":
            continue
        segment = row.get("segment")
        if segment not in {"NSE_FO", "BSE_FO"} or row.get("instrument_type") not in {"CE", "PE"}:
            continue
        symbol, key = row.get("underlying_symbol"), row.get("underlying_key")
        exchange = "NSE" if segment == "NSE_FO" else "BSE"
        if (
            not isinstance(symbol, str) or not 1 <= len(symbol) <= 32
            or not isinstance(key, str) or not key.startswith(exchange + "_INDEX|")
        ):
            raise ValueError("Invalid index-option identity")
        item = OptionIndex(
            symbol=symbol, instrument_key=key, name=key.split("|", 1)[1],
            exchange=exchange, derivatives_exchange="NFO" if exchange == "NSE" else "BFO",
        )
        if symbol in found and found[symbol] != item:
            raise ValueError("Conflicting index-option identity")
        found[symbol] = item
    return sorted(found.values(), key=lambda item: (item.symbol != "NIFTY", item.symbol))


_cache: tuple[datetime, list[OptionIndex]] | None = None


async def option_indexes() -> list[OptionIndex]:
    global _cache
    now = datetime.now(timezone.utc)
    if _cache and (now - _cache[0]).total_seconds() < 3600:
        return list(_cache[1])
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            async with client.stream("GET", MASTER_URL) as response:
                response.raise_for_status()
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 16_000_000:
                        raise ValueError("Instrument master exceeds download limit")
        with gzip.GzipFile(fileobj=BytesIO(raw)) as archive:
            decoded = archive.read(128_000_001)
        if len(decoded) > 128_000_000:
            raise ValueError("Instrument master exceeds decoded limit")
        result = parse_option_indexes(json.loads(decoded))
        if not result:
            raise ValueError("No listed index options")
    except (httpx.HTTPError, ValueError, OSError, EOFError) as error:
        raise MarketDataProviderError("Index-option catalog is unavailable") from error
    _cache = (now, result)
    return list(result)


async def resolve_option_index(underlying: str) -> OptionIndex:
    # Preserve existing NIFTY requests without adding a public-master dependency.
    if underlying.casefold() in {"nifty", "nifty 50", "nse_index|nifty 50"}:
        return OptionIndex(symbol="NIFTY", instrument_key="NSE_INDEX|Nifty 50",
                           name="Nifty 50", exchange="NSE", derivatives_exchange="NFO")
    for item in await option_indexes():
        if underlying.casefold() in {item.symbol.casefold(), item.instrument_key.casefold()}:
            return item
    raise MarketDataProviderError("Selected index has no listed NSE/BSE option contracts")
