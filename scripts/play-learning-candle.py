"""L-Earning Candle for NIFTY 5-minute candles: guess green/red before each candle, learn after.

Usage:
  PYTHONPATH=. python scripts/play-learning-candle.py --days 90   # learn from history
  PYTHONPATH=. python scripts/play-learning-candle.py --live      # play during market hours
  PYTHONPATH=. python scripts/play-learning-candle.py --reset ... # forget everything

Read-only Upstox candle GETs. What it learned is kept in data/learning_candle/state.json, so
each run continues from the last one. Simulated research only: not a signal or investment advice.
"""

import argparse
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from packages.learning.learning_candle import IST, Learner, Turn, play
from packages.market_data.contracts import HistoricalBar
from packages.market_data.provider import get_market_data_provider
from packages.market_data.upstox import UpstoxMarketDataProvider
from packages.recorder.universe import INDIA_VIX_KEY, NIFTY_INDEX_KEY

STATE = Path("data/learning_candle/state.json")
FIVE = timedelta(minutes=5)


async def history(provider: UpstoxMarketDataProvider, key: str, days: int) -> list[HistoricalBar]:
    end, bars = datetime.now(IST), {}
    while days > 0:  # Upstox serves minute candles about a month per request
        chunk = min(days, 28)
        for bar in await provider.get_historical(key, end - timedelta(days=chunk), end, "5m"):
            bars[bar.timestamp] = bar
        end, days = end - timedelta(days=chunk + 1), days - chunk - 1
        await asyncio.sleep(0.5)
    for bar in await provider.get_intraday(key, "5m"):
        bars[bar.timestamp] = bar
    now = datetime.now(IST)
    return [bar for _, bar in sorted(bars.items()) if bar.timestamp + FIVE <= now]  # completed only


def report(learner: Learner, turns: list[Turn], show: int) -> None:
    for turn in turns[-show:]:
        guess = "GREEN" if turn.p_green >= 0.5 else "RED  "
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
    learner = Learner() if args.reset else Learner.load(STATE)
    while True:
        nifty = await history(provider, NIFTY_INDEX_KEY, args.days)
        vix = await history(provider, INDIA_VIX_KEY, args.days)
        turns = play(learner, nifty, vix)
        learner.save(STATE)
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
    asyncio.run(main(parser.parse_args()))
