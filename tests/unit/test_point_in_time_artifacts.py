import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from packages.regime.point_in_time_artifacts import ArtifactRevision, select_declared_revision

START = datetime(2026, 10, 23, 9, 55, tzinfo=timezone.utc)


def revision(number: int, *, received_offset: int, raw: bytes) -> ArtifactRevision:
    return ArtifactRevision(
        kind="NIFTY_5M_BARS", provider="fixture", instrument_key="NSE_INDEX|Nifty 50",
        artifact_key="fixture-2026-10-23-0955", revision=number,
        event_at=START, available_at=START + timedelta(seconds=received_offset - 1),
        received_at=START + timedelta(seconds=received_offset),
        sha256=hashlib.sha256(raw).hexdigest(), raw=raw,
    )


def select(*items: ArtifactRevision, seconds: int = 10) -> ArtifactRevision | None:
    return select_declared_revision(
        items, kind="NIFTY_5M_BARS", provider="fixture",
        instrument_key="NSE_INDEX|Nifty 50",
        decision_at=START + timedelta(seconds=seconds),
    )


def test_late_initial_artifact_abstains() -> None:
    assert select(revision(0, received_offset=11, raw=b"original")) is None


def test_correction_after_decision_cannot_replace_earlier_bytes() -> None:
    original = revision(0, received_offset=2, raw=b"original")
    corrected = revision(1, received_offset=11, raw=b"corrected")
    assert select(original, corrected) == original
    assert select(original, corrected, seconds=12) == corrected


def test_corrected_or_mismatched_artifact_fails_closed() -> None:
    original = revision(0, received_offset=2, raw=b"original")
    with pytest.raises(ValueError, match="digest"):
        select(replace(original, raw=b"changed"))
    with pytest.raises(ValueError, match="identity"):
        select(replace(original, provider="other"))
    with pytest.raises(ValueError, match="identity"):
        select(original, replace(revision(1, received_offset=4, raw=b"different event"),
                                 event_at=START + timedelta(seconds=1)))
    with pytest.raises(ValueError, match="history"):
        select(original, revision(2, received_offset=4, raw=b"missing one"))
    with pytest.raises(ValueError, match="predates"):
        select(original, revision(1, received_offset=1, raw=b"early correction"))
    with pytest.raises(ValueError, match="inconsistent"):
        select(replace(original, available_at=START + timedelta(seconds=3)))
