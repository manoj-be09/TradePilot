"""L-Earning Candle: before each NIFTY 5-minute candle, guess green or red, then learn from it.

An online logistic regression. Each completed candle is scored on the guess made before it was
seen (so the accuracy is honest, out-of-sample), then the weights move by the error: a confident
right guess barely changes them, a confident wrong guess changes them most. Each candle is learned
once; replaying the same window does not count twice. Simulated research only — not advice.
"""

import json
import math
from dataclasses import dataclass, field
from datetime import time, timedelta, timezone
from pathlib import Path
from typing import Any

from packages.market_data.contracts import HistoricalBar

IST = timezone(timedelta(hours=5, minutes=30))
FEATURES = (
    "body", "upper_wick", "lower_wick", "ret_1", "ret_3", "ret_6", "streak", "rsi_14",
    "ema_9_21_gap", "day_position", "from_day_open", "time_of_day", "range_expansion",
    "vix_change", "vix_level",
)
_WARMUP = 21  # bars needed before the slow EMA and RSI mean anything
_RECENT = 100
_LAST_CANDLE = time(15, 25)


def _bps(now: float, before: float) -> float:
    return (now / before - 1) * 10_000


def _ema(values: list[float], period: int) -> float:
    alpha, value = 2 / (period + 1), values[0]
    for item in values[1:]:
        value += alpha * (item - value)
    return value


def _rsi(closes: list[float], period: int = 14) -> float:
    moves = [b - a for a, b in zip(closes[-period - 1:], closes[-period:], strict=False)]
    gain, loss = sum(m for m in moves if m > 0), -sum(m for m in moves if m < 0)
    return 50.0 if gain + loss == 0 else 100 * gain / (gain + loss) - 50


def color(bar: HistoricalBar) -> int | None:
    """1 green, 0 red, None for a doji (neither, so never scored or learned)."""
    return None if bar.close == bar.open else int(bar.close > bar.open)


def features(bars: list[HistoricalBar], vix: dict[Any, HistoricalBar]) -> list[float] | None:
    """Everything known when the last bar closes. None (skip) when history or VIX is missing."""
    if len(bars) < _WARMUP:
        return None
    last = bars[-1]
    vix_now = vix.get(last.timestamp)
    vix_before = vix.get(bars[-2].timestamp)
    if vix_now is None or vix_before is None or vix_before.close <= 0:
        return None
    o, h, low, c = (float(last.open), float(last.high), float(last.low), float(last.close))
    span = h - low
    closes = [float(bar.close) for bar in bars[-_WARMUP - 5:]]
    day = last.timestamp.astimezone(IST).date()
    today = [bar for bar in bars if bar.timestamp.astimezone(IST).date() == day]
    day_high = max(float(bar.high) for bar in today)
    day_low = min(float(bar.low) for bar in today)
    streak = 0
    for bar in reversed(bars[-6:]):
        side = color(bar)
        if side is None or (streak and (side == 1) != (streak > 0)):
            break
        streak += 1 if side else -1
    ranges = [float(bar.high - bar.low) for bar in bars[-13:-1]]
    clock = last.timestamp.astimezone(IST)
    return [
        (c - o) / span if span else 0.0,
        (h - max(o, c)) / span if span else 0.0,
        (min(o, c) - low) / span if span else 0.0,
        _bps(c, closes[-2]), _bps(c, closes[-4]), _bps(c, closes[-7]),
        float(streak), _rsi(closes),
        _bps(_ema(closes, 9), _ema(closes, 21)),
        (c - day_low) / (day_high - day_low) if day_high > day_low else 0.5,
        _bps(c, float(today[0].open)),
        ((clock.hour - 9) * 60 + clock.minute - 15) / 375,
        span / (sum(ranges) / len(ranges)) if sum(ranges) else 1.0,
        _bps(float(vix_now.close), float(vix_before.close)),
        float(vix_now.close),
    ]


