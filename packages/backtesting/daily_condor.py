"""One-night condors priced from end-of-day option files. Research only.

A rougher form of `very_rich_night_condor` for years without intraday bars: the reading and the
entry both use the opening price, the exit uses the close, and past movement comes from the
index's daily open, high, low and close. Because the opening print sets both the reading and the
fill, noise in it can flatter a trade; the study measures that on recorded bars and reports it.

Wings and size follow the index, so older years carry the same risk as today: the wing is the
same share of the index as 300 points was at 24,500, and each trade holds enough units that a full
loss of the wing costs what 300 points cost on one lot of 65. Results are simulated, not a forecast.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from math import log, sqrt
from statistics import mean

from packages.backtesting.candidate_algorithms import CURRENT_LOT
from packages.backtesting.global_strategies import option_delta
from packages.backtesting.published_strategies import (
    _TICK,
    HalfSpread,
    Side,
    _adverse,
    _opposite,
)
from packages.backtesting.rich_premium import (
    _DELTA_TOLERANCE_SHARE,
    _FORECAST_SESSIONS,
    _LARGEST_CENTRE_GAP,
    _MINIMUM_PAST_SESSIONS,
    _RECENT_SESSIONS,
    _SMALLEST_DELTA_TOLERANCE,
    _STRADDLE_PER_MOVE,
    _VARIANCE_FLOOR,
    WING_POINTS,
)
from packages.core.charges import order_charges

REFERENCE_INDEX = 24500.0
WING_SHARE = WING_POINTS / REFERENCE_INDEX
FULL_WING_RUPEES = WING_POINTS * CURRENT_LOT
TODAYS_CHARGES = date(2026, 11, 1)
_STRIKE_STEP = 50
_MINUTES_OPEN_TO_EXPIRY = 375
_MINUTES_PER_DAY = 1440
_DAYS_PER_YEAR = 365
_GARMAN_CLOSE_WEIGHT = 2 * log(2) - 1


@dataclass(frozen=True)
class DailyQuote:
    """A contract's session. Open and close are None when it did not trade."""

    open: float | None
    close: float | None


@dataclass(frozen=True)
class IndexDay:
    open: float
    high: float
    low: float
    close: float


Chain = Mapping[tuple[int, str], DailyQuote]


@dataclass(frozen=True)
class DailyMarket:
    sessions: tuple[date, ...]
    chains: Mapping[date, Mapping[date, Chain]]
    index: Mapping[date, IndexDay]


@dataclass(frozen=True)
class DailyReading:
    session: date
    expiry: date
    reading: float
    before_break: bool


@dataclass(frozen=True)
class DailyTrade:
    session: date
    expiry: date
    exit_session: date
    strikes: tuple[int, int, int, int]
    units: int
    credit: float
    gross: float
    charges: float

    @property
    def net(self) -> float:
        return self.gross - self.charges


def session_variances(market: DailyMarket) -> dict[date, float]:
    """Squared overnight gap plus the Garman-Klass range, for sessions with a prior close."""
    variances: dict[date, float] = {}
    previous: IndexDay | None = None
    for session in market.sessions:
        day = market.index.get(session)
        if day is None:
            continue
        if previous is not None:
            gap = log(day.open / previous.close)
            ranged = (0.5 * log(day.high / day.low) ** 2
                      - _GARMAN_CLOSE_WEIGHT * log(day.close / day.open) ** 2)
            variances[session] = gap * gap + max(ranged, 0.0)
        previous = day
    return variances


def forecast(past: Sequence[float]) -> float | None:
    """The same blend of last session, week and month as the intraday reading."""
    if len(past) < _MINIMUM_PAST_SESSIONS:
        return None
    window = past[-_FORECAST_SESSIONS:]
    blend = (window[-1] + mean(window[-_RECENT_SESSIONS:]) + mean(window)) / 3
    return max(blend, _VARIANCE_FLOOR)


def forward_from_open(chain: Chain) -> float | None:
    """Opening parity at the strike where call and put are closest; None without a traded pair."""
    best: tuple[float, int, float, float] | None = None
    for (strike, kind), quote in chain.items():
        if kind != "CE" or quote.open is None:
            continue
        put = chain.get((strike, "PE"))
        if put is None or put.open is None:
            continue
        candidate = (abs(quote.open - put.open), strike, quote.open, put.open)
        if best is None or candidate[:2] < best[:2]:
            best = candidate
    return None if best is None else best[1] + best[2] - best[3]


