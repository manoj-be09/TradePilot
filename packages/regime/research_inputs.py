"""Validate frozen daily research bytes before allowing any replay adapter access."""

import hashlib
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from packages.market_data.contracts import HistoricalBar
from packages.paper_trading.equity import ZONE
from packages.regime.candidate_registry import TrialManifest
from packages.regime.research_identity import research_code_digest


class DailyResearchDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(default=1, ge=1, le=1)
    provider: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    instrument_key: str = Field(min_length=1, max_length=100)
    bars: list[HistoricalBar] = Field(min_length=1, max_length=10000)


@dataclass(frozen=True)
class ResearchWindows:
    """Only pre-holdout bars are exposed; no evaluated or promotion status."""
    reviewed: tuple[HistoricalBar, ...]
    training_count: int
    validation_count: int
    reserved_count: int
    holdout_input_excluded: bool


def prepare_daily_windows(
    raw: bytes, manifest: TrialManifest, *, provider: str, instrument_key: str,
) -> ResearchWindows:
    """Check exact artifact bytes/code and chronology, without fetching or executing.

    Matching hashes prove identity only, not licensing, corporate-action quality,
    source authenticity or holdout secrecy outside this process. Cost compatibility
    remains the replay adapter's responsibility; this function computes no metrics.
    """
    if len(raw) > 8_000_000:
        raise ValueError("Daily research artifact exceeds the size limit")
    if hashlib.sha256(raw).hexdigest() != manifest.dataset_digest:
        raise ValueError("Dataset bytes do not match the frozen manifest")
    if research_code_digest() != manifest.calculation_digest:
        raise ValueError("Calculation source differs from the frozen manifest")
    data = DailyResearchDataset.model_validate_json(raw)
    if data.provider != provider or data.instrument_key != instrument_key:
        raise ValueError("Dataset instrument identity differs from the requested study")
    if any(bar.timestamp.tzinfo is None for bar in data.bars):
        raise ValueError("Daily bars require timezone-aware timestamps")
    days = [bar.timestamp.astimezone(ZONE).date() for bar in data.bars]
    if any(a >= b for a, b in zip(days, days[1:], strict=False)):
        raise ValueError("Daily bars must be unique and chronological")
    for bar in data.bars:
        prices = (bar.open, bar.high, bar.low, bar.close)
        if (any(not price.is_finite() or price <= 0 for price in prices)
                or not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high):
            raise ValueError("Daily bar prices are inconsistent")
    separated = manifest.holdout_artifact_digest is not None
    if separated and manifest.holdout_artifact_digest == manifest.dataset_digest:
        raise ValueError("Holdout artifact must differ from the evaluation dataset")
    expected_end = manifest.validation_end if separated else manifest.holdout_end
    if days[0] != manifest.train_start or days[-1] != expected_end:
        raise ValueError("Dataset coverage must exactly match the declared study")
    endpoints = [manifest.train_end, manifest.validation_start, manifest.validation_end]
    if not separated:
        endpoints.append(manifest.holdout_start)
    if not all(day in days for day in endpoints):
        raise ValueError("Every window endpoint must have an observed session")
    if any((b - a).days > 10 for a, b in zip(days, days[1:], strict=False)):
        raise ValueError("Daily dataset has an unexplained gap over ten days")
    reviewed = tuple(bar for day, bar in zip(days, data.bars, strict=True)
                     if day <= manifest.validation_end)
    return ResearchWindows(
        reviewed=reviewed,
        training_count=sum(manifest.train_start <= day <= manifest.train_end for day in days),
        validation_count=sum(manifest.validation_start <= day <= manifest.validation_end
                             for day in days),
        reserved_count=sum(manifest.holdout_start <= day <= manifest.holdout_end for day in days),
        holdout_input_excluded=separated,
    )
