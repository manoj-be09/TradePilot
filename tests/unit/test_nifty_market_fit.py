from datetime import datetime, timedelta, timezone
from decimal import Decimal

from packages.market_data.contracts import OptionChain, OptionContractQuote, Quote
from packages.market_data.quality import assess_option_chain
from packages.options.market_fit import assess_nifty_market_fit
from packages.regime.classifier import MarketRegimeResult


def _snapshot(now: datetime) -> OptionChain:
    expiry = (now + timedelta(days=7)).date()
    contracts = []
    for strike in (90, 95, 100, 105, 110):
        for option_type in ("CE", "PE"):
            premium = (
                Decimal("6") if strike == 100
                else Decimal("3") if strike in (95, 105)
                else Decimal("1")
            )
            spread = Decimal("0.01") if premium == 1 else Decimal("0.1")
            token = f"{strike}-{option_type}"
            contracts.append(
                OptionContractQuote(
                    instrument_token=token,
                    expiry=expiry,
                    strike=strike,
                    option_type=option_type,
                    quote=Quote(
                        instrument_token=token,
                        timestamp=now,
                        timestamp_basis="exchange",
                        book_as_of=now,
                        book_timestamp_basis="exchange",
                        last_price=premium,
                        bid=premium,
                        ask=premium + spread,
                        bid_quantity=100,
                        ask_quantity=100,
                        volume=100,
                    ),
                    open_interest=100,
                    previous_open_interest=90,
                    open_interest_change=10,
                    change=0,
                    bid_quantity=100,
                    ask_quantity=100,
                    implied_volatility=20,
                )
            )
    chain = OptionChain(
        underlying="NIFTY",
        underlying_spot=100,
        expiry=expiry,
        as_of=now,
        timestamp_basis="exchange",
        contracts=contracts,
    )
    return chain.model_copy(update={"quality": assess_option_chain(chain)})


def _regime(now: datetime, label: str = "TRENDING_UP") -> MarketRegimeResult:
    return MarketRegimeResult(
        as_of=now - timedelta(minutes=5),
        regime=label,
        directional_bias="UP",
        adx_14=30,
        realized_volatility_percentile=50,
        reasons=["Synthetic classifier fixture"],
        method=["Unit-test fixture"],
    )


def test_quote_backed_bull_vertical_has_independent_expiry_economics():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    result = assess_nifty_market_fit(_snapshot(now), Decimal(100), _regime(now), evaluated_at=now)
    bull = next(item for item in result.candidates if item.strategy_key == "bull_call_spread")

    assert result.action == "NO_TRADE"
    assert result.probability_of_profit is None
    assert bull.fit == "MATCH"
    assert [(leg.side, leg.strike, leg.entry_quote) for leg in bull.legs] == [
        ("BUY", Decimal(100), Decimal("6.1")),
        ("SELL", Decimal(105), Decimal(3)),
    ]
    assert bull.maximum_loss == Decimal("3.1")
    assert bull.maximum_profit == Decimal("1.9")
    assert bull.breakevens == [Decimal("103.1")]
    assert bull.provisional_stop == Decimal("0.78")
    assert bull.provisional_target == Decimal("0.48")
    assert result.candidates[0].strategy_key == "bull_call_spread"


def test_missing_book_time_and_stale_regime_abstain_without_fake_pop():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    chain = _snapshot(now)
    for contract in chain.contracts:
        contract.quote.book_as_of = None
        contract.quote.book_timestamp_basis = "unknown"
    stale = _regime(now - timedelta(hours=1))

    result = assess_nifty_market_fit(chain, Decimal(100), stale, evaluated_at=now)

    assert result.action == "NO_TRADE"
    assert result.probability_of_profit is None
    assert result.regime == "UNKNOWN"
    assert any("stale" in reason for reason in result.reasons)
    assert all(item.fit != "MATCH" for item in result.candidates)
    assert any("book time" in reason for item in result.candidates for reason in item.reasons)
