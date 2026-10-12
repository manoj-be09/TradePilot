from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class SecurityActivityResponse(BaseModel):
    id: UUID
    event_type: Literal[
        "auth.registration.succeeded",
        "auth.login.succeeded",
        "auth.password_change.failed",
        "auth.password_changed",
        "auth.session.revoked",
        "auth.session.legacy_upgraded",
    ]
    created_at: datetime


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: EmailStr
    role: str


class InstrumentInput(BaseModel):
    exchange: str = Field(min_length=1, max_length=16)
    segment: str = Field(min_length=1, max_length=32)
    instrument_token: str = Field(min_length=1, max_length=128)
    trading_symbol: str = Field(min_length=1, max_length=128)
    underlying: str = Field(min_length=1, max_length=32)
    instrument_type: Literal["INDEX", "EQUITY", "ETF", "FUTURE", "OPTION"]
    expiry: date | None = None
    strike: Decimal | None = Field(default=None, gt=0)
    option_type: Literal["CE", "PE"] | None = None
    lot_size: int = Field(gt=0)
    tick_size: Decimal = Field(gt=0)
    status: Literal["ACTIVE", "INACTIVE", "EXPIRED"] = "ACTIVE"
    effective_from: datetime
    effective_to: datetime | None = None
    provider: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_option_fields(self) -> Self:
        if self.instrument_type == "OPTION":
            if self.expiry is None or self.strike is None or self.option_type is None:
                raise ValueError("Options require expiry, strike, and option_type")
        elif self.instrument_type == "FUTURE":
            if self.expiry is None or self.strike is not None or self.option_type is not None:
                raise ValueError("Futures require expiry and must omit option strike and type")
        elif any(value is not None for value in (self.expiry, self.strike, self.option_type)):
            raise ValueError("Spot instruments must omit expiry, strike, and option_type")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be later than effective_from")
        if self.effective_from.tzinfo is None or (
            self.effective_to is not None and self.effective_to.tzinfo is None
        ):
            raise ValueError("effective timestamps must include a timezone")
        return self


class InstrumentResponse(InstrumentInput):
    model_config = ConfigDict(from_attributes=True)
    id: UUID

    @model_validator(mode="before")
    @classmethod
    def normalize_database_timestamps(cls, value: Any) -> Any:
        # SQLite drops tzinfo on round-trip; stored timestamp values are UTC.
        if not isinstance(value, dict) and hasattr(value, "effective_from"):
            obj = value
            value = {name: getattr(obj, name) for name in InstrumentInput.model_fields}
            value["id"] = obj.id
        if isinstance(value, dict):
            for name in ("effective_from", "effective_to"):
                timestamp = value.get(name)
                if timestamp is not None and timestamp.tzinfo is None:
                    value[name] = timestamp.replace(tzinfo=timezone.utc)
        return value
