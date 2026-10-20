"""Freeze bounded, non-executable NIFTY comparison policies before observation."""
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.database.models import ResearchPolicySet, ResearchRecommendation, ResearchStudy
from packages.paper_trading.strategies import STRATEGY_REGISTRY
from packages.regime.candidate_registry import fingerprint
from packages.regime.frozen_nifty_study import FrozenNiftyStudy


class FrozenPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    strategy_key: Literal["bull_call_spread", "bear_put_spread", "iron_condor"]
    strategy_version: str = Field(min_length=1, max_length=64)
    policy_version: str = Field(min_length=1, max_length=64)
    policy_artifact_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    entry_rule: str = Field(min_length=20, max_length=1000)
    exit_rule: str = Field(min_length=20, max_length=1000)
    risk_rule: str = Field(min_length=20, max_length=1000)


class FrozenPolicySet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    incumbent: FrozenPolicy
    challengers: list[FrozenPolicy] = Field(default_factory=list, max_length=2)
    no_trade_comparator: Literal["NO_TRADE"] = "NO_TRADE"
    limitation: str = Field(min_length=20, max_length=1000)

    @model_validator(mode="after")
    def distinct_policies(self) -> "FrozenPolicySet":
        keys = [item.strategy_key for item in [self.incumbent, *self.challengers]]
        if len(set(keys)) != len(keys):
            raise ValueError("Incumbent and challengers must use distinct strategy structures")
        return self


def freeze_policy_set(session: Session, owner: UUID, study_id: UUID,
                      policies: FrozenPolicySet) -> ResearchPolicySet:
    study_row = session.scalar(select(ResearchStudy).where(
        ResearchStudy.id == study_id, ResearchStudy.user_id == owner,
    ))
    if study_row is None:
        raise LookupError("Study not found")
    study = FrozenNiftyStudy.model_validate(study_row.manifest)
    if study.manifest_digest != study_row.manifest_digest:
        raise ValueError("Study manifest integrity failed")
    if policies.incumbent.policy_version != study.incumbent_rule_version:
        raise ValueError("Incumbent policy version differs from its frozen study")
    payload = policies.model_dump(mode="json")
    for role in ("incumbent", "challengers"):
        items = [payload[role]] if role == "incumbent" else payload[role]
        for item in items:
            template = STRATEGY_REGISTRY.get(item["strategy_key"])
            if template is None or template.version != item["strategy_version"]:
                raise ValueError("Policy template version is unavailable for freezing")
            item["template_digest"] = fingerprint(asdict(template))
    payload["study_manifest_digest"] = study.manifest_digest
    payload["cost_model_id"] = study.cost_model_id
    payload["cost_artifact_digest"] = study.cost_artifact_digest
    payload["parent_search_budget"] = study.parent_search_budget
    payload["holdout_artifact_digest"] = study.holdout_artifact_digest
    digest = fingerprint(payload)
    existing = session.scalar(select(ResearchPolicySet).where(
        ResearchPolicySet.study_id == study_id, ResearchPolicySet.user_id == owner,
    ))
    if existing is not None:
        if existing.manifest_digest == digest:
            return existing
        raise ValueError("Study already has a different immutable policy set")
    if session.scalar(select(ResearchRecommendation.id).where(
        ResearchRecommendation.user_id == owner,
        ResearchRecommendation.study_id == study_id,
    ).limit(1)) is not None:
        raise ValueError("Policies must be frozen before recommendation observations")
    row = ResearchPolicySet(
        user_id=owner, study_id=study_id, manifest=payload,
        manifest_digest=digest, created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(select(ResearchPolicySet).where(
            ResearchPolicySet.study_id == study_id, ResearchPolicySet.user_id == owner,
        ))
        if existing is None or existing.manifest_digest != digest:
            raise
        return existing
    return row


def policy_set_view(row: ResearchPolicySet) -> dict[str, object]:
    return {
        "id": row.id, "study_id": row.study_id, "manifest": row.manifest,
        "manifest_digest": row.manifest_digest, "created_at": row.created_at,
        "execution_enabled": False, "promotion_eligible": False,
    }


def current_policy_set_ids(row: ResearchPolicySet) -> set[tuple[str, str]]:
    """Fail closed if a pinned template was changed without a new version."""
    if fingerprint(row.manifest) != row.manifest_digest:
        raise ValueError("Frozen policy set integrity failed")
    try:
        items = [row.manifest["incumbent"], *row.manifest["challengers"]]
    except (KeyError, TypeError):
        raise ValueError("Frozen policy set is incomplete") from None
    identities: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Frozen policy set is incomplete")
        key, version, digest = (
            item.get("strategy_key"), item.get("strategy_version"),
            item.get("template_digest"),
        )
        if not isinstance(key, str) or not isinstance(version, str):
            raise ValueError("Frozen policy set is incomplete")
        template = STRATEGY_REGISTRY.get(key)
        if (template is None or template.version != version
                or fingerprint(asdict(template)) != digest):
            raise ValueError("Frozen template differs from current source")
        identities.add((key, version))
    return identities
