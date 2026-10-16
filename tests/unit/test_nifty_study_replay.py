from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from packages.backtesting.contracts import ScheduledOptionsBacktestResult
from packages.database.base import Base
from packages.database.models import (
    BacktestRun,
    ResearchBudgetSlot,
    ResearchStudy,
    ResearchTrial,
    User,
)
from packages.regime.candidate_registry import (
    CandidateDefinition,
    TrialManifest,
    declare_trial,
    register_candidate,
)
from packages.regime.frozen_nifty_study import FrozenNiftyStudy
from packages.regime.nifty_study_replay import assess_nifty_scheduled_replay
from packages.regime.registered_evaluator import record_registered_nifty_replay
from packages.regime.research_studies import freeze_nifty_study


def study(**changes):
    return FrozenNiftyStudy(**({
        "study_key": "nifty50-paper-v1", "objective": "Compare the incumbent rule with no trade.",
        "incumbent_rule_version": "nifty-paper-v1", "dataset_digest": "a" * 64,
        "calculation_digest": "b" * 64, "cost_model_id": "illustrative-v1",
        "capital": "100000.00", "parent_search_budget": 10,
        "review_interval_days": 30, "uncertainty_note": "Evidence remains unverified.",
        "train_start": "2020-01-01", "train_end": "2022-12-31",
        "validation_start": "2023-01-01", "validation_end": "2024-12-31",
        "holdout_start": "2025-01-01", "holdout_end": "2025-12-31",
    } | changes))


def replay(**changes):
    return ScheduledOptionsBacktestResult(**({
        "run_id": uuid4(), "provider": "fixture", "mode": "historical_scheduled_expiry_replay",
        "strategy_key": "nifty-paper", "strategy_name": "NIFTY paper fixture",
        "strategy_version": "nifty-paper-v1", "start_date": date(2020, 1, 1),
        "end_date": date(2024, 12, 31), "entry_sessions_before_expiry": 2,
        "lots": 1, "fee_per_order": Decimal("1"),
        "slippage_per_unit_per_side": Decimal("0.1"), "trade_count": 4,
        "gross_pnl": Decimal("100"), "estimated_cost": Decimal("20"),
        "net_pnl": Decimal("80"), "max_closed_trade_drawdown": Decimal("10"),
        "winning_trades": 2, "observed_input_digest": "c" * 64, "trades": [],
        "assumptions": ["Fixture replay."], "warnings": ["Indicative replay."],
    } | changes))


def test_assessment_keeps_replay_unverified_and_holdout_closed():
    result = assess_nifty_scheduled_replay(study(), replay())
    assert result.status == "INSUFFICIENT_SAMPLE"
    assert result.holdout_evaluated is False
    assert result.promotion_eligible is False
    assert result.replay_input_digest == "c" * 64
    assert "calibrated profit probability" in " ".join(result.limitations)


def test_twenty_trades_are_reviewable_but_not_qualified():
    result = assess_nifty_scheduled_replay(study(), replay(trade_count=20))
    assert result.status == "REVIEWABLE_UNVERIFIED"
    assert result.promotion_eligible is False


