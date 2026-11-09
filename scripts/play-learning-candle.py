"""L-Earning Candle for NIFTY 5-minute candles: guess green/red before each candle, learn after.

Usage:
  PYTHONPATH=. python scripts/play-learning-candle.py --days 90   # learn from history
  PYTHONPATH=. python scripts/play-learning-candle.py --live      # play during market hours
  PYTHONPATH=. python scripts/play-learning-candle.py --reset ... # forget everything
  PYTHONPATH=. python scripts/play-learning-candle.py --after 2   # late-call game (see below)
  PYTHONPATH=. python scripts/play-learning-candle.py --size      # Big or Small (see below)

--after N plays a different game: each candle is called N minutes after it opens, from the move
so far, passing when that move is under --min-move bps. It watches part of the candle, so it is
not a forecast; it keeps no state. All three games feed the arena page, index.html.

--size plays Big or Small: before each candle, guess whether its high-low range beats the usual
range for that time of day, calling only when 75/25 sure. It learns like the colour game (state
in data/learning_candle/size_state.json) and says nothing about direction.

Read-only Upstox candle GETs. What it learned is kept in data/learning_candle/state.json, so
each run continues from the last one. Simulated research only: not a signal or investment advice.
"""

import argparse
import asyncio
import json
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

from packages.learning.learning_candle import (
    IST,
    PASS_BELOW,
    SIZE_FEATURES,
    SIZE_SURE,
    Learner,
    Turn,
    late_calls,
    play,
    size_calls,
)
from packages.learning.learning_candle_page import ARENA, calls_state, live_state
from packages.learning.learning_candle_ticker import Ticker, bucket, tick_state
from packages.market_data.contracts import HistoricalBar, MarketDataProviderError
from packages.market_data.provider import get_market_data_provider
from packages.market_data.upstox import UpstoxMarketDataProvider
from packages.recorder.universe import INDIA_VIX_KEY, NIFTY_INDEX_KEY, nifty_futures

STATE = Path("data/learning_candle/state.json")
PAGE = STATE.with_name("index.html")
SIZE_STATE = STATE.with_name("size_state.json")


def publish(name: str, state: dict[str, object]) -> None:
    """Write one live JSON file for the arena page; replace so it never reads half a file."""
    PAGE.parent.mkdir(parents=True, exist_ok=True)
    if not PAGE.exists() or PAGE.read_bytes() != ARENA.read_bytes():
        shutil.copyfile(ARENA, PAGE)
    temp = PAGE.with_name(name + ".tmp")
    temp.write_text(json.dumps(state), encoding="utf-8")
    try:
        temp.replace(PAGE.with_name(name))
    except PermissionError:
        pass  # the page server is reading it this instant (Windows); the next write catches up


def market_open(now: datetime) -> bool:
    return now.weekday() < 5 and (9, 15) <= (now.hour, now.minute) < (15, 30)


async def seed(provider: UpstoxMarketDataProvider, ticker: Ticker, now: datetime,
               fut_key: str | None, cumulative: float | None) -> None:
    """Start the forming candle from 1-minute candles, so joining mid-candle keeps its real open."""
    start = bucket(now)
    try:
        minutes = [b for b in await provider.get_intraday(NIFTY_INDEX_KEY, "1m")
                   if bucket(b.timestamp) == start]
        fut_minutes = ([b for b in await provider.get_intraday(fut_key, "1m")
                        if bucket(b.timestamp) == start] if fut_key else [])
    except MarketDataProviderError:
        return  # no seed: the candle shows as partial
    if minutes and minutes[0].timestamp == start:
        volume = sum(float(b.volume) for b in fut_minutes) if fut_minutes else None
        ticker.seed(start, float(minutes[0].open), float(max(b.high for b in minutes)),
                    float(min(b.low for b in minutes)), volume, cumulative)


async def tick_loop(provider: UpstoxMarketDataProvider) -> None:
    """One read-only quote a second for NIFTY, VIX and near-month futures -> tick.json."""
    try:
        futures = nifty_futures(await provider.search_instruments(
            "NIFTY FUT", exchange="NSE", segment="FO"), date.today())
    except MarketDataProviderError:
        futures = []
    fut_key = futures[0].instrument_key if futures else None
    symbol = futures[0].trading_symbol if futures else None
    keys = [NIFTY_INDEX_KEY, INDIA_VIX_KEY, *([fut_key] if fut_key else [])]
    ticker, seeded = Ticker(), False
    while market_open(now := datetime.now(IST)):
        try:
            items = await provider.get_quote_items(keys)
        except MarketDataProviderError:
            items = {}  # this second shows nothing new rather than an old price
        nifty, fut = items.get(NIFTY_INDEX_KEY), items.get(fut_key) if fut_key else None
        price = nifty.get("last_price") if nifty else None
        cumulative = fut.get("volume") if fut else None
        cumulative = float(cumulative) if isinstance(cumulative, (int, float)) else None
        if isinstance(price, (int, float)) and price > 0:
            if not seeded:
                await seed(provider, ticker, now, fut_key, cumulative)
                seeded = True
            ticker.update(now, float(price), cumulative)
        publish("tick.json", tick_state(ticker, nifty, items.get(INDIA_VIX_KEY), fut, now,
                                        symbol))
        await asyncio.sleep(max(0.0, 1 - (datetime.now(IST) - now).total_seconds()))
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
        publish("late.json", {
            "updated": datetime.now(IST).isoformat(), "after": args.after,
            "current": ({"at": last.timestamp.isoformat(), "call": "pass" if last.p_green == 0.5
                         else "green" if last.p_green else "red"}
                        if last is not None and last.actual is None else None),
            "calls": len(calls), "right": right, "candles": len(done)})
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


