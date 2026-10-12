from decimal import Decimal

from packages.paper_trading.rules import EntryCondition, evaluate_entry_conditions


def test_entry_conditions_report_pass_fail_and_unknown_without_coercing_missing_data():
    conditions = [
        EntryCondition("put_call_ratio_oi", "GTE", Decimal("0.9")),
        EntryCondition("put_call_ratio_volume", "LT", Decimal("1.2")),
        EntryCondition("atm_iv_skew_put_minus_call_points", "LTE", Decimal("0")),
    ]

    results = evaluate_entry_conditions(
        conditions,
        {
            "put_call_ratio_oi": Decimal("1"),
            "put_call_ratio_volume": Decimal("1.3"),
            "atm_iv_skew_put_minus_call_points": None,
        },
    )

    assert [result.passed for result in results] == [True, False, None]
    assert results[2].actual is None


def test_empty_entry_rules_are_unrestricted():
    assert evaluate_entry_conditions([], {}) == []
