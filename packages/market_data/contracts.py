from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class Quote(BaseModel):
    instrument_token: str
    timestamp: datetime
    timestamp_basis: Literal[
        "exchange", "provider_response", "provider_tick", "api_response_received", "unknown"
    ] = "unknown"
    book_as_of: datetime | None = None
    book_timestamp_basis: Literal[
        "exchange", "provider_response", "api_response_received", "unknown"
    ] = "unknown"
    last_trade_time: datetime | None = None
    last_price: Decimal = Field(ge=0)
    bid: Decimal | None = Field(default=None, ge=0)
    ask: Decimal | None = Field(default=None, ge=0)
    bid_quantity: int | None = Field(default=None, ge=0)
    ask_quantity: int | None = Field(default=None, ge=0)
    volume: int | None = Field(default=None, ge=0)


class DiscoveredInstrument(BaseModel):
    """Read-only provider discovery; not a validated trading entitlement."""

    model_config = ConfigDict(extra="ignore")

    instrument_key: str = Field(min_length=3, max_length=160)
    trading_symbol: str = Field(min_length=1, max_length=128)
    name: str | None = Field(default=None, max_length=256)
    exchange: str = Field(min_length=1, max_length=16)
    segment: str = Field(min_length=1, max_length=32)
    instrument_type: str = Field(min_length=1, max_length=24)
    expiry: date | None = None
    lot_size: int | None = Field(default=None, gt=0)
    tick_size: Decimal | None = Field(default=None, gt=0)


class OptionContractQuote(BaseModel):
    instrument_token: str
    trading_symbol: str | None = None
    expiry: date
    strike: Decimal = Field(gt=0)
    option_type: str
    quote: Quote
    open_interest: int | None = Field(default=None, ge=0)
    previous_open_interest: int | None = Field(default=None, ge=0)
    open_interest_change: int | None = None
    change: Decimal | None = None
    bid_quantity: int | None = Field(default=None, ge=0)
    ask_quantity: int | None = Field(default=None, ge=0)
    implied_volatility: Decimal | None = Field(default=None, ge=0)
    greeks: dict[str, Decimal | None] = Field(default_factory=dict)


class OptionContract(BaseModel):
    """Provider-neutral identity and trading metadata for a listed option."""

    model_config = ConfigDict(extra="ignore")
    instrument_key: str
    trading_symbol: str
    expiry: date
    strike: Decimal = Field(gt=0)
    option_type: str
    lot_size: int = Field(gt=0)
    tick_size: Decimal = Field(gt=0)


class DataQualityIssue(BaseModel):
    code: str
    severity: Literal["warning", "critical"]
    count: int = Field(ge=1)
    message: str


class DataQualityReport(BaseModel):
    status: Literal["PASS", "WARN", "FAIL"]
    checked_at: datetime
    source_timestamp_verified: bool
    contract_count: int = Field(ge=0)
    strike_count: int = Field(ge=0)
    complete_strike_count: int = Field(ge=0)
    missing_field_count: int = Field(ge=0)
    crossed_market_count: int = Field(ge=0)
    abnormal_spread_count: int = Field(ge=0)
    issues: list[DataQualityIssue] = Field(default_factory=list)


class OptionChain(BaseModel):
    underlying: str
    underlying_spot: Decimal | None = Field(default=None, ge=0)
    expiry: date
    as_of: datetime
    timestamp_basis: Literal["exchange", "provider", "api_response_received", "unknown"] = "unknown"
    contracts: list[OptionContractQuote]
    quality: DataQualityReport | None = None


