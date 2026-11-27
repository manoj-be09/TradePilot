"""L-Earning Candle Arena: a static game page that polls live JSON the game scripts write.

The page (learning_candle_arena.html) holds no data itself. Each game pass writes a small JSON
file next to it; the page fetches those every few seconds and animates what changed.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from packages.learning.learning_candle import IST, PASS_BELOW, Learner
from packages.market_data.contracts import HistoricalBar

ARENA = Path(__file__).with_name("learning_candle_arena.html")
_LABELS = {
    "body": "Last candle body", "upper_wick": "Upper wick", "lower_wick": "Lower wick",
    "ret_1": "Move, 1 candle", "ret_3": "Move, 3 candles", "ret_6": "Move, 6 candles",
    "streak": "Colour streak", "rsi_14": "RSI 14", "ema_9_21_gap": "EMA 9 vs 21",
    "day_position": "Place in day range", "from_day_open": "Move from day open",
    "time_of_day": "Time of day", "range_expansion": "Range expansion",
    "vix_change": "VIX change", "vix_level": "VIX level", "last_colour": "Opposite of last colour",
    "day_extreme": "Near day high or low", "move_x_range": "Move x range expansion",
    "move_size": "Size of last move", "slot_green_rate": "Green rate at this time",
}


def call_name(p: float | None, sure: float = PASS_BELOW, up: str = "green",
              down: str = "red") -> str | None:
    if p is None:
        return None
    return "pass" if abs(p - 0.5) < sure else up if p >= 0.5 else down


def live_state(learner: Learner, next_p: float | None, next_at: datetime | None,
               bars: list[HistoricalBar], updated: datetime) -> dict[str, Any]:
    """What the arena shows for the colour game. No next call when there is no candle to call."""
    has_next = next_p is not None and next_at is not None
    return {
        "updated": updated.astimezone(IST).isoformat(),
        "next": {"at": next_at.astimezone(IST).isoformat() if next_at else None,
                 "p": next_p, "call": call_name(next_p)} if has_next else None,
        "score": {"played": learner.played, "right": learner.right, "calls": learner.calls,
                  "calls_right": learner.calls_right, "repeat_right": learner.repeat_right,
                  "recent": sum(learner.recent), "recent_n": len(learner.recent)},
        "history": learner.history[-60:],
        "weights": [[_LABELS[name], round(w, 3)] for name, w in learner.top_weights(6)],
        "bars": [[bar.timestamp.astimezone(IST).isoformat(), float(bar.open), float(bar.high),
                  float(bar.low), float(bar.close)] for bar in bars[-36:]],
    }


def calls_state(learner: Learner, next_p: float | None, next_at: datetime | None,
                updated: datetime, sure: float) -> dict[str, Any]:
    """Compact state for the Big or Small side game (p is the chance of a big candle)."""
    return {
        "updated": updated.astimezone(IST).isoformat(),
        "next": {"at": next_at.astimezone(IST).isoformat() if next_at else None, "p": next_p,
                 "call": call_name(next_p, sure, "big", "small")} if next_p is not None else None,
        "calls": learner.calls, "calls_right": learner.calls_right, "played": learner.played,
        "history": [[t, p, a] for t, p, a in learner.history[-24:]],
    }
