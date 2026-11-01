"""Options rules published online, tested on imported NIFTY candles. Research only.

Sources describe these rules without audited records, so a rule's popularity is no evidence. Some
are naked and could never be armed in paper trading, which is defined-risk only; they run here to
test the published claim. Results are simulated history, not a forecast.

Fills: entries and scheduled exits use a bar's open or close moved against the trade by the
half-spread. A leg stop triggers on the bar's high and fills at the stop, or at the open if the bar
gapped through it. Every trade is one lot of 65, so results compare across the period's lot changes.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal

from packages.backtesting.candidate_algorithms import (
    CURRENT_LOT,
    HOLDOUT_START,
    MAXIMUM_DRAWDOWN,
    MAXIMUM_WORST_TRADE,
    CandidateResult,
    WeeklyResult,
    summarize_results,
)
from packages.backtesting.entry_signals import EntrySignal
from packages.backtesting.history_backtest import _IST, synthetic_spot
from packages.core.charges import order_charges

__all__ = ["HOLDOUT_START"]

Kind = Literal["CE", "PE"]
Side = Literal["BUY", "SELL"]
_BAR = timedelta(minutes=5)
_TICK = 0.05
_ENTRY_RETRIES = 3
_ENTRY_TIME = time(9, 20)
_LATE_ENTRY_TIME = time(15, 15)
_INTRADAY_EXIT_BAR = time(15, 10)
_SQUARE_OFF_BAR = time(15, 5)
_OPENING_BAR = time(9, 15)
_RANGE_END = time(9, 30)
_LAST_BREAKOUT = time(14, 0)
_WEEKLY_ENTRY_DAYS = 7
_RICH_RANK = 2 / 3
LEG_STOP_SHARE = 0.25
MINIMUM_DEVELOPMENT_TRADES = 15
DEVELOPMENT_T = 1.5
MAXIMUM_ADVANCED = 3
MINIMUM_HOLDOUT_TRADES = 15
# A two-sided 5% test shared across the three rules that can reach the holdout (Bonferroni).
HOLDOUT_T = 2.45


@dataclass(frozen=True)
class Bar:
    open: float
    high: float
    low: float
    close: float


@dataclass
class Book:
    """One expiry's bars by strike and type; moments are IST and each names its bar's start."""

    expiry: date
    bars: dict[tuple[Decimal, Kind], dict[datetime, Bar]]
    moments_by_day: dict[date, list[datetime]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.moments_by_day:
            days: dict[date, set[datetime]] = {}
            for series in self.bars.values():
                for moment in series:
                    days.setdefault(moment.date(), set()).add(moment)
            self.moments_by_day = {day: sorted(items) for day, items in days.items()}

    @property
    def strikes(self) -> list[Decimal]:
        return sorted({strike for strike, _ in self.bars})


@dataclass(frozen=True)
class Leg:
    offset: int
    kind: Kind
    side: Side
    # The bought leg that closes when this sold leg is stopped.
    wing: int | None = None


@dataclass(frozen=True)
class TradeResult:
    session: date
    expiry: date
    gross_pnl: float
    charges: float

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.charges


@dataclass
class _Open:
    strike: Decimal
    kind: Kind
    side: Side
    entry_price: float
    wing: int | None
    closed: bool = False


def _adverse(price: float, side: Side, half_spread_pct: float) -> float:
    moved = price * (1 + half_spread_pct / 100 if side == "BUY" else 1 - half_spread_pct / 100)
    return max(_TICK, round(moved, 2))


def _opposite(side: Side) -> Side:
    return "SELL" if side == "BUY" else "BUY"


def spot_from_closed_bars(book: Book, decided_at: datetime) -> float | None:
    """Parity at the last bar of that session already closed at the decision moment."""
    closed = [moment for moment in book.moments_by_day.get(decided_at.date(), [])
              if moment + _BAR <= decided_at]
    if not closed:
        return None
    moment = closed[-1]
    calls = {strike: Decimal(str(series[moment].close)) for (strike, kind), series
             in book.bars.items() if kind == "CE" and moment in series}
    puts = {strike: Decimal(str(series[moment].close)) for (strike, kind), series
            in book.bars.items() if kind == "PE" and moment in series}
    spot = synthetic_spot(calls, puts)
    return float(spot) if spot is not None else None


def _strikes_for(book: Book, spot: float, legs: Sequence[Leg]) -> list[Decimal] | None:
    strikes = book.strikes
    centre = min(range(len(strikes)), key=lambda at: (abs(float(strikes[at]) - spot), at))
    positions = [centre + leg.offset for leg in legs]
    if min(positions) < 0 or max(positions) >= len(strikes):
        return None
    return [strikes[position] for position in positions]


def simulate(
    book: Book, legs: Sequence[Leg], strikes: Sequence[Decimal], entry_at: datetime,
    exit_bar: datetime, half_spread_pct: float, leg_stop_share: float | None = None,
    target_share: float | None = None,
) -> TradeResult | None:
    """One lot from the entry bar's open to the exit bar's close; None when a leg has no price."""
    series = [book.bars.get((strike, leg.kind), {}) for strike, leg in zip(strikes, legs,
                                                                          strict=True)]
    if any(entry_at not in item for item in series):
        return None
    if any(not any(moment.date() == exit_bar.date() and moment <= exit_bar for moment in item)
           for item in series):
        return None
    positions = [
        _Open(strike, leg.kind, leg.side,
              _adverse(item[entry_at].open, leg.side, half_spread_pct), leg.wing)
        for strike, leg, item in zip(strikes, legs, series, strict=True)
    ]
    credit = sum(item.entry_price * (1 if item.side == "SELL" else -1) for item in positions)
    fills: list[tuple[date, Side, float]] = [
        (entry_at.date(), item.side, item.entry_price) for item in positions
    ]
    realized = 0.0
    last_close = [item[entry_at].open for item in series]
    last_seen = entry_at

    def close(index: int, price: float, moment: datetime) -> None:
        nonlocal realized
        position = positions[index]
        side = _opposite(position.side)
        fill = _adverse(price, side, half_spread_pct)
        fills.append((moment.date(), side, fill))
        realized += (fill - position.entry_price) * (1 if position.side == "BUY" else -1)
        position.closed = True

    moments = sorted({moment for days in book.moments_by_day.values() for moment in days
                      if entry_at <= moment <= exit_bar})
    for moment in moments:
        last_seen = moment
        for index, item in enumerate(series):
            if moment in item:
                last_close[index] = item[moment].close
        if leg_stop_share is not None:
            for index, position in enumerate(positions):
                if position.closed or position.side != "SELL" or moment not in series[index]:
                    continue
                bar = series[index][moment]
                stop = position.entry_price * (1 + leg_stop_share)
                if bar.high >= stop:
                    close(index, max(stop, bar.open), moment)
                    if position.wing is not None and not positions[position.wing].closed:
                        close(position.wing, last_close[position.wing], moment)
        if target_share is not None and credit > 0:
            marked = realized + sum(
                (_adverse(last_close[index], _opposite(position.side), half_spread_pct)
                 - position.entry_price) * (1 if position.side == "BUY" else -1)
                for index, position in enumerate(positions) if not position.closed
            )
            if marked >= credit * target_share:
                break
    for index, position in enumerate(positions):
        if not position.closed:
            close(index, last_close[index], last_seen)
    charges = sum(
        float(order_charges(instrument="OPT", side=side, price=Decimal(str(price)),
                            quantity=CURRENT_LOT, trade_date=day).total)
        for day, side, price in fills
    )
    return TradeResult(entry_at.date(), book.expiry, round(realized * CURRENT_LOT, 2),
                       round(charges, 2))


class Market:
    def __init__(self, books: Sequence[Book], half_spread_pct: float,
                 signals: dict[date, EntrySignal] | None = None) -> None:
        self.books = sorted(books, key=lambda book: book.expiry)
        self.half_spread_pct = half_spread_pct
        self.signals = signals or {}
        self.sessions = sorted({day for book in books for day in book.moments_by_day})

    def nearest_book(self, session: date, *, after_session: bool = False) -> Book | None:
        for book in self.books:
            trades_then = session in book.moments_by_day
            if trades_then and (book.expiry > session or
                                (book.expiry == session and not after_session)):
                return book
        return None


def _at(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock, tzinfo=_IST)


def _enter_and_run(
    market: Market, book: Book, legs: Sequence[Leg], decided_at: datetime, exit_bar: datetime,
    leg_stop_share: float | None = None, target_share: float | None = None,
) -> TradeResult | None:
    spot = spot_from_closed_bars(book, decided_at)
    strikes = _strikes_for(book, spot, legs) if spot is not None else None
    if strikes is None:
        return None
    # A thin far leg may print no bar at the decision; the strikes stay fixed for three more bars.
    for attempt in range(_ENTRY_RETRIES + 1):
        entry_at = decided_at + _BAR * attempt
        result = simulate(book, legs, strikes, entry_at, exit_bar, market.half_spread_pct,
                          leg_stop_share, target_share)
        if result is not None:
            return result
    return None


def run_intraday(
    legs: Sequence[Leg], leg_stop_share: float | None, skip_expiry_day: bool = False,
) -> Callable[[Market], list[TradeResult]]:
    def run(market: Market) -> list[TradeResult]:
        results = []
        for session in market.sessions:
            book = market.nearest_book(session)
            if book is None or (skip_expiry_day and book.expiry == session):
                continue
            result = _enter_and_run(market, book, legs, _at(session, _ENTRY_TIME),
                                    _at(session, _INTRADAY_EXIT_BAR), leg_stop_share)
            if result is not None:
                results.append(result)
        return results
    return run


def run_weekly(
    legs: Sequence[Leg], target_share: float | None, only_when_rich: bool = False,
) -> Callable[[Market], list[TradeResult]]:
    def run(market: Market) -> list[TradeResult]:
        results = []
        for book in market.books:
            first = book.expiry - timedelta(days=_WEEKLY_ENTRY_DAYS)
            days = [day for day in sorted(book.moments_by_day) if first <= day < book.expiry]
            if not days or book.expiry not in book.moments_by_day:
                continue
            if only_when_rich:
                signal = market.signals.get(book.expiry)
                rank = signal.premium_rank if signal else None
                if rank is None or rank < _RICH_RANK:
                    continue
            result = _enter_and_run(market, book, legs, _at(days[0], _ENTRY_TIME),
                                    _at(book.expiry, _SQUARE_OFF_BAR), target_share=target_share)
            if result is not None:
                results.append(result)
        return results
    return run


def run_overnight(legs: Sequence[Leg]) -> Callable[[Market], list[TradeResult]]:
    """Sell near the close and buy back about five minutes after the next open."""
    def run(market: Market) -> list[TradeResult]:
        results = []
        for session, following in zip(market.sessions, market.sessions[1:], strict=False):
            book = market.nearest_book(session, after_session=True)
            if book is None or following not in book.moments_by_day:
                continue
            result = _enter_and_run(market, book, legs, _at(session, _LATE_ENTRY_TIME),
                                    _at(following, _OPENING_BAR))
            if result is not None:
                results.append(result)
        return results
    return run


def run_opening_range_breakout(market: Market) -> list[TradeResult]:
    """Buy an at-the-money option on the first close beyond the 09:15-09:30 parity range.

    The stop is a close beyond the far side of the range, and the trade ends by 15:15.
    """
    results = []
    for session in market.sessions:
        book = market.nearest_book(session)
        if book is None:
            continue
        readings = [
            (moment, spot) for moment in book.moments_by_day[session]
            if (spot := spot_from_closed_bars(book, moment + _BAR)) is not None
        ]
        opening = [spot for moment, spot in readings if moment.time() < _RANGE_END]
        if len(opening) < 3:
            continue
        high, low = max(opening), min(opening)
        after = [item for item in readings if _RANGE_END <= item[0].time() <= _LAST_BREAKOUT]
        broke = next((item for item in after if item[1] > high or item[1] < low), None)
        if broke is None:
            continue
        going_up = broke[1] > high
        leg = Leg(0, "CE" if going_up else "PE", "BUY")
        strikes = _strikes_for(book, broke[1], [leg])
        entry_at = broke[0] + _BAR
        if strikes is None:
            continue
        final = _at(session, _INTRADAY_EXIT_BAR)
        stopped = next(
            (moment for moment, spot in readings
             if entry_at <= moment <= final and (spot < low if going_up else spot > high)),
            final,
        )
        result = simulate(book, [leg], strikes, entry_at, stopped, market.half_spread_pct)
        if result is not None:
            results.append(result)
    return results


_STRADDLE = (Leg(0, "CE", "SELL"), Leg(0, "PE", "SELL"))
_IRON_FLY = (Leg(0, "CE", "SELL", wing=2), Leg(0, "PE", "SELL", wing=3),
             Leg(6, "CE", "BUY"), Leg(-6, "PE", "BUY"))
_WIDE_CONDOR = (Leg(4, "CE", "SELL", wing=2), Leg(-4, "PE", "SELL", wing=3),
                Leg(9, "CE", "BUY"), Leg(-9, "PE", "BUY"))
_STRANGLE = (Leg(4, "CE", "SELL"), Leg(-4, "PE", "SELL"))


@dataclass(frozen=True)
class PublishedRule:
    name: str
    source: str
    run: Callable[[Market], list[TradeResult]]
    defined_risk: bool


PUBLISHED_RULES = (
    PublishedRule("straddle_920_leg_stop", "Zerodha forum 9:20 analysis: 25% stop per leg",
                  run_intraday(_STRADDLE, LEG_STOP_SHARE), False),
    PublishedRule("straddle_920_no_stop", "Zerodha forum December 2024 post: no stop",
                  run_intraday(_STRADDLE, None), False),
    PublishedRule("iron_fly_920_leg_stop", "Hedged form of the 9:20 straddle, from the forum",
                  run_intraday(_IRON_FLY, LEG_STOP_SHARE), True),
    PublishedRule("iron_fly_920_skip_expiry_day", "Bank Nifty blog: expiry days do worse",
                  run_intraday(_IRON_FLY, LEG_STOP_SHARE, skip_expiry_day=True), True),
    PublishedRule("condor_take_half", "tastylive: close at 50% of credit, no loss stop",
                  run_weekly(_WIDE_CONDOR, 0.5), True),
    PublishedRule("condor_take_half_when_rich", "tastylive: same, only when volatility is high",
                  run_weekly(_WIDE_CONDOR, 0.5, only_when_rich=True), True),
    PublishedRule("iron_fly_take_quarter", "tastylive: straddles close at 25% of credit",
                  run_weekly(_IRON_FLY, 0.25), True),
    PublishedRule("short_strangle_weekly", "Bangur 2020 favours short strangles; weekly form",
                  run_weekly(_STRANGLE, None), False),
    PublishedRule("overnight_straddle", "Muravyev and Ni: short option returns come overnight",
                  run_overnight(_STRADDLE), False),
    PublishedRule("overnight_iron_fly", "Same overnight idea with hedges",
                  run_overnight(_IRON_FLY), True),
    PublishedRule("opening_range_breakout", "AlgoTest and OneTradeJournal 15-minute range rules",
                  run_opening_range_breakout, True),
)


def summarize_trades(trades: Sequence[TradeResult]) -> CandidateResult:
    return summarize_results([WeeklyResult(trade.session, trade.net_pnl) for trade in trades])


def advances(result: CandidateResult) -> bool:
    return (
        result.trades >= MINIMUM_DEVELOPMENT_TRADES
        and result.average_net_pnl > 0
        and result.t_value is not None and result.t_value >= DEVELOPMENT_T
    )


def holdout_failures(result: CandidateResult) -> tuple[str, ...]:
    gates = {
        "makes money after charges": result.average_net_pnl > 0,
        f"at least {MINIMUM_HOLDOUT_TRADES} trades": result.trades >= MINIMUM_HOLDOUT_TRADES,
        "clear of chance": result.t_value is not None and result.t_value >= HOLDOUT_T,
        "worst trade within the per-trade limit": -result.worst_trade <= MAXIMUM_WORST_TRADE,
        "drawdown within the limit": result.drawdown <= MAXIMUM_DRAWDOWN,
    }
    return tuple(name for name, passed in gates.items() if not passed)
