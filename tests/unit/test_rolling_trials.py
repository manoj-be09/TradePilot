from datetime import date, timedelta

import pytest

from packages.regime.candidate_registry import TrialManifest
from packages.regime.rolling_trials import rolling_manifests


def test_rolling_plans_preserve_holdout_identity_and_reject_leakage():
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(40)]
    base = TrialManifest(
        dataset_digest="a" * 64, calculation_digest="b" * 64,
        cost_model_id="test-cost-v1", experiment_family="fixed-study", seed=7,
        trial_number=2, declared_search_budget=4,
        train_start=days[0], train_end=days[9], validation_start=days[12],
        validation_end=days[16], holdout_start=days[30], holdout_end=days[39],
    )
    kwargs = dict(step_sessions=4, purge_sessions=2, count=3)
    plans = rolling_manifests(base, days, **kwargs)
    assert [p.trial_number for p in plans] == [2, 3, 4]
    assert plans[-1].train_start == days[8]
    assert plans[-1].validation_end == days[24]
    assert all(p.holdout_start == days[30] and p.holdout_end == days[39] for p in plans)
    assert all(p.dataset_digest == base.dataset_digest and p.seed == 7 for p in plans)
    assert base.train_start == days[0]
    assert plans == rolling_manifests(base, days, **kwargs)
    for changes, calendar in (
        ({"count": 4}, days),  # Budget cannot be silently truncated.
        ({"step_sessions": 6}, days),  # Validation reaches protected gap.
        ({"purge_sessions": 3}, days),
        ({"step_sessions": True}, days),
        ({}, list(reversed(days))),
        ({}, days + [days[-1]]),
        ({}, days[1:]),
    ):
        with pytest.raises(ValueError):
            rolling_manifests(base, calendar, **(kwargs | changes))
