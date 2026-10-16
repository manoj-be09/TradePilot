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
