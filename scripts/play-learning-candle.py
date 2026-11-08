"""L-Earning Candle for NIFTY 5-minute candles: guess green/red before each candle, learn after.

Usage:
  PYTHONPATH=. python scripts/play-learning-candle.py --days 90   # learn from history
  PYTHONPATH=. python scripts/play-learning-candle.py --live      # play during market hours
  PYTHONPATH=. python scripts/play-learning-candle.py --reset ... # forget everything
  PYTHONPATH=. python scripts/play-learning-candle.py --after 2   # late-call game (see below)

--after N plays a different game: each candle is called N minutes after it opens, from the move
so far, passing when that move is under --min-move bps. It watches part of the candle, so it is
not a forecast; it keeps no state and writes no page.

Read-only Upstox candle GETs. What it learned is kept in data/learning_candle/state.json, so
each run continues from the last one. Simulated research only: not a signal or investment advice.
"""

import argparse
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from packages.learning.learning_candle import IST, PASS_BELOW, Learner, Turn, late_calls, play
from packages.learning.learning_candle_page import render
from packages.market_data.contracts import HistoricalBar
from packages.market_data.provider import get_market_data_provider
from packages.market_data.upstox import UpstoxMarketDataProvider
from packages.recorder.universe import INDIA_VIX_KEY, NIFTY_INDEX_KEY

STATE = Path("data/learning_candle/state.json")
PAGE = STATE.with_name("index.html")
FIVE = timedelta(minutes=5)


async def history(provider: UpstoxMarketDataProvider, key: str, days: int,
                  interval: str = "5m") -> list[HistoricalBar]:
    end, bars = datetime.now(IST), {}
    while days > 0:  # Upstox serves minute candles about a month per request
        chunk = min(days, 28)
        for bar in await provider.get_historical(key, end - timedelta(days=chunk), end, interval):
            bars[bar.timestamp] = bar
        end, days = end - timedelta(days=chunk + 1), days - chunk - 1
        await asyncio.sleep(0.5)
    for bar in await provider.get_intraday(key, interval):
        bars[bar.timestamp] = bar
    now, step = datetime.now(IST), FIVE if interval == "5m" else timedelta(minutes=1)
    return [bar for _, bar in sorted(bars.items()) if bar.timestamp + step <= now]  # completed only


async def late_game(provider: UpstoxMarketDataProvider, args: argparse.Namespace) -> None:
    while True:
        minutes = await history(provider, NIFTY_INDEX_KEY, args.days, "1m")
        turns = late_calls(minutes, args.after, args.min_move)
        done = [t for t in turns if t.actual is not None]
        calls = [t for t in done if t.p_green != 0.5]
        right = sum((t.p_green == 1.0) == t.actual for t in calls)
        last = turns[-1] if turns else None
        if last is not None and last.actual is None:
            call = "PASS" if last.p_green == 0.5 else "GREEN" if last.p_green else "RED"
            print(f"  {last.timestamp:%d %b %H:%M} candle, called at minute {args.after}: {call}")
        if calls:
            print(f"Late call after {args.after} min: {100 * right / len(calls):.1f}% right on "
                  f"{len(calls)} calls ({100 * len(calls) / len(done):.0f}% of {len(done)} "
                  "candles). Simulated; it watches part of each candle, so not a forecast.")
        if not args.live:
            return
        now = datetime.now(IST)
        start = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)
        wake = start + timedelta(minutes=args.after if now < start + timedelta(
            minutes=args.after) else 5 + args.after)
        if not (9 <= wake.hour < 15 or (wake.hour == 15 and wake.minute < 30)):
            print("Market closed; stopping.")
            return
        args.days = 1
        await asyncio.sleep((wake - now).total_seconds() + 5)


def report(learner: Learner, turns: list[Turn], show: int) -> None:
    for turn in turns[-show:]:
        guess = ("PASS " if abs(turn.p_green - 0.5) < PASS_BELOW
                 else "GREEN" if turn.p_green >= 0.5 else "RED  ")
        if turn.timestamp is None:
            print(f"  next candle    : guess {guess} (p_green {turn.p_green:.2f})")
        else:
            got = {1: "GREEN", 0: "RED  ", None: "DOJI "}[turn.actual]
            mark = "" if turn.actual is None else ("right" if (turn.p_green >= 0.5) == turn.actual
                                                   else "WRONG")
            when = turn.timestamp.astimezone(IST).strftime("%d %b %H:%M")
            print(f"  {when}  : guess {guess} ({turn.p_green:.2f}) got {got} {mark}")
    print(learner.summary())
    weights = ", ".join(f"{name} {w:+.3f}" for name, w in learner.top_weights())
    print("Strongest learned weights:", weights)


async def main(args: argparse.Namespace) -> None:
    provider = get_market_data_provider()
    if not isinstance(provider, UpstoxMarketDataProvider):
        raise SystemExit("Configured Upstox provider required")
    if args.after:
        return await late_game(provider, args)
    learner = Learner() if args.reset else Learner.load(STATE)
    while True:
        nifty = await history(provider, NIFTY_INDEX_KEY, args.days)
        vix = await history(provider, INDIA_VIX_KEY, args.days)
        turns = play(learner, nifty, vix)
        learner.save(STATE)
        pending = turns[-1] if turns and turns[-1].timestamp is None else None
        PAGE.write_text(render(learner, pending.p_green if pending else None,
                               nifty[-1].timestamp + FIVE if nifty else None,
                               datetime.now(IST)), encoding="utf-8")
        report(learner, turns, 1 if args.live and learner.played else args.show)
        if not args.live:
            return
        now = datetime.now(IST)
        wake = now.replace(second=0, microsecond=0) + timedelta(minutes=5 - now.minute % 5)
        if not (9 <= wake.hour < 15 or (wake.hour == 15 and wake.minute <= 30)):
            print("Market closed; stopping.")
            return
        args.days = 5  # live passes only need today's candles plus warm-up history
        await asyncio.sleep((wake - now).total_seconds() + 5)  # let the candle settle at Upstox


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=60, help="history to learn from first")
    parser.add_argument("--live", action="store_true", help="keep playing each 5 minutes")
    parser.add_argument("--reset", action="store_true", help="start from an untrained learner")
    parser.add_argument("--show", type=int, default=10, help="recent turns to print")
    parser.add_argument("--after", type=int, choices=(1, 2, 3, 4),
                        help="late-call game: call each candle this many minutes in")
    parser.add_argument("--min-move", type=float, default=2.0,
                        help="late-call game: pass when the move so far is under this (bps)")
    asyncio.run(main(parser.parse_args()))
