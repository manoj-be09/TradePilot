"""Exit rules must not be triggered by the bid/ask spread or a tiny gain alone."""

from decimal import Decimal

from packages.paper_trading.exits import evaluate_exit
from packages.paper_trading.template_policy import template_policy


def test_a_position_that_barely_gained_keeps_its_protective_stop():
    # Short call condor, 30 Oct 2026: a Rs 3.25 peak turned a Rs 1,097 stop into about Rs 90.
    decision = evaluate_exit(
        pnl=Decimal("-94.25"), peak_pnl=Decimal("3.25"), stop_loss_amount=Decimal("1097.20"),
        target_profit_amount=Decimal("253.50"), trailing_stop_amount=Decimal("88.72"),
    )
    assert decision.reason is None


def test_the_trailing_stop_starts_once_the_peak_gain_reaches_the_trail():
    def exit_at(pnl: str, peak: str) -> str | None:
        return evaluate_exit(
            pnl=Decimal(pnl), peak_pnl=Decimal(peak), stop_loss_amount=Decimal("1000"),
            target_profit_amount=None, trailing_stop_amount=Decimal("100"),
        ).reason

    assert exit_at("-1", "99.99") is None
    assert exit_at("0", "100") == "TRAILING_STOP"
    assert exit_at("150", "300") == "TRAILING_STOP"
    assert exit_at("250", "300") is None


def test_a_hard_stop_still_fires_with_or_without_a_gain():
    decision = evaluate_exit(
        pnl=Decimal("-1100"), peak_pnl=Decimal("50"), stop_loss_amount=Decimal("1000"),
        target_profit_amount=None, trailing_stop_amount=Decimal("100"),
    )
    assert decision.reason == "STOP_LOSS"


def test_the_stop_clears_the_spread_on_a_narrow_structure():
    # Iron butterfly, 30 Oct 2026: 40% of a Rs 308.75 maximum loss was hit in 18 seconds.
    policy = template_policy(
        "iron_butterfly", Decimal("308.75"), Decimal("2941.25"), Decimal("70"),
    )
    assert policy is not None
    assert policy.stop_loss == Decimal("210.00")


def test_the_stop_never_exceeds_the_maximum_loss():
    policy = template_policy(
        "iron_butterfly", Decimal("150"), Decimal("500"), Decimal("100"),
    )
    assert policy is not None
    assert policy.stop_loss == Decimal("150.00")


def test_a_small_spread_leaves_the_usual_sizing():
    policy = template_policy("long_call", Decimal("9100"), None, Decimal("30"))
    assert policy is not None
    assert policy.stop_loss == Decimal("3640.00")
    assert policy.target_profit == Decimal("5460.00")
    assert policy.trailing_stop == Decimal("1911.00")


def test_no_policy_without_a_positive_maximum_loss():
    assert template_policy("long_call", Decimal(0), None, Decimal(1)) is None


def test_entry_rules_match_the_template_view_and_always_limit_the_spread():
    def metrics(key: str) -> list[tuple[str, str]]:
        policy = template_policy(key, Decimal(1000), Decimal(1000), Decimal(0))
        assert policy is not None
        return [(item["metric"], item["operator"]) for item in policy.entry_conditions]

    spread = ("median_relative_spread_pct", "LTE")
    assert metrics("long_call") == [("put_call_ratio_oi", "GTE"), spread]
    assert metrics("long_put") == [("put_call_ratio_oi", "LTE"), spread]
    assert metrics("iron_condor") == [
        ("put_call_ratio_oi", "GTE"), ("put_call_ratio_oi", "LTE"), spread,
    ]
    assert metrics("strip") == [spread]
