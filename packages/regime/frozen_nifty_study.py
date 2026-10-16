"""Small frozen NIFTY study contract; no data access, replay or promotion."""
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.regime.candidate_registry import fingerprint


class FrozenNiftyStudy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    study_key: str = Field(min_length=3, max_length=100)
    underlying: Literal["NIFTY"] = "NIFTY"
    market: Literal["NIFTY_OPTIONS"] = "NIFTY_OPTIONS"
    objective: str = Field(min_length=20, max_length=1000)
    incumbent_rule_version: str = Field(min_length=1, max_length=64)
    no_trade_comparator: Literal["NO_TRADE"] = "NO_TRADE"
    dataset_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    calculation_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    holdout_artifact_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    cost_model_id: str = Field(min_length=3, max_length=100)
    capital: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    parent_search_budget: int = Field(ge=1, le=10000)
    review_interval_days: int = Field(ge=1, le=365)
    uncertainty_note: str = Field(min_length=20, max_length=1000)
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    holdout_start: date
    holdout_end: date

    @model_validator(mode="after")
    def chronology(self) -> "FrozenNiftyStudy":
        if not (self.train_start <= self.train_end < self.validation_start
                <= self.validation_end < self.holdout_start <= self.holdout_end):
            raise ValueError("Study windows must be chronological and non-overlapping")
        return self

    @property
    def manifest(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    @property
    def manifest_digest(self) -> str:
        return fingerprint(self.manifest)
