import hashlib
import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from packages.paper_trading.equity import STRATEGIES, ReplayTrade
from packages.regime import fixed_rule_evaluator as evaluator
from packages.regime.candidate_registry import TrialManifest, fingerprint
from packages.regime.research_identity import research_code_digest


def inputs():
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(90)]
    raw = json.dumps({"provider": "fixture", "instrument_key": "TEST_ONLY", "bars": [
        {"timestamp": f"{day}T09:15:00+05:30", "open": "100", "high": "102",
         "low": "99", "close": "101", "volume": 10} for day in days
    ]}).encode()
    manifest = TrialManifest(
        dataset_digest=hashlib.sha256(raw).hexdigest(), calculation_digest=research_code_digest(),
        cost_model_id=evaluator.COST_MODEL_ID, experiment_family="offline-fixture",
        trial_number=1, declared_search_budget=1, seed=0,
        train_start=days[0], train_end=days[39], validation_start=days[42],
        validation_end=days[69], holdout_start=days[72], holdout_end=days[-1],
    )
    spec = evaluator.FixedEquitySpec(strategy=next(iter(STRATEGIES)), budget=Decimal("10000"),
        risk_budget=Decimal("100"), provider="fixture", instrument_key="TEST_ONLY")
    return raw, manifest, spec, fingerprint(spec.model_dump(mode="json"))


def test_actual_fixed_engine_is_deterministic_and_never_claims_probability():
    raw, manifest, spec, digest = inputs()
    first = evaluator.evaluate_fixed_equity(raw, manifest, spec, frozen_spec_digest=digest)
    assert first == evaluator.evaluate_fixed_equity(raw, manifest, spec, frozen_spec_digest=digest)
    assert first["reviewed_bars"] == 70
    assert first["holdout_evaluated"] is False
    assert first["promotion_eligible"] is False
    assert first["profit_probability"] is None
    assert first["evidence_origin"] == "OFFLINE_FIXED_RULE_REPLAY"
    assert first["cost_model"]["illustrative"] is True
    assert first["report_digest"] == fingerprint({k: v for k, v in first.items()
                                                 if k != "report_digest"})


def test_adapter_excludes_crossing_trades_and_passes_no_holdout_to_engine(monkeypatch):
    raw, manifest, spec, digest = inputs()

    def event(signal, entry, exit_day, pnl):
        stamps = [datetime.fromisoformat(f"2020-{day}T09:15:00+05:30")
                  for day in (signal, entry, exit_day)]
        return ReplayTrade(*stamps, Decimal(pnl), Decimal("10"))

    def replay(key, bars, budget, risk):
        assert key == spec.strategy and budget == spec.budget and risk == spec.risk_budget
        assert all(bar.timestamp.date() <= manifest.validation_end for bar in bars)
        return [event("01-10", "01-11", "01-12", "10"),
                event("02-08", "02-09", "02-13", "9999"),
                event("02-15", "02-16", "02-17", "-5")], True

    monkeypatch.setattr(evaluator, "replay_events", replay)
    result = evaluator.evaluate_fixed_equity(raw, manifest, spec, frozen_spec_digest=digest)
    assert result["metrics"]["training"]["net_pnl"] == 10
    assert result["metrics"]["validation"]["net_pnl"] == -5
    assert result["boundary_or_gap_trades_excluded"] == 1
    assert result["open_trade_excluded"] is True
    assert result["status"] == "INSUFFICIENT_SAMPLE"
    with pytest.raises(ValueError, match="Adapter settings"):
        evaluator.evaluate_fixed_equity(raw, manifest, spec, frozen_spec_digest="0" * 64)
    wrong_cost = TrialManifest.model_validate(manifest.model_dump() | {"cost_model_id": "other"})
    with pytest.raises(ValueError, match="cost model"):
        evaluator.evaluate_fixed_equity(raw, wrong_cost, spec, frozen_spec_digest=digest)
    with pytest.raises(ValueError):
        evaluator.FixedEquitySpec(**(spec.model_dump() | {"strategy": "generated-code"}))
    with pytest.raises(ValueError):
        evaluator.FixedEquitySpec(**(spec.model_dump() | {"risk_budget": "20000"}))


