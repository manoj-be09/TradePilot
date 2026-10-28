from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, TypeDecorator

from packages.database.base import Base


class PortableJSON(TypeDecorator[dict[str, Any]]):
    """Use JSONB in PostgreSQL and JSON for lightweight SQLite tests."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('ADMIN', 'TRADER', 'VIEWER')", name="ck_users_role"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="VIEWER", server_default="VIEWER")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (Index("ix_auth_sessions_user_created", "user_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = (
        CheckConstraint("lot_size > 0", name="ck_instruments_lot_size_positive"),
        CheckConstraint("tick_size > 0", name="ck_instruments_tick_size_positive"),
        CheckConstraint(
            "instrument_type IN ('INDEX', 'EQUITY', 'ETF', 'FUTURE', 'OPTION')",
            name="ck_instruments_type",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE', 'EXPIRED')", name="ck_instruments_status"
        ),
        CheckConstraint(
            "option_type IN ('CE', 'PE') OR option_type IS NULL", name="ck_instruments_option_type"
        ),
        CheckConstraint(
            "(instrument_type = 'OPTION' AND expiry IS NOT NULL AND strike IS NOT NULL "
            "AND option_type IN ('CE', 'PE')) OR "
            "(instrument_type IN ('INDEX', 'EQUITY', 'ETF') "
            "AND expiry IS NULL AND strike IS NULL AND option_type IS NULL) OR "
            "(instrument_type = 'FUTURE' AND expiry IS NOT NULL AND strike IS NULL "
            "AND option_type IS NULL)",
            name="ck_instruments_option_fields",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_instruments_effective_range",
        ),
        UniqueConstraint(
            "exchange", "instrument_token", "effective_from", name="uq_instruments_token_version"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    exchange: Mapped[str] = mapped_column(String(16))
    segment: Mapped[str] = mapped_column(String(32))
    instrument_token: Mapped[str] = mapped_column(String(128))
    trading_symbol: Mapped[str] = mapped_column(String(128), index=True)
    underlying: Mapped[str] = mapped_column(String(32), index=True)
    instrument_type: Mapped[str] = mapped_column(String(32))
    expiry: Mapped[date | None] = mapped_column(Date)
    strike: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    option_type: Mapped[str | None] = mapped_column(String(2))
    lot_size: Mapped[int] = mapped_column(Integer)
    tick_size: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", server_default="ACTIVE")
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider: Mapped[str] = mapped_column(String(64))


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", "instrument_key", name="uq_watchlist_user_key"),
        Index("ix_watchlist_user_created", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    instrument_key: Mapped[str] = mapped_column(String(160))
    trading_symbol: Mapped[str] = mapped_column(String(128))
    name: Mapped[str | None] = mapped_column(String(256))
    exchange: Mapped[str] = mapped_column(String(16))
    segment: Mapped[str] = mapped_column(String(32))
    instrument_type: Mapped[str] = mapped_column(String(24))
    expiry: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index(
            "ix_audit_events_owner_correlation_type_created",
            "actor_user_id", "correlation_id", "event_type", "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor_user_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    event_type: Mapped[str] = mapped_column(String(128))
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    details: Mapped[dict[str, Any]] = mapped_column(
        PortableJSON, default=dict, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )


class PaperAccount(Base):
    __tablename__ = "paper_accounts"
    __table_args__ = (
        CheckConstraint("initial_cash > 0", name="ck_paper_initial_cash_positive"),
        CheckConstraint("cash_balance >= 0", name="ck_paper_cash_balance_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), unique=True)
    initial_cash: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    kill_switch_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaperTrade(Base):
    __tablename__ = "paper_trades"
    __table_args__ = (
        CheckConstraint("lots > 0", name="ck_paper_trade_lots_positive"),
        CheckConstraint("lot_size > 0", name="ck_paper_trade_lot_size_positive"),
        CheckConstraint("max_loss >= 0", name="ck_paper_trade_max_loss_nonnegative"),
        CheckConstraint("status IN ('OPEN', 'CLOSED')", name="ck_paper_trade_status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("paper_accounts.id"), index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    strategy_key: Mapped[str] = mapped_column(String(64))
    strategy_name: Mapped[str] = mapped_column(String(128))
    underlying: Mapped[str] = mapped_column(String(32))
    expiry: Mapped[date] = mapped_column(Date)
    reference_strike: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    lots: Mapped[int] = mapped_column(Integer)
    lot_size: Mapped[int] = mapped_column(Integer)
    max_loss: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    entry_cash_flow: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    entry_rule_snapshot: Mapped[dict[str, Any]] = mapped_column(
        PortableJSON, default=dict, server_default=text("'{}'")
    )
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    stop_loss_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    target_profit_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    trailing_stop_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    peak_unrealized_pnl: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal(0), server_default="0"
    )
    exit_reason: Mapped[str | None] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default="OPEN", server_default="OPEN")
    legs: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaperOrder(Base):
    __tablename__ = "paper_orders"
    __table_args__ = (
        CheckConstraint("action IN ('OPEN', 'CLOSE')", name="ck_paper_order_action"),
        CheckConstraint("status = 'EXECUTED'", name="ck_paper_order_status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("paper_accounts.id"), index=True)
    trade_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("paper_trades.id"), index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), default="EXECUTED", server_default="EXECUTED")
    net_cash_flow: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    fill_snapshot: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaperCapitalAdjustment(Base):
    """Auditable positive-only simulated cash funding for an F&O paper account."""

    __tablename__ = "paper_capital_adjustments"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "idempotency_key", name="uq_paper_capital_adjustment_key"
        ),
        CheckConstraint("cash_delta > 0", name="ck_paper_capital_adjustment_positive"),
        CheckConstraint(
            "cash_after = cash_before + cash_delta",
            name="ck_paper_capital_adjustment_balance",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("paper_accounts.id"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    cash_before: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    cash_delta: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    cash_after: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )


class UserNotification(Base):
    __tablename__ = "user_notifications"
    __table_args__ = (Index("ix_user_notifications_user_created", "user_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(160))
    message: Mapped[str] = mapped_column(String(512))
    details: Mapped[dict[str, Any]] = mapped_column(
        PortableJSON, default=dict, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    __table_args__ = (Index("ix_backtest_runs_user_created", "user_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    result_data: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaperStrategyPreset(Base):
    __tablename__ = "paper_strategy_presets"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_paper_strategy_presets_user_name"),
        Index("ix_paper_strategy_presets_user_created", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    strategy_key: Mapped[str] = mapped_column(String(64))
    entry_conditions: Mapped[list[dict[str, Any]]] = mapped_column(PortableJSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (
        UniqueConstraint("user_id", "name", "version_number", name="uq_strategy_version_number"),
        Index("ix_strategy_versions_user_name", "user_id", "name"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    version_number: Mapped[int] = mapped_column(Integer)
    definition: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    definition_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaperEntryCommand(Base):
    __tablename__ = "paper_entry_commands"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_paper_entry_command"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    trade_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("paper_trades.id"))
    response_data: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaperDeployment(Base):
    """An armed options paper rule that waits for a future qualifying chain."""

    __tablename__ = "paper_deployments"
    __table_args__ = (
        UniqueConstraint("user_id", "command_key", name="uq_paper_deployment_command"),
        Index("ix_paper_deployment_status", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    command_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    request_data: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    status: Mapped[str] = mapped_column(String(24))
    detail: Mapped[str] = mapped_column(String(256))
    trade_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("paper_trades.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EquityAccount(Base):
    __tablename__ = "equity_accounts"

    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), primary_key=True)
    initial_cash: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("1000000"), server_default="1000000.00"
    )
    cash: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("1000000"))


class EquityCashEvent(Base):
    """Append-only paper cash trail; a legacy checkpoint does not invent past fills."""

    __tablename__ = "equity_cash_events"
    __table_args__ = (
        UniqueConstraint("user_id", "event_key", name="uq_equity_cash_event_key"),
        Index("ix_equity_cash_user_id", "user_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    event_key: Mapped[str] = mapped_column(String(128), nullable=False)
    deployment_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    delta: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    cash_after: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        server_default=func.now(), nullable=False,
    )


class EquityDeployment(Base):
    __tablename__ = "equity_deployments"
    __table_args__ = (
        UniqueConstraint("user_id", "command_key", name="uq_equity_command"),
        Index("ix_equity_user_status", "user_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    command_key: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(32))
    instrument_key: Mapped[str] = mapped_column(String(160))
    symbol: Mapped[str] = mapped_column(String(128))
    strategy: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(32))
    budget: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    risk_budget: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    status: Mapped[str] = mapped_column(String(32))
    state: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EquityValidationStudy(Base):
    """Immutable account-scoped generations of equity validation evidence."""

    __tablename__ = "equity_validation_studies"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "provider", "instrument_key", "generation",
            name="uq_equity_validation_generation",
        ),
        UniqueConstraint("parent_study_id", name="uq_equity_validation_parent"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    parent_study_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("equity_validation_studies.id", name="fk_equity_validation_parent"),
        nullable=True,
    )
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    instrument_key: Mapped[str] = mapped_column(String(160))
    symbol: Mapped[str] = mapped_column(String(128))
    rule_version: Mapped[str] = mapped_column(String(32))
    allocation: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    risk_budget: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    dataset_digest: Mapped[str] = mapped_column(String(64))
    dataset_manifest: Mapped[dict[str, Any] | None] = mapped_column(
        PortableJSON, nullable=True
    )
    report_digest: Mapped[str] = mapped_column(String(64))
    report: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EquityPolicyTrial(Base):
    """One immutable, predeclared exit-policy comparison per frozen study and rule."""

    __tablename__ = "equity_policy_trials"
    __table_args__ = (
        UniqueConstraint(
            "study_id", "strategy", "registry_version", name="uq_equity_policy_trial"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    study_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("equity_validation_studies.id")
    )
    strategy: Mapped[str] = mapped_column(String(32))
    registry_version: Mapped[str] = mapped_column(String(40))
    trial_manifest: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    manifest_digest: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    result_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StrategyCatalogEntry(Base):
    """Curated, source-linked methods; paper eligibility is explicit."""

    __tablename__ = "strategy_catalog"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(24))
    implementation_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer)
    catalog_version: Mapped[str] = mapped_column(String(24))
    details: Mapped[dict[str, Any]] = mapped_column(PortableJSON)


class WorkerLease(Base):
    __tablename__ = "worker_leases"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64))
    generation: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24))
    detail: Mapped[str] = mapped_column(String(512), default="")


class ResearchCandidate(Base):
    """Frozen account-owned hypotheses; never executable strategy deployments."""
    __tablename__ = "research_candidates"
    __table_args__ = (UniqueConstraint("user_id", "definition_digest",
                                       name="uq_research_candidate_definition"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    definition_digest: Mapped[str] = mapped_column(String(64))
    definition: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResearchTrial(Base):
    """Predeclared attempts with single-finalization evidence, including failures."""
    __tablename__ = "research_trials"
    __table_args__ = (UniqueConstraint("user_id", "candidate_id", "manifest_digest",
                                       name="uq_research_trial_manifest"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    candidate_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("research_candidates.id"))
    manifest_digest: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    result_digest: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[dict[str, Any] | None] = mapped_column(PortableJSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResearchExperiment(Base):
    """Frozen account/family budget and study scope; not a promotion decision."""
    __tablename__ = "research_experiments"
    __table_args__ = (
        UniqueConstraint("user_id", "family", name="uq_research_experiment_family"),
        CheckConstraint("budget >= 1 AND budget <= 10000", name="ck_research_budget"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    family: Mapped[str] = mapped_column(String(100))
    budget: Mapped[int] = mapped_column(Integer)
    scope_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResearchBudgetSlot(Base):
    """An occupied slot stays consumed after success, failure or interruption."""
    __tablename__ = "research_budget_slots"
    __table_args__ = (
        UniqueConstraint("trial_id", name="uq_research_budget_trial"),
        CheckConstraint("slot >= 1 AND slot <= 10000", name="ck_research_slot"),
    )
    experiment_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("research_experiments.id"), primary_key=True,
    )
    slot: Mapped[int] = mapped_column(Integer, primary_key=True)
    trial_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("research_trials.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResearchEvaluation(Base):
    """Frozen typed replay binding and separate engine-owned research evidence."""
    __tablename__ = "research_evaluations"
    trial_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("research_trials.id"), primary_key=True,
    )
    adapter: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    adapter_digest: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, Any] | None] = mapped_column(PortableJSON)
    result_digest: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResearchEvidence(Base):
    """Immutable reviewed evidence references; never a promotion decision."""
    __tablename__ = "research_evidence"
    __table_args__ = (
        UniqueConstraint("trial_id", "key", name="uq_research_evidence_trial_key"),
        CheckConstraint(
            "status IN ('PASS', 'FAIL', 'UNKNOWN', 'NOT_APPLICABLE')",
            name="ck_research_evidence_status",
        ),
        CheckConstraint(
            "allowed_use IN ('INSPECT_RESEARCH_RECORDS', 'RESEARCH_ONLY', 'QUALIFIED_PAPER')",
            name="ck_research_evidence_allowed_use",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    trial_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("research_trials.id"), index=True)
    key: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20))
    detail: Mapped[str] = mapped_column(String(1000))
    artifact_ref: Mapped[str] = mapped_column(String(500))
    artifact_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_version: Mapped[str] = mapped_column(String(128))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    allowed_use: Mapped[str] = mapped_column(String(32))
    evidence_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResearchStudy(Base):
    """Immutable account-scoped parent study contract and search budget."""
    __tablename__ = "research_studies"
    __table_args__ = (UniqueConstraint("user_id", "study_key",
                                       name="uq_research_study_key"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    study_key: Mapped[str] = mapped_column(String(100))
    manifest_digest: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResearchRecommendation(Base):
    """Immutable prospective review/abstention snapshot; never an order."""
    __tablename__ = "research_recommendations"
    __table_args__ = (
        UniqueConstraint("user_id", "snapshot_digest",
                         name="uq_research_recommendation_snapshot"),
        CheckConstraint(
            "decision IN ('ABSTAIN', 'REVIEW_CANDIDATE')",
            name="ck_research_recommendation_decision",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    study_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("research_studies.id"), index=True)
    decision: Mapped[str] = mapped_column(String(24))
    reason_code: Mapped[str] = mapped_column(String(48))
    snapshot: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    snapshot_digest: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResearchPolicySet(Base):
    """One frozen, research-only policy comparison bound to a parent study."""
    __tablename__ = "research_policy_sets"
    __table_args__ = (UniqueConstraint("study_id", name="uq_research_policy_set_study"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    study_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("research_studies.id"), index=True)
    manifest: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    manifest_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResearchOpportunityEvent(Base):
    """Append-only, unqualified outcome evidence for a research opportunity."""
    __tablename__ = "research_opportunity_events"
    __table_args__ = (
        UniqueConstraint("recommendation_id", "event_key",
                         name="uq_research_opportunity_event_key"),
        CheckConstraint(
            "status IN ('UNRESOLVED', 'SHADOW_NO_FILL', 'SHADOW_REJECTED')",
            name="ck_research_opportunity_event_status",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    recommendation_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("research_recommendations.id"), index=True)
    event_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    payload_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
