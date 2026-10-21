"""Offline registry-to-replay service; no scheduler, HTTP execution or promotion."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.backtesting.contracts import ScheduledOptionsBacktestResult
from packages.database.models import (
    ResearchCandidate,
    ResearchEvaluation,
    ResearchStudy,
    ResearchTrial,
)
from packages.regime.candidate_registry import TrialManifest, fingerprint
from packages.regime.fixed_rule_evaluator import FixedEquitySpec, evaluate_fixed_equity
from packages.regime.frozen_nifty_study import FrozenNiftyStudy
from packages.regime.nifty_study_replay import assess_nifty_scheduled_replay
from packages.regime.research_budget import reserve_trial_slot
from packages.regime.research_scheduler_lease import ResearchJobOutcome, run_research_job


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


def run_registered_equity_job(
    session: Session, owner: UUID, trial_id: UUID, raw: bytes,
    job_owner: str, lease_seconds: int,
) -> tuple[ResearchJobOutcome, ResearchEvaluation | None]:
    """Run the fixed evaluator under a fenced research lease.

    This is an offline composition boundary for a future scheduler. It keeps
    the evaluator's account-scoped budget/evidence rules and returns no result
    when the lease is busy, stale or invalid. It does not expose HTTP execution
    or grant trading/order capability.
    """
    saved: ResearchEvaluation | None = None

    def evaluate() -> None:
        nonlocal saved
        saved = run_registered_equity(session, owner, trial_id, raw)

    outcome = run_research_job(session, job_owner, lease_seconds, evaluate)
    return outcome, saved


def bind_nifty_replay(session: Session, owner: UUID, trial_id: UUID,
                      study: FrozenNiftyStudy) -> ResearchEvaluation:
    """Freeze a registered NIFTY trial to one parent study boundary."""
    saved_study = session.scalar(select(ResearchStudy).where(
        ResearchStudy.user_id == owner, ResearchStudy.study_key == study.study_key,
    ))
    if (saved_study is None or saved_study.manifest_digest != study.manifest_digest
            or fingerprint(saved_study.manifest) != saved_study.manifest_digest):
        raise ValueError("NIFTY replay requires the matching frozen account study")
    trial = owned_trial(session, owner, trial_id)
    candidate = session.scalar(select(ResearchCandidate).where(
        ResearchCandidate.id == trial.candidate_id, ResearchCandidate.user_id == owner,
    ))
    if candidate is None or candidate.definition.get("market") != "NIFTY_OPTIONS":
        raise ValueError("Only an owned NIFTY options hypothesis can bind to this adapter")
    if fingerprint(candidate.definition) != candidate.definition_digest:
        raise ValueError("Candidate definition identity differs")
    manifest = TrialManifest.model_validate(trial.manifest)
    if fingerprint(manifest.model_dump(mode="json", exclude_none=True)) != trial.manifest_digest:
        raise ValueError("Stored trial manifest identity differs")
    for key in ("rights_artifact_digest", "dataset_digest", "calculation_digest",
                "holdout_artifact_digest",
                "cost_model_id", "cost_artifact_digest", "validation_end",
                "holdout_start", "holdout_end"):
        if getattr(manifest, key) != getattr(study, key):
            raise ValueError("Trial manifest differs from the frozen NIFTY study")
    if manifest.declared_search_budget != study.parent_search_budget:
        raise ValueError("Trial search budget must match the frozen NIFTY parent budget")
    payload = {
        "type": "NIFTY_SCHEDULED_REPLAY",
        "study_key": study.study_key,
        "study_manifest_digest": study.manifest_digest,
        "strategy_version": study.incumbent_rule_version,
        "cost_model_id": study.cost_model_id,
        "cost_artifact_digest": study.cost_artifact_digest,
        "parent_search_budget": study.parent_search_budget,
    }
    digest = fingerprint(payload)
    existing = session.get(ResearchEvaluation, trial_id)
    if existing is not None:
        if existing.adapter_digest != digest:
            raise ValueError("Trial already has different frozen adapter settings")
        if manifest.experiment_family != study.study_key and existing.result_digest is None:
            raise ValueError("Trial family must match the frozen NIFTY parent study key")
        return existing
    if manifest.experiment_family != study.study_key:
        raise ValueError("Trial family must match the frozen NIFTY parent study key")
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
    reserve_trial_slot(
        session, owner, trial_id, nifty_parent_dataset_digest=study.dataset_digest,
    )
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


def record_registered_nifty_replay_job(
    session: Session, owner: UUID, trial_id: UUID, study: FrozenNiftyStudy,
    replay: ScheduledOptionsBacktestResult, job_owner: str, lease_seconds: int,
) -> tuple[ResearchJobOutcome, ResearchEvaluation | None]:
    """Record one supplied NIFTY replay under a fenced research lease.

    The replay/provider call remains outside this boundary. This only combines
    the existing frozen-study recorder with the offline lease and preserves its
    unqualified, promotion-ineligible result semantics.
    """
    saved: ResearchEvaluation | None = None

    def record() -> None:
        nonlocal saved
        saved = record_registered_nifty_replay(session, owner, trial_id, study, replay)

    outcome = run_research_job(session, job_owner, lease_seconds, record)
    return outcome, saved