def nearest_expiry(market: DailyMarket, session: date) -> date | None:
    return min((expiry for expiry in market.chains.get(session, {}) if expiry >= session),
               default=None)


def sessions_left(market: DailyMarket, session: date, expiry: date) -> int:
    """Sessions from this one through expiry, with each break before expiry counted as one more."""
    remaining = [day for day in market.sessions if session <= day <= expiry]
    breaks = sum(1 for day, later in zip(remaining, remaining[1:], strict=False)
                 if (later - day).days > 1)
    return len(remaining) + breaks


def read_opening(market: DailyMarket, session: date, expiry: date,
                 past: Sequence[float]) -> float | None:
    """Move implied by the opening straddle over the move forecast, using earlier sessions only."""
    expected = forecast(past)
    chain = market.chains[session][expiry]
    spot = forward_from_open(chain)
    if expected is None or spot is None:
        return None
    centre = min({strike for strike, _ in chain}, key=lambda strike: (abs(strike - spot), strike))
    if abs(centre - spot) > _LARGEST_CENTRE_GAP:
        return None
    call, put = chain.get((centre, "CE")), chain.get((centre, "PE"))
    if call is None or put is None or call.open is None or put.open is None:
        return None
    implied_move = (call.open + put.open) / (_STRADDLE_PER_MOVE * spot)
    return implied_move / sqrt(expected * sessions_left(market, session, expiry))


def daily_readings(market: DailyMarket) -> list[DailyReading]:
    variances = session_variances(market)
    past: list[float] = []
    readings: list[DailyReading] = []
    for at, session in enumerate(market.sessions):
        expiry = nearest_expiry(market, session)
        if expiry is not None:
            value = read_opening(market, session, expiry, past)
            following = market.sessions[at + 1] if at + 1 < len(market.sessions) else None
            before_break = (session != expiry and following is not None
                            and (following - session).days > 1)
            if value is not None:
                readings.append(DailyReading(session, expiry, value, before_break))
        if session in variances:
            past.append(variances[session])
    return readings


def top_share_by_trailing_rank(
    readings: Sequence[DailyReading], share: float, lookback: int = 250, minimum: int = 60,
) -> set[date]:
    """Days whose reading sits in the top `share` of the readings before them; no later data."""
    chosen: set[date] = set()
    for at, item in enumerate(readings):
        earlier = [other.reading for other in readings[max(0, at - lookback):at]]
        if len(earlier) < minimum:
            continue
        if sum(value >= item.reading for value in earlier) / len(earlier) <= share:
            chosen.add(item.session)
    return chosen


def _short_strike(chain: Chain, kind: str, spot: float, years: float, target: float) -> int | None:
    tolerance = max(_SMALLEST_DELTA_TOLERANCE, target * _DELTA_TOLERANCE_SHARE)
    best: tuple[float, int] | None = None
    for (strike, side), quote in chain.items():
        if side != kind or quote.open is None:
            continue
        if (kind == "CE" and strike <= spot) or (kind == "PE" and strike >= spot):
            continue
        delta = option_delta(kind, quote.open, spot, float(strike), years)  # type: ignore[arg-type]
        if delta is None:
            continue
        candidate = (abs(abs(delta) - target), strike)
        if best is None or candidate < best:
            best = candidate
    return best[1] if best is not None and best[0] <= tolerance else None


def _wing_strike(chain: Chain, kind: str, short: int, spot: float,
                 wing_share: float | None) -> int | None:
    """The bought strike. A fixed wing must trade exactly; a scaled one may be one step off."""
    direction = 1 if kind == "CE" else -1
    if wing_share is None:
        widths = [WING_POINTS]
    else:
        width = max(_STRIKE_STEP, round(wing_share * spot / _STRIKE_STEP) * _STRIKE_STEP)
        widths = [width, width + _STRIKE_STEP]
        if width > _STRIKE_STEP:
            widths.append(width - _STRIKE_STEP)
    for width in widths:
        quote = chain.get((short + direction * width, kind))
        if quote is not None and quote.open is not None:
            return short + direction * width
    return None


