from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WatchlistAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_key: str = Field(min_length=3, max_length=160)
    provider_id: str | None = Field(default=None, min_length=1, max_length=64)
    query: str = Field(min_length=3, max_length=50)
    exchange: Literal["NSE", "BSE", "MCX"] = "NSE"
    segment: Literal["EQ", "FO", "FUT", "OPT", "INDEX"] = "EQ"


class WatchlistItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: str
    instrument_key: str
    trading_symbol: str
    name: str | None
    exchange: str
    segment: str
    instrument_type: str
    expiry: date | None
    created_at: datetime
