"""Options strategies from global research and indexes, tested on NIFTY candles. Research only.

Evidence from other markets does not transfer by itself, so each idea gets its own test. Strikes
chosen by delta use volatility implied from bars closed by the decision. Several rules are naked and
could never be armed in paper trading. Results are simulated history, not a forecast.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from math import log, sqrt
from statistics import NormalDist, mean

from packages.backtesting.candidate_algorithms import CURRENT_LOT
from packages.backtesting.history_backtest import _IST
from packages.backtesting.published_strategies import (
    _BAR,
    _ENTRY_RETRIES,
    _ENTRY_TIME,
    _LATE_ENTRY_TIME,
    _OPENING_BAR,
    _SQUARE_OFF_BAR,
    _WEEKLY_ENTRY_DAYS,
    Book,
    Kind,
    Leg,
    Market,
    PublishedRule,
    Side,
    TradeResult,
    _at,
    _enter_and_run,
    simulate,
    spot_from_closed_bars,
)
from packages.core.charges import order_charges
from packages.options.pricing import _implied_volatility

_NORMAL = NormalDist()
_DELTA_TOLERANCE = 0.03
_LATE_ENTRY_DAYS = 3
_FUTURES_HALF_SPREAD_PCT = 0.01
_ZERO_DAY_EXIT_BAR = time(10, 55)
_MOMENTUM_WEEKS = 4


def option_delta(
    kind: Kind, price: float, spot: float, strike: float, years: float,
) -> float | None:
    implied = _implied_volatility(kind, price, spot, strike, years, 0.0, 0.0)
    if not implied or implied <= 0 or years <= 0:
        return None
    d1 = (log(spot / strike) + implied**2 * years / 2) / (implied * sqrt(years))
    call = _NORMAL.cdf(d1)
    return call if kind == "CE" else call - 1


def _years(book: Book, moment: datetime) -> float:
    expires = datetime.combine(book.expiry, time(15, 30), tzinfo=_IST)
    return (expires - moment).total_seconds() / (365 * 86400)


def _last_closed(book: Book, strike: Decimal, kind: Kind, decided_at: datetime) -> float | None:
    series = book.bars.get((strike, kind), {})
    closed = [moment for moment in book.moments_by_day.get(decided_at.date(), [])
              if moment + _BAR <= decided_at and moment in series]
    return series[closed[-1]].close if closed else None


def strike_at_delta(
    book: Book, kind: Kind, decided_at: datetime, spot: float, target: float,
) -> Decimal | None:
    """The out-of-the-money strike closest to the delta; None when none comes near enough."""
    years = _years(book, decided_at)
    best: tuple[float, Decimal] | None = None
    for strike in book.strikes:
        if (kind == "CE" and float(strike) <= spot) or (kind == "PE" and float(strike) >= spot):
            continue
        price = _last_closed(book, strike, kind, decided_at)
        if price is None:
            continue
        delta = option_delta(kind, price, spot, float(strike), years)
        if delta is None:
            continue
        gap = abs(abs(delta) - target)
        if best is None or gap < best[0]:
            best = (gap, strike)
    return best[1] if best is not None and best[0] <= _DELTA_TOLERANCE else None


def centre_strike(book: Book, spot: float) -> Decimal:
    return min(book.strikes, key=lambda strike: (abs(float(strike) - spot), strike))


@dataclass(frozen=True)
class LegSpec:
    kind: Kind
    side: Side
    # None means at the money.
    delta: float | None = None
    # A wing this many strikes beyond the leg at index `beyond_leg`.
    strikes_beyond: int = 0
    beyond_leg: int | None = None


def choose_strikes(
    book: Book, specs: Sequence[LegSpec], decided_at: datetime,
) -> list[Decimal] | None:
    spot = spot_from_closed_bars(book, decided_at)
    if spot is None:
        return None
    chosen: list[Decimal] = []
    ladder = book.strikes
    for spec in specs:
        if spec.beyond_leg is not None:
            direction = 1 if spec.kind == "CE" else -1
            position = ladder.index(chosen[spec.beyond_leg]) + direction * spec.strikes_beyond
            if not 0 <= position < len(ladder):
                return None
            strike: Decimal | None = ladder[position]
        elif spec.delta is None:
            strike = centre_strike(book, spot)
        else:
            strike = strike_at_delta(book, spec.kind, decided_at, spot, spec.delta)
        if strike is None:
            return None
        chosen.append(strike)
    return chosen


def _legs(specs: Sequence[LegSpec]) -> list[Leg]:
    return [Leg(0, spec.kind, spec.side) for spec in specs]


def _run_fixed(
    market: Market, book: Book, specs: Sequence[LegSpec], decided_at: datetime, exit_bar: datetime,
    target_share: float | None = None,
) -> TradeResult | None:
    strikes = choose_strikes(book, specs, decided_at)
    if strikes is None:
        return None
    for attempt in range(_ENTRY_RETRIES + 1):
        result = simulate(book, _legs(specs), strikes, decided_at + _BAR * attempt, exit_bar,
                          market.half_spread_pct, target_share=target_share)
        if result is not None:
            return result
    return None


def _entry_session(book: Book, days_before: int) -> date | None:
    first = book.expiry - timedelta(days=days_before)
    days = [day for day in sorted(book.moments_by_day) if first <= day < book.expiry]
    return days[0] if days and book.expiry in book.moments_by_day else None


def run_held(
    specs: Sequence[LegSpec], days_before: int = _WEEKLY_ENTRY_DAYS,
    allowed: Callable[[Market, Book], bool] | None = None,
) -> Callable[[Market], list[TradeResult]]:
    """One trade an expiry, held to the expiry-day square-off with no stop or target."""
    def run(market: Market) -> list[TradeResult]:
        results = []
        for book in market.books:
            session = _entry_session(book, days_before)
            if session is None or (allowed is not None and not allowed(market, book)):
                continue
            result = _run_fixed(market, book, specs, _at(session, _ENTRY_TIME),
                                _at(book.expiry, _SQUARE_OFF_BAR))
            if result is not None:
                results.append(result)
        return results
    return run


def _options_look_cheap(market: Market, book: Book) -> bool:
    signal = market.signals.get(book.expiry)
    return signal is not None and signal.volatility_premium < 1


def run_zero_day_condor(market: Market) -> list[TradeResult]:
    specs = (LegSpec("CE", "SELL", delta=0.14), LegSpec("PE", "SELL", delta=0.14),
             LegSpec("CE", "BUY", strikes_beyond=3, beyond_leg=0),
             LegSpec("PE", "BUY", strikes_beyond=3, beyond_leg=1))
    results = []
    for book in market.books:
        if book.expiry not in book.moments_by_day:
            continue
        result = _run_fixed(market, book, specs, _at(book.expiry, _ENTRY_TIME),
                            _at(book.expiry, _ZERO_DAY_EXIT_BAR), target_share=0.3)
        if result is not None:
            results.append(result)
    return results


def run_weekend_straddle(market: Market) -> list[TradeResult]:
    """Sell before a weekend and buy back about five minutes after the next open."""
    legs = (Leg(0, "CE", "SELL"), Leg(0, "PE", "SELL"))
    results = []
    for session, following in zip(market.sessions, market.sessions[1:], strict=False):
        if (following - session).days < 3:
            continue
        book = market.nearest_book(session, after_session=True)
        if book is None or following not in book.moments_by_day:
            continue
        result = _enter_and_run(market, book, legs, _at(session, _LATE_ENTRY_TIME),
                                _at(following, _OPENING_BAR))
        if result is not None:
            results.append(result)
    return results


def run_straddle_momentum(market: Market) -> list[TradeResult]:
    """Buy this week's straddle only if the last four finished ones made money on average."""
    held = run_held(_LONG_STRADDLE)(market)
    taken = []
    for trade in held:
        finished = [item.net_pnl for item in held if item.expiry < trade.session]
        if len(finished) >= _MOMENTUM_WEEKS and mean(finished[-_MOMENTUM_WEEKS:]) > 0:
            taken.append(trade)
    return taken


