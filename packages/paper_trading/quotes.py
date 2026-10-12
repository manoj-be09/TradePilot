"""Execution eligibility is stricter than a descriptive research quality report."""

from datetime import datetime, timezone
from decimal import Decimal

from packages.market_data.contracts import OptionChain, OptionContractQuote


class PaperQuoteError(ValueError):
    pass


def require_fresh(
    timestamp: datetime,
    *,
    now: datetime,
    max_age_seconds: int,
) -> None:
    if timestamp.tzinfo is None:
        raise PaperQuoteError("Quote timestamp must include a timezone")
    age = (now - timestamp.astimezone(timezone.utc)).total_seconds()
    if age > max_age_seconds or age < -5:
        raise PaperQuoteError("Quote timestamp is stale or in the future; no paper fill recorded")


def require_quote(
    chain: OptionChain,
    instrument_key: str,
    side: str,
    quantity: int,
    *,
    max_age_seconds: int = 60,
    allow_unverified: bool = False,
    now: datetime | None = None,
) -> tuple[Decimal, OptionContractQuote]:
    checked_at = now or datetime.now(timezone.utc)
    if chain.timestamp_basis != "exchange" and not allow_unverified:
        raise PaperQuoteError(
            "Exchange quote freshness is unverified; paper execution is blocked. "
            "Educational simulation requires an explicit server configuration override."
        )
    require_fresh(chain.as_of, now=checked_at, max_age_seconds=max_age_seconds)
    contract = next((c for c in chain.contracts if c.instrument_token == instrument_key), None)
    if contract is None:
        raise PaperQuoteError("Selected contract is missing from the current chain")
    quote = contract.quote
    if quote.timestamp_basis != "exchange" and not allow_unverified:
        raise PaperQuoteError(
            "Selected contract exchange quote freshness is unverified; "
            "paper execution is blocked."
        )
    require_fresh(quote.timestamp, now=checked_at, max_age_seconds=max_age_seconds)
    if (
        quote.book_timestamp_basis != "exchange" or quote.book_as_of is None
    ) and not allow_unverified:
        raise PaperQuoteError(
            "Selected contract bid/ask book update time is unverified; "
            "paper execution is blocked."
        )
    if quote.book_as_of is not None:
        require_fresh(quote.book_as_of, now=checked_at, max_age_seconds=max_age_seconds)
    if (
        quote.bid is None
        or quote.ask is None
        or quote.bid <= 0
        or quote.ask <= 0
        or quote.bid > quote.ask
    ):
        raise PaperQuoteError("A selected contract has an unusable two-sided quote")
    midpoint = (quote.bid + quote.ask) / Decimal(2)
    if (quote.ask - quote.bid) / midpoint > Decimal("0.30"):
        raise PaperQuoteError("A selected contract spread is too wide for a paper fill")
    available = contract.ask_quantity if side == "BUY" else contract.bid_quantity
    if available is None or available < quantity:
        raise PaperQuoteError("Displayed quote size is insufficient for this paper order")
    return (quote.ask if side == "BUY" else quote.bid), contract
