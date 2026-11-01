"""The one frozen test of the one-night condor on NSE daily files. See ALGORITHM_RESEARCH.md.

Results are simulated and private: this writes to `work/`, which git ignores. Run with `python -I`
because it reads downloaded files.
"""

import argparse
import json
import statistics
import sys
from collections.abc import Callable, Sequence
from datetime import date
from math import sqrt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.backtesting.daily_condor import (  # noqa: E402
    DailyMarket,
    DailyQuote,
    DailyReading,
    DailyTrade,
    IndexDay,
    daily_readings,
    run_night_condor,
    top_share_by_trailing_rank,
)
from packages.history.nse_daily import file_name, nifty_options, read_member  # noqa: E402

FIRST_TRADE = date(2019, 2, 11)
READING_CUT = 1.20
SHORT_DELTA = 0.15
OTHER_DELTA = 0.20
TRAILING_SHARE = 0.14
_EXPIRIES_KEPT = 2
_SPECIAL_SESSION_SHARE = 0.15
_VOLUME_SESSIONS = 22
_LARGEST_WORST = 20000.0
_LARGEST_DRAWDOWN = 60000.0
_MINIMUM_TRADES = 15
_PASS_T = 2.45
_WINDOWS = (
    ("2019", date(2019, 1, 1), date(2019, 12, 31)),
    ("2020", date(2020, 1, 1), date(2020, 12, 31)),
    ("2021", date(2021, 1, 1), date(2021, 12, 31)),
    ("2022", date(2022, 1, 1), date(2022, 12, 31)),
    ("2023", date(2023, 1, 1), date(2023, 12, 31)),
    ("2024 to Sep", date(2024, 1, 1), date(2024, 9, 30)),
    ("Crash, 20 Feb to 30 Apr 2020", date(2020, 2, 20), date(2020, 4, 30)),
)


def load_market(directory: Path, index_path: Path, first: date, last: date,
                ) -> tuple[DailyMarket, list[date], list[date]]:
    """Sessions with a file and an index row, dropping thin special ones by contract count alone."""
    raw_index = json.loads(index_path.read_text(encoding="utf-8"))
    chains: dict[date, dict[date, dict[tuple[int, str], DailyQuote]]] = {}
    sessions: list[date] = []
    missing: list[date] = []
    special: list[date] = []
    counts: list[int] = []
    for key in sorted(raw_index):
        day = date.fromisoformat(key)
        if not first <= day <= last:
            continue
        path = directory / file_name(day)
        if not path.exists():
            missing.append(day)
            continue
        rows = list(nifty_options(read_member(path.read_bytes())))
        contracts = sum(row.contracts for row in rows)
        if (len(counts) >= _VOLUME_SESSIONS and contracts
                < _SPECIAL_SESSION_SHARE * statistics.median(counts[-_VOLUME_SESSIONS:])):
            special.append(day)
            continue
        counts.append(contracts)
        kept = sorted({row.expiry for row in rows if row.expiry >= day})[:_EXPIRIES_KEPT]
        session: dict[date, dict[tuple[int, str], DailyQuote]] = {expiry: {} for expiry in kept}
        for row in rows:
            if row.expiry in session:
                session[row.expiry][(row.strike, row.kind)] = DailyQuote(row.open, row.close)
        if session:
            chains[day] = session
            sessions.append(day)
    index = {day: IndexDay(*raw_index[day.isoformat()]) for day in sessions}
    return DailyMarket(tuple(sessions), chains, index), missing, special


def half_spread_from(path: Path) -> Callable[[float], float]:
    saved = json.loads(path.read_text(encoding="utf-8"))
    bands = [(float(band["lower"]), float(band["median_pct"]))
             for band in saved["config"]["spread_model"]["bands"]]

    def half_spread(price: float) -> float:
        for lower, pct in reversed(bands):
            if price >= lower:
                return pct
        return bands[0][1]
    return half_spread