def hedge_result(
    readings: Sequence[tuple[date, float, float]], half_spread_pct: float,
) -> tuple[float, float]:
    """Gross and charges from whole-lot futures against (day, forward, option delta per unit).

    Each reading resizes the hedge to cancel the option delta; the last one closes it.
    """
    lots = 0
    gross = charges = 0.0
    for index, (day, forward, option_delta_now) in enumerate(readings):
        wanted = 0 if index == len(readings) - 1 else -round(option_delta_now)
        change = wanted - lots
        if not change:
            continue
        side: Side = "BUY" if change > 0 else "SELL"
        fill = forward * (1 + half_spread_pct / 100 if change > 0 else 1 - half_spread_pct / 100)
        units = abs(change) * CURRENT_LOT
        gross -= change * CURRENT_LOT * fill
        charges += float(order_charges(
            instrument="FUT", side=side, price=Decimal(str(round(fill, 2))), quantity=units,
            trade_date=day,
        ).total)
        lots = wanted
    return gross, charges


def run_delta_hedged_straddle(market: Market) -> list[TradeResult]:
    """Sell the weekly straddle and rebalance a whole-lot hedge near each close.

    Parity at the option's own strikes is that expiry's forward, which stands in for the future.
    """
    results = []
    legs = (Leg(0, "CE", "SELL"), Leg(0, "PE", "SELL"))
    for book in market.books:
        session = _entry_session(book, _WEEKLY_ENTRY_DAYS)
        if session is None:
            continue
        decided_at = _at(session, _ENTRY_TIME)
        spot = spot_from_closed_bars(book, decided_at)
        if spot is None:
            continue
        strike = centre_strike(book, spot)
        base = simulate(book, legs, [strike, strike], decided_at,
                        _at(book.expiry, _SQUARE_OFF_BAR), market.half_spread_pct)
        if base is None:
            continue
        readings: list[tuple[date, float, float]] = []
        for day in sorted(day for day in book.moments_by_day if session <= day <= book.expiry):
            moment = _at(day, _SQUARE_OFF_BAR) + _BAR
            forward = spot_from_closed_bars(book, moment)
            call = _last_closed(book, strike, "CE", moment)
            put = _last_closed(book, strike, "PE", moment)
            if forward is None or call is None or put is None:
                continue
            years = _years(book, moment)
            call_delta = option_delta("CE", call, forward, float(strike), years)
            put_delta = option_delta("PE", put, forward, float(strike), years)
            if call_delta is None or put_delta is None:
                continue
            readings.append((day, forward, -(call_delta + put_delta)))
        gross, charges = hedge_result(readings, _FUTURES_HALF_SPREAD_PCT)
        results.append(TradeResult(base.session, base.expiry, round(base.gross_pnl + gross, 2),
                                   round(base.charges + charges, 2)))
    return results


