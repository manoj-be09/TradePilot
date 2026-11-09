from datetime import datetime, timedelta
from decimal import Decimal

from packages.learning.learning_candle import (
    IST,
    SIZE_FEATURES,
    Learner,
    late_calls,
    play,
    size_calls,
)
from packages.market_data.contracts import HistoricalBar


def _bars(colours: list[int], days: int = 1) -> list[HistoricalBar]:
    bars, price = [], Decimal(24000)
    for day in range(days):
        start = datetime(2026, 11, 2 + day, 9, 15, tzinfo=IST)
        for i, green in enumerate(colours):
            close = price + (Decimal(5) if green else Decimal(-5))
            bars.append(HistoricalBar(timestamp=start + timedelta(minutes=5 * i), open=price,
                                      high=max(price, close) + 2, low=min(price, close) - 2,
                                      close=close, volume=0))
            price = close
    return bars


def _vix(bars: list[HistoricalBar]) -> list[HistoricalBar]:
    return [bar.model_copy(update={"open": Decimal(14), "high": Decimal(14), "low": Decimal(14),
                                   "close": Decimal(14)}) for bar in bars]


def test_learns_an_alternating_pattern_and_beats_its_start() -> None:
    bars = _bars([i % 2 for i in range(75)], days=8)  # alternating, 09:15-15:25
    learner = Learner()
    turns = play(learner, bars, _vix(bars))
    assert learner.played > 400
    assert sum(learner.recent) / len(learner.recent) > 0.9  # pattern learned
    assert turns[-1].actual is not None  # session ended at 15:25: no guess for tomorrow
    early = play(Learner(), bars[:-10], _vix(bars))
    assert early[-1].actual is None and early[-1].timestamp is None  # guess for the next candle


def test_each_candle_is_learned_once_and_missing_vix_skips() -> None:
    bars = _bars([i % 2 for i in range(75)], days=2)
    learner = Learner()
    play(learner, bars, _vix(bars))
    played = learner.played
    play(learner, bars, _vix(bars))
    assert learner.played == played  # replay of the same window does not count twice
    fresh = Learner()
    play(fresh, bars, [])
    assert fresh.played == 0  # no VIX, no guess: fail closed rather than invent a feature


def test_page_shows_the_next_call_or_waits() -> None:
    from packages.learning.learning_candle_page import render

    bars = _bars([i % 2 for i in range(75)], days=3)
    learner = Learner()
    play(learner, bars, _vix(bars))
    now = datetime(2026, 11, 5, 10, 0, tzinfo=IST)
    page = render(learner, 0.62, now, now)
    assert "Candle starting 10:00" in page and ">Green<" in page and "simulated" in page.lower()
    assert page.count('class="tile') == 60  # last 60 scored candles
    assert "Waiting" in render(learner, None, None, now)


def test_unsure_calls_are_passes_and_scored_apart() -> None:
    from packages.learning.learning_candle_page import render

    learner = Learner()
    learner.score(0.52, 1, 0)  # leaning green but unsure: counts on every candle, not as a call
    learner.score(0.70, 0, 0)
    assert (learner.played, learner.right, learner.calls, learner.calls_right) == (2, 1, 1, 0)
    now = datetime(2026, 11, 5, 10, 0, tzinfo=IST)
    assert ">Pass<" in render(learner, 0.52, now, now)


def test_late_call_follows_the_move_so_far_and_passes_on_tiny_moves() -> None:

    start = datetime(2026, 11, 5, 10, 0, tzinfo=IST)

    def minute(i: int, o: str, c: str) -> HistoricalBar:
        return HistoricalBar(timestamp=start + timedelta(minutes=i), open=Decimal(o),
                             high=max(Decimal(o), Decimal(c)), low=min(Decimal(o), Decimal(c)),
                             close=Decimal(c), volume=0)

    up = [minute(0, "24000", "24004"), minute(1, "24004", "24010"), minute(2, "24010", "23990"),
          minute(3, "23990", "23995"), minute(4, "23995", "23998")]  # +4 bps at minute 2, ends red
    flat = [minute(5, "24000", "24001"), minute(6, "24001", "24002")]  # under 2 bps, in progress
    turns = late_calls(up + flat, after=2)
    assert [(t.p_green, t.actual) for t in turns] == [(1.0, 0), (0.5, None)]
    assert late_calls(up[1:], after=2) == []  # first minute missing: no call


def _clustered(days: int) -> list[HistoricalBar]:
    """Flat price, but the range runs in blocks: 15 wide candles, then 15 narrow ones."""
    bars, price = [], Decimal(24000)
    for day in range(days):
        start = datetime(2026, 9, 1, 9, 15, tzinfo=IST) + timedelta(days=day)
        for i in range(75):
            wick = Decimal(20) if (i // 15 + day) % 2 else Decimal(3)
            bars.append(HistoricalBar(timestamp=start + timedelta(minutes=5 * i), open=price,
                                      high=price + wick, low=price - wick,
                                      close=price + 1, volume=0))
    return bars


def test_big_or_small_learns_clustered_ranges_once_and_skips_missing_vix() -> None:
    bars = _clustered(30)
    learner = Learner.fresh(len(SIZE_FEATURES))
    turns = size_calls(learner, bars, _vix(bars))
    assert learner.played and turns[0].timestamp.date() > bars[19 * 75].timestamp.date()
    assert learner.calls > 100 and learner.calls_right / learner.calls > 0.8
    played = learner.played
    size_calls(learner, bars, _vix(bars))
    assert learner.played == played  # replaying the same candles learns nothing twice
    assert size_calls(Learner.fresh(len(SIZE_FEATURES)), bars, []) == []  # no VIX: no call
    pending = size_calls(Learner.fresh(len(SIZE_FEATURES)), bars[:-10], _vix(bars))
    assert pending[-1].timestamp is None and pending[-1].actual is None
