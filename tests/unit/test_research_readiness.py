from datetime import datetime, timezone
from uuid import uuid4

from packages.database.models import ResearchEvaluation, ResearchTrial
from packages.regime.candidate_registry import fingerprint
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
