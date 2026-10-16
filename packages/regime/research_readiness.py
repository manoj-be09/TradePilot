"""Read-only assessment of stored research evidence, never a promotion decision."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from packages.database.models import ResearchEvaluation, ResearchTrial
from packages.regime.candidate_registry import fingerprint


class ReadinessCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    label: str
    status: Literal["PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"]
    detail: str
    evidence_ref: str | None = None
    evidence_recorded_at: datetime | None = None

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
                 evaluation: ResearchEvaluation | None = None) -> ReadinessAssessment:
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
    checks.append(ReadinessCheck(key="costs", label="Costs and capital",
        status="FAIL" if illustrative else "UNKNOWN",
        detail="Recorded costs are illustrative; complete charges and capital are unqualified."
        if illustrative else "No reviewed full-cost and capital assessment is recorded.",
        evidence_ref=result_ref if illustrative else None,
        evidence_recorded_at=result_time if illustrative else None))
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
        checks.append(ReadinessCheck(key=key, label=label, status="UNKNOWN", detail=detail))
    return ReadinessAssessment(
        assessed_at=datetime.now(timezone.utc), trial_id=str(row.id),
        manifest_digest=row.manifest_digest,
        status="BLOCKED" if any(check.status == "FAIL" for check in checks) else "UNVERIFIED",
        allowed_uses=["INSPECT_RESEARCH_RECORDS"], checks=checks,
    )
