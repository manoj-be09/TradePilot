"""Fingerprint shipped calculation source; never read credentials or Git state."""

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = (
    "packages/paper_trading/equity.py",
    "packages/backtesting/performance.py",
    "packages/regime/chronological_validation.py",
    "packages/regime/equity_policy_trials.py",
    "packages/regime/research_identity.py",
)


def research_code_digest() -> str:
    """Hash a declared source set, including filenames and byte boundaries.

    This records source present on disk, not a complete environment or proof of
    loaded Python bytecode identity. Missing source must fail rather than guess.
    """
    digest = hashlib.sha256()
    for name in FILES:
        source = (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(len(source).to_bytes(8, "big"))
        digest.update(source)
    return digest.hexdigest()