_LONG_STRADDLE = (LegSpec("CE", "BUY"), LegSpec("PE", "BUY"))

GLOBAL_RULES = (
    PublishedRule("weekly_put_write", "Cboe WPUT: sell an at-the-money put each week, held",
                  run_held((LegSpec("PE", "SELL"),)), False),
    PublishedRule("far_put_write", "Early Retirement Now: sell a 5-delta put a few days out",
                  run_held((LegSpec("PE", "SELL", delta=0.05),), _LATE_ENTRY_DAYS), False),
    PublishedRule("index_style_condor", "Cboe CNDR form: sell 20-delta, buy 5-delta, held",
                  run_held((LegSpec("CE", "SELL", delta=0.2), LegSpec("PE", "SELL", delta=0.2),
                            LegSpec("CE", "BUY", delta=0.05), LegSpec("PE", "BUY", delta=0.05))),
                  True),
    PublishedRule("index_style_butterfly", "Cboe BFLY form: sell the centre, buy 5-delta, held",
                  run_held((LegSpec("CE", "SELL"), LegSpec("PE", "SELL"),
                            LegSpec("CE", "BUY", delta=0.05), LegSpec("PE", "BUY", delta=0.05))),
                  True),
    PublishedRule("near_strangle", "Santa-Clara and Saretto: short near-dated OTM strangle",
                  run_held((LegSpec("CE", "SELL", delta=0.1), LegSpec("PE", "SELL", delta=0.1))),
                  False),
    PublishedRule("delta_hedged_straddle", "Academic volatility premium: short and delta hedged",
                  run_delta_hedged_straddle, False),
    PublishedRule("weekend_straddle", "Jones and Shemesh: options overprice non-trading time",
                  run_weekend_straddle, False),
    PublishedRule("zero_day_condor", "US 0DTE form: 14-delta, 30% target, out before 11:00",
                  run_zero_day_condor, True),
    PublishedRule("straddle_momentum", "Heston and others: option returns persist; index form",
                  run_straddle_momentum, True),
    PublishedRule("volatility_gap_straddle", "Goyal and Saretto: buy when implied is under recent",
                  run_held(_LONG_STRADDLE, allowed=_options_look_cheap), True),
    PublishedRule("tail_hedge_puts", "Universa-style: buy far puts each week",
                  run_held((LegSpec("PE", "BUY", delta=0.05),)), True),
)
