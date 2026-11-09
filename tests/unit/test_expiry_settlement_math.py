from decimal import Decimal

import pytest

from packages.paper_trading.expiry_settlement import calculate_expiry_settlement


def test_cash_intrinsic_respects_both_sides_of_a_spread():
    calculation = calculate_expiry_settlement([
        {"instrument_key": "call-long", "option_type": "CE", "side": "BUY",
         "strike": "25000", "quantity_units": 25},
        {"instrument_key": "call-short", "option_type": "CE", "side": "SELL",
         "strike": "25050", "quantity_units": 25},
    ], Decimal("-750"), Decimal("25100.00"))
    assert [leg.intrinsic_value for leg in calculation.legs] == [
        Decimal("100.00"), Decimal("50.00")
    ]
    assert calculation.settlement_cash_flow == Decimal("1250.00")
    assert calculation.realized_pnl == Decimal("500.00")


def test_settlement_rejects_malformed_or_non_finite_inputs():
    leg = {"instrument_key": "call", "option_type": "CE", "side": "BUY",
           "strike": "25000", "quantity_units": 25}
    with pytest.raises(ValueError):
        calculate_expiry_settlement([leg], Decimal("-100"), Decimal("NaN"))
    with pytest.raises(ValueError):
        calculate_expiry_settlement([{**leg, "quantity_units": 0}],
                                    Decimal("-100"), Decimal("25100"))


def test_a_later_expiry_leg_is_never_settled_at_intrinsic():
    leg = {"instrument_key": "NSE_FO|1", "option_type": "CE", "side": "BUY", "strike": "25000",
           "quantity_units": 65, "expiry": "2026-11-24"}
    with pytest.raises(ValueError, match="later expiry"):
        calculate_expiry_settlement([leg], Decimal("-1000"), Decimal("25100.00"))
