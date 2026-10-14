from datetime import date, datetime, timedelta, timezone
from typing import Literal

import pytest

from packages.market_data.contracts import OptionChain, OptionContractQuote, Quote
from packages.market_data.quality import assess_option_chain
from packages.paper_trading.quotes import PaperQuoteError, require_quote


def _contract(
    option_type: str,
    instrument_key: str,
    *,
    bid: str = "9.00",
    ask: str = "11.00",
    volume: int | None = 100,
    open_interest: int | None = 250,
    previous_open_interest: int | None = 200,
    implied_volatility: str | None = "12.5",
) -> OptionContractQuote:
    oi_change = (
        open_interest - previous_open_interest
        if open_interest is not None and previous_open_interest is not None
        else None
    )
    return OptionContractQuote(
        instrument_token=instrument_key,
        expiry=date.today() + timedelta(days=1),
        strike="25000",
        option_type=option_type,
        quote=Quote(
            instrument_token=instrument_key,
            timestamp=datetime.now(timezone.utc),
            timestamp_basis="exchange",
            book_as_of=datetime.now(timezone.utc),
            book_timestamp_basis="exchange",
            last_price="10.00",
            bid=bid,
            ask=ask,
            volume=volume,
        ),
        open_interest=open_interest,
        previous_open_interest=previous_open_interest,
        open_interest_change=oi_change,
        change="0.25",
        bid_quantity=100,
        ask_quantity=120,
        implied_volatility=implied_volatility,
    )


def _chain(
    contracts: list[OptionContractQuote],
    *,
    timestamp_basis: Literal[
        "exchange", "provider", "api_response_received", "unknown"
    ] = "api_response_received",
) -> OptionChain:
    return OptionChain(
        underlying="NIFTY",
        underlying_spot="25010",
        expiry=date.today() + timedelta(days=1),
        as_of=datetime.now(timezone.utc),
        timestamp_basis=timestamp_basis,
        contracts=contracts,
    )


def test_clean_exchange_timestamped_chain_passes():
    result = assess_option_chain(
        _chain(
            [_contract("CE", "call-key"), _contract("PE", "put-key")],
            timestamp_basis="exchange",
        )
    )

    assert result.status == "PASS"
    assert result.source_timestamp_verified is True
    assert result.complete_strike_count == 1
    assert result.issues == []


def test_complete_chain_reports_unverified_provider_timestamp():
    result = assess_option_chain(_chain([_contract("CE", "call-key"), _contract("PE", "put-key")]))

    assert result.status == "WARN"
    assert result.source_timestamp_verified is False
    assert result.contract_count == 2
    assert result.strike_count == 1
    assert result.complete_strike_count == 1
    assert result.missing_field_count == 0
    assert [issue.code for issue in result.issues] == ["SOURCE_TIMESTAMP_UNVERIFIED"]


def test_exchange_labeled_chain_with_unverified_leg_quote_is_not_verified():
    call = _contract("CE", "call-key")
    call.quote.timestamp_basis = "api_response_received"
    result = assess_option_chain(_chain(
        [call, _contract("PE", "put-key")], timestamp_basis="exchange"
    ))

    assert result.status == "WARN"
    assert result.source_timestamp_verified is False
    assert result.issues[0].code == "SOURCE_TIMESTAMP_UNVERIFIED"
    assert result.issues[0].count == 1

    chain = _chain([call, _contract("PE", "put-key")], timestamp_basis="exchange")
    with pytest.raises(PaperQuoteError, match="Selected contract exchange quote freshness"):
        require_quote(chain, "call-key", "BUY", 1)
    price, _ = require_quote(chain, "call-key", "BUY", 1, allow_unverified=True)
    assert price == 11


def test_exchange_labeled_quote_without_book_time_cannot_fill():
    call = _contract("CE", "call-key")
    call.quote.book_as_of = None
    call.quote.book_timestamp_basis = "unknown"
    chain = _chain([call, _contract("PE", "put-key")], timestamp_basis="exchange")
    report = assess_option_chain(chain)
    assert report.status == "WARN"
    assert report.source_timestamp_verified is False
    assert any(issue.code == "BOOK_TIMESTAMP_UNVERIFIED" for issue in report.issues)
    with pytest.raises(PaperQuoteError, match="bid/ask book update time"):
        require_quote(chain, "call-key", "BUY", 1)
    assert require_quote(chain, "call-key", "BUY", 1, allow_unverified=True)[0] == 11


def test_stale_exchange_book_cannot_fill():
    call = _contract("CE", "call-key")
    call.quote.book_as_of = datetime.now(timezone.utc) - timedelta(minutes=2)
    chain = _chain([call, _contract("PE", "put-key")], timestamp_basis="exchange")
    assert any(issue.code == "STALE_BOOK" for issue in assess_option_chain(chain).issues)
    with pytest.raises(PaperQuoteError, match="stale"):
        require_quote(chain, "call-key", "BUY", 1)


@pytest.mark.parametrize("allow_unverified", [False, True])
@pytest.mark.parametrize("field", ["chain", "quote", "book"])
@pytest.mark.parametrize("offset", [-61, 6])
def test_fresh_receipt_cannot_hide_stale_or_future_execution_time(
    allow_unverified, field, offset,
):
    now = datetime.now(timezone.utc)
    call = _contract("CE", "call-key")
    chain = _chain([call], timestamp_basis="exchange")
    chain.as_of = call.quote.timestamp = call.quote.book_as_of = now
    invalid_time = now + timedelta(seconds=offset)
    if field == "chain":
        chain.as_of = invalid_time
    elif field == "quote":
        call.quote.timestamp = invalid_time
    else:
        call.quote.book_as_of = invalid_time
    with pytest.raises(PaperQuoteError, match="stale or in the future"):
        require_quote(
            chain, "call-key", "BUY", 1, now=now,
            allow_unverified=allow_unverified,
        )


def test_quality_detects_crossed_market_duplicate_and_missing_side():
    call = _contract("CE", "call-key", bid="12.00", ask="10.00")
    duplicate_call = _contract("CE", "call-key", bid="9.00", ask="11.00")
    result = assess_option_chain(_chain([call, duplicate_call]))
    codes = {issue.code for issue in result.issues}

    assert result.status == "FAIL"
    assert result.crossed_market_count == 1
    assert "DUPLICATE_INSTRUMENT" in codes
    assert "DUPLICATE_STRIKE_SIDE" in codes
    assert "INCOMPLETE_STRIKE" in codes


def test_quality_warns_for_missing_fields_and_abnormal_spread():
    call = _contract(
        "CE",
        "call-key",
        bid="1.00",
        ask="10.00",
        volume=None,
        open_interest=None,
        previous_open_interest=None,
        implied_volatility=None,
    )
    result = assess_option_chain(_chain([call]))
    codes = {issue.code for issue in result.issues}

    assert result.status == "WARN"
    assert result.missing_field_count >= 4
    assert result.abnormal_spread_count == 1
    assert "MISSING_FIELDS" in codes
    assert "ABNORMAL_SPREAD" in codes