async def wait_for_next_candle() -> bool:
    """Sleep until just after the next 5-minute candle closes; False once the market is shut."""
    now = datetime.now(IST)
    wake = now.replace(second=0, microsecond=0) + timedelta(minutes=5 - now.minute % 5)
    if not (9 <= wake.hour < 15 or (wake.hour == 15 and wake.minute <= 30)):
        print("Market closed; stopping.")
        return False
    await asyncio.sleep((wake - now).total_seconds() + 5)  # let the candle settle at Upstox
    return True


async def size_game(provider: UpstoxMarketDataProvider, args: argparse.Namespace) -> None:
    n = len(SIZE_FEATURES)
    learner = Learner.fresh(n) if args.reset else Learner.load(SIZE_STATE, n)
    while True:
        days = max(args.days, 35)  # the usual range for a time of day needs 20 earlier sessions
        nifty = await history(provider, NIFTY_INDEX_KEY, days)
        vix = await history(provider, INDIA_VIX_KEY, days)
        turns = size_calls(learner, nifty, vix)
        learner.save(SIZE_STATE)
        pending = turns[-1] if turns and turns[-1].timestamp is None else None
        publish("size.json", calls_state(learner, pending.p_green if pending else None,
                                         nifty[-1].timestamp + FIVE if pending else None,
                                         datetime.now(IST), SIZE_SURE))
        for turn in turns[-(1 if args.live and learner.played else args.show):]:
            guess = ("PASS " if abs(turn.p_green - 0.5) < SIZE_SURE
                     else "BIG  " if turn.p_green >= 0.5 else "SMALL")
            if turn.timestamp is None:
                print(f"  next candle    : {guess} (p_big {turn.p_green:.2f})")
                continue
            got = {1: "BIG  ", 0: "SMALL", None: "?    "}[turn.actual]
            mark = "" if guess == "PASS " or turn.actual is None else (
                "right" if (turn.p_green >= 0.5) == turn.actual else "WRONG")
            print(f"  {turn.timestamp.astimezone(IST):%d %b %H:%M}  : {guess} "
                  f"({turn.p_green:.2f}) got {got} {mark}")
        if learner.calls:
            print(f"Big or Small, {learner.played} candles: "
                  f"{100 * learner.calls_right / learner.calls:.1f}% right on {learner.calls} sure "
                  f"calls ({100 * learner.calls / learner.played:.0f}% of candles); "
                  f"{100 * learner.right / learner.played:.1f}% on every candle; 'same size as "
                  f"the last candle' {100 * learner.repeat_right / learner.played:.1f}%. "
                  "Simulated; says nothing about direction.")
        if not args.live or not await wait_for_next_candle():
            return


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
    if args.size:
        return await size_game(provider, args)
    if args.live:
        await asyncio.gather(colour_game(provider, args), tick_loop(provider))
    else:
        await colour_game(provider, args)


async def colour_game(provider: UpstoxMarketDataProvider, args: argparse.Namespace) -> None:
    learner = Learner() if args.reset else Learner.load(STATE)
    while True:
        nifty = await history(provider, NIFTY_INDEX_KEY, args.days)
        vix = await history(provider, INDIA_VIX_KEY, args.days)
        turns = play(learner, nifty, vix)
        learner.save(STATE)
        pending = turns[-1] if turns and turns[-1].timestamp is None else None
        publish("live.json", live_state(learner, pending.p_green if pending else None,
                                        nifty[-1].timestamp + FIVE if pending else None,
                                        nifty, datetime.now(IST)))
        report(learner, turns, 1 if args.live and learner.played else args.show)
        if not args.live:
            return
        args.days = 5  # live passes only need today's candles plus warm-up history
        if not await wait_for_next_candle():
            return


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=60, help="history to learn from first")
    parser.add_argument("--live", action="store_true", help="keep playing each 5 minutes")
    parser.add_argument("--reset", action="store_true", help="start from an untrained learner")
    parser.add_argument("--show", type=int, default=10, help="recent turns to print")
    parser.add_argument("--after", type=int, choices=(1, 2, 3, 4),
                        help="late-call game: call each candle this many minutes in")
    parser.add_argument("--size", action="store_true",
                        help="Big or Small: guess each candle's size instead of its colour")
    parser.add_argument("--min-move", type=float, default=2.0,
                        help="late-call game: pass when the move so far is under this (bps)")
    asyncio.run(main(parser.parse_args()))
