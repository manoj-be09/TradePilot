import json
from collections.abc import AsyncIterator
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from google.protobuf.message import DecodeError
from pydantic import ValidationError
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from packages.market_data import upstox_v3_pb2
from packages.market_data.contracts import (
    DiscoveredInstrument,
    ExchangeSession,
    ExpiredOptionContract,
    HistoricalBar,
    MarketDataPlanRequired,
    MarketDataProviderError,
    OptionChain,
    OptionContract,
    OptionContractQuote,
    ProviderHealth,
    Quote,
)
from packages.market_data.index_catalog import resolve_option_index

UPSTOX_API_BASE = "https://api.upstox.com"
NIFTY_INDEX_KEY = "NSE_INDEX|Nifty 50"


UpstoxOptionContract = OptionContract


class UpstoxMarketDataProvider:
    """Read-only adapter for Upstox Analytics Token market-data endpoints."""

    provider_id = "upstox"

    def __init__(
        self,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = UPSTOX_API_BASE,
    ) -> None:
        self._token = token
        self._transport = transport
        self._base_url = base_url.rstrip("/")

    async def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self._token}",
                },
                timeout=15.0,
                transport=self._transport,
            ) as client:
                response = await client.get(path, params=params)
        except httpx.TimeoutException as error:
            raise MarketDataProviderError("Upstox request timed out") from error
        except httpx.HTTPError as error:
            raise MarketDataProviderError("Could not connect to Upstox") from error

        if response.status_code == 401 or response.status_code == 403:
            if path.startswith("/v2/expired-instruments/") and b"UDAPI1149" in response.content:
                raise MarketDataPlanRequired(
                    "Expired-contract history requires an Upstox Plus subscription"
                )
            raise MarketDataProviderError(
                f"Upstox rejected the token (HTTP {response.status_code}); "
                "check its validity and permissions"
            )
        if response.status_code == 429:
            raise MarketDataProviderError("Upstox rate limit reached; wait before retrying")
        if not response.is_success:
            raise MarketDataProviderError(f"Upstox returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as error:
            raise MarketDataProviderError("Upstox returned an invalid JSON response") from error
        if not isinstance(payload, dict) or payload.get("status") not in (None, "success"):
            raise MarketDataProviderError("Upstox returned an unsuccessful response")
        data = payload.get("data")
        if not isinstance(data, (dict, list)):
            raise MarketDataProviderError("Upstox response did not contain expected data")
        return data

    @staticmethod
    def _ensure_nifty(underlying: str) -> str:
        if underlying.casefold() in {"nifty", "nifty 50", NIFTY_INDEX_KEY.casefold()}:
            return NIFTY_INDEX_KEY
        raise MarketDataProviderError("This Upstox adapter currently supports NIFTY 50 only")

    async def search_instruments(
        self, query: str, *, exchange: str = "NSE", segment: str = "EQ"
    ) -> list[DiscoveredInstrument]:
        data = await self._get(
            "/v2/instruments/search",
            {"query": query, "exchanges": exchange, "segments": segment,
             "records": "20", "page_number": "1"},
        )
        if not isinstance(data, list):
            raise MarketDataProviderError("Upstox instrument search had an unexpected shape")
        try:
            return [DiscoveredInstrument.model_validate(row) for row in data]
        except ValidationError as error:
            raise MarketDataProviderError(
                "Upstox instrument search contained invalid contract metadata"
            ) from error

    async def get_nfo_session(self, trading_day: date) -> ExchangeSession | None:
        return await self._exchange_session(trading_day, "NFO")

    async def get_index_option_session(
        self, underlying: str, trading_day: date
    ) -> ExchangeSession | None:
        index = await resolve_option_index(underlying)
        return await self._exchange_session(trading_day, index.derivatives_exchange)

    async def get_equity_session(self, trading_day: date) -> ExchangeSession | None:
        return await self._exchange_session(trading_day, "NSE")

    async def get_recent_news(self, instrument_key: str) -> dict[str, Any]:
        """Observe current Upstox headlines; index coverage is not yet established."""
        data = await self._get(
            "/v2/news",
            {"category": "instrument_keys", "instrument_keys": instrument_key,
             "page_number": "1", "page_size": "10"},
        )
        received_at = datetime.now(timezone.utc)
        if not isinstance(data, dict) or (data and instrument_key not in data):
            raise MarketDataProviderError("Upstox news response had an unexpected shape")
        articles = data.get(instrument_key, [])
        if not isinstance(articles, list):
            raise MarketDataProviderError("Upstox news response had an unexpected shape")
        items: list[dict[str, str]] = []
        for item in articles:
            if not isinstance(item, dict):
                continue
            heading, link, published_ms = (
                item.get("heading"), item.get("article_link"), item.get("published_time")
            )
            if (not isinstance(heading, str) or not heading.strip()
                    or not isinstance(link, str) or type(published_ms) is not int):
                continue
            try:
                parsed = urlparse(link)
                valid_link = parsed.scheme == "https" and parsed.hostname == "upstox.com"
            except ValueError:
                valid_link = False
            if not valid_link or len(link) > 500:
                continue
            try:
                published_at = datetime.fromtimestamp(published_ms / 1000, timezone.utc)
            except (OSError, OverflowError, ValueError):
                continue
            if published_at > received_at or (received_at - published_at).days >= 7:
                continue
            items.append({
                "heading": heading.strip()[:240],
                "article_link": link,
                "published_at": published_at.isoformat(),
            })
        return {
            "source": "upstox_news_v2",
            "instrument_key": instrument_key,
            "received_at": received_at.isoformat(),
            "coverage_reviewed": False,
            "status": "OBSERVED_UNVERIFIED_COVERAGE" if items else "EMPTY_UNVERIFIED_COVERAGE",
            "items": items[:5],
        }

    async def _exchange_session(self, trading_day: date, exchange: str) -> ExchangeSession | None:
        data = await self._get(f"/v2/market/timings/{trading_day.isoformat()}")
        if not isinstance(data, list):
            raise MarketDataProviderError("Upstox market-timings response had an unexpected shape")
        matches = [
            row for row in data if isinstance(row, dict) and row.get("exchange") == exchange
        ]
        if not matches:
            return None
        if len(matches) != 1:
            raise MarketDataProviderError("Upstox returned duplicate NFO market timings")
        row = matches[0]
        start_ms, end_ms = row.get("start_time"), row.get("end_time")
        if type(start_ms) is not int or type(end_ms) is not int:
            raise MarketDataProviderError("Upstox NFO market timings are invalid")
        try:
            start = datetime.fromtimestamp(start_ms / 1000, timezone.utc)
            end = datetime.fromtimestamp(end_ms / 1000, timezone.utc)
        except (OverflowError, ValueError) as error:
            raise MarketDataProviderError("Upstox NFO market timings are invalid") from error
        zone = ZoneInfo("Asia/Kolkata")
        if (
            end <= start
            or (end - start).total_seconds() > 12 * 3600
            or start.astimezone(zone).date() != trading_day
            or end.astimezone(zone).date() != trading_day
        ):
            raise MarketDataProviderError("Upstox NFO market timings are invalid")
        return ExchangeSession(exchange=exchange, start=start, end=end)

    async def get_option_contracts(
        self, underlying: str = "NIFTY", expiry: date | None = None
    ) -> list[UpstoxOptionContract]:
        index = await resolve_option_index(underlying)
        params = {"instrument_key": index.instrument_key}
        if expiry is not None:
            params["expiry_date"] = expiry.isoformat()
        data = await self._get("/v2/option/contract", params)
        raw_contracts = data if isinstance(data, list) else data.get("data")
        if not isinstance(raw_contracts, list):
            raise MarketDataProviderError("Upstox option-contract response had an unexpected shape")
        for item in raw_contracts:
            if not isinstance(item, dict) or (
                not isinstance(item.get("instrument_key"), str)
                or not item["instrument_key"].startswith(index.exchange + "_FO|")
                or item.get("underlying_key") != index.instrument_key
                or (expiry is not None and item.get("expiry") != expiry.isoformat())
            ):
                raise MarketDataProviderError(
                    "Option contract does not match the selected index, expiry or derivatives venue"
                )
        keys = [item["instrument_key"] for item in raw_contracts]
        if len(keys) != len(set(keys)):
            raise MarketDataProviderError("Upstox returned duplicate option contract keys")
        try:
            return [
                UpstoxOptionContract(
                    instrument_key=item["instrument_key"],
                    trading_symbol=item["trading_symbol"],
                    expiry=item["expiry"],
                    strike=item["strike_price"],
                    option_type=item["instrument_type"],
                    lot_size=item["lot_size"],
                    tick_size=item["tick_size"],
                )
                for item in raw_contracts
                if isinstance(item, dict)
            ]
        except (KeyError, TypeError, ValueError) as error:
            raise MarketDataProviderError(
                "Upstox option-contract response contained an invalid contract"
            ) from error

    async def get_expired_option_expiries(self, underlying: str = "NIFTY") -> list[date]:
        data = await self._get(
            "/v2/expired-instruments/expiries",
            {"instrument_key": self._ensure_nifty(underlying)},
        )
        if not isinstance(data, list):
            raise MarketDataProviderError(
                "Upstox expired-expiries response had an unexpected shape"
            )
        try:
            return sorted({date.fromisoformat(str(item)) for item in data}, reverse=True)
        except (TypeError, ValueError) as error:
            raise MarketDataProviderError(
                "Upstox returned an invalid expired-option expiry"
            ) from error

    async def get_expired_option_contracts(
        self, underlying: str, expiry: date
    ) -> list[ExpiredOptionContract]:
        data = await self._get(
            "/v2/expired-instruments/option/contract",
            {
                "instrument_key": self._ensure_nifty(underlying),
                "expiry_date": expiry.isoformat(),
            },
        )
        if not isinstance(data, list):
            raise MarketDataProviderError(
                "Upstox expired-option-contract response had an unexpected shape"
            )
        try:
            contracts = [
                ExpiredOptionContract(
                    instrument_key=item["instrument_key"],
                    trading_symbol=item["trading_symbol"],
                    expiry=item["expiry"],
                    strike=item["strike_price"],
                    option_type=item["instrument_type"],
                    lot_size=item["lot_size"],
                    tick_size=item["tick_size"],
                )
                for item in data
                if isinstance(item, dict)
            ]
        except (KeyError, TypeError, ValueError) as error:
            raise MarketDataProviderError(
                "Upstox expired-option response contained an invalid contract"
            ) from error
        if len(contracts) != len(data):
            raise MarketDataProviderError(
                "Upstox expired-option response contained a malformed contract"
            )
        return contracts

    async def get_option_chain(self, underlying: str, expiry: date) -> OptionChain:
        index = await resolve_option_index(underlying)
        key = index.instrument_key
        data = await self._get(
            "/v2/option/chain",
            {"instrument_key": key, "expiry_date": expiry.isoformat()},
        )
        rows = data if isinstance(data, list) else data.get("data")
        if not isinstance(rows, list):
            raise MarketDataProviderError("Upstox option-chain response had an unexpected shape")

        received_at = datetime.now(timezone.utc)
        contracts: list[OptionContractQuote] = []
        underlying_spot: Decimal | None = None
        try:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if ("underlying_key" in row and row["underlying_key"] != key):
                    raise MarketDataProviderError(
                        "Option-chain row does not match the selected underlying"
                    )
                underlying_spot = _decimal(row.get("underlying_spot_price")) or underlying_spot
                row_expiry = date.fromisoformat(str(row.get("expiry", expiry.isoformat()))[:10])
                strike = _decimal(row.get("strike_price"))
                if strike is None:
                    continue
                for side_key, option_type in (("call_options", "CE"), ("put_options", "PE")):
                    option = row.get(side_key)
                    if not isinstance(option, dict):
                        continue
                    market = option.get("market_data") or {}
                    greeks = option.get("option_greeks") or {}
                    if not isinstance(market, dict) or not isinstance(greeks, dict):
                        continue
                    ltp = _decimal(market.get("ltp"))
                    instrument_key = option.get("instrument_key")
                    if ltp is None or not isinstance(instrument_key, str):
                        continue
                    if not instrument_key.startswith(index.exchange + "_FO|"):
                        raise MarketDataProviderError(
                            "Option-chain leg belongs to a different derivatives venue"
                        )
                    open_interest = _integer(market.get("oi"))
                    previous_open_interest = _integer(market.get("prev_oi"))
                    close_price = _decimal(market.get("close_price"))
                    contracts.append(
                        OptionContractQuote(
                            instrument_token=instrument_key,
                            trading_symbol=option.get("trading_symbol"),
                            expiry=row_expiry,
                            strike=strike,
                            option_type=option_type,
                            quote=Quote(
                                instrument_token=instrument_key,
                                timestamp=received_at,
                                received_at=received_at,
                                timestamp_basis="api_response_received",
                                last_price=ltp,
                                bid=_decimal(market.get("bid_price")),
                                ask=_decimal(market.get("ask_price")),
                                volume=_integer(market.get("volume")),
                            ),
                            open_interest=open_interest,
                            previous_open_interest=previous_open_interest,
                            open_interest_change=(
                                open_interest - previous_open_interest
                                if open_interest is not None and previous_open_interest is not None
                                else None
                            ),
                            change=ltp - close_price if close_price is not None else None,
                            bid_quantity=_integer(market.get("bid_qty")),
                            ask_quantity=_integer(market.get("ask_qty")),
                            implied_volatility=_decimal(greeks.get("iv")),
                            greeks={
                                name: _decimal(greeks.get(name))
                                for name in ("delta", "gamma", "theta", "vega", "iv", "pop")
                            },
                        )
                    )
        except (TypeError, ValueError, InvalidOperation) as error:
            raise MarketDataProviderError(
                "Upstox option-chain response contained invalid values"
            ) from error

        if not contracts:
            raise MarketDataProviderError("Upstox returned no option contracts for that expiry")
        return OptionChain(
            underlying=index.symbol,
            underlying_spot=underlying_spot,
            expiry=expiry,
            as_of=received_at,
            timestamp_basis="api_response_received",
            contracts=contracts,
        )

    async def get_quote(self, instrument_token: str) -> Quote:
        data = await self._get("/v3/market-quote/quotes", {"instrument_key": instrument_token})
        item = _quote_item(data, instrument_token)
        try:
            depth = item.get("depth") or {}
            buys = depth.get("buy") or []
            sells = depth.get("sell") or []
            provider_timestamp = _datetime(item.get("timestamp"))
            received_at = datetime.now(timezone.utc)
            timestamp = provider_timestamp or received_at
            last_trade_time = _millisecond_datetime(item.get("last_trade_time"))
            return Quote(
                instrument_token=instrument_token,
                timestamp=timestamp,
                received_at=received_at,
                timestamp_basis=(
                    "provider_response" if provider_timestamp else "api_response_received"
                ),
                last_trade_time=last_trade_time,
                last_price=item["last_price"],
                bid=buys[0].get("price") if buys else None,
                ask=sells[0].get("price") if sells else None,
                bid_quantity=_integer(buys[0].get("quantity")) if buys else None,
                ask_quantity=_integer(sells[0].get("quantity")) if sells else None,
                volume=_integer(item.get("volume")),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise MarketDataProviderError(
                "Upstox quote response contained invalid values"
            ) from error

    async def get_historical(
        self, instrument_token: str, start: datetime, end: datetime, interval: str
    ) -> list[HistoricalBar]:
        if start > end:
            raise MarketDataProviderError("Historical start must be before end")
        interval_map = {
            "1m": ("minutes", "1"),
            "5m": ("minutes", "5"),
            "15m": ("minutes", "15"),
            "30m": ("minutes", "30"),
            "1h": ("hours", "1"),
            "1d": ("days", "1"),
        }
        if interval not in interval_map:
            raise MarketDataProviderError(
                "Supported historical intervals: 1m, 5m, 15m, 30m, 1h, 1d"
            )
        unit, size = interval_map[interval]
        encoded_key = quote(instrument_token, safe="")
        path = (
            f"/v3/historical-candle/{encoded_key}/{unit}/{size}/"
            f"{end.date().isoformat()}/{start.date().isoformat()}"
        )
        data = await self._get(path)
        candles = data.get("candles")
        if not isinstance(candles, list):
            raise MarketDataProviderError("Upstox candle response had an unexpected shape")
        return _parse_historical_bars(candles)

    async def get_intraday(
        self, instrument_token: str, interval: str
    ) -> list[HistoricalBar]:
        interval_map = {
            "1m": ("minutes", "1"),
            "5m": ("minutes", "5"),
            "15m": ("minutes", "15"),
            "30m": ("minutes", "30"),
            "1h": ("hours", "1"),
            "1d": ("days", "1"),
        }
        if interval not in interval_map:
            raise MarketDataProviderError(
                "Supported intraday intervals: 1m, 5m, 15m, 30m, 1h, 1d"
            )
        unit, size = interval_map[interval]
        encoded_key = quote(instrument_token, safe="")
        data = await self._get(
            f"/v3/historical-candle/intraday/{encoded_key}/{unit}/{size}"
        )
        candles = data.get("candles")
        if not isinstance(candles, list):
            raise MarketDataProviderError("Upstox intraday candle response had an unexpected shape")
        return _parse_historical_bars(candles)

    async def get_expired_historical(
        self, instrument_key: str, start: date, end: date, interval: str
    ) -> list[HistoricalBar]:
        if start > end:
            raise MarketDataProviderError("Historical start must be before end")
        interval_map = {
            "1m": "1minute",
            "3m": "3minute",
            "5m": "5minute",
            "15m": "15minute",
            "30m": "30minute",
            "1d": "day",
        }
        if interval not in interval_map:
            raise MarketDataProviderError(
                "Supported expired-option intervals: 1m, 3m, 5m, 15m, 30m, 1d"
            )
        encoded_key = quote(instrument_key, safe="")
        path = (
            f"/v2/expired-instruments/historical-candle/{encoded_key}/"
            f"{interval_map[interval]}/{end.isoformat()}/{start.isoformat()}"
        )
        data = await self._get(path)
        candles = data.get("candles")
        if not isinstance(candles, list):
            raise MarketDataProviderError("Upstox expired-candle response had an unexpected shape")
        return _parse_historical_bars(candles)

    def subscribe(self, instrument_tokens: list[str]) -> AsyncIterator[Quote]:
        """Stream read-only V3 full-feed quotes without inventing exchange book times."""
        keys = list(dict.fromkeys(instrument_tokens))

        async def stream() -> AsyncIterator[Quote]:
            if not keys or len(keys) > 100 or any(not key.strip() for key in keys):
                raise MarketDataProviderError("Subscribe to 1–100 nonempty instrument keys")
            data = await self._get("/v3/feed/market-data-feed/authorize")
            uri = data.get("authorized_redirect_uri") or data.get("authorizedRedirectUri")
            if not isinstance(uri, str) or not uri.startswith("wss://"):
                raise MarketDataProviderError("Upstox did not provide a secure feed URL")
            request = json.dumps({
                "guid": str(uuid4()), "method": "sub",
                "data": {"mode": "full", "instrumentKeys": keys},
            }).encode("utf-8")
            try:
                async with connect(uri, proxy=None, open_timeout=10, max_size=1_000_000) as ws:
                    await ws.send(request)
                    async for payload in ws:
                        if not isinstance(payload, bytes):
                            raise MarketDataProviderError("Upstox sent a nonbinary market frame")
                        try:
                            frame = upstox_v3_pb2.FeedResponse.FromString(payload)
                        except DecodeError as error:
                            raise MarketDataProviderError(
                                "Upstox sent an invalid market frame"
                            ) from error
                        if frame.type == upstox_v3_pb2.market_info:
                            continue
                        if frame.type not in (
                            upstox_v3_pb2.initial_feed, upstox_v3_pb2.live_feed,
                        ):
                            continue
                        received_at = datetime.now(timezone.utc)
                        tick_at = _millisecond_datetime(frame.currentTs)
                        for key in keys:
                            if key in frame.feeds:
                                live_quote = _stream_quote(
                                    key, frame.feeds[key], tick_at, received_at
                                )
                                if live_quote is not None:
                                    yield live_quote
            except (OSError, TimeoutError, WebSocketException):
                # WebSocket errors may contain the authorized URL; never surface it.
                raise MarketDataProviderError("Upstox live feed connection failed") from None

        return stream()

    async def health_check(self) -> ProviderHealth:
        if not self._token.strip():
            return ProviderHealth(
                provider="upstox",
                configured=False,
                healthy=False,
                detail="UPSTOX_ANALYTICS_TOKEN is empty",
            )
        try:
            await self.get_option_contracts("NIFTY")
        except MarketDataProviderError as error:
            return ProviderHealth(
                provider="upstox", configured=True, healthy=False, detail=str(error)
            )
        return ProviderHealth(
            provider="upstox",
            configured=True,
            healthy=True,
            detail="Read-only NIFTY option-contract request succeeded",
        )


def _stream_quote(
    instrument_key: str, feed: Any, tick_at: datetime | None, received_at: datetime,
) -> Quote | None:
    mode = feed.WhichOneof("FeedUnion")
    depth = None
    volume = None
    if mode == "fullFeed":
        full = feed.fullFeed
        if full.WhichOneof("FullFeedUnion") == "marketFF":
            market = full.marketFF
            last_trade = market.ltpc
            if market.marketLevel.bidAskQuote:
                depth = market.marketLevel.bidAskQuote[0]
            volume = int(market.vtt)
        elif full.WhichOneof("FullFeedUnion") == "indexFF":
            last_trade = full.indexFF.ltpc
        else:
            return None
    elif mode == "firstLevelWithGreeks":
        first = feed.firstLevelWithGreeks
        last_trade = first.ltpc
        depth = first.firstDepth if first.HasField("firstDepth") else None
        volume = int(first.vtt)
    elif mode == "ltpc":
        last_trade = feed.ltpc
    else:
        return None
    price = _decimal(last_trade.ltp)
    if price is None or not price.is_finite() or price <= 0:
        return None
    return Quote(
        instrument_token=instrument_key,
        timestamp=tick_at or received_at,
        received_at=received_at,
        timestamp_basis="provider_tick" if tick_at else "api_response_received",
        book_as_of=None,
        book_timestamp_basis="unknown",
        last_trade_time=_millisecond_datetime(last_trade.ltt),
        last_price=price,
        bid=_decimal(depth.bidP) if depth is not None else None,
        ask=_decimal(depth.askP) if depth is not None else None,
        bid_quantity=int(depth.bidQ) if depth is not None else None,
        ask_quantity=int(depth.askQ) if depth is not None else None,
        volume=volume,
    )


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _integer(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError, OverflowError):
        return None


def _datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _millisecond_datetime(value: Any) -> datetime | None:
    milliseconds = _integer(value)
    if milliseconds is None or milliseconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(milliseconds / 1000, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_historical_bars(candles: list[Any]) -> list[HistoricalBar]:
    bars: list[HistoricalBar] = []
    try:
        for candle in candles:
            if not isinstance(candle, list) or len(candle) < 6:
                raise MarketDataProviderError("Upstox candle response contained a malformed row")
            bars.append(
                HistoricalBar(
                    timestamp=_datetime(candle[0]),
                    open=candle[1],
                    high=candle[2],
                    low=candle[3],
                    close=candle[4],
                    volume=candle[5],
                    open_interest=_integer(candle[6]) if len(candle) > 6 else None,
                )
            )
    except (TypeError, ValueError) as error:
        raise MarketDataProviderError("Upstox candle response contained invalid values") from error
    bars.sort(key=lambda item: item.timestamp)
    if any(left.timestamp == right.timestamp for left, right in zip(bars, bars[1:], strict=False)):
        raise MarketDataProviderError("Upstox candle response contained duplicate timestamps")
    return bars


def _quote_item(data: dict[str, Any], instrument_key: str) -> dict[str, Any]:
    normalized_key = instrument_key.replace("|", ":")
    for key, value in data.items():
        if key in {instrument_key, normalized_key} and isinstance(value, dict):
            return value
    if len(data) == 1:
        item = next(iter(data.values()))
        if isinstance(item, dict):
            return item
    raise MarketDataProviderError("Upstox quote response did not include the requested instrument")
