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

    def to_json(self) -> str:
        return json.dumps({
            "user_email": self.user_email,
            "version_ids": [str(item) for item in self.version_ids],
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
        }, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "ForwardPlan":
        data = json.loads(text)
        return cls(
            user_email=data["user_email"],
            version_ids=tuple(UUID(item) for item in data["version_ids"]),
            start=date.fromisoformat(data["start"]),
            end=date.fromisoformat(data["end"]),
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