class HistoricalBar(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp: datetime
    open: Decimal = Field(ge=0)
    high: Decimal = Field(ge=0)
    low: Decimal = Field(ge=0)
    close: Decimal = Field(ge=0)
    volume: int = Field(ge=0)
    open_interest: int | None = Field(default=None, ge=0)


class ExpiredOptionContract(BaseModel):
    instrument_key: str
    trading_symbol: str
    expiry: date
    strike: Decimal = Field(gt=0)
    option_type: Literal["CE", "PE"]
    lot_size: int = Field(gt=0)
    tick_size: Decimal = Field(gt=0)


class ProviderHealth(BaseModel):
    provider: str
    configured: bool
    healthy: bool
    detail: str


@dataclass(frozen=True)
class ExchangeSession:
    exchange: str
    start: datetime
    end: datetime


class MarketDataProvider(Protocol):
    provider_id: str

    async def search_instruments(
        self, query: str, *, exchange: str = "NSE", segment: str = "EQ"
    ) -> list[DiscoveredInstrument]: ...

    async def get_nfo_session(self, trading_day: date) -> ExchangeSession | None: ...

    async def get_equity_session(self, trading_day: date) -> ExchangeSession | None: ...

    async def get_quote(self, instrument_token: str) -> Quote: ...

    async def get_option_chain(self, underlying: str, expiry: date) -> OptionChain: ...

    async def get_option_contracts(
        self, underlying: str = "NIFTY", expiry: date | None = None
    ) -> list[OptionContract]: ...

    async def get_historical(
        self, instrument_token: str, start: datetime, end: datetime, interval: str
    ) -> list[HistoricalBar]: ...

    async def get_intraday(
        self, instrument_token: str, interval: str
    ) -> list[HistoricalBar]: ...

    async def get_expired_option_expiries(self, underlying: str) -> list[date]: ...

    async def get_expired_option_contracts(
        self, underlying: str, expiry: date
    ) -> list[ExpiredOptionContract]: ...

    async def get_expired_historical(
        self, instrument_key: str, start: date, end: date, interval: str
    ) -> list[HistoricalBar]: ...

    def subscribe(self, instrument_tokens: list[str]) -> AsyncIterator[Quote]: ...

    async def health_check(self) -> ProviderHealth: ...


class MarketDataUnavailable(RuntimeError):
    """Raised when no real provider is configured for a market-data operation."""


class MarketDataProviderError(RuntimeError):
    """Raised when a configured provider rejects or cannot fulfill a request."""


class MarketDataPlanRequired(MarketDataProviderError):
    """Raised when the provider requires a paid plan for a data capability."""


class UnconfiguredMarketDataProvider:
    """Honest default adapter: fails closed and never synthesizes market observations."""

    provider_id = "unconfigured"

    async def get_equity_session(self, trading_day: date) -> ExchangeSession | None:
        raise MarketDataUnavailable("No market-data provider is configured")

    async def search_instruments(
        self, query: str, *, exchange: str = "NSE", segment: str = "EQ"
    ) -> list[DiscoveredInstrument]:
        del query, exchange, segment
        raise MarketDataUnavailable("No market-data provider is configured")

    async def get_nfo_session(self, trading_day: date) -> ExchangeSession | None:
        del trading_day
        raise MarketDataUnavailable("No market-data provider is configured")

    async def get_quote(self, instrument_token: str) -> Quote:
        raise MarketDataUnavailable("No market-data provider is configured")

    async def get_option_chain(self, underlying: str, expiry: date) -> OptionChain:
        raise MarketDataUnavailable("No market-data provider is configured")

    async def get_option_contracts(
        self, underlying: str = "NIFTY", expiry: date | None = None
    ) -> list[OptionContract]:
        del underlying, expiry
        raise MarketDataUnavailable("No market-data provider is configured")

    async def get_historical(
        self, instrument_token: str, start: datetime, end: datetime, interval: str
    ) -> list[HistoricalBar]:
        raise MarketDataUnavailable("No market-data provider is configured")

    async def get_intraday(
        self, instrument_token: str, interval: str
    ) -> list[HistoricalBar]:
        del instrument_token, interval
        raise MarketDataUnavailable("No market-data provider is configured")

    async def get_expired_option_expiries(self, underlying: str) -> list[date]:
        del underlying
        raise MarketDataUnavailable("No market-data provider is configured")

    async def get_expired_option_contracts(
        self, underlying: str, expiry: date
    ) -> list[ExpiredOptionContract]:
        del underlying, expiry
        raise MarketDataUnavailable("No market-data provider is configured")

    async def get_expired_historical(
        self, instrument_key: str, start: date, end: date, interval: str
    ) -> list[HistoricalBar]:
        del instrument_key, start, end, interval
        raise MarketDataUnavailable("No market-data provider is configured")

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            provider="unconfigured",
            configured=False,
            healthy=False,
            detail="Provider selection and official capability review are pending",
        )

    def subscribe(self, instrument_tokens: list[str]) -> AsyncIterator[Quote]:
        async def unavailable() -> AsyncIterator[Quote]:
            raise MarketDataUnavailable("No market-data provider is configured")
            yield  # pragma: no cover

        return unavailable()
