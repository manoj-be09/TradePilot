import pytest

from packages.regime.strategy_ranking import profit_review


def evidence(n=40, mean=50, win=45, drawdown=300, risk=200):
    return {
        "closed_trades": n, "average_net_pnl": mean,
        "win_rate": win, "closed_trade_drawdown": drawdown,
        "average_r": mean / risk if mean is not None else None,
        "closed_trade_drawdown_r": drawdown / risk,
    }


def test_high_win_rate_does_not_promote_a_losing_strategy():
    profitable = profit_review(evidence(win=35, mean=50), -2, 200)
    losing = profit_review(evidence(win=90, mean=-20), 4, 200)
    assert profitable["tier"] > losing["tier"]
    assert losing["label"] == "No positive replay edge"


def test_one_lucky_trade_is_not_promoted_above_positive_larger_sample():
    tiny = profit_review(evidence(n=1, mean=5000, win=100, drawdown=0), 4, 200)
    larger = profit_review(evidence(n=30, mean=20, win=40), 0, 200)
    assert tiny["tier"] < larger["tier"]
    assert tiny["sample_weight"] < larger["sample_weight"]
    assert "small sample" in tiny["label"]


def test_lower_drawdown_and_higher_net_expectancy_improve_same_tier_rank():
    base = profit_review(evidence(), 2, 200)
    safer = profit_review(evidence(drawdown=100), 2, 200)
    better_net = profit_review(evidence(mean=100), 2, 200)
    assert safer["score"] > base["score"]
    assert better_net["score"] > base["score"]


def test_unknown_win_rate_is_unknown_and_probability_unavailable():
    result = profit_review(evidence(n=0, mean=None, win=None, drawdown=0), 4, 200)
    assert result["tier"] == 0
    assert result["sample_weight"] == 0
    assert "unknown" in result["summary"]
    assert result["estimated_profit_probability_pct"] is None
    assert result["validated_profit_probability_pct"] is None
    assert result["probability_confidence"] == "unavailable"


def test_profit_probability_is_smoothed_and_confidence_weighted():
    tiny = profit_review(evidence(n=1, mean=5000, win=100, drawdown=0), 4, 200)
    larger = profit_review(evidence(n=50, mean=40, win=60, drawdown=100), 4, 200)
    assert tiny["estimated_profit_probability_pct"] < 100
    assert tiny["probability_confidence"] == "low"
    assert larger["probability_confidence"] == "high"
    assert larger["validated_profit_probability_pct"] is None


def test_score_is_scale_invariant_and_zero_risk_is_rejected():
    base = profit_review(evidence(), 2, 200)
    scaled = profit_review(evidence(mean=100, drawdown=600, risk=400), 2, 400)
    assert base["score"] == scaled["score"]
    with pytest.raises(ValueError):
        profit_review(evidence(), 2, 0)


def test_verified_paper_feedback_is_separate_bounded_and_sample_weighted():
    replay = evidence(n=40, mean=50, win=45, drawdown=300)
    base = profit_review(replay, 2, 200)
    one_win = profit_review(replay, 2, 200, {
        "closed_trades": 1, "risk_recorded_trades": 1, "average_r": 4.0,
        "win_rate": 100.0,
    })
    repeated_losses = profit_review(replay, 2, 200, {
        "closed_trades": 40, "risk_recorded_trades": 40, "average_r": -2.0,
        "win_rate": 10.0,
    })
    assert one_win["tier"] == base["tier"] == repeated_losses["tier"]
    assert 0 < one_win["paper_feedback_score"] < 0.02
    assert -0.2 <= repeated_losses["paper_feedback_score"] < 0
    assert repeated_losses["score"] < base["score"] < one_win["score"]
    assert one_win["estimated_profit_probability_pct"] == base["estimated_profit_probability_pct"]
    assert one_win["paper_profit_probability_pct"] < 100
    assert profit_review(replay, 2, 200, {
        "closed_trades": 1, "risk_recorded_trades": 0, "average_r": None,
        "win_rate": 100.0,
    })["paper_feedback_score"] == 0
