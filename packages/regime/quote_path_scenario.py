"""Offline NIFTY quote-path screen; supplied books remain unreviewed evidence."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from packages.market_data.contracts import OptionContractQuote, Quote
from packages.options.models import PayoffRequest
from packages.options.upstox_cost_benchmark import upstox_option_two_price_scenario


def _side_price(
    quote: Quote, side: str, units: int, signal_at: datetime, available_at: datetime,
) -> Decimal | None:
    book_at, received_at = quote.book_as_of, quote.received_at
    if (
        quote.book_timestamp_basis != "exchange"
        or book_at is None or received_at is None
        or book_at.tzinfo is None or received_at.tzinfo is None
        or not signal_at <= book_at <= received_at <= available_at
        or available_at - book_at > timedelta(seconds=2)
        or quote.bid is None or quote.ask is None
        or not quote.bid.is_finite() or not quote.ask.is_finite()
        or quote.bid <= 0 or quote.ask < quote.bid
    ):
        return None
    mid = (quote.bid + quote.ask) / 2
    if (quote.ask - quote.bid) / mid > Decimal("0.05"):
        return None
    size = quote.ask_quantity if side == "BUY" else quote.bid_quantity
    if size is None or size < units:
        return None
    return quote.ask if side == "BUY" else quote.bid


def offline_nifty_quote_path_scenario(
    request: PayoffRequest,
    entry: Sequence[OptionContractQuote],
    exit_: Sequence[OptionContractQuote],
    *,
    signal_at: datetime,
    entry_available_at: datetime,
    exit_signal_at: datetime,
    exit_available_at: datetime,
) -> dict[str, object] | None:
    """Screen complete later-book baskets and compute an unqualified cost scenario.

    This trusts caller-supplied book labels and does not prove exchange origin,
    licensed retention, full depth, actual execution, or frozen-policy decisions.
    """
    times = (signal_at, entry_available_at, exit_signal_at, exit_available_at)
    if (
        any(stamp.tzinfo is None for stamp in times)
        or not signal_at < entry_available_at < exit_signal_at < exit_available_at
        or len(entry) != len(request.legs) or len(exit_) != len(request.legs)
        or request.multiplier != request.multiplier.to_integral_value()
    ):
        return None
    entry_times: list[datetime] = []
    exit_times: list[datetime] = []
    exit_prices: list[Decimal] = []
    for leg, opening, closing in zip(request.legs, entry, exit_, strict=True):
        if (
            opening.option_type != leg.option_type
            or closing.option_type != leg.option_type
            or opening.strike != leg.strike or closing.strike != leg.strike
            or opening.expiry != closing.expiry
            or not opening.instrument_token
            or opening.instrument_token != closing.instrument_token
            or opening.quote.instrument_token != opening.instrument_token
            or closing.quote.instrument_token != closing.instrument_token
        ):
            return None
        units = abs(leg.quantity) * int(request.multiplier)
        side = "BUY" if leg.quantity > 0 else "SELL"
        reverse = "SELL" if side == "BUY" else "BUY"
        entry_price = _side_price(
            opening.quote, side, units, signal_at, entry_available_at,
        )
        exit_price = _side_price(
            closing.quote, reverse, units, exit_signal_at, exit_available_at,
        )
        if entry_price is None or exit_price is None or leg.entry_premium != entry_price:
            return None
        assert opening.quote.book_as_of is not None
        assert closing.quote.book_as_of is not None
        entry_times.append(opening.quote.book_as_of)
        exit_times.append(closing.quote.book_as_of)
        exit_prices.append(exit_price)
    if (
        max(entry_times) - min(entry_times) > timedelta(seconds=1)
        or max(exit_times) - min(exit_times) > timedelta(seconds=1)
    ):
        return None
    scenario = upstox_option_two_price_scenario(request, exit_prices)
    if scenario is None:
        return None
    return scenario | {
        "evidence_origin": "CALLER_SUPPLIED_BOOK_PATH_SCENARIO",
        "book_labels_checked": True,
        "exchange_origin_verified": False,
        "source_rights_reviewed": False,
        "policy_match_verified": False,
    }
