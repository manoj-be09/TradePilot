import pytest
from pydantic import ValidationError

from packages.regime.frozen_nifty_study import FrozenNiftyStudy


def study(**changes):
    return FrozenNiftyStudy(**({
        "study_key": "nifty50-paper-v1",
        "objective": ("Compare the incumbent NIFTY option rule with a no-trade "
                       "comparator on later unseen sessions."),
        "incumbent_rule_version": "nifty-paper-v1", "dataset_digest": "a" * 64,
        "calculation_digest": "b" * 64, "cost_model_id": "option-close-illustrative-v1",
        "capital": "100000.00", "review_interval_days": 30,
        "uncertainty_note": ("Results remain indicative until source rights, fill timing "
                             "and expiry evidence are reviewed."),
        "train_start": "2020-01-01", "train_end": "2022-12-31",
        "validation_start": "2023-01-01", "validation_end": "2024-12-31",
        "holdout_start": "2025-01-01", "holdout_end": "2025-12-31",
    } | changes))


def test_manifest_is_canonical_and_frozen():
    first = study()
    second = study()
    assert first.manifest == second.manifest
    assert first.manifest_digest == second.manifest_digest
    with pytest.raises(ValidationError):
        first.study_key = "changed"


@pytest.mark.parametrize("changes", [
    {"validation_start": "2022-12-31"},
    {"holdout_start": "2024-06-01"},
    {"market": "EQUITY"},
    {"capital": "0"},
])
def test_contract_rejects_leakage_or_unsupported_scope(changes):
    with pytest.raises(ValidationError):
        study(**changes)
