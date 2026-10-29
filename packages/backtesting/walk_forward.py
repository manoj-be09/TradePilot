"""Walk-forward evaluation of history-backtest settings by expiry.

Expiries are split into consecutive windows: settings are chosen on a training window
of past expiries (highest net P&L after charges, ties to the earlier candidate), then
scored on the following test window the choice never saw. Windows roll forward by the
test size, so every test expiry is out of sample exactly once. The out-of-sample trades
are summarised with the same statistics as a plain backtest.

Each candidate's trade per expiry is computed once and reused across windows, so the
cost is one replay per candidate per expiry. Simulated results only.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import Any

from packages.backtesting.history_backtest import (
    BacktestConfig,
    BacktestTrade,
    ExpiryHistory,
    SkippedExpiry,
    _stats,
    run_expiry,
)

MODEL_ID = "nifty-history-walk-forward-v1"


@dataclass(frozen=True)
class Candidate:
    """A named set of BacktestConfig overrides, e.g. {"entry_days_before_expiry": 2}."""

    label: str
    overrides: dict[str, Any]


@dataclass(frozen=True)
class WalkForwardWindow:
    train_first: date
    train_last: date
    test_first: date
    test_last: date
    chosen: str
    train_net_pnl: Decimal
    train_trades: int
    test_trades: list[BacktestTrade]

    @property
    def test_net_pnl(self) -> Decimal:
        return sum((trade.net_pnl for trade in self.test_trades), Decimal(0))


@dataclass
class WalkForwardReport:
    base: BacktestConfig
    candidates: list[Candidate]
    train_size: int
    test_size: int
    windows: list[WalkForwardWindow] = field(default_factory=list)

    def out_of_sample(self) -> list[BacktestTrade]:
        trades = [trade for window in self.windows for trade in window.test_trades]
        return sorted(trades, key=lambda trade: trade.exit_at)

    def summary(self) -> dict[str, Any]:
        chosen: dict[str, int] = {}
        for window in self.windows:
            chosen[window.chosen] = chosen.get(window.chosen, 0) + 1
        return {
            "model_id": MODEL_ID,
            "base_config": self.base.snapshot(),
            "candidates": [{"label": item.label, "overrides": {
                key: str(value) if value is not None else None
                for key, value in item.overrides.items()
            }} for item in self.candidates],
            "train_expiries": self.train_size,
            "test_expiries": self.test_size,
            "windows": [{
                "train": [window.train_first.isoformat(), window.train_last.isoformat()],
                "test": [window.test_first.isoformat(), window.test_last.isoformat()],
                "chosen": window.chosen,
                "train_net_pnl": float(window.train_net_pnl),
                "train_trades": window.train_trades,
                "test_net_pnl": float(window.test_net_pnl),
                "test_trades": len(window.test_trades),
            } for window in self.windows],
            "times_chosen": chosen,
            "out_of_sample": _stats(self.out_of_sample()),
            "note": "Simulated. Settings are chosen on earlier expiries and scored on later ones.",
        }


def walk_forward(
    histories: Sequence[ExpiryHistory], base: BacktestConfig, candidates: Sequence[Candidate],
    *, train_size: int, test_size: int,
) -> WalkForwardReport:
    if not candidates:
        raise ValueError("Walk-forward needs at least one candidate")
    if train_size < 1 or test_size < 1:
        raise ValueError("Train and test windows need at least one expiry each")
    if len({item.label for item in candidates}) != len(candidates):
        raise ValueError("Candidate labels must be unique")
    ordered = sorted(histories, key=lambda item: item.expiry)
    configs = [replace(base, **item.overrides) for item in candidates]
    outcomes: list[list[BacktestTrade | None]] = [
        [_trade(run_expiry(history, config)) for history in ordered] for config in configs
    ]
    report = WalkForwardReport(base=base, candidates=list(candidates),
                               train_size=train_size, test_size=test_size)
    start = 0
    while start + train_size < len(ordered):
        train = range(start, start + train_size)
        test = range(start + train_size, min(start + train_size + test_size, len(ordered)))
        scores = [
            sum((trade.net_pnl for index in train if (trade := row[index]) is not None),
                Decimal(0))
            for row in outcomes
        ]
        best = max(range(len(candidates)), key=lambda index: (scores[index], -index))
        report.windows.append(WalkForwardWindow(
            train_first=ordered[train[0]].expiry, train_last=ordered[train[-1]].expiry,
            test_first=ordered[test[0]].expiry, test_last=ordered[test[-1]].expiry,
            chosen=candidates[best].label, train_net_pnl=scores[best],
            train_trades=sum(outcomes[best][index] is not None for index in train),
            test_trades=[trade for index in test if (trade := outcomes[best][index]) is not None],
        ))
        start += test_size
    return report


def _trade(outcome: BacktestTrade | SkippedExpiry) -> BacktestTrade | None:
    return None if isinstance(outcome, SkippedExpiry) else outcome
