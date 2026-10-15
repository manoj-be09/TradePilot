import pytest

from packages.paper_trading.option_policy import (
    EXPIRY_TEST_POLICY_VERSION,
    OptionExitPolicy,
    parse_option_exit_policy,
)


def test_expiry_test_is_explicit_and_preserves_legacy_policy():
    normal = OptionExitPolicy(20)
    assert not normal.holds_for_expiry_test
    assert not parse_option_exit_policy(None, legacy_cutoff=20).holds_for_expiry_test
    test = OptionExitPolicy(20, version=EXPIRY_TEST_POLICY_VERSION)
    assert parse_option_exit_policy(test.as_dict()).holds_for_expiry_test
    assert normal.as_dict() == {
        "version": "nifty-option-exit-v1", "square_off_minutes_before_close": 20,
    }
    with pytest.raises(ValueError):
        OptionExitPolicy(20, version="unknown")
    with pytest.raises(ValueError):
        OptionExitPolicy(0, version=EXPIRY_TEST_POLICY_VERSION)
