from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from packages.backtesting.contracts import ScheduledOptionsBacktestResult
from packages.regime.frozen_nifty_study import FrozenNiftyStudy
from packages.regime.nifty_study_replay import assess_nifty_scheduled_replay


def study(**changes):
    return FrozenNiftyStudy(**({
        "study_key": "nifty50-paper-v1", "objective": "Compare the incumbent rule with no trade.",
        "incumbent_rule_version": "nifty-paper-v1", "dataset_digest": "a" * 64,
        "calculation_digest": "b" * 64, "cost_model_id": "illustrative-v1",
        "capital": "100000.00", "parent_search_budget": 10,
        "review_interval_days": 30, "uncertainty_note": "Evidence remains unverified.",
        "train_start": "2020-01-01", "train_end": "2022-12-31",
        "validation_start": "2023-01-01", "validation_end": "2024-12-31",
        "holdout_start": "2025-01-01", "holdout_end": "2025-12-31",
    } | changes))


def replay(**changes):
    return ScheduledOptionsBacktestResult(**({
        "run_id": uuid4(), "provider": "fixture", "mode": "historical_scheduled_expiry_replay",
        "strategy_key": "nifty-paper", "strategy_name": "NIFTY paper fixture",
        "strategy_version": "nifty-paper-v1", "start_date": date(2020, 1, 1),
        "end_date": date(2024, 12, 31), "entry_sessions_before_expiry": 2,
        "lots": 1, "fee_per_order": Decimal("1"),
        "slippage_per_unit_per_side": Decimal("0.1"), "trade_count": 4,
        "gross_pnl": Decimal("100"), "estimated_cost": Decimal("20"),
        "net_pnl": Decimal("80"), "max_closed_trade_drawdown": Decimal("10"),
        "winning_trades": 2, "observed_input_digest": "c" * 64, "trades": [],
        "assumptions": ["Fixture replay."], "warnings": ["Indicative replay."],
    } | changes))


def test_assessment_keeps_replay_unverified_and_holdout_closed():
    result = assess_nifty_scheduled_replay(study(), replay())
    assert result.status == "INSUFFICIENT_SAMPLE"
    assert result.holdout_evaluated is False
    assert result.promotion_eligible is False
    assert result.replay_input_digest == "c" * 64
    assert "calibrated profit probability" in " ".join(result.limitations)


def test_twenty_trades_are_reviewable_but_not_qualified():
    result = assess_nifty_scheduled_replay(study(), replay(trade_count=20))
    assert result.status == "REVIEWABLE_UNVERIFIED"
    assert result.promotion_eligible is False


@pytest.mark.parametrize("changes", [
    {"strategy_version": "other-version"},
    {"end_date": date(2025, 1, 1)},
    {"mode": "historical_single_expiry_replay"},
    {"observed_input_digest": "short"},
])
def test_assessment_rejects_unfrozen_or_unidentified_replays(changes):
    with pytest.raises(ValueError):
        assess_nifty_scheduled_replay(study(), replay(**changes))
