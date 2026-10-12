from functools import lru_cache
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = (
        "postgresql+psycopg://platform:local-only-change-me@localhost:5432/nifty_platform"
    )
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: SecretStr = SecretStr("development-only-change-me")
    upstox_analytics_token: SecretStr | None = None
    cors_origins: str = (
        "http://127.0.0.1:3000,http://localhost:3000,"
        "http://127.0.0.1:3010,http://localhost:3010"
    )
    environment: str = "development"
    live_trading_enabled: bool = False
    paper_trading_enabled: bool = True
    initial_paper_capital: int = Field(default=1_000_000, gt=0)
    initial_equity_paper_capital: int = Field(default=1_000_000, gt=0)
    paper_max_trade_risk_pct: int = Field(default=2, gt=0, le=100)
    paper_max_portfolio_risk_pct: int = Field(default=10, gt=0, le=100)
    paper_daily_loss_limit_pct: int = Field(default=5, gt=0, le=100)
    paper_max_open_trades: int = Field(default=10, gt=0, le=100)
    paper_quote_max_age_seconds: int = Field(default=60, ge=1, le=300)
    paper_expiry_square_off_minutes_before_close: int = Field(default=20, ge=5, le=60)
    paper_allow_unverified_quotes: bool = False
    paper_worker_interval_seconds: int = Field(default=15, ge=1, le=60)
    paper_worker_lease_seconds: int = Field(default=120, ge=30, le=600)
    access_token_minutes: int = Field(default=30, gt=0, le=1440)
    session_max_minutes: int = Field(default=720, gt=0, le=1440)
    login_ip_attempts: int = Field(default=20, ge=1, le=1000)
    login_identifier_attempts: int = Field(default=8, ge=1, le=1000)
    login_window_seconds: int = Field(default=900, ge=30, le=3600)
    registration_ip_attempts: int = Field(default=12, ge=1, le=1000)
    registration_identifier_attempts: int = Field(default=3, ge=1, le=1000)
    registration_window_seconds: int = Field(default=3600, ge=30, le=86400)

    @model_validator(mode="after")
    def require_deployment_secret(self) -> Self:
        if self.environment.lower() != "development":
            secret = self.jwt_secret.get_secret_value()
            if len(secret) < 32 or secret.startswith("replace-with-"):
                raise ValueError(
                    "JWT_SECRET must be unique, non-placeholder, and at least "
                    "32 characters outside development"
                )
        return self

    @property
    def effective_live_trading_enabled(self) -> bool:
        """Live trading remains locked in this foundation phase."""
        return False

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
