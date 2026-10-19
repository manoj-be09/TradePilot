from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.research_routes import create_study, studies
from packages.database.base import Base
from packages.database.models import ResearchStudy, User
from packages.regime.frozen_nifty_study import FrozenNiftyStudy
from packages.regime.research_studies import freeze_nifty_study


def study(**changes):
    return FrozenNiftyStudy(**({
        "study_key": "nifty50-paper-v1",
        "objective": ("Compare the incumbent NIFTY option rule with a no-trade "
                       "comparator on later unseen sessions."),
        "incumbent_rule_version": "nifty-paper-v1", "dataset_digest": "a" * 64,
        "calculation_digest": "b" * 64, "cost_model_id": "option-close-illustrative-v1",
        "capital": "100000.00", "parent_search_budget": 10, "review_interval_days": 30,
        "uncertainty_note": ("Results remain indicative until source rights, fill timing "
                             "and expiry evidence are reviewed."),
        "train_start": "2020-01-01", "train_end": "2022-12-31",
        "validation_start": "2023-01-01", "validation_end": "2024-12-31",
        "holdout_start": "2025-01-01", "holdout_end": "2025-12-31",
    } | changes))


def test_parent_study_is_account_scoped_idempotent_and_immutable():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner, other = uuid4(), uuid4()
    with Session(engine) as session:
        session.add_all([User(id=owner, email="one@example.org", password_hash="fixture"),
                         User(id=other, email="two@example.org", password_hash="fixture")])
        session.commit()
        saved = freeze_nifty_study(session, owner, study())
        assert freeze_nifty_study(session, owner, study()).id == saved.id
        with pytest.raises(ValueError, match="different immutable"):
            freeze_nifty_study(session, owner, study(parent_search_budget=11))
        assert freeze_nifty_study(session, other, study()).id != saved.id
        assert len(list(session.scalars(select(ResearchStudy)))) == 2
    engine.dispose()


def test_study_routes_return_read_only_account_scoped_view():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner = uuid4()
    with Session(engine) as session:
        session.add(User(id=owner, email="route@example.org", password_hash="fixture"))
        session.commit()
        response = create_study(study(), SimpleNamespace(id=owner), session)
        assert response["execution_enabled"] is False
        out = studies(SimpleNamespace(id=owner), session, SimpleNamespace(headers={}), 25, 0)
        assert out[0]["manifest_digest"] == response["manifest_digest"]
    engine.dispose()


def test_study_list_is_newest_first_with_deterministic_id_tie_break():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner = uuid4()
    with Session(engine) as session:
        session.add(User(id=owner, email="study-order@example.org", password_hash="fixture"))
        session.commit()
        older = study(study_key="nifty-study-older")
        newer = study(study_key="nifty-study-newer")
        timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        session.add_all([
            ResearchStudy(id=uuid4(), user_id=owner, study_key=older.study_key,
                          manifest=older.manifest, manifest_digest=older.manifest_digest,
                          created_at=timestamp),
            ResearchStudy(id=uuid4(), user_id=owner, study_key=newer.study_key,
                          manifest=newer.manifest, manifest_digest=newer.manifest_digest,
                          created_at=timestamp),
        ])
        session.commit()
        rows = studies(SimpleNamespace(id=owner), session, SimpleNamespace(headers={}), 25, 0)
        expected = sorted(rows, key=lambda row: (row["created_at"], row["id"]), reverse=True)
        assert rows == expected
    engine.dispose()
