"""Validate scheduled NIFTY replay output against a frozen study boundary."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from packages.backtesting.contracts import ScheduledOptionsBacktestResult
from packages.regime.frozen_nifty_study import FrozenNiftyStudy


class NiftyReplayAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    study_key: str
    study_manifest_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    strategy_version: str
    replay_input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["REVIEWABLE_UNVERIFIED", "INSUFFICIENT_SAMPLE"]
    trade_count: int = Field(ge=0)
    holdout_evaluated: Literal[False] = False
    promotion_eligible: Literal[False] = False
    limitations: tuple[str, ...]


def assess_nifty_scheduled_replay(
    study: FrozenNiftyStudy, replay: ScheduledOptionsBacktestResult,
) -> NiftyReplayAssessment:
    """Apply the frozen NIFTY study's chronological boundary to replay output.

    This validates an already-produced scheduled option-close replay. It does
    not fetch data, calculate a probability, or establish fill/settlement
    equivalence with the paper worker. Holdout output is always rejected.
    """
    if replay.mode != "historical_scheduled_expiry_replay":
        raise ValueError("Only scheduled NIFTY expiry replay is supported")
    if replay.strategy_version != study.incumbent_rule_version:
        raise ValueError("Replay strategy version differs from the frozen study")
    if replay.start_date < study.train_start or replay.end_date > study.validation_end:
        raise ValueError("Replay crosses the frozen validation or protected holdout boundary")
    if replay.end_date >= study.holdout_start:
        raise ValueError("Replay reaches the protected holdout")
    if len(replay.observed_input_digest) != 64:
        raise ValueError("Replay input identity is missing")
    status: Literal["REVIEWABLE_UNVERIFIED", "INSUFFICIENT_SAMPLE"] = (
        "REVIEWABLE_UNVERIFIED" if replay.trade_count >= 20 else "INSUFFICIENT_SAMPLE"
    )
    return NiftyReplayAssessment(
        study_key=study.study_key, study_manifest_digest=study.manifest_digest,
        strategy_version=replay.strategy_version,
        replay_input_digest=replay.observed_input_digest, status=status,
        trade_count=replay.trade_count,
        limitations=(
            "Scheduled option-close replay is indicative, not intraday policy evidence.",
            "Daily OHLC does not prove executable fills, depth, or exchange settlement.",
            "This assessment does not establish a calibrated profit probability.",
        ),
    )
