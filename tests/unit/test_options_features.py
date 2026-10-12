from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from packages.features.options import calculate_option_features
from packages.market_data.contracts import OptionChain, OptionContractQuote, Quote


def _contract(
    side: str, strike: str, suffix: str, oi: int, volume: int, iv: str
) -> OptionContractQuote:
    instrument = f"NSE_FO|{suffix}"
    return OptionContractQuote(
        instrument_token=instrument,
        expiry=date.today() + timedelta(days=7),
        strike=strike,
        option_type=side,
        quote=Quote(
            instrument_token=instrument,
            timestamp=datetime.now(timezone.utc),
            last_price="10",
            bid="9",
            ask="11",
            volume=volume,
        ),
        open_interest=oi,
        previous_open_interest=oi - 10,
        open_interest_change=10,
        implied_volatility=iv,
        greeks={"delta": "0.5", "gamma": "0.01", "theta": "-2", "vega": "4"},
    )


def test_option_features_calculate_ratios_skew_concentration_and_spread():
    expiry = date.today() + timedelta(days=7)
    chain = OptionChain(
        underlying="NIFTY",
        underlying_spot="100",
        expiry=expiry,
        as_of=datetime.now(timezone.utc),
        contracts=[
            _contract("CE", "100", "C100", 100, 10, "20"),
            _contract("PE", "100", "P100", 150, 15, "25"),
            _contract("CE", "110", "C110", 200, 20, "18"),
            _contract("PE", "90", "P90", 50, 5, "22"),
        ],
    )

    result = calculate_option_features(chain)

    assert result.put_call_ratio_oi is not None
    assert abs(result.put_call_ratio_oi - Decimal(2) / 3) < Decimal("1e-25")
    assert result.put_call_ratio_volume is not None
    assert abs(result.put_call_ratio_volume - Decimal(2) / 3) < Decimal("1e-25")
    assert result.atm_strike == 100
    assert result.atm_iv_skew_put_minus_call_points == 5
    assert result.largest_call_oi_strike == 110
    assert result.largest_call_oi_share_pct is not None
    assert abs(result.largest_call_oi_share_pct - Decimal(20000) / 300) < Decimal("1e-25")
    assert result.two_sided_quote_count == 4
    assert result.median_relative_spread_pct == 20
    assert result.contracts_with_delta == 4
    assert result.iv_rank_pct is None
    assert result.term_structure is None
