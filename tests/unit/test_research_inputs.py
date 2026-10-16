import hashlib
import json
from datetime import date, timedelta

import pytest

from packages.regime.candidate_registry import TrialManifest
from packages.regime.research_identity import research_code_digest
from packages.regime.research_inputs import prepare_daily_windows


def dataset():
    start = date(2020, 1, 1)
    return {"provider": "fixture", "instrument_key": "TEST_ONLY", "bars": [
        {"timestamp": f"{start + timedelta(days=i)}T09:15:00+05:30",
         "open": "100", "high": "102", "low": "99", "close": "101", "volume": 10}
        for i in range(90)
    ]}


def encode(data):
    raw = json.dumps(data).encode()
    manifest = TrialManifest(
        dataset_digest=hashlib.sha256(raw).hexdigest(),
        calculation_digest=research_code_digest(), cost_model_id="test-only",
        experiment_family="input-check", trial_number=1, declared_search_budget=1, seed=0,
        train_start="2020-01-01", train_end="2020-02-09",
        validation_start="2020-02-12", validation_end="2020-03-10",
        holdout_start="2020-03-13", holdout_end="2020-03-30",
    )
    return raw, manifest


def prepare(raw, manifest):
    return prepare_daily_windows(raw, manifest, provider="fixture", instrument_key="TEST_ONLY")


def test_prepared_windows_never_expose_holdout_and_preserve_exact_identity():
    raw, manifest = encode(dataset())
    result = prepare(raw, manifest)
    assert result.training_count == 40
    assert result.validation_count == 28
    assert result.reserved_count == 18
    assert len(result.reviewed) == 70
    assert all(bar.timestamp.date() <= manifest.validation_end for bar in result.reviewed)
    with pytest.raises(ValueError, match="Dataset bytes"):
        prepare(raw + b" ", manifest)
    stale = TrialManifest.model_validate(manifest.model_dump() | {"calculation_digest": "0" * 64})
    with pytest.raises(ValueError, match="Calculation source"):
        prepare(raw, stale)


@pytest.mark.parametrize("problem", ["reverse", "duplicate", "naive", "prices", "instrument",
                                    "endpoint", "gap", "nonpositive"])
def test_rejects_bad_input_even_when_hash_matches(problem):
    data = dataset()
    if problem == "reverse":
        data["bars"].reverse()
    elif problem == "duplicate":
        data["bars"][1] = data["bars"][0]
    elif problem == "naive":
        data["bars"][0]["timestamp"] = "2020-01-01T09:15:00"
    elif problem == "prices":
        data["bars"][0]["high"] = "99"
    elif problem == "instrument":
        data["instrument_key"] = "DIFFERENT"
    elif problem == "endpoint":
        data["bars"].pop(39)
    elif problem == "gap":
        del data["bars"][10:25]
    elif problem == "nonpositive":
        data["bars"][0]["low"] = "0"
    raw, manifest = encode(data)
    with pytest.raises(ValueError):
        prepare(raw, manifest)
