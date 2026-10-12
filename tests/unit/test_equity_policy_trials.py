from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from packages.market_data.contracts import HistoricalBar
from packages.paper_trading.equity import DEFAULT_EXIT_POLICY, ReplayTrade
from packages.regime.equity_policy_trials import (
    POLICIES,
    compare_exit_policies,
)


def _bars():
    start = datetime(2023, 1, 1, tzinfo=timezone.utc)
    return [HistoricalBar(
        timestamp=start + timedelta(days=index), open=Decimal(100),
        high=Decimal(101), low=Decimal(99), close=Decimal(100), volume=100,
    ) for index in range(760)]


def test_trial_registry_is_bounded_and_does_not_replay_reserved_bars(monkeypatch):
    bars = _bars()
    observed = []

    def replay(_key, input_bars, _budget, _risk, *, policy):
        observed.append((len(input_bars), policy.version))
        return [], False

    monkeypatch.setattr("packages.regime.equity_policy_trials.replay_events", replay)
    result = compare_exit_policies(
        bars, provider="fixture", symbol="TEST", strategy="trend",
        study_id="frozen", dataset_digest="a" * 64,
        budget=Decimal(10000), risk_budget=Decimal(200),
    )
    assert POLICIES[0] == DEFAULT_EXIT_POLICY
    assert len({policy.version for policy in POLICIES}) == 3
    assert observed == [(608, policy.version) for policy in POLICIES]
    assert result.reserved_from == bars[608].timestamp.date()
    assert not result.holdout_evaluated
    assert not result.parameters_changed and not result.ranking_changed
    assert all(row.status == "insufficient" and row.validation_win_pct is None
               for row in result.rows)


def test_trial_metrics_require_each_policy_to_have_both_window_samples(monkeypatch):
    bars = _bars()

    def event(signal_index, exit_index, pnl):
        return ReplayTrade(
            signal_at=bars[signal_index].timestamp,
            entry_at=bars[signal_index + 1].timestamp,
            exit_at=bars[exit_index].timestamp,
            net_pnl=Decimal(pnl), initial_risk=Decimal(100),
        )

    def replay(_key, _input_bars, _budget, _risk, *, policy):
        training = [event(index, index + 1, "100") for index in range(100, 120)]
        validation = [event(index, index + 1, "-50") for index in range(500, 520)]
        crossing = [event(450, 460, "1000")]
        if policy.version != DEFAULT_EXIT_POLICY.version:
            validation.pop()
        return training + crossing + validation, False

    monkeypatch.setattr("packages.regime.equity_policy_trials.replay_events", replay)
    result = compare_exit_policies(
        bars, provider="fixture", symbol="TEST", strategy="trend",
        study_id="frozen", dataset_digest="a" * 64,
        budget=Decimal(10000), risk_budget=Decimal(200),
    )
    baseline, *candidates = result.rows
    assert baseline.status == "reviewable"
    assert baseline.training_trades == 20 and baseline.validation_trades == 20
    assert baseline.boundary_trades_excluded == 1
    assert baseline.training_average_r == 1
    assert baseline.validation_average_r == -0.5
    assert baseline.validation_average_net_pnl == -50
    assert baseline.training_win_pct == 100 and baseline.validation_win_pct == 0
    assert all(row.status == "insufficient" and row.validation_average_r is None
               for row in candidates)
    with pytest.raises(ValueError, match="Unknown"):
        compare_exit_policies(
            bars, provider="fixture", symbol="TEST", strategy="not-in-catalog",
            study_id="frozen", dataset_digest="a" * 64,
            budget=Decimal(10000), risk_budget=Decimal(200),
        )
