"""Read the saved history results that the Trade page ranks strategies by.

A strategy uses its replay of the real paper rules when there is one, else its walk-forward
result, which used similar rules. The result files are built from private market data, so they
stay on this machine and out of git. A missing or unreadable file means no past test for that
strategy, never an error. Win rates here describe simulated history; they are not a forecast of
the next trade.
"""

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_RESULTS_DIRECTORY = Path("work/bt")
TEMPLATE_RULES_MODEL_ID = "nifty-history-template-rules-v1"
_WALK_FORWARD_MODEL_ID = "nifty-history-walk-forward-v1"


@dataclass(frozen=True)
class PastTest:
    strategy_version: str
    exact_rules: bool
    trades: int
    expiries_skipped: int
    win_rate_pct: Decimal
    net_pnl: Decimal
    average_net_pnl: Decimal
    drawdown: Decimal
    first_exit: date
    last_exit: date
    half_spread_pct: Decimal


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _past_test(
    config: dict[str, Any], result: dict[str, Any], *, exact_rules: bool, expiries_skipped: int,
) -> tuple[str, PastTest] | None:
    if result["closed_trades"] < 1:
        return None
    return config["strategy_key"], PastTest(
        strategy_version=config["strategy_version"],
        exact_rules=exact_rules,
        trades=result["closed_trades"],
        expiries_skipped=expiries_skipped,
        win_rate_pct=_money(result["win_rate"]),
        net_pnl=_money(result["net_pnl"]),
        average_net_pnl=_money(result["average_net_pnl"]),
        drawdown=_money(result["closed_trade_drawdown"]),
        first_exit=datetime.fromisoformat(result["first_exit_at"]).date(),
        last_exit=datetime.fromisoformat(result["last_exit_at"]).date(),
        half_spread_pct=Decimal(str(config["half_spread_pct"])),
    )


def _read_walk_forward(path: Path) -> tuple[str, PastTest] | None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw["model_id"] != _WALK_FORWARD_MODEL_ID:
        return None
    return _past_test(raw["base_config"], raw["out_of_sample"], exact_rules=False,
                      expiries_skipped=0)


def _read_template_rules(path: Path) -> tuple[str, PastTest] | None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw["results_model"] != TEMPLATE_RULES_MODEL_ID:
        return None
    return _past_test(raw["config"], raw["overall"], exact_rules=True,
                      expiries_skipped=raw["expiries_skipped"])


def load_past_tests(directory: Path = DEFAULT_RESULTS_DIRECTORY) -> dict[str, PastTest]:
    tests: dict[str, PastTest] = {}
    # The real-rule results come second so that they replace walk-forward ones.
    for pattern, read in (
        ("wf-*.json", _read_walk_forward), ("rules-*.json", _read_template_rules),
    ):
        for path in sorted(directory.glob(pattern)):
            try:
                loaded = read(path)
            except (OSError, ValueError, KeyError, TypeError, ArithmeticError) as error:
                logger.warning("Skipping unreadable past test %s: %s", path.name, error)
                continue
            if loaded is not None:
                tests[loaded[0]] = loaded[1]
    return tests
