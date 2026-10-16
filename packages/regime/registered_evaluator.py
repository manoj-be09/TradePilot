"""Offline registry-to-replay service; no scheduler, HTTP execution or promotion."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.backtesting.contracts import ScheduledOptionsBacktestResult
from packages.database.models import ResearchCandidate, ResearchEvaluation, ResearchTrial
from packages.regime.candidate_registry import TrialManifest, fingerprint
from packages.regime.fixed_rule_evaluator import FixedEquitySpec, evaluate_fixed_equity
from packages.regime.frozen_nifty_study import FrozenNiftyStudy
from packages.regime.nifty_study_replay import assess_nifty_scheduled_replay
from packages.regime.research_budget import reserve_trial_slot


def owned_trial(session: Session, owner: UUID, trial_id: UUID) -> ResearchTrial:
    trial = session.scalar(select(ResearchTrial).where(
        ResearchTrial.id == trial_id, ResearchTrial.user_id == owner,
    ))
    if trial is None:
        raise LookupError("Trial not found")
    return trial


def bind_fixed_equity(session: Session, owner: UUID, trial_id: UUID,
                      spec: FixedEquitySpec) -> ResearchEvaluation:
    """Persist an explicit operator rule binding before any evaluation.

    Free-text hypotheses are not compiled or verified against the selected rule.
    This association remains unreviewed; generated rule text never executes.
    """
    trial = owned_trial(session, owner, trial_id)
    candidate = session.scalar(select(ResearchCandidate).where(
        ResearchCandidate.id == trial.candidate_id, ResearchCandidate.user_id == owner,
    ))
    if candidate is None or candidate.definition.get("market") != "EQUITY":
        raise ValueError("Only an owned equity hypothesis can bind to this adapter")
    if fingerprint(candidate.definition) != candidate.definition_digest:
        raise ValueError("Candidate definition identity differs")
    payload = spec.model_dump(mode="json")
    digest = fingerprint(payload)
    existing = session.get(ResearchEvaluation, trial_id)
    if existing is not None:
        if existing.adapter_digest != digest:
            raise ValueError("Trial already has different frozen adapter settings")
        return existing
    if trial.result_digest is not None:
        raise ValueError("Cannot bind after operator result finalization")
    row = ResearchEvaluation(trial_id=trial_id, adapter=payload, adapter_digest=digest,
                             created_at=datetime.now(timezone.utc))
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        existing = session.get(ResearchEvaluation, trial_id)
        if existing is None or existing.adapter_digest != digest:
            raise ValueError("Trial already has different frozen adapter settings") from None
        row = existing
    session.commit()
    return row


def run_registered_equity(session: Session, owner: UUID, trial_id: UUID,
                          raw: bytes) -> ResearchEvaluation:
    """Consume durable budget before replay and atomically retain one result.

    Results stay unqualified even if metrics look good. This offline entry point
    is not exposed to HTTP or the trading worker. Identical concurrent runs can
    duplicate pure calculation; a database null-result condition protects writes.
    """
    trial = owned_trial(session, owner, trial_id)
    row = session.get(ResearchEvaluation, trial_id)
    if row is None:
        raise ValueError("Freeze a typed adapter binding before replay")
    if row.result_digest is not None:
        return row
    if trial.result_digest is not None:
        raise ValueError("Operator-finalized trials cannot be evaluated")
    reserve_trial_slot(session, owner, trial_id)
    # The reservation has committed before any calculation, including failed input checks.
    try:
        manifest = TrialManifest.model_validate(trial.manifest)
        report = evaluate_fixed_equity(raw, manifest, FixedEquitySpec.model_validate(row.adapter),
                                      frozen_spec_digest=row.adapter_digest)
        result = {"outcome": "COMPLETED", "evidence_origin": "ENGINE_RESEARCH_REPLAY",
                  "binding_review": "UNREVIEWED", "promotion_eligible": False, "report": report}
    except (ValueError, OSError):
        result = {"outcome": "FAILED", "reason_code": "INPUT_OR_IDENTITY_CHECK_FAILED",
                  "evidence_origin": "ENGINE_RESEARCH_REPLAY", "promotion_eligible": False}
    except Exception:
        # Never retain arbitrary provider/engine error strings or credentials.
        result = {"outcome": "ERROR", "reason_code": "RUN_FAILED",
                  "evidence_origin": "ENGINE_RESEARCH_REPLAY", "promotion_eligible": False}
    digest = fingerprint(result)
    session.execute(update(ResearchEvaluation).where(
        ResearchEvaluation.trial_id == trial_id, ResearchEvaluation.result_digest.is_(None),
    ).values(result=result, result_digest=digest, completed_at=datetime.now(timezone.utc)))
    session.commit()
    saved = session.scalar(select(ResearchEvaluation).where(
        ResearchEvaluation.trial_id == trial_id,
    ).execution_options(populate_existing=True))
    assert saved is not None
    if saved.result_digest != digest:
        raise ValueError("Evaluation already finalized with different evidence")
    return saved


def bind_nifty_replay(session: Session, owner: UUID, trial_id: UUID,
                      study: FrozenNiftyStudy) -> ResearchEvaluation:
    """Freeze a registered NIFTY trial to one parent study boundary."""
    trial = owned_trial(session, owner, trial_id)
    candidate = session.scalar(select(ResearchCandidate).where(
        ResearchCandidate.id == trial.candidate_id, ResearchCandidate.user_id == owner,
    ))
    if candidate is None or candidate.definition.get("market") != "NIFTY_OPTIONS":
        raise ValueError("Only an owned NIFTY options hypothesis can bind to this adapter")
    if fingerprint(candidate.definition) != candidate.definition_digest:
        raise ValueError("Candidate definition identity differs")
    manifest = TrialManifest.model_validate(trial.manifest)
    for key in ("dataset_digest", "calculation_digest", "cost_model_id", "validation_end",
                "holdout_start", "holdout_end"):
        if getattr(manifest, key) != getattr(study, key):
            raise ValueError("Trial manifest differs from the frozen NIFTY study")
    payload = {
        "type": "NIFTY_SCHEDULED_REPLAY",
        "study_key": study.study_key,
        "study_manifest_digest": study.manifest_digest,
        "strategy_version": study.incumbent_rule_version,
        "cost_model_id": study.cost_model_id,
    }
    digest = fingerprint(payload)
    existing = session.get(ResearchEvaluation, trial_id)
    if existing is not None:
        if existing.adapter_digest != digest:
            raise ValueError("Trial already has different frozen adapter settings")
        return existing
    if trial.result_digest is not None:
        raise ValueError("Cannot bind after operator result finalization")
    row = ResearchEvaluation(trial_id=trial_id, adapter=payload, adapter_digest=digest,
                             created_at=datetime.now(timezone.utc))
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        existing = session.get(ResearchEvaluation, trial_id)
        if existing is None or existing.adapter_digest != digest:
            raise ValueError("Trial already has different frozen adapter settings") from None
        row = existing
    session.commit()
    return row


def record_registered_nifty_replay(
    session: Session, owner: UUID, trial_id: UUID, study: FrozenNiftyStudy,
    replay: ScheduledOptionsBacktestResult,
) -> ResearchEvaluation:
    """Persist a bounded NIFTY replay assessment after a separate engine run.

    The engine/provider call remains outside this service. This boundary only
    validates and records its result, consumes one durable family slot, and
    keeps the result unqualified and execution-disabled.
    """
    trial = owned_trial(session, owner, trial_id)
    row = bind_nifty_replay(session, owner, trial_id, study)
    if row.result_digest is not None:
        return row
    if trial.result_digest is not None:
        raise ValueError("Operator-finalized trials cannot be evaluated")
    reserve_trial_slot(session, owner, trial_id)
    try:
        report = assess_nifty_scheduled_replay(study, replay)
        result = {
            "outcome": "COMPLETED", "evidence_origin": "ENGINE_RESEARCH_REPLAY",
            "binding_review": "UNREVIEWED", "promotion_eligible": False,
            "replay_result_digest": fingerprint(replay.model_dump(mode="json")),
            "report": report.model_dump(mode="json"),
        }
    except ValueError:
        result = {
            "outcome": "FAILED", "reason_code": "BOUNDARY_OR_INPUT_CHECK_FAILED",
            "evidence_origin": "ENGINE_RESEARCH_REPLAY", "promotion_eligible": False,
        }
    digest = fingerprint(result)
    session.execute(update(ResearchEvaluation).where(
        ResearchEvaluation.trial_id == trial_id, ResearchEvaluation.result_digest.is_(None),
    ).values(result=result, result_digest=digest, completed_at=datetime.now(timezone.utc)))
    session.commit()
    saved = session.scalar(select(ResearchEvaluation).where(
        ResearchEvaluation.trial_id == trial_id,
    ).execution_options(populate_existing=True))
    assert saved is not None
    if saved.result_digest != digest:
        raise ValueError("Evaluation already finalized with different evidence")
    return saved
