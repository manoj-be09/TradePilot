"""Run a synthetic registered replay in a disposable local SQLite database.

No environment/database URLs, provider credentials, network or owner account are
read. Output is diagnostic runtime evidence, never market/performance evidence.
Run from any directory with the project's Python environment.
"""

import hashlib
import json
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.database.base import Base  # noqa: E402
from packages.database.models import ResearchBudgetSlot, ResearchTrial, User  # noqa: E402
from packages.regime.candidate_registry import (  # noqa: E402
    CandidateDefinition,
    TrialManifest,
    declare_trial,
    register_candidate,
)
from packages.regime.fixed_rule_evaluator import COST_MODEL_ID, FixedEquitySpec  # noqa: E402
from packages.regime.registered_evaluator import (  # noqa: E402
    bind_fixed_equity,
    run_registered_equity,
)
from packages.regime.research_identity import research_code_digest  # noqa: E402


def main() -> None:
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(900)]
    bars = []
    for index, day in enumerate(days):
        phase = index % 30
        close = Decimal(100 + (phase if phase <= 20 else 60 - 2 * phase))
        bars.append({"timestamp": f"{day}T09:15:00+05:30", "open": str(close),
                     "high": str(close + 1), "low": str(close - 1), "close": str(close),
                     "volume": 10000})
    raw = json.dumps({"provider": "synthetic", "instrument_key": "DEMO_ONLY", "bars": bars},
                     sort_keys=True, separators=(",", ":")).encode()
    manifest = TrialManifest(
        dataset_digest=hashlib.sha256(raw).hexdigest(), calculation_digest=research_code_digest(),
        cost_model_id=COST_MODEL_ID, experiment_family="synthetic-demo", trial_number=1,
        declared_search_budget=1, seed=0, train_start=days[0], train_end=days[539],
        validation_start=days[542], validation_end=days[719],
        holdout_start=days[722], holdout_end=days[-1],
    )
    definition = CandidateDefinition(
        name="SYNTHETIC DEMO ONLY", family="diagnostic", market="EQUITY",
        hypothesis="Demonstrate registered replay accounting on an invented periodic price series.",
        source_url="https://example.org/synthetic-demo", source_license="SYNTHETIC_FIXTURE",
        entry_rule="Use the shipped momentum rule with an explicit frozen adapter binding.",
        exit_rule="Use the existing default equity exit policy.",
        limitation="Invented daily calendar and prices; no source or market qualification.",
    )
    spec = FixedEquitySpec(strategy="momentum", provider="synthetic", instrument_key="DEMO_ONLY",
                           budget=Decimal("10000"), risk_budget=Decimal("100"))
    work = ROOT / "work"
    work.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="research-demo-", dir=work) as disposable:
        engine = create_engine(f"sqlite:///{Path(disposable) / 'demo.sqlite'}")
        try:
            Base.metadata.create_all(engine)
            with Session(engine) as session:
                owner = uuid4()
                session.add(User(id=owner, email="synthetic-demo@example.org",
                                 password_hash="unused"))
                session.commit()
                candidate = register_candidate(session, owner, definition)
                trial = declare_trial(session, owner, candidate.id, manifest)
                bind_fixed_equity(session, owner, trial.id, spec)
                result = run_registered_equity(session, owner, trial.id, raw)
                first_digest = result.result_digest
                repeated = run_registered_equity(session, owner, trial.id, raw)
                operator_untouched = session.get(ResearchTrial, trial.id).result is None
                slots = session.scalar(select(func.count()).select_from(ResearchBudgetSlot))
                assert first_digest == repeated.result_digest and slots == 1 and operator_untouched
                print(json.dumps({"evidence": "SYNTHETIC_DEMO_NOT_MARKET_EVIDENCE",
                    "database": "disposable SQLite; no configured account database accessed",
                    "dataset_digest": manifest.dataset_digest, "calculation_digest": (
                        manifest.calculation_digest), "budget_slots_consumed": slots,
                    "idempotent_retry": True, "operator_result_untouched": operator_untouched,
                    "engine_result": result.result, "result_digest": first_digest}, indent=2))
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
