"""Read-only assessment of stored research evidence, never a promotion decision."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from packages.database.models import ResearchEvaluation, ResearchEvidence, ResearchTrial
from packages.regime.candidate_registry import fingerprint
from packages.regime.research_evidence import persisted_evidence_digest


class ReadinessCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    label: str
    status: Literal["PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"]
    detail: str
    evidence_ref: str | None = None
    evidence_recorded_at: datetime | None = None
    artifact_digest: str | None = None
    artifact_version: str | None = None
    allowed_use: Literal[
        "INSPECT_RESEARCH_RECORDS", "RESEARCH_ONLY", "QUALIFIED_PAPER"
    ] | None = None

    @field_validator("evidence_recorded_at")
    @classmethod
    def recorded_utc(cls, value: datetime | None) -> datetime | None:
        # Registry services write UTC; SQLite round-trips drop the timezone.
        # This applies to internal record metadata, never provider event times.
        return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


class ReadinessAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    policy_version: Literal["stored-research-readiness-v1"] = "stored-research-readiness-v1"
    assessed_at: datetime
    trial_id: str
    manifest_digest: str
    status: Literal["BLOCKED", "UNVERIFIED"]
    allowed_uses: list[Literal["INSPECT_RESEARCH_RECORDS"]]
    promotion_eligible: Literal[False] = False
    checks: list[ReadinessCheck]


def assess_trial(row: ResearchTrial,
                 evaluation: ResearchEvaluation | None = None,
                 evidence: list[ResearchEvidence] | None = None) -> ReadinessAssessment:
    """Hashes attest stored JSON consistency only, not artifact bytes or authenticity.

    This assessment is recomputed on read. Its timestamp never replaces the time
    evidence was recorded. Unrecorded reviews stay unknown, including old results.
    No filesystem, provider, database write or execution capability is used.
    """
    manifest_ok = fingerprint(row.manifest) == row.manifest_digest
    checks = [ReadinessCheck(
        key="manifest_integrity", label="Stored manifest",
        status="PASS" if manifest_ok else "FAIL",
        detail="Stored declaration matches its digest; input artifacts were not rechecked."
        if manifest_ok else "Stored manifest differs from its saved digest; review required.",
        evidence_ref=f"trial:{row.id}:manifest:{row.manifest_digest}",
        evidence_recorded_at=row.created_at,
    )]
    result_ok = False
    result = evaluation.result if evaluation is not None else None
    result_ref = (f"trial:{row.id}:engine:{evaluation.result_digest}"
                  if evaluation is not None else None)
    result_time = evaluation.completed_at if evaluation is not None else None
    if evaluation is not None and result is not None:
        result_ok = (evaluation.trial_id == row.id
                     and fingerprint(result) == evaluation.result_digest
                     and fingerprint(evaluation.adapter) == evaluation.adapter_digest)
        checks.append(ReadinessCheck(
            key="engine_record", label="Stored engine evidence",
            status="PASS" if result_ok else "FAIL",
            detail="Stored result and adapter match their digests; this is not a repeat run."
            if result_ok else "Engine evidence identity differs; do not rely on these metrics.",
            evidence_ref=f"trial:{row.id}:engine:{evaluation.result_digest}",
            evidence_recorded_at=evaluation.completed_at,
        ))
    else:
        checks.append(ReadinessCheck(key="engine_record", label="Stored engine evidence",
            status="UNKNOWN", detail="No completed engine evidence is recorded."))
    if result_ok and result is not None and result.get("outcome") in {"FAILED", "ERROR"}:
        checks.append(ReadinessCheck(key="replay_outcome", label="Replay outcome",
            status="FAIL", detail="The recorded attempt failed; retained history is not a result.",
            evidence_ref=result_ref, evidence_recorded_at=result_time))
    report = result.get("report") if result_ok and result is not None else None
    costs = report.get("cost_model") if isinstance(report, dict) else None
    illustrative = isinstance(costs, dict) and costs.get("illustrative") is True
    reviewed = {item.key: item for item in (evidence or [])}
    cost_item = reviewed.get("costs")
    if cost_item is None:
        checks.append(ReadinessCheck(key="costs", label="Costs and capital",
            status="FAIL" if illustrative else "UNKNOWN",
            detail="Recorded costs are illustrative; complete charges and capital are unqualified."
            if illustrative else "No reviewed full-cost and capital assessment is recorded.",
            evidence_ref=result_ref if illustrative else None,
            evidence_recorded_at=result_time if illustrative else None))
    else:
        cost_status = cost_item.status
        cost_detail = cost_item.detail
        cost_integrity = persisted_evidence_digest(cost_item) == cost_item.evidence_digest
        if not cost_integrity:
            cost_status = "FAIL"
            cost_detail = "Stored evidence fields do not match their immutable evidence digest."
        elif illustrative:
            cost_status = "FAIL"
            cost_detail = ("A reviewed cost reference exists, but the stored engine report "
                           "still uses illustrative charges; complete historical charges "
                           "remain unqualified.")
        elif (cost_status == "PASS"
              and cost_item.artifact_version != row.manifest.get("cost_model_id")):
            cost_status = "FAIL"
            cost_detail = ("Reviewed cost evidence version does not match the trial's frozen cost "
                           "model; qualification remains blocked.")
        checks.append(ReadinessCheck(
            key="costs", label="Costs and capital", status=cost_status, detail=cost_detail,
            evidence_ref=(f"evidence:{cost_item.id}:artifact:"
                          f"{cost_item.artifact_digest or cost_item.artifact_ref}"),
            evidence_recorded_at=cost_item.recorded_at, artifact_digest=cost_item.artifact_digest,
            artifact_version=cost_item.artifact_version, allowed_use=cost_item.allowed_use,
        ))
    manifest_artifacts = {
        "data": row.manifest.get("dataset_digest"),
        "binding": row.manifest.get("calculation_digest"),
        "holdout": row.manifest.get("holdout_artifact_digest"),
    }
    for key, label, detail in (
        ("rights", "Source and data rights", "Declared licenses are not recorded rights approval."),
        ("data", "Historical data and timestamps",
         "Authenticity, session coverage, revisions and availability times are unreviewed."),
        ("binding", "Rules and validation policy",
         "Rule correspondence, boundary state and execution-policy equivalence are unreviewed."),
        ("holdout", "Protected holdout",
         "Excluded replay dates do not prove restricted access to holdout data."),
        ("budget", "Campaign search history",
         "Family slots do not establish canonical parent budgets or complete search history."),
        ("runtime", "Deployed research runtime",
         "No study-linked PostgreSQL concurrency and recovery acceptance is recorded."),
    ):
        item = reviewed.get(key)
        if item is None:
            checks.append(ReadinessCheck(key=key, label=label, status="UNKNOWN", detail=detail))
            continue
        status = item.status
        expected_digest = manifest_artifacts.get(key)
        evidence_integrity = persisted_evidence_digest(item) == item.evidence_digest
        if not evidence_integrity:
            status = "FAIL"
            detail = "Stored evidence fields do not match their immutable evidence digest."
        holdout_unbound = key == "holdout" and status == "PASS" and not expected_digest
        if holdout_unbound:
            status = "FAIL"
            detail = ("A protected holdout PASS record is not bound to an immutable trial "
                      "artifact digest; qualification remains blocked.")
        identity_mismatch = (status == "PASS" and expected_digest
                             and item.artifact_digest != expected_digest)
        if identity_mismatch:
            status = "FAIL"
            detail = (f"Reviewed artifact identity does not match the trial's immutable {key} "
                      "digest; qualification remains blocked.")
        if not identity_mismatch and evidence_integrity and not holdout_unbound:
            detail = item.detail
        checks.append(ReadinessCheck(
            key=key, label=label, status=status, detail=detail,
            evidence_ref=f"evidence:{item.id}:artifact:{item.artifact_digest or item.artifact_ref}",
            evidence_recorded_at=item.recorded_at, artifact_digest=item.artifact_digest,
            artifact_version=item.artifact_version, allowed_use=item.allowed_use,
        ))
    return ReadinessAssessment(
        assessed_at=datetime.now(timezone.utc), trial_id=str(row.id),
        manifest_digest=row.manifest_digest,
        status="BLOCKED" if any(check.status == "FAIL" for check in checks) else "UNVERIFIED",
        allowed_uses=["INSPECT_RESEARCH_RECORDS"], checks=checks,
    )
