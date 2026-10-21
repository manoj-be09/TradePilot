"""Reserve a durable numbered family slot before future evaluator execution."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.database.models import ResearchBudgetSlot, ResearchExperiment, ResearchTrial
from packages.regime.candidate_registry import TrialManifest, fingerprint


def reserve_trial_slot(
    session: Session, owner: UUID, trial_id: UUID, *,
    nifty_parent_dataset_digest: str | None = None,
) -> ResearchBudgetSlot:
    """Keep budget/scope immutable and reject slot reuse across candidates.

    NIFTY callers can bind the family to an exact frozen dataset digest. This
    does not detect differently hashed copies of overlapping observations.
    Legacy trials are not backfilled or qualified; reservation never authorizes
    replay or promotion by itself.
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
        "rights_artifact_digest", "dataset_digest", "calculation_digest",
        "holdout_artifact_digest", "cost_model_id",
        "cost_artifact_digest",
        "holdout_start", "holdout_end",
    )})
    family = manifest.experiment_family
    if nifty_parent_dataset_digest is not None:
        if nifty_parent_dataset_digest != manifest.dataset_digest:
            raise ValueError("NIFTY parent dataset differs from the trial manifest")
        family = f"nifty-dataset:{nifty_parent_dataset_digest}"
        legacy = None
        if manifest.experiment_family != family:
            legacy = session.scalar(select(ResearchExperiment).where(
                ResearchExperiment.user_id == owner,
                ResearchExperiment.family == manifest.experiment_family,
            ))
        if legacy is not None and session.scalar(select(ResearchBudgetSlot.trial_id).where(
            ResearchBudgetSlot.experiment_id == legacy.id,
        ).limit(1)) is not None:
            raise ValueError("Legacy family reservations require parent-budget review")
    query = select(ResearchExperiment).where(
        ResearchExperiment.user_id == owner,
        ResearchExperiment.family == family,
    )
    experiment = session.scalar(query)
    if experiment is None:
        experiment = ResearchExperiment(
            user_id=owner, family=family,
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
