"""Reserve a durable numbered family slot before future evaluator execution."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.database.models import ResearchBudgetSlot, ResearchExperiment, ResearchTrial
from packages.regime.candidate_registry import TrialManifest, fingerprint


def reserve_trial_slot(session: Session, owner: UUID, trial_id: UUID) -> ResearchBudgetSlot:
    """Keep budget/scope immutable and reject slot reuse across candidates.

    This is family scoped, not protection against an operator inventing another
    family name. Legacy trials are not backfilled or qualified. A reservation is
    not source/data review and cannot itself authorize replay or promotion.
    """
    trial = session.scalar(select(ResearchTrial).where(
        ResearchTrial.id == trial_id, ResearchTrial.user_id == owner,
    ))
    if trial is None:
        raise LookupError("Trial not found")
    existing = session.scalar(select(ResearchBudgetSlot).where(
        ResearchBudgetSlot.trial_id == trial_id,
    ))
    if existing is not None:
        return existing
    if trial.result_digest is not None:
        raise ValueError("Cannot reserve a budget after trial finalization")
    manifest = TrialManifest.model_validate(trial.manifest)
    # Preserve hashes for legacy manifests that predate optional holdout identity.
    payload = manifest.model_dump(mode="json", exclude_none=True)
    if fingerprint(payload) != trial.manifest_digest:
        raise ValueError("Stored trial manifest identity differs")
    scope = fingerprint({key: payload.get(key) for key in (
        "dataset_digest", "calculation_digest", "holdout_artifact_digest", "cost_model_id",
        "holdout_start", "holdout_end",
    )})
    query = select(ResearchExperiment).where(
        ResearchExperiment.user_id == owner,
        ResearchExperiment.family == manifest.experiment_family,
    )
    experiment = session.scalar(query)
    if experiment is None:
        experiment = ResearchExperiment(
            user_id=owner, family=manifest.experiment_family,
            budget=manifest.declared_search_budget,
            scope_digest=scope, created_at=datetime.now(timezone.utc),
        )
        try:
            with session.begin_nested():
                session.add(experiment)
                session.flush()
        except IntegrityError:
            experiment = session.scalar(query)
            if experiment is None:
                raise
    if experiment.budget != manifest.declared_search_budget or experiment.scope_digest != scope:
        raise ValueError("Experiment family budget or protected scope differs")
    occupied = session.get(ResearchBudgetSlot, (experiment.id, manifest.trial_number))
    if occupied is not None:
        raise ValueError("Experiment trial slot is already consumed")
    reservation = ResearchBudgetSlot(
        experiment_id=experiment.id, slot=manifest.trial_number, trial_id=trial_id,
        created_at=datetime.now(timezone.utc),
    )
    try:
        with session.begin_nested():
            session.add(reservation)
            session.flush()
    except IntegrityError:
        existing = session.get(ResearchBudgetSlot, (experiment.id, manifest.trial_number))
        if existing is None or existing.trial_id != trial_id:
            raise ValueError("Experiment trial slot is already consumed") from None
        reservation = existing
    session.commit()
    return reservation
