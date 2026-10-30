from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from packages.market_data.contracts import Quote
from packages.paper_trading.futures_strategies import (
    FuturesExitPolicy,
    evaluate_futures_conditions,
    front_contract_expiry,
    futures_rule_metrics,
    get_futures_template,
    in_rollover_window,
    list_futures_templates,
    parse_futures_exit_policy,
)


def _quote(bid="25000", ask="25010") -> Quote:
    return Quote(
        instrument_token="NSE_FO|X", timestamp=datetime.now(timezone.utc), last_price=Decimal(bid),
        bid=None if bid is None else Decimal(bid), ask=None if ask is None else Decimal(ask),
    )


def test_every_template_has_rules_and_a_reward_above_its_stop():
    templates = list_futures_templates()
    assert len(templates) == 4 and len({row.key for row in templates}) == 4
    for row in templates:
        assert row.target_points > row.stop_points > 0 and row.trailing_points > 0
        assert {condition.metric for condition in row.conditions} >= {
            "regime_direction", "futures_relative_spread_pct",
        }


def test_short_rules_mirror_long_rules():
    long_rules = get_futures_template("nifty_futures_positional_long").conditions
    short_rules = get_futures_template("nifty_futures_positional_short").conditions
    assert (long_rules[0].operator, long_rules[0].threshold) == ("GTE", Decimal(1))
    assert (short_rules[0].operator, short_rules[0].threshold) == ("LTE", Decimal(-1))


def test_amounts_scale_with_lots_and_read_the_lot_size():
    template = get_futures_template("nifty_futures_intraday_long")
    assert template.amounts(2, 75) == (Decimal("9000.00"), Decimal("18000.00"), Decimal("7500.00"))
    assert template.amounts(1, 65)[0] == Decimal("3900.00")


def test_stop_budget_sits_inside_the_two_percent_cap_at_one_lot():
    for template in list_futures_templates():
        assert template.amounts(1, 75)[0] <= Decimal("20000")


@pytest.mark.parametrize(("days", "inside"), [(0, True), (2, True), (3, False)])
def test_rollover_window_boundary(days, inside):
    today = date(2026, 11, 2)
    assert in_rollover_window(date(2026, 11, 2 + days), today, 2) is inside


def test_front_contract_skips_the_rolling_expiry():
    today = date(2026, 11, 22)
    expiries = [date(2026, 11, 24), date(2026, 12, 29), date(2027, 1, 26)]
    assert front_contract_expiry(expiries, today, 2) == date(2026, 12, 29)
    assert front_contract_expiry(expiries, date(2026, 11, 1), 2) == date(2026, 11, 24)
    assert front_contract_expiry([date(2026, 11, 23)], today, 2) is None


def test_metrics_compute_spread_and_basis():
    metrics = futures_rule_metrics(
        quote=_quote("25000", "25010"), spot=Decimal("24905"),
        regime_bias="DOWN", put_call_ratio_oi=Decimal("0.9"),
    )
    assert metrics["regime_direction"] == Decimal(-1)
    assert metrics["futures_relative_spread_pct"] == Decimal("0.0400")
    assert metrics["futures_basis_pct"] == Decimal("0.4015")


def test_missing_inputs_stay_unknown_and_never_pass():
    metrics = futures_rule_metrics(
        quote=_quote("0", "0"), spot=None, regime_bias=None, put_call_ratio_oi=None,
    )
    assert set(metrics.values()) == {None}
    template = get_futures_template("nifty_futures_positional_long")
    results = evaluate_futures_conditions(template.conditions, metrics)
    assert all(row.passed is None for row in results)


def test_rules_evaluate_at_their_thresholds():
    template = get_futures_template("nifty_futures_positional_long")
    results = evaluate_futures_conditions(template.conditions, {
        "regime_direction": Decimal(1), "put_call_ratio_oi": Decimal("1.00"),
        "futures_relative_spread_pct": Decimal("0.05"), "futures_basis_pct": None,
    })
    assert all(row.passed is True for row in results)
    results = evaluate_futures_conditions(template.conditions, {
        "regime_direction": Decimal(0), "put_call_ratio_oi": Decimal("0.99"),
        "futures_relative_spread_pct": Decimal("0.06"), "futures_basis_pct": None,
    })
    assert all(row.passed is False for row in results)


def test_exit_policy_round_trips_and_rejects_bad_values():
    policy = FuturesExitPolicy(20, intraday=True, rollover_days_before_expiry=2)
    assert parse_futures_exit_policy(policy.as_dict()) == policy
    for bad in (
        None, {}, {**policy.as_dict(), "extra": 1}, {**policy.as_dict(), "version": "other"},
        {**policy.as_dict(), "square_off_minutes_before_close": 1},
        {**policy.as_dict(), "intraday": "yes"},
        {**policy.as_dict(), "rollover_days_before_expiry": 0},
    ):
        with pytest.raises(ValueError):
            parse_futures_exit_policy(bad)
