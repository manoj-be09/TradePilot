from datetime import datetime, timezone
from uuid import uuid4

from packages.database.models import ResearchEvaluation, ResearchEvidence, ResearchTrial
from packages.regime.candidate_registry import fingerprint
from packages.regime.research_evidence import EvidenceRecord
from packages.regime.research_readiness import assess_trial


def records():
    stamp = datetime(2020, 1, 1, tzinfo=timezone.utc)
    manifest = {"dataset_digest": "a" * 64}
    row = ResearchTrial(id=uuid4(), manifest=manifest, manifest_digest=fingerprint(manifest),
                        created_at=stamp)
    adapter = {"strategy": "fixture"}
    result = {"outcome": "COMPLETED", "report": {"holdout_evaluated": False,
              "cost_model": {"illustrative": True}, "profit_probability": .99}}
    evaluation = ResearchEvaluation(trial_id=row.id, adapter=adapter,
        adapter_digest=fingerprint(adapter), result=result, result_digest=fingerprint(result),
        created_at=stamp, completed_at=stamp)
    return row, evaluation


def test_valid_hashes_never_qualify_costs_holdout_or_claimed_probability():
    row, evaluation = records()
    assessment = assess_trial(row, evaluation)
    checks = {check.key: check for check in assessment.checks}
    assert checks["manifest_integrity"].status == checks["engine_record"].status == "PASS"
    assert checks["costs"].status == "FAIL"
    assert checks["holdout"].status == checks["rights"].status == "UNKNOWN"
    assert assessment.status == "BLOCKED" and assessment.promotion_eligible is False
    assert assessment.allowed_uses == ["INSPECT_RESEARCH_RECORDS"]
    assert "profit_probability" not in assessment.model_dump_json()
    assert checks["engine_record"].evidence_recorded_at == evaluation.completed_at
    assert assessment.assessed_at > evaluation.completed_at


def test_absent_evidence_and_nonillustrative_claim_remain_unknown():
    row, evaluation = records()
    assert assess_trial(row).status == "UNVERIFIED"
    evaluation.result["report"]["cost_model"]["illustrative"] = False
    evaluation.result_digest = fingerprint(evaluation.result)
    checks = {check.key: check for check in assess_trial(row, evaluation).checks}
    assert checks["costs"].status == "UNKNOWN"
    assert all(check.status != "NOT_APPLICABLE" for check in checks.values())


def test_tampered_or_wrong_trial_records_fail_without_trusting_report():
    row, evaluation = records()
    row.manifest["dataset_digest"] = "b" * 64
    evaluation.trial_id = uuid4()
    assessment = assess_trial(row, evaluation)
    checks = {check.key: check.status for check in assessment.checks}
    assert checks["manifest_integrity"] == checks["engine_record"] == "FAIL"
    assert checks["costs"] == "UNKNOWN"
    evaluation.trial_id = row.id
    evaluation.result["outcome"] = "FAILED"
    assert "replay_outcome" not in {check.key for check in assess_trial(row, evaluation).checks}


def test_failed_attempt_is_retained_without_changing_records():
    row, evaluation = records()
    evaluation.result = {"outcome": "ERROR", "reason_code": "RUN_FAILED"}
    evaluation.result_digest = fingerprint(evaluation.result)
    before = (dict(row.manifest), dict(evaluation.result), evaluation.completed_at)
    first, second = assess_trial(row, evaluation), assess_trial(row, evaluation)
    assert first.checks == second.checks
    assert any(check.key == "replay_outcome" and check.status == "FAIL" for check in first.checks)
    assert before == (row.manifest, evaluation.result, evaluation.completed_at)


def test_sqlite_record_metadata_keeps_registry_utc_convention():
    row, evaluation = records()
    evaluation.completed_at = evaluation.completed_at.replace(tzinfo=None)
    assessment = assess_trial(row, evaluation)
    recorded = next(check for check in assessment.checks if check.key == "engine_record")
    assert recorded.evidence_recorded_at.isoformat() == "2020-01-01T00:00:00+00:00"


