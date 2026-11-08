"""L-Earning Candle: before each NIFTY 5-minute candle, guess green or red, then learn from it.

An online logistic regression with AdaGrad steps. Each completed candle is scored on the guess
made before it was seen (so the accuracy is honest, out-of-sample), then the weights move by the
error: a confident right guess barely changes them, a confident wrong guess changes them most.
When the model is less than 55/45 sure it passes; calls and passes are scored separately. Each
candle is learned once; replaying the same window does not count twice. Simulated research only.

Chosen on 2 years of NIFTY 5m candles: tuned on Nov 2024-May 2026, then checked once on the
following six months (53.1% on every candle, 55.1% on calls; the old plain-SGD model 51.3%).
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
    "vix_change", "vix_level", "last_colour", "day_extreme", "move_x_range", "move_size",
    "slot_green_rate",
)
_WARMUP = 21  # bars needed before the slow EMA and RSI mean anything
_RECENT = 100
_KEEP = 500  # guesses kept for the page
_LAST_CANDLE = time(15, 25)
_WINDOW = 110  # one whole session (75 bars) plus warm-up; keeps each step O(1)
PASS_BELOW = 0.05  # |p_green - 0.5| under this is a pass, not a call


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
    position = (c - day_low) / (day_high - day_low) if day_high > day_low else 0.5
    move = _bps(c, closes[-2])
    expansion = span / (sum(ranges) / len(ranges)) if sum(ranges) else 1.0
    last_colour = color(last)
    return [
        (c - o) / span if span else 0.0,
        (h - max(o, c)) / span if span else 0.0,
        (min(o, c) - low) / span if span else 0.0,
        move, _bps(c, closes[-4]), _bps(c, closes[-7]),
        float(streak), _rsi(closes),
        _bps(_ema(closes, 9), _ema(closes, 21)),
        position,
        _bps(c, float(today[0].open)),
        ((clock.hour - 9) * 60 + clock.minute - 15) / 375,
        expansion,
        _bps(float(vix_now.close), float(vix_before.close)),
        float(vix_now.close),
        0.0 if last_colour is None else 1.0 - 2 * last_colour,
        abs(position - 0.5), move * expansion, abs(move),
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
    history: list[list[Any]] = field(default_factory=list)  # [start ISO, p_green, actual]
    calls: int = 0
    calls_right: int = 0
    squares: list[float] = field(default_factory=lambda: [1e-8] * len(FEATURES))
    slots: dict[str, list[float]] = field(default_factory=dict)  # "HH:MM" -> [greens, seen]
    rate: float = 0.05
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
        for i, (w, v) in enumerate(zip(self.weights, z, strict=True)):
            step = error * v - self.l2 * w
            self.squares[i] += step * step  # AdaGrad: features that keep moving get smaller steps
            self.weights[i] = w + self.rate * step / math.sqrt(self.squares[i])
        self.bias += self.rate * 0.1 * error

    def slot_rate(self, slot: str) -> float:
        greens, seen = self.slots.get(slot, [1.0, 2.0])
        return greens / seen - 0.5

    def score(self, p: float, actual: int, last_color: int | None) -> None:
        guess = int(p >= 0.5)
        self.played += 1
        self.right += guess == actual
        if abs(p - 0.5) >= PASS_BELOW:
            self.calls += 1
            self.calls_right += guess == actual
        self.repeat_right += last_color == actual
        self.recent = [*self.recent, int(guess == actual)][-_RECENT:]

    def summary(self) -> str:
        if not self.played:
            return "No candles scored yet."
        pct = 100 * self.right / self.played
        recent = 100 * sum(self.recent) / len(self.recent)
        repeat = 100 * self.repeat_right / self.played
        called = 100 * self.calls_right / self.calls if self.calls else 0.0
        return (f"{self.played} candles: {pct:.1f}% right overall, {recent:.1f}% over the last "
                f"{len(self.recent)}; {called:.1f}% on {self.calls} confident calls; "
                f"'repeat last colour' baseline {repeat:.1f}%")

    def top_weights(self, n: int = 5) -> list[tuple[str, float]]:
        return sorted(zip(FEATURES, self.weights, strict=True), key=lambda kv: -abs(kv[1]))[:n]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.__dict__, indent=1))

    @classmethod
    def load(cls, path: Path) -> "Learner":
        if not path.exists():
            return cls()
        saved = json.loads(path.read_text())
        if len(saved.get("weights", [])) != len(FEATURES):
            return cls()  # older model with other features: start over rather than misalign
        return cls(**saved)


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
        x = features(bars[max(0, i + 1 - _WINDOW):i + 1], vix) if same_day else None
        if x is None:
            continue
        slot = (bars[i].timestamp + timedelta(minutes=5)).astimezone(IST).strftime("%H:%M")
        x.append(learner.slot_rate(slot))
        z = learner._scale(x, update=nxt is not None)
        p = learner.probability(z)
        actual = None if nxt is None else color(nxt)
        turns.append(Turn(nxt.timestamp if nxt else None, p, actual))
        if nxt is None:
            break
        if actual is not None:
            learner.score(p, actual, color(bars[i]))
            learner.history = [*learner.history, [nxt.timestamp.isoformat(), p, actual]][-_KEEP:]
            learner.learn(z, actual, p)
            seen = learner.slots.setdefault(slot, [1.0, 2.0])
            seen[0], seen[1] = seen[0] + actual, seen[1] + 1
        learner.learned_until = nxt.timestamp.isoformat()
    return turns
