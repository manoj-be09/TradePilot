from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.api.paper_schemas import PaperEntryConditionRequest, StrategyLegDefinitionResponse
from packages.market_data.index_catalog import OptionIndex


class StrategyVersionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    strategy_key: str = Field(min_length=1, max_length=64)
    lots: int = Field(ge=1, le=10)
    entry_conditions: list[PaperEntryConditionRequest] = Field(default_factory=list, max_length=5)
    stop_loss_amount: Decimal | None = Field(default=None, gt=0)
    target_profit_amount: Decimal | None = Field(default=None, gt=0)
    trailing_stop_amount: Decimal | None = Field(default=None, gt=0)
    base_version_id: UUID | None = None
    underlying: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Strategy name cannot be blank")
        return value


class StrategyDefinitionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    option_index: OptionIndex | None = None
    template_key: str
    template_version: str
    template_legs: list[StrategyLegDefinitionResponse]
    lots: int
    entry_conditions: list[PaperEntryConditionRequest]
    stop_loss_amount: Decimal | None
    target_profit_amount: Decimal | None
    trailing_stop_amount: Decimal | None


class StrategyVersionResponse(BaseModel):
    id: UUID
    name: str
    version_number: int
    definition_hash: str
    definition: StrategyDefinitionSnapshot
    created_at: datetime

    @classmethod
    def from_model(cls, model: Any) -> "StrategyVersionResponse":
        created_at = model.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return cls(
            id=model.id,
            name=model.name,
            version_number=model.version_number,
            definition_hash=model.definition_hash,
            definition=StrategyDefinitionSnapshot.model_validate(model.definition),
            created_at=created_at,
        )