def test_reviewed_artifact_identity_must_match_immutable_trial_digest():
    row, evaluation = records()
    row.manifest["calculation_digest"] = "b" * 64
    row.manifest_digest = fingerprint(row.manifest)
    stamp = datetime(2020, 1, 2, tzinfo=timezone.utc)
    reviewed = EvidenceRecord(
        key="data", status="PASS", detail="Reviewed dataset identity.",
        artifact_ref="artifact://dataset", artifact_digest="c" * 64,
        artifact_version="dataset-v1", recorded_at=stamp, allowed_use="RESEARCH_ONLY",
    )
    evidence = ResearchEvidence(
        id=uuid4(), user_id=uuid4(), trial_id=row.id, key="data", status="PASS",
        detail="Reviewed dataset identity.", artifact_ref="artifact://dataset",
        artifact_digest="c" * 64, artifact_version="dataset-v1", recorded_at=stamp,
        allowed_use="RESEARCH_ONLY", evidence_digest=fingerprint(reviewed.model_dump(mode="json")),
        created_at=stamp,
    )
    checks = {check.key: check for check in assess_trial(row, evaluation, [evidence]).checks}
    assert checks["data"].status == "FAIL"
    assert "does not match" in checks["data"].detail
    evidence.artifact_digest = row.manifest["dataset_digest"]
    reviewed.artifact_digest = evidence.artifact_digest
    evidence.evidence_digest = fingerprint(reviewed.model_dump(mode="json"))
    checks = {check.key: check for check in assess_trial(row, evaluation, [evidence]).checks}
    assert checks["data"].status == "PASS"


def test_protected_holdout_pass_requires_declared_artifact_identity():
    row, evaluation = records()
    stamp = datetime(2020, 1, 2, tzinfo=timezone.utc)
    reviewed = EvidenceRecord(
        key="holdout", status="PASS", detail="Protected holdout review.",
        artifact_ref="artifact://holdout", artifact_digest="c" * 64,
        artifact_version="holdout-v1", recorded_at=stamp, allowed_use="RESEARCH_ONLY",
    )
    evidence = ResearchEvidence(
        id=uuid4(), user_id=uuid4(), trial_id=row.id, key="holdout", status="PASS",
        detail="Protected holdout review.", artifact_ref="artifact://holdout",
        artifact_digest="c" * 64, artifact_version="holdout-v1", recorded_at=stamp,
        allowed_use="RESEARCH_ONLY", evidence_digest=fingerprint(reviewed.model_dump(mode="json")),
        created_at=stamp,
    )
    checks = {check.key: check for check in assess_trial(row, evaluation, [evidence]).checks}
    assert checks["holdout"].status == "FAIL"
    assert "immutable trial artifact digest" in checks["holdout"].detail

    row.manifest["holdout_artifact_digest"] = "c" * 64
    row.manifest_digest = fingerprint(row.manifest)
    checks = {check.key: check for check in assess_trial(row, evaluation, [evidence]).checks}
    assert checks["holdout"].status == "PASS"


def test_cost_review_must_match_frozen_cost_model_version():
    row, evaluation = records()
    row.manifest["cost_model_id"] = "frozen-cost-v1"
    row.manifest_digest = fingerprint(row.manifest)
    evaluation.result["report"]["cost_model"]["illustrative"] = False
    evaluation.result_digest = fingerprint(evaluation.result)
    stamp = datetime(2020, 1, 2, tzinfo=timezone.utc)
    reviewed = EvidenceRecord(
        key="costs", status="PASS", detail="Reviewed cost model.",
        artifact_ref="artifact://cost-model", artifact_digest="c" * 64,
        artifact_version="other-cost-v1", recorded_at=stamp, allowed_use="RESEARCH_ONLY",
    )
    evidence = ResearchEvidence(
        id=uuid4(), user_id=uuid4(), trial_id=row.id, key="costs", status="PASS",
        detail=reviewed.detail, artifact_ref=reviewed.artifact_ref,
        artifact_digest=reviewed.artifact_digest, artifact_version=reviewed.artifact_version,
        recorded_at=stamp, allowed_use=reviewed.allowed_use,
        evidence_digest=fingerprint(reviewed.model_dump(mode="json")), created_at=stamp,
    )
    checks = {check.key: check for check in assess_trial(row, evaluation, [evidence]).checks}
    assert checks["costs"].status == "FAIL"
    assert "frozen cost model" in checks["costs"].detail