def _exit_mark(exit_chain: Chain, entry_chain: Chain, strike: int, kind: str, side: Side,
               index_close: float) -> float:
    """The session's close. A quiet bought leg is worthless; a sold leg never marks below value."""
    quote = exit_chain.get((strike, kind))
    close = quote.close if quote is not None else None
    if side == "BUY":
        return _TICK if close is None else close
    if close is None:
        opened = entry_chain[(strike, kind)]
        close = opened.close if opened.close is not None else opened.open
    if close is None:
        raise ValueError("A sold leg must have traded at entry")
    intrinsic = index_close - strike if kind == "CE" else strike - index_close
    return max(close, intrinsic)


def trade_night_condor(
    market: DailyMarket, session: date, short_delta: float, half_spread_pct: HalfSpread,
    wing_share: float | None = WING_SHARE, charge_date: date = TODAYS_CHARGES,
) -> DailyTrade | None:
    """Sell at the open, leave at the next close, or today's on expiry day; None when unpriced."""
    expiry = nearest_expiry(market, session)
    if expiry is None:
        return None
    position = market.sessions.index(session)
    if session == expiry:
        exit_session = session
    elif position + 1 < len(market.sessions):
        exit_session = market.sessions[position + 1]
    else:
        return None
    chain = market.chains[session][expiry]
    exit_chain = market.chains.get(exit_session, {}).get(expiry)
    exit_index = market.index.get(exit_session)
    spot = forward_from_open(chain)
    if exit_chain is None or exit_index is None or spot is None:
        return None
    years = (((expiry - session).days * _MINUTES_PER_DAY + _MINUTES_OPEN_TO_EXPIRY)
             / (_DAYS_PER_YEAR * _MINUTES_PER_DAY))
    call = _short_strike(chain, "CE", spot, years, short_delta)
    put = _short_strike(chain, "PE", spot, years, short_delta)
    if call is None or put is None:
        return None
    far_call = _wing_strike(chain, "CE", call, spot, wing_share)
    far_put = _wing_strike(chain, "PE", put, spot, wing_share)
    if far_call is None or far_put is None:
        return None
    # Equal risk: the wider wing sets the units, so a full loss either side stays in the limit.
    width = max(far_call - call, put - far_put)
    units = CURRENT_LOT if wing_share is None else max(1, round(FULL_WING_RUPEES / width))
    legs: tuple[tuple[int, str, Side], ...] = (
        (call, "CE", "SELL"), (put, "PE", "SELL"), (far_call, "CE", "BUY"), (far_put, "PE", "BUY"),
    )
    credit = gross = charges = 0.0
    for strike, kind, side in legs:
        opened = chain[(strike, kind)].open
        if opened is None:
            return None
        sign = 1 if side == "SELL" else -1
        closing = _opposite(side)
        entry = _adverse(opened, side, half_spread_pct)
        mark = _exit_mark(exit_chain, chain, strike, kind, side, exit_index.close)
        out = _adverse(mark, closing, half_spread_pct)
        credit += sign * entry
        gross += sign * (entry - out) * units
        for order_side, price in ((side, entry), (closing, out)):
            charges += float(order_charges(instrument="OPT", side=order_side,
                                           price=Decimal(str(price)), quantity=units,
                                           trade_date=charge_date).total)
    return DailyTrade(session, expiry, exit_session, (call, put, far_call, far_put), units,
                      round(credit, 2), round(gross, 2), round(charges, 2))


def run_night_condor(
    market: DailyMarket, chosen: Collection[date], short_delta: float,
    half_spread_pct: HalfSpread, wing_share: float | None = WING_SHARE,
) -> tuple[list[DailyTrade], list[date]]:
    """Trades on the chosen sessions, and the sessions that could not be priced."""
    trades: list[DailyTrade] = []
    skipped: list[date] = []
    for session in sorted(chosen):
        trade = trade_night_condor(market, session, short_delta, half_spread_pct, wing_share)
        if trade is None:
            skipped.append(session)
        else:
            trades.append(trade)
    return trades, skipped
