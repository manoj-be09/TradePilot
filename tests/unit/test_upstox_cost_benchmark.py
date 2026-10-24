from decimal import Decimal

from packages.options.models import PayoffLeg, PayoffRequest
from packages.options.upstox_cost_benchmark import (
    upstox_option_cost_scenario,
    upstox_option_two_price_scenario,
)


def _request(*legs: PayoffLeg, multiplier: str = "75") -> PayoffRequest:
    return PayoffRequest(
        underlying="NIFTY", multiplier=Decimal(multiplier), price_min=Decimal(0),
        price_max=Decimal(50000), grid_points=2, legs=list(legs),
    )


def test_upstox_public_benchmark_charges_each_entry_and_exit_side() -> None:
    one = _request(PayoffLeg(option_type="CE", strike=Decimal(25000),
                             quantity=1, entry_premium=Decimal(100)))
    scenario = upstox_option_cost_scenario(one)
    assert scenario is not None
    assert scenario["entry_charge"] == "26.98"
    assert scenario["unchanged_premium_exit_charge"] == "38.00"
    assert scenario["round_trip_scenario_charge"] == "64.98"
    assert scenario["complete_broker_costs"] is False

    spread = _request(one.legs[0], PayoffLeg(
        option_type="CE", strike=Decimal(25100), quantity=-1,
        entry_premium=Decimal(100),
    ))
    assert upstox_option_cost_scenario(spread)["round_trip_scenario_charge"] == "129.96"


def test_benchmark_withholds_non_nifty_or_non_contract_multiplier() -> None:
    option = PayoffLeg(option_type="PE", strike=Decimal(25000), quantity=1,
                       entry_premium=Decimal(100))
    fractional = _request(option, multiplier="0.5")
    assert upstox_option_cost_scenario(fractional) is None
    other = _request(option).model_copy(update={"underlying": "BANKNIFTY"})
    assert upstox_option_cost_scenario(other) is None


def test_two_price_scenario_uses_exit_premium_and_reverse_side_charges() -> None:
    long_call = _request(PayoffLeg(
        option_type="CE", strike=Decimal(25000), quantity=1,
        entry_premium=Decimal(100),
    ))
    scenario = upstox_option_two_price_scenario(long_call, [Decimal(120)])
    assert scenario is not None
    assert scenario["gross_scenario_pnl"] == "1500.00"
    assert scenario["exit_charge"] == "40.89"
    assert scenario["net_scenario_pnl"] == "1432.13"
    assert scenario["evidence_origin"] == "SUPPLIED_PRICE_SCENARIO"
    assert scenario["complete_broker_costs"] is False
    assert scenario["promotion_eligible"] is False
    assert scenario["profit_probability"] is None

    short_call = _request(long_call.legs[0].model_copy(update={"quantity": -1}))
    short_scenario = upstox_option_two_price_scenario(short_call, [Decimal(120)])
    assert short_scenario is not None
    assert short_scenario["gross_scenario_pnl"] == "-1500.00"
    assert Decimal(short_scenario["net_scenario_pnl"]) < Decimal("-1500")


def test_two_price_scenario_abstains_on_missing_or_invalid_exit_price() -> None:
    request = _request(PayoffLeg(
        option_type="PE", strike=Decimal(25000), quantity=1,
        entry_premium=Decimal(100),
    ))
    assert upstox_option_two_price_scenario(request, []) is None
    assert upstox_option_two_price_scenario(request, [Decimal("NaN")]) is None
    assert upstox_option_two_price_scenario(request, [Decimal(0)]) is None
    assert upstox_option_two_price_scenario(
        request.model_copy(update={"underlying": "BANKNIFTY"}), [Decimal(120)],
    ) is None
