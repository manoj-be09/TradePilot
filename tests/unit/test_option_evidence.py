from decimal import Decimal

from packages.database.models import PaperTrade
from packages.paper_trading.option_evidence import observed_pcr_condition, option_policy_signature


def test_missing_pcr_is_unknown_not_bearish():
    assert observed_pcr_condition(None) == "UNKNOWN"
    assert observed_pcr_condition(Decimal("NaN")) == "UNKNOWN"
    assert observed_pcr_condition(Decimal("0.94")) == "BEARISH"
    assert observed_pcr_condition(Decimal("1.00")) == "NEUTRAL"
    assert observed_pcr_condition(Decimal("1.06")) == "BULLISH"


def test_option_policy_signature_separates_stop_and_entry_rules():
    baseline = PaperTrade(
        strategy_key="long_call",
        lots=1,
        stop_loss_amount=Decimal("100"),
        target_profit_amount=Decimal("200"),
        trailing_stop_amount=Decimal("50"),
        entry_rule_snapshot={
            "template_version": "1.0.0",
            "strategy_definition_hash": None,
            "conditions": [{"metric": "underlying_spot", "operator": "GT", "threshold": "25000"}],
            "exit_policy": {
                "version": "nifty-option-exit-v1",
                "square_off_minutes_before_close": 20,
            },
        },
    )
    original = option_policy_signature(baseline)
    baseline.stop_loss_amount = Decimal("150")
    assert option_policy_signature(baseline) != original
    baseline.entry_rule_snapshot = {
        **baseline.entry_rule_snapshot,
        "exit_policy": {
            "version": "nifty-option-exit-v1",
            "square_off_minutes_before_close": 30,
        },
    }
    assert option_policy_signature(baseline) != original
    baseline.stop_loss_amount = Decimal("100")
    baseline.entry_rule_snapshot = {
        **baseline.entry_rule_snapshot,
        "conditions": [{"metric": "underlying_spot", "operator": "GT", "threshold": "25100"}],
    }
    assert option_policy_signature(baseline) != original