def test_registered_replay_retains_engine_evidence_separately_and_consumes_failures(tmp_path):
    from uuid import uuid4

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from packages.database.base import Base
    from packages.database.models import ResearchBudgetSlot, ResearchEvaluation, ResearchTrial, User
    from packages.regime.candidate_registry import (
        CandidateDefinition,
        declare_trial,
        register_candidate,
    )
    from packages.regime.registered_evaluator import bind_fixed_equity, run_registered_equity

    engine = create_engine(f"sqlite:///{tmp_path / 'registered.sqlite'}")
    Base.metadata.create_all(engine)
    owner, other = uuid4(), uuid4()
    raw, manifest, spec, _ = inputs()
    manifest = TrialManifest.model_validate(manifest.model_dump() | {"declared_search_budget": 2})
    definition = CandidateDefinition(
        name="Fixture trend hypothesis", family="trend", market="EQUITY",
        hypothesis="Test a fixed rule with synthetic data, not qualified performance evidence.",
        source_url="https://example.org/research", source_license="UNREVIEWED",
        entry_rule="Use the explicit fixed adapter binding.",
        exit_rule="Use its existing default exit policy.", limitation="Synthetic test only.",
    )
    with Session(engine) as session:
        session.add_all([User(id=uid, email=f"{uid}@example.org", password_hash="fixture")
                         for uid in (owner, other)])
        session.commit()
        candidate = register_candidate(session, owner, definition)
        trial = declare_trial(session, owner, candidate.id, manifest)
        trial_id = trial.id
        with pytest.raises(ValueError, match="Freeze"):
            run_registered_equity(session, owner, trial.id, raw)
        bind_fixed_equity(session, owner, trial.id, spec)
        with pytest.raises(ValueError, match="different frozen"):
            bind_fixed_equity(session, owner, trial.id, evaluator.FixedEquitySpec.model_validate(
                spec.model_dump() | {"budget": "20000"}))
        with pytest.raises(LookupError):
            run_registered_equity(session, other, trial.id, raw)
        success = run_registered_equity(session, owner, trial.id, raw)
        assert success.result["outcome"] == "COMPLETED"
        assert success.result["promotion_eligible"] is False
        assert success.result["report"]["holdout_evaluated"] is False
        assert success.result["evidence_origin"] == "ENGINE_RESEARCH_REPLAY"
        assert session.get(ResearchTrial, trial.id).result is None
        from types import SimpleNamespace

        from fastapi import HTTPException, Response

        from apps.api.research_routes import trials

        response = Response()
        visible = trials(candidate.id, SimpleNamespace(id=owner), session, response, 25, 0)
        assert response.headers["Cache-Control"] == "no-store"
        assert visible[0]["engine_evaluation"]["result"]["evidence_origin"] == (
            "ENGINE_RESEARCH_REPLAY")
        assert visible[0]["result"] is None
        assert visible[0]["readiness"]["status"] == "BLOCKED"
        assert visible[0]["readiness"]["allowed_uses"] == ["INSPECT_RESEARCH_RECORDS"]
        assert trials(candidate.id, SimpleNamespace(id=owner), session, Response(), 25, 25) == []
        with pytest.raises(HTTPException) as denied:
            trials(candidate.id, SimpleNamespace(id=other), session, Response(), 25, 0)
        assert denied.value.status_code == 404
        saved_digest = success.result_digest
        assert run_registered_equity(session, owner, trial.id, raw).result_digest == saved_digest
        failed = declare_trial(session, owner, candidate.id, TrialManifest.model_validate(
            manifest.model_dump() | {"trial_number": 2}))
        bind_fixed_equity(session, owner, failed.id, spec)
        failure = run_registered_equity(session, owner, failed.id, raw + b" ")
        assert failure.result["reason_code"] == "INPUT_OR_IDENTITY_CHECK_FAILED"
        assert run_registered_equity(session, owner, failed.id, raw).result_digest == (
            failure.result_digest)
        assert len(list(session.scalars(select(ResearchBudgetSlot)))) == 2
    with Session(engine) as session:
        assert session.get(ResearchEvaluation, trial_id).result_digest == saved_digest
        assert session.get(ResearchTrial, trial_id).result is None
    engine.dispose()
