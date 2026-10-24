"""Offline deterministic adapter for existing equity rules, never generated code."""

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from packages.backtesting.performance import PerformanceTrade, summarize
from packages.paper_trading.equity import FEE, SLIPPAGE, STRATEGIES, ZONE, replay_events
from packages.regime.candidate_registry import TrialManifest, fingerprint
from packages.regime.research_inputs import prepare_daily_windows

COST_MODEL_ID = "equity-daily-illustrative-v1"
MIN_TRADES = 20


class FixedEquitySpec(BaseModel):
    """Explicit adapter inputs: callers must freeze this separately before replay."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    strategy: str
    budget: Decimal = Field(gt=0, le=100_000_000)
    risk_budget: Decimal = Field(gt=0, le=100_000_000)
    provider: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    instrument_key: str = Field(min_length=1, max_length=100)

    @field_validator("strategy")
    @classmethod
    def existing_rule_only(cls, value: str) -> str:
        if value not in STRATEGIES:
            raise ValueError("Only existing fixed equity rules are supported")
        return value

    @model_validator(mode="after")
    def risk_within_allocation(self) -> "FixedEquitySpec":
        if self.risk_budget > self.budget:
            raise ValueError("Research risk cannot exceed allocation")
        return self


def evaluate_fixed_equity(
    raw: bytes, manifest: TrialManifest, spec: FixedEquitySpec, *, frozen_spec_digest: str,
) -> dict[str, Any]:
    """Evaluate pre-holdout closed trades with the shipped daily OHLC engine.

    This entry point is offline only: no database, worker or API execution path.
    A matching digest does not prove preregistration or source/data qualification.
    """
    spec_payload = spec.model_dump(mode="json")
    if fingerprint(spec_payload) != frozen_spec_digest:
        raise ValueError("Adapter settings differ from their frozen digest")
    if manifest.cost_model_id != COST_MODEL_ID:
        raise ValueError("Manifest cost model is not supported by this adapter")
    windows = prepare_daily_windows(raw, manifest, provider=spec.provider,
                                    instrument_key=spec.instrument_key)
    events, open_excluded = replay_events(
        spec.strategy, list(windows.reviewed), spec.budget, spec.risk_budget,
    )
    partitions: dict[str, list[PerformanceTrade]] = {"training": [], "validation": []}
    excluded = 0
    for event in events:
        dates = [stamp.astimezone(ZONE).date() for stamp in (
            event.signal_at, event.entry_at, event.exit_at,
        )]
        group = None
        for label, start, end in (
            ("training", manifest.train_start, manifest.train_end),
            ("validation", manifest.validation_start, manifest.validation_end),
        ):
            if all(start <= day <= end for day in dates):
                group = label
                break
        if group is None:
            excluded += 1
        else:
            partitions[group].append(PerformanceTrade(
                event.net_pnl, event.initial_risk, event.exit_at,
            ))
    # The engine emits closes in validated bar order; filtering preserves that order.
    metrics = {name: summarize(trades) for name, trades in partitions.items()}
    report = {
        "schema_version": 1, "evidence_origin": "OFFLINE_FIXED_RULE_REPLAY",
        "manifest_digest": fingerprint(manifest.model_dump(mode="json", exclude_none=True)),
        "adapter": spec_payload, "adapter_digest": frozen_spec_digest,
        "cost_model": {"id": COST_MODEL_ID, "fee_fraction": str(FEE),
                       "slippage_fraction": str(SLIPPAGE), "illustrative": True},
        "status": "REVIEWABLE_UNVERIFIED" if all(len(rows) >= MIN_TRADES
                    for rows in partitions.values()) else "INSUFFICIENT_SAMPLE",
        "metrics": metrics, "boundary_or_gap_trades_excluded": excluded,
        "open_trade_excluded": open_excluded, "reviewed_bars": len(windows.reviewed),
        "holdout_evaluated": False,
        "holdout_input_excluded": windows.holdout_input_excluded,
        "promotion_eligible": False,
        "profit_probability": None,
        "limitations": ["Source, licensing and point-in-time data quality remain unverified.",
                        "Input exclusion does not prove independent holdout access control.",
                        "Daily OHLC replay uses illustrative costs and stop-first ambiguity.",
                        "Closed-trade drawdown excludes open equity and is not account drawdown.",
                        "Win frequency is descriptive, not calibrated profit probability.",
                        "The offline adapter alone enforces no durable budget or finalization."],
    }
    return report | {"report_digest": fingerprint(report)}
