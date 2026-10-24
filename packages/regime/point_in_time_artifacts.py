"""Validate declared, offline research revisions without retaining provider bytes.

These checks establish internal chronology and byte identity only. They cannot
establish source authenticity, publication time, retention rights, or permission
to use a live feed for simulation.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class ArtifactRevision:
    kind: Literal["NIFTY_CHAIN", "NIFTY_5M_BARS"]
    provider: str
    instrument_key: str
    artifact_key: str
    revision: int
    event_at: datetime
    available_at: datetime
    received_at: datetime
    sha256: str
    raw: bytes


def select_declared_revision(
    revisions: tuple[ArtifactRevision, ...], *, kind: Literal["NIFTY_CHAIN", "NIFTY_5M_BARS"],
    provider: str, instrument_key: str, decision_at: datetime,
) -> ArtifactRevision | None:
    """Select the latest revision received by a decision, or abstain.

    All supplied revisions must describe one artifact. Late corrections may be
    present for audit, but never replace the version visible at an earlier
    decision. A missing revision-zero ancestor makes the history incomplete.
    """
    if decision_at.tzinfo is None:
        raise ValueError("Decision time must include a timezone")
    if not revisions:
        return None
    artifact_key = revisions[0].artifact_key
    seen: set[int] = set()
    for item in revisions:
        if (item.kind != kind or item.provider != provider
                or item.instrument_key != instrument_key
                or not artifact_key or item.artifact_key != artifact_key
                or item.event_at != revisions[0].event_at):
            raise ValueError("Artifact source or identity mismatch")
        if item.revision < 0 or item.revision in seen:
            raise ValueError("Artifact revisions must be unique and nonnegative")
        seen.add(item.revision)
        if any(value.tzinfo is None for value in (
            item.event_at, item.available_at, item.received_at,
        )) or not item.event_at <= item.available_at <= item.received_at:
            raise ValueError("Artifact event, availability and receipt times are inconsistent")
        if len(item.sha256) != 64 or hashlib.sha256(item.raw).hexdigest() != item.sha256:
            raise ValueError("Artifact bytes differ from the declared digest")
    if seen != set(range(len(revisions))):
        raise ValueError("Artifact correction history is incomplete")
    ordered = sorted(revisions, key=lambda item: item.revision)
    if any(later.available_at < earlier.available_at
           or later.received_at < earlier.received_at
           for earlier, later in zip(ordered, ordered[1:], strict=False)):
        raise ValueError("Artifact correction predates its earlier revision")
    eligible = [item for item in ordered if item.received_at <= decision_at]
    return eligible[-1] if eligible else None
