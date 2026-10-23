from decimal import Decimal

from packages.options.models import PayoffLeg, PayoffRequest
from packages.options.upstox_cost_benchmark import upstox_option_cost_scenario


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
