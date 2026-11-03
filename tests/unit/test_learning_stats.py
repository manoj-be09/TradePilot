"""The v2 evidence, budget and power checks hold their error rates on simulated data."""

import random

import pytest

from packages.learning.budget import FAMILY_WISE_ALPHA, alpha_for, remaining_after
from packages.learning.evidence import Trade, futility_evidence, growth_evidence
from packages.learning.power import power_check


def _condor_trades(rng: random.Random, count: int, edge: float) -> list[Trade]:
    """Win the credit often, lose the wing sometimes; `edge` shifts the win rate."""
    trades = []
    for _ in range(count):
        win = rng.random() < 0.8 + edge
        trades.append(Trade(4_000.0 if win else -16_000.0, 20_000.0, 5_000.0))
    return trades


def _peak(trades: list[Trade], evidence) -> float:
    """Highest evidence over every nightly look, as a peeking analyst would see it."""
    return max(evidence(trades[:end]) for end in range(1, len(trades) + 1))


def test_a_rule_without_an_edge_is_almost_never_promoted_however_often_it_is_checked():
    rng = random.Random(7)
    alpha = 0.05
    # Win rate 0.8 with these sizes is an average of exactly zero.
    false = sum(_peak(_condor_trades(rng, 120, 0.0), growth_evidence) >= 1 / alpha
                for _ in range(400))
    assert false / 400 <= alpha


def test_a_real_edge_is_found_and_a_losing_rule_is_dropped_early():
    rng = random.Random(11)
    winners = [growth_evidence(_condor_trades(rng, 150, 0.1)) for _ in range(50)]
    # A win rate of 0.9 is a large edge (₹2,000 a trade); most runs find it by 150 trades.
    assert sum(value >= 20 for value in winners) >= 30
    # A ₹3,000-a-trade loser: futility evidence of 10 means at most a 10% chance of dropping a
    # rule that does not lose, which only costs a missed idea, so it can be looser than promotion.
    losers = [futility_evidence(_condor_trades(rng, 100, -0.15)) for _ in range(50)]
    assert sum(value >= 10 for value in losers) >= 30


def test_trades_outside_their_bounds_are_refused():
    with pytest.raises(ValueError):
        Trade(-25_000.0, 20_000.0, 5_000.0)
    with pytest.raises(ValueError):
        Trade(100.0, 0.0, 5_000.0)


def test_the_budget_never_exceeds_five_percent_and_tightens_with_each_idea():
    slices = [alpha_for(k) for k in range(1, 5001)]
    assert sum(slices) < FAMILY_WISE_ALPHA
    assert all(later < earlier for earlier, later in zip(slices, slices[1:], strict=False))
    assert remaining_after(0) == FAMILY_WISE_ALPHA
    assert sum(slices[:100]) + remaining_after(100) == pytest.approx(FAMILY_WISE_ALPHA)
    with pytest.raises(ValueError):
        alpha_for(0)


def test_power_check_refuses_ideas_that_could_not_finish_in_six_months():
    alpha = alpha_for(1)
    weak = power_check(50.0, 6_000.0, 20_000.0, 0.3, alpha)
    strong = power_check(3_000.0, 6_000.0, 20_000.0, 0.5, alpha)
    assert not weak.feasible and weak.sessions_needed > 125
    assert strong.feasible and strong.trades_needed < 125
    assert not power_check(-10.0, 6_000.0, 20_000.0, 1.0, alpha).feasible


def test_the_backtest_screen_rejects_noise_and_keeps_a_steady_edge():
    from datetime import date, timedelta

    from packages.learning.screen import (
        deflated_sharpe,
        overfit_probability,
        placebo_p,
        screen,
    )

    rng = random.Random(3)
    days = [date(2025, 1, 1) + timedelta(days=offset) for offset in range(200)]
    noise = [(day, rng.gauss(0, 3_000)) for day in days]
    edge = [(day, rng.gauss(1_500, 3_000)) for day in days]
    assert screen(noise, days, trials=50)
    assert screen(edge, days, trials=50) == []
    assert screen(edge[:10], days, trials=1)[0].startswith("10 backtest trades")
    # More trials raise the bar a lucky Sharpe has to clear.
    values = [value for _, value in edge[:60]]
    assert deflated_sharpe(values, 1000) < deflated_sharpe(values, 1)
    every_day = [rng.gauss(0, 3_000) for _ in range(400)]
    assert placebo_p(rng.sample(every_day, 50), every_day) > 0.05
    assert placebo_p([5_000.0] * 50, every_day) < 0.01
    variants = [[rng.gauss(0, 1) for _ in range(160)] for _ in range(10)]
    assert overfit_probability(variants) > 0.2
    steady = [[rng.gauss(1 if row == 0 else 0, 0.3) for _ in range(160)] for row in range(10)]
    assert overfit_probability(steady) < 0.05