def test_cost_review_must_match_frozen_cost_artifact_digest():
    row, evaluation = records()
    row.manifest["cost_model_id"] = "frozen-cost-v1"
    row.manifest["cost_artifact_digest"] = "e" * 64
    row.manifest_digest = fingerprint(row.manifest)
    evaluation.result["report"]["cost_model"]["illustrative"] = False
    evaluation.result_digest = fingerprint(evaluation.result)
    stamp = datetime(2020, 1, 2, tzinfo=timezone.utc)
    reviewed = EvidenceRecord(
        key="costs", status="PASS", detail="Reviewed cost artifact.",
        artifact_ref="artifact://cost-model", artifact_digest="c" * 64,
        artifact_version="frozen-cost-v1", recorded_at=stamp, allowed_use="RESEARCH_ONLY",
    )
    evidence = ResearchEvidence(
        id=uuid4(), user_id=uuid4(), trial_id=row.id, key="costs", status="PASS",
        detail=reviewed.detail, artifact_ref=reviewed.artifact_ref,
        artifact_digest=reviewed.artifact_digest, artifact_version=reviewed.artifact_version,
        recorded_at=stamp, allowed_use=reviewed.allowed_use,
        evidence_digest=fingerprint(reviewed.model_dump(mode="json")), created_at=stamp,
    )
    checks = {check.key: check for check in assess_trial(row, evaluation, [evidence]).checks}
    assert checks["costs"].status == "FAIL"
    assert "immutable cost artifact digest" in checks["costs"].detail

    evidence.artifact_digest = row.manifest["cost_artifact_digest"]
    reviewed.artifact_digest = evidence.artifact_digest
    evidence.evidence_digest = fingerprint(reviewed.model_dump(mode="json"))
    checks = {check.key: check for check in assess_trial(row, evaluation, [evidence]).checks}
    assert checks["costs"].status == "PASS"


def test_reviewed_rights_must_match_declared_source_artifact_digest():
    row, evaluation = records()
    row.manifest["rights_artifact_digest"] = "e" * 64
    row.manifest_digest = fingerprint(row.manifest)
    stamp = datetime(2020, 1, 2, tzinfo=timezone.utc)
    reviewed = EvidenceRecord(
        key="rights", status="PASS", detail="Reviewed source rights.",
        artifact_ref="artifact://source-rights", artifact_digest="c" * 64,
        artifact_version="rights-v1", recorded_at=stamp, allowed_use="RESEARCH_ONLY",
    )
    evidence = ResearchEvidence(
        id=uuid4(), user_id=uuid4(), trial_id=row.id, key="rights", status="PASS",
        detail=reviewed.detail, artifact_ref=reviewed.artifact_ref,
        artifact_digest=reviewed.artifact_digest, artifact_version=reviewed.artifact_version,
        recorded_at=stamp, allowed_use=reviewed.allowed_use,
        evidence_digest=fingerprint(reviewed.model_dump(mode="json")), created_at=stamp,
    )
    checks = {check.key: check for check in assess_trial(row, evaluation, [evidence]).checks}
    assert checks["rights"].status == "FAIL"
    assert "immutable rights" in checks["rights"].detail

    evidence.artifact_digest = row.manifest["rights_artifact_digest"]
    reviewed.artifact_digest = evidence.artifact_digest
    evidence.evidence_digest = fingerprint(reviewed.model_dump(mode="json"))
    checks = {check.key: check for check in assess_trial(row, evaluation, [evidence]).checks}
    assert checks["rights"].status == "PASS"
