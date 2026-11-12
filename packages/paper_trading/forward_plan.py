"""The frozen rules of a forward paper period: which saved strategy versions run, and until when."""

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True)
class ForwardPlan:
    user_email: str
    version_ids: tuple[UUID, ...]
    start: date
    end: date
    # Frozen versions the owner stopped mid-period: never armed again, still reported.
    stopped_version_ids: tuple[UUID, ...] = ()
    stopped_note: str = ""

    def __post_init__(self) -> None:
        if not set(self.stopped_version_ids) <= set(self.version_ids):
            raise ValueError("A stopped version must be one of the plan's frozen versions")

    def to_json(self) -> str:
        data = {
            "user_email": self.user_email,
            "version_ids": [str(item) for item in self.version_ids],
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
        }
        if self.stopped_version_ids:
            data["stopped_version_ids"] = [str(item) for item in self.stopped_version_ids]
            data["stopped_note"] = self.stopped_note
        return json.dumps(data, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "ForwardPlan":
        data = json.loads(text)
        return cls(
            user_email=data["user_email"],
            version_ids=tuple(UUID(item) for item in data["version_ids"]),
            start=date.fromisoformat(data["start"]),
            end=date.fromisoformat(data["end"]),
            stopped_version_ids=tuple(UUID(item) for item in data.get("stopped_version_ids", [])),
            stopped_note=data.get("stopped_note", ""),
        )


def load_plan(path: Path) -> ForwardPlan:
    return ForwardPlan.from_json(path.read_text(encoding="utf-8"))


def save_new_plan(path: Path, plan: ForwardPlan) -> None:
    """Refuse to overwrite: a frozen period is not changed by rerunning the command."""
    if path.exists():
        raise FileExistsError(f"{path} already exists; a forward period is already frozen")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plan.to_json(), encoding="utf-8")


def next_expiry(expiries: list[date], today: date) -> date | None:
    """The nearest expiry after today; an expiry-day entry would be squared off within hours."""
    return min((item for item in expiries if item > today), default=None)


def nearest_strike(strikes: list[Decimal], spot: Decimal) -> Decimal | None:
    return min(strikes, key=lambda strike: abs(strike - spot), default=None)
