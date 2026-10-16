"""Fingerprint shipped calculation source; never read credentials or Git state."""

import hashlib
import platform
import sys
from decimal import getcontext
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = (
    "packages/paper_trading/equity.py",
    "packages/backtesting/performance.py",
    "packages/regime/chronological_validation.py",
    "packages/regime/equity_policy_trials.py",
    "packages/regime/research_identity.py",
    "packages/regime/research_inputs.py",
    "packages/regime/fixed_rule_evaluator.py",
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


def research_environment() -> dict[str, object]:
    """Record declared runtime inputs, not a complete machine/environment manifest."""
    try:
        dependencies = {name: version(name) for name in ("pydantic", "pydantic_core")}
    except PackageNotFoundError:
        raise OSError("Calculation runtime metadata is unavailable") from None
    context = getcontext()
    return {
        "schema_version": 1, "python_version": platform.python_version(),
        "python_implementation": sys.implementation.name, "dependencies": dependencies,
        "decimal": {
            "precision": context.prec, "rounding": context.rounding,
            "emin": context.Emin, "emax": context.Emax, "clamp": context.clamp,
            "traps": sorted(
                signal.__name__ for signal, enabled in context.traps.items() if enabled
            ),
        },
    }