def test_registered_replay_persists_assessment_and_consumes_one_family_slot():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner = uuid4()
    frozen = study()
    manifest = TrialManifest(
        dataset_digest=frozen.dataset_digest, calculation_digest=frozen.calculation_digest,
        cost_model_id=frozen.cost_model_id, experiment_family="nifty-study-fixture",
        trial_number=1, declared_search_budget=10, seed=0,
        train_start=frozen.train_start, train_end=frozen.train_end,
        validation_start=frozen.validation_start, validation_end=frozen.validation_end,
        holdout_start=frozen.holdout_start, holdout_end=frozen.holdout_end,
    )
    definition = CandidateDefinition(
        name="Fixture NIFTY options hypothesis", family="nifty-options",
        market="NIFTY_OPTIONS",
        hypothesis="Test a frozen scheduled replay boundary with synthetic data only.",
        source_url="https://example.org/nifty-study", source_license="UNREVIEWED",
        entry_rule="Use the explicitly frozen incumbent strategy version.",
        exit_rule="Use the scheduled option-close replay adapter.",
        limitation="Synthetic fixture; not eligible for trading.",
    )
    with Session(engine) as session:
        session.add(User(id=owner, email="nifty-replay@example.org", password_hash="fixture"))
        session.commit()
        candidate = register_candidate(session, owner, definition)
        trial = declare_trial(session, owner, candidate.id, manifest)
        saved = record_registered_nifty_replay(session, owner, trial.id, frozen, replay())
        assert saved.adapter["type"] == "NIFTY_SCHEDULED_REPLAY"
        assert saved.adapter["parent_search_budget"] == frozen.parent_search_budget
        assert saved.result["report"]["status"] == "INSUFFICIENT_SAMPLE"
        assert saved.result["promotion_eligible"] is False
        assert session.get(ResearchTrial, trial.id).result is None
        assert len(list(session.scalars(select(ResearchBudgetSlot)))) == 1
        assert record_registered_nifty_replay(
            session, owner, trial.id, frozen, replay(trade_count=20),
        ).result_digest == saved.result_digest
        mismatched = declare_trial(session, owner, candidate.id, TrialManifest.model_validate(
            manifest.model_dump() | {"trial_number": 2, "declared_search_budget": 2}))
        with pytest.raises(ValueError, match="parent budget"):
            record_registered_nifty_replay(session, owner, mismatched.id, frozen, replay())
    engine.dispose()


def test_declared_holdout_artifact_must_match_frozen_study():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner = uuid4()
    frozen = study(holdout_artifact_digest="d" * 64)
    manifest = TrialManifest(
        dataset_digest=frozen.dataset_digest, calculation_digest=frozen.calculation_digest,
        cost_model_id=frozen.cost_model_id, experiment_family="holdout-binding-fixture",
        trial_number=1, declared_search_budget=frozen.parent_search_budget, seed=0,
        train_start=frozen.train_start, train_end=frozen.train_end,
        validation_start=frozen.validation_start, validation_end=frozen.validation_end,
        holdout_start=frozen.holdout_start, holdout_end=frozen.holdout_end,
        holdout_artifact_digest="e" * 64,
    )
    definition = CandidateDefinition(
        name="Holdout binding hypothesis", family="nifty-options", market="NIFTY_OPTIONS",
        hypothesis="Test that a protected holdout cannot be silently substituted.",
        source_url="https://example.org/holdout-binding", source_license="UNREVIEWED",
        entry_rule="Use the frozen incumbent.", exit_rule="Use the scheduled replay.",
        limitation="Synthetic fixture; not eligible for trading.",
    )
    with Session(engine) as session:
        session.add(User(id=owner, email="holdout-binding@example.org", password_hash="fixture"))
        session.commit()
        candidate = register_candidate(session, owner, definition)
        trial = declare_trial(session, owner, candidate.id, manifest)
        from packages.regime.registered_evaluator import bind_nifty_replay

        with pytest.raises(ValueError, match="frozen NIFTY study"):
            bind_nifty_replay(session, owner, trial.id, frozen)
    engine.dispose()