def summary(trades: Sequence[DailyTrade]) -> dict[str, float | int | bool]:
    values = [trade.net for trade in sorted(trades, key=lambda trade: trade.session)]
    if not values:
        return {"n": 0, "passes": False}
    mean = statistics.fmean(values)
    deviation = statistics.stdev(values) if len(values) > 1 else 0.0
    t_value = mean / (deviation / sqrt(len(values))) if deviation else 0.0
    peak = total = drawdown = 0.0
    for value in values:
        total += value
        peak = max(peak, total)
        drawdown = max(drawdown, peak - total)
    worst = min(values)
    wins = sum(value > 0 for value in values)
    return {"n": len(values), "avg": round(mean), "win": round(wins / len(values), 3),
            "t": round(t_value, 2), "worst": round(worst), "drawdown": round(drawdown),
            "total": round(total),
            "passes": bool(mean > 0 and len(values) >= _MINIMUM_TRADES and t_value >= _PASS_T
                           and worst >= -_LARGEST_WORST and drawdown <= _LARGEST_DRAWDOWN)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True,
                        help="Daily NIFTY open, high, low, close")
    parser.add_argument("--spread-model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit-only", action="store_true", help="Sessions only, no results")
    parser.add_argument("--first", type=date.fromisoformat, default=date(2019, 1, 1))
    parser.add_argument("--last", type=date.fromisoformat, default=date(2024, 9, 30))
    arguments = parser.parse_args()
    market, missing, special = load_market(arguments.directory, arguments.index, arguments.first,
                                           arguments.last)
    readings = daily_readings(market)
    usable = [item for item in readings
              if item.session >= FIRST_TRADE and not item.before_break]
    print(f"Sessions {len(market.sessions)}, no file {len(missing)}, thin special {len(special)}, "
          f"readings usable {len(usable)}")
    print("No file:", [str(day) for day in missing])
    print("Thin special:", [str(day) for day in special])
    if arguments.audit_only:
        return 0
    half = half_spread_from(arguments.spread_model)
    doubled = lambda price: 2 * half(price)  # noqa: E731
    chosen = {item.session for item in usable if item.reading >= READING_CUT}
    every = {item.session for item in usable}
    trailing = top_share_by_trailing_rank(_with_warmup(readings), TRAILING_SHARE) & every
    trades, skipped = run_night_condor(market, chosen, SHORT_DELTA, half)
    all_trades, all_skipped = run_night_condor(market, every, SHORT_DELTA, half)
    rows = {
        "primary": summary(trades),
        "double spreads": summary(run_night_condor(market, chosen, SHORT_DELTA, doubled)[0]),
        "20-delta": summary(run_night_condor(market, chosen, OTHER_DELTA, half)[0]),
        "fixed 300-point wings, 65 units": summary(
            run_night_condor(market, chosen, SHORT_DELTA, half, None)[0]),
        "trailing top 14%": summary(run_night_condor(market, trailing, SHORT_DELTA, half)[0]),
        "every day": summary(all_trades),
    }
    for label, value in rows.items():
        print(f"{label:34} {value}")
    print(f"Chosen {len(chosen)}, could not price {len(skipped)}; every day could not price "
          f"{len(all_skipped)}")
    periods = {}
    for label, start, stop in _WINDOWS:
        periods[label] = {
            "primary": summary([t for t in trades if start <= t.session <= stop]),
            "every day": summary([t for t in all_trades if start <= t.session <= stop]),
        }
        print(f"{label:30} primary {periods[label]['primary']}")
        print(f"{'':30} every day {periods[label]['every day']}")
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps({
        "rows": rows, "periods": periods, "skipped": [str(day) for day in skipped],
        "trades": [{"session": str(t.session), "expiry": str(t.expiry), "exit": str(t.exit_session),
                    "strikes": t.strikes, "units": t.units, "credit": t.credit,
                    "net": round(t.net, 2)}
                   for t in trades],
    }, indent=1), encoding="utf-8")
    return 0


def _with_warmup(readings: Sequence[DailyReading]) -> list[DailyReading]:
    """Ranks use every earlier reading that was not before a break, from the first file on."""
    return [item for item in readings if not item.before_break]


if __name__ == "__main__":
    raise SystemExit(main())
