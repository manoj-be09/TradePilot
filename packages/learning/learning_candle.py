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
from collections import defaultdict, deque
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
SIZE_FEATURES = (
    "last_range", "range_3", "range_12", "vix_level", "time_of_day", "last_body",
    "log_last_range", "log_range_3", "log_range_12",
)
SIZE_SURE = 0.25  # Big or Small calls only when at least 75/25 sure
_SIZE_DAYS = 20  # "usual" range for a time of day: median of the previous 20 sessions


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

    def score(self, p: float, actual: int, last_color: int | None,
              sure: float = PASS_BELOW) -> None:
        guess = int(p >= 0.5)
        self.played += 1
        self.right += guess == actual
        if abs(p - 0.5) >= sure:
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
    def fresh(cls, n: int = len(FEATURES)) -> "Learner":
        return cls(weights=[0.0] * n, mean=[0.0] * n, m2=[0.0] * n, squares=[1e-8] * n)

    @classmethod
    def load(cls, path: Path, n: int = len(FEATURES)) -> "Learner":
        if not path.exists():
            return cls.fresh(n)
        saved = json.loads(path.read_text())
        if len(saved.get("weights", [])) != n:
            return cls.fresh(n)  # older model with other features: start over, don't misalign
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


def late_calls(minutes: list[HistoricalBar], after: int = 2,
               min_move_bps: float = 2.0) -> list[Turn]:
    """The "late call" game: call each 5-minute candle `after` minutes in, not before it opens.

    Calls the direction so far (minute `after` close vs the candle's open) and passes (p 0.5)
    when that move is under `min_move_bps`. Chosen on Nov 2025-Jul 2026 1-minute candles and
    checked on the next four months: after 2 minutes with a 2 bps pass, 80.7% then 84.1% right
    on about half the candles. It watches part of the candle, so it is not a forecast.
    """
    buckets: dict[Any, list[HistoricalBar]] = {}
    for bar in minutes:
        moment = bar.timestamp.astimezone(IST)
        buckets.setdefault(moment.replace(minute=moment.minute - moment.minute % 5), []).append(bar)
    turns = []
    for start, bars in sorted(buckets.items()):
        if len(bars) < after or bars[0].timestamp != start or bars[0].open <= 0:
            continue  # missing minutes: no call rather than a guess
        move = _bps(float(bars[after - 1].close), float(bars[0].open))
        p = 0.5 if abs(move) < min_move_bps else float(move > 0)
        whole = len(bars) == 5
        actual = (None if not whole or bars[-1].close == bars[0].open
                  else int(bars[-1].close > bars[0].open))
        turns.append(Turn(start, p, actual))
    return turns


def size_calls(learner: Learner, bars: list[HistoricalBar],
               vix_bars: list[HistoricalBar]) -> list[Turn]:
    """Big or Small: before each candle, guess whether its high-low range will beat the usual
    range for that time of day (median of the previous 20 sessions). p_green holds p(big).

    Same honest loop as play(): guess, score, learn once per candle. Calls only at 75/25 or
    surer (SIZE_SURE). Checked on 2 years of 5m candles: about 82% right on the quarter of
    candles it calls in held-back months. It says nothing about colour or direction.
    """
    vix = {bar.timestamp: float(bar.close) for bar in vix_bars}
    ranges = [float(bar.high - bar.low) for bar in bars]
    usual: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=_SIZE_DAYS))
    turns: list[Turn] = []
    for i, bar in enumerate(bars):
        nxt = bars[i + 1] if i + 1 < len(bars) else None
        if nxt is None and bar.timestamp.astimezone(IST).time() >= _LAST_CANDLE:
            break  # session over: no next candle to size
        start = nxt.timestamp if nxt else bar.timestamp + timedelta(minutes=5)
        past = usual[start.astimezone(IST).strftime("%H:%M")]
        median = sorted(past)[len(past) // 2] if len(past) == _SIZE_DAYS else 0.0
        actual = None if nxt is None or not median else int(ranges[i + 1] > median)
        x = None
        same_day = start.astimezone(IST).date() == bar.timestamp.astimezone(IST).date()
        if (median > 0 and i >= 11 and same_day and bar.timestamp in vix
                and min(ranges[i - 11:i + 1]) > 0):
            r1, r3, r12 = (sum(ranges[i + 1 - k:i + 1]) / k / median for k in (1, 3, 12))
            clock = bar.timestamp.astimezone(IST)
            x = [r1, r3, r12, vix[bar.timestamp],
                 ((clock.hour - 9) * 60 + clock.minute - 15) / 375,
                 abs(float(bar.close - bar.open)) / median,
                 math.log(r1), math.log(r3), math.log(r12)]
        if nxt is not None:
            past.append(ranges[i + 1])  # after the guess, so the usual range never sees the answer
            if learner.learned_until and nxt.timestamp.isoformat() <= learner.learned_until:
                continue
        if x is None:
            continue
        z = learner._scale(x, update=nxt is not None)
        p = learner.probability(z)
        turns.append(Turn(nxt.timestamp if nxt else None, p, actual))
        if nxt is None:
            break
        if actual is not None:
            learner.score(p, actual, int(ranges[i] > median), sure=SIZE_SURE)
            learner.history = [*learner.history, [nxt.timestamp.isoformat(), p, actual]][-_KEEP:]
            learner.learn(z, actual, p)
        learner.learned_until = nxt.timestamp.isoformat()
    return turns