def test_declared_cost_artifact_is_retained_in_frozen_adapter():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner = uuid4()
    frozen = study(cost_artifact_digest="d" * 64)
    manifest = TrialManifest(
        dataset_digest=frozen.dataset_digest, calculation_digest=frozen.calculation_digest,
        cost_model_id=frozen.cost_model_id, cost_artifact_digest=frozen.cost_artifact_digest,
        experiment_family="cost-artifact-binding-fixture", trial_number=1,
        declared_search_budget=frozen.parent_search_budget, seed=0,
        train_start=frozen.train_start, train_end=frozen.train_end,
        validation_start=frozen.validation_start, validation_end=frozen.validation_end,
        holdout_start=frozen.holdout_start, holdout_end=frozen.holdout_end,
    )
    definition = CandidateDefinition(
        name="Cost artifact binding hypothesis", family="nifty-options",
        market="NIFTY_OPTIONS", hypothesis="Test that frozen cost identity stays visible.",
        source_url="https://example.org/cost-artifact-binding", source_license="UNREVIEWED",
        entry_rule="Use the frozen incumbent.", exit_rule="Use the scheduled replay.",
        limitation="Synthetic fixture; not eligible for trading.",
    )
    with Session(engine) as session:
        session.add(User(id=owner, email="cost-artifact@example.org", password_hash="fixture"))
        session.commit()
        candidate = register_candidate(session, owner, definition)
        trial = declare_trial(session, owner, candidate.id, manifest)
        from packages.regime.registered_evaluator import bind_nifty_replay

        bound = bind_nifty_replay(session, owner, trial.id, frozen)
        assert bound.adapter["cost_artifact_digest"] == "d" * 64
    engine.dispose()


def test_authenticated_route_records_only_owned_stored_replay():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner, other = uuid4(), uuid4()
    frozen = study()
    manifest = TrialManifest(
        dataset_digest=frozen.dataset_digest, calculation_digest=frozen.calculation_digest,
        cost_model_id=frozen.cost_model_id, experiment_family="nifty-route-fixture",
        trial_number=1, declared_search_budget=10, seed=0,
        train_start=frozen.train_start, train_end=frozen.train_end,
        validation_start=frozen.validation_start, validation_end=frozen.validation_end,
        holdout_start=frozen.holdout_start, holdout_end=frozen.holdout_end,
    )
    definition = CandidateDefinition(
        name="Route NIFTY options hypothesis", family="nifty-options",
        market="NIFTY_OPTIONS", hypothesis="Route fixture only; never trading evidence.",
        source_url="https://example.org/nifty-route", source_license="UNREVIEWED",
        entry_rule="Use frozen incumbent.", exit_rule="Use scheduled replay.",
        limitation="Synthetic fixture; not eligible for trading.",
    )
    with Session(engine) as session:
        session.add_all([
            User(id=owner, email="route-owner@example.org", password_hash="fixture"),
            User(id=other, email="route-other@example.org", password_hash="fixture"),
        ])
        session.commit()
        study_row = freeze_nifty_study(session, owner, frozen)
        candidate = register_candidate(session, owner, definition)
        trial = declare_trial(session, owner, candidate.id, manifest)
        run_id = uuid4()
        session.add(BacktestRun(id=run_id, user_id=owner,
                                result_data=replay().model_dump(mode="json")))
        session.commit()
        from apps.api.research_routes import NiftyReplayRecordRequest, record_nifty_replay

        visible = record_nifty_replay(
            trial.id, NiftyReplayRecordRequest(study_id=study_row.id, run_id=run_id),
            SimpleNamespace(id=owner), session,
        )
        assert visible["engine_evaluation"]["result"]["report"]["status"] == (
            "INSUFFICIENT_SAMPLE")
        assert visible["result"] is None
        with pytest.raises(HTTPException) as denied:
            record_nifty_replay(
                trial.id, NiftyReplayRecordRequest(study_id=study_row.id, run_id=run_id),
                SimpleNamespace(id=other), session,
            )
        assert denied.value.status_code == 404
        assert session.get(ResearchStudy, study_row.id).user_id == owner
    engine.dispose()


@pytest.mark.parametrize("changes", [
    {"strategy_version": "other-version"},
    {"end_date": date(2025, 1, 1)},
    {"mode": "historical_single_expiry_replay"},
    {"observed_input_digest": "short"},
])
def test_assessment_rejects_unfrozen_or_unidentified_replays(changes):
    with pytest.raises(ValueError):
        assess_nifty_scheduled_replay(study(), replay(**changes))
