"""Versioned, immutable paper exit settings for NIFTY option positions."""

from dataclasses import dataclass
from typing import Any

OPTION_EXIT_POLICY_VERSION = "nifty-option-exit-v1"
EXPIRY_TEST_POLICY_VERSION = "nifty-option-expiry-test-v1"
# Also squares off at the cutoff of the first session after the entry day (the night condors).
NEXT_SESSION_POLICY_VERSION = "nifty-option-next-session-v1"
_VERSIONS = {OPTION_EXIT_POLICY_VERSION, EXPIRY_TEST_POLICY_VERSION, NEXT_SESSION_POLICY_VERSION}


@dataclass(frozen=True)
class OptionExitPolicy:
    square_off_minutes_before_close: int
    version: str = OPTION_EXIT_POLICY_VERSION

    def __post_init__(self) -> None:
        if self.version not in _VERSIONS:
            raise ValueError("Unknown NIFTY option paper exit policy version")
        if (
            type(self.square_off_minutes_before_close) is not int
            or not 5 <= self.square_off_minutes_before_close <= 60
        ):
            raise ValueError("NIFTY option square-off cutoff must be 5–60 minutes")

    @property
    def holds_for_expiry_test(self) -> bool:
        return self.version == EXPIRY_TEST_POLICY_VERSION

    @property
    def exits_next_session(self) -> bool:
        return self.version == NEXT_SESSION_POLICY_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "square_off_minutes_before_close": self.square_off_minutes_before_close,
        }


def parse_option_exit_policy(
    raw: object, *, legacy_cutoff: int | None = None
) -> OptionExitPolicy:
    """Missing old metadata can retain its prior runtime fallback; malformed cannot."""
    if raw is None and legacy_cutoff is not None:
        return OptionExitPolicy(legacy_cutoff)
    if not isinstance(raw, dict) or set(raw) != {
        "version", "square_off_minutes_before_close",
    }:
        raise ValueError("Saved NIFTY option exit policy is missing or malformed")
    return OptionExitPolicy(
        square_off_minutes_before_close=raw["square_off_minutes_before_close"],
        version=raw["version"],
    )
