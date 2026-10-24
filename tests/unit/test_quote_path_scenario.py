from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from packages.market_data.contracts import OptionContractQuote, Quote
from packages.options.models import PayoffLeg, PayoffRequest
from packages.regime.quote_path_scenario import offline_nifty_quote_path_scenario

T = datetime(2026, 10, 27, 4, 30, tzinfo=timezone.utc)


def _contract(
    token: str, strike: int, when: datetime, bid: str, ask: str,
    *, bid_size: int = 75, ask_size: int = 75,
) -> OptionContractQuote:
    return OptionContractQuote(
        instrument_token=token, expiry=date(2026, 10, 27),
        strike=Decimal(strike), option_type="CE",
        quote=Quote(
            instrument_token=token, timestamp=when, received_at=when,
            last_price=Decimal(ask), bid=Decimal(bid), ask=Decimal(ask),
            bid_quantity=bid_size, ask_quantity=ask_size,
            book_as_of=when, book_timestamp_basis="exchange",
        ),
    )


def _request(*legs: PayoffLeg) -> PayoffRequest:
    return PayoffRequest(
        underlying="NIFTY", multiplier=Decimal(75),
        price_min=Decimal(0), price_max=Decimal(50000),
        grid_points=2, legs=list(legs),
    )


def _evaluate(
    request: PayoffRequest,
    entry: list[OptionContractQuote],
    exit_: list[OptionContractQuote],
) -> dict[str, object] | None:
    return offline_nifty_quote_path_scenario(
        request, entry, exit_, signal_at=T,
        entry_available_at=T + timedelta(seconds=1),
        exit_signal_at=T + timedelta(minutes=5),
        exit_available_at=T + timedelta(minutes=5, seconds=1),
    )


def test_quote_path_uses_later_bid_and_keeps_result_unqualified() -> None:
    request = _request(PayoffLeg(
        option_type="CE", strike=Decimal(25000), quantity=1,
        entry_premium=Decimal(100),
    ))
    result = _evaluate(
        request,
        [_contract("CE25000", 25000, T, "99", "100")],
        [_contract("CE25000", 25000, T + timedelta(minutes=5), "120", "121")],
    )
    assert result is not None
    assert result["gross_scenario_pnl"] == "1500.00"
    assert result["evidence_origin"] == "CALLER_SUPPLIED_BOOK_PATH_SCENARIO"
    assert result["exchange_origin_verified"] is False
    assert result["source_rights_reviewed"] is False
    assert result["policy_match_verified"] is False
    assert result["promotion_eligible"] is False


def test_quote_path_rejects_missing_size_identity_and_book_provenance() -> None:
    request = _request(PayoffLeg(
        option_type="CE", strike=Decimal(25000), quantity=1,
        entry_premium=Decimal(100),
    ))
    opening = _contract("CE25000", 25000, T, "99", "100")
    closing = _contract("CE25000", 25000, T + timedelta(minutes=5), "120", "121")
    assert _evaluate(request, [opening], [closing]) is not None
    assert _evaluate(request, [
        opening.model_copy(update={"quote": opening.quote.model_copy(
            update={"ask_quantity": 74})}),
    ], [closing]) is None
    assert _evaluate(request, [opening], [
        closing.model_copy(update={"instrument_token": "OTHER"}),
    ]) is None
    assert _evaluate(request, [opening], [
        closing.model_copy(update={"quote": closing.quote.model_copy(
            update={"instrument_token": "OTHER"})}),
    ]) is None
    assert _evaluate(request, [
        opening.model_copy(update={"quote": opening.quote.model_copy(
            update={"book_timestamp_basis": "unknown"})}),
    ], [closing]) is None
    assert _evaluate(request, [
        _contract("CE25000", 25000, T - timedelta(seconds=2), "99", "100"),
    ], [closing]) is None


def test_quote_path_rejects_async_basket_and_wrong_quote_side() -> None:
    request = _request(
        PayoffLeg(option_type="CE", strike=Decimal(25000), quantity=1,
                  entry_premium=Decimal(100)),
        PayoffLeg(option_type="CE", strike=Decimal(25100), quantity=-1,
                  entry_premium=Decimal(80)),
    )
    entry = [
        _contract("CE25000", 25000, T, "99", "100"),
        _contract("CE25100", 25100, T, "80", "81"),
    ]
    exit_ = [
        _contract("CE25000", 25000, T + timedelta(minutes=5), "120", "121"),
        _contract("CE25100", 25100, T + timedelta(minutes=5), "70", "71"),
    ]
    assert _evaluate(request, entry, exit_) is not None
    assert _evaluate(request, [
        entry[0], _contract("CE25100", 25100, T + timedelta(seconds=2), "80", "81"),
    ], exit_) is None
    wrong_entry_price = request.model_copy(update={
        "legs": [request.legs[0].model_copy(
            update={"entry_premium": Decimal(99)}), request.legs[1]],
    })
    assert _evaluate(wrong_entry_price, entry, exit_) is None