@dataclass
class Learner:
    """Online-standardised logistic regression plus an honest scoreboard."""

    weights: list[float] = field(default_factory=lambda: [0.0] * len(FEATURES))
    bias: float = 0.0
    count: int = 0
    mean: list[float] = field(default_factory=lambda: [0.0] * len(FEATURES))
    m2: list[float] = field(default_factory=lambda: [0.0] * len(FEATURES))
    learned_until: str | None = None
    played: int = 0
    right: int = 0
    repeat_right: int = 0  # baseline: "next candle repeats the last one's colour"
    recent: list[int] = field(default_factory=list)
    rate: float = 0.02
    l2: float = 0.001

    def _scale(self, x: list[float], update: bool = True) -> list[float]:
        # Welford on features only: no label is involved, so nothing leaks
        self.count += update
        for i, value in enumerate(x if update else ()):
            delta = value - self.mean[i]
            self.mean[i] += delta / self.count
            self.m2[i] += delta * (value - self.mean[i])
        return [(value - self.mean[i]) / (math.sqrt(self.m2[i] / self.count) or 1.0)
                if self.count > 1 else 0.0 for i, value in enumerate(x)]

    def probability(self, z: list[float]) -> float:
        score = self.bias + sum(w * v for w, v in zip(self.weights, z, strict=True))
        return 1 / (1 + math.exp(-max(-30.0, min(30.0, score))))

    def learn(self, z: list[float], green: int, p: float) -> None:
        error = green - p  # large when confidently wrong, tiny when confidently right
        self.weights = [w + self.rate * (error * v - self.l2 * w)
                        for w, v in zip(self.weights, z, strict=True)]
        self.bias += self.rate * error

    def score(self, guess: int, actual: int, last_color: int | None) -> None:
        self.played += 1
        self.right += guess == actual
        self.repeat_right += last_color == actual
        self.recent = [*self.recent, int(guess == actual)][-_RECENT:]

    def summary(self) -> str:
        if not self.played:
            return "No candles scored yet."
        pct = 100 * self.right / self.played
        recent = 100 * sum(self.recent) / len(self.recent)
        repeat = 100 * self.repeat_right / self.played
        return (f"{self.played} candles: {pct:.1f}% right overall, {recent:.1f}% over the last "
                f"{len(self.recent)}; 'repeat last colour' baseline {repeat:.1f}%")

    def top_weights(self, n: int = 5) -> list[tuple[str, float]]:
        return sorted(zip(FEATURES, self.weights, strict=True), key=lambda kv: -abs(kv[1]))[:n]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.__dict__, indent=1))

    @classmethod
    def load(cls, path: Path) -> "Learner":
        return cls(**json.loads(path.read_text())) if path.exists() else cls()


@dataclass(frozen=True)
class Turn:
    timestamp: Any
    p_green: float
    actual: int | None  # None while the candle has not finished yet


def play(learner: Learner, bars: list[HistoricalBar], vix_bars: list[HistoricalBar]) -> list[Turn]:
    """Walk completed bars in order. Each new candle: guess from the bars before it, score, learn.

    The last turn has actual=None: the guess for the candle that has not started yet.
    """
    vix = {bar.timestamp: bar for bar in vix_bars}
    turns: list[Turn] = []
    for i in range(len(bars)):
        nxt = bars[i + 1] if i + 1 < len(bars) else None
        if nxt is not None and learner.learned_until and nxt.timestamp.isoformat() <= (
                learner.learned_until):
            continue
        if nxt is None and bars[i].timestamp.astimezone(IST).time() >= _LAST_CANDLE:
            break  # session over: there is no next candle to guess
        same_day = nxt is None or (nxt.timestamp.astimezone(IST).date()
                                   == bars[i].timestamp.astimezone(IST).date())
        x = features(bars[:i + 1], vix) if same_day else None
        if x is None:
            continue
        z = learner._scale(x, update=nxt is not None)
        p = learner.probability(z)
        actual = None if nxt is None else color(nxt)
        turns.append(Turn(nxt.timestamp if nxt else None, p, actual))
        if nxt is None:
            break
        if actual is not None:
            learner.score(int(p >= 0.5), actual, color(bars[i]))
            learner.learn(z, actual, p)
        learner.learned_until = nxt.timestamp.isoformat()
    return turns
