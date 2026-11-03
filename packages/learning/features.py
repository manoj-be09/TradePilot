"""Point-in-time features and the event calendar, for hypotheses that filter their entries.

Every value uses only what was known at the decision: bars closed by the entry time, earlier
sessions' moves, the previous session's India VIX close, and a calendar of scheduled events kept
by the owner (`work/learning/events.json`, a list of {"date", "name"}). A filter whose feature
cannot be read fails closed: no trade on that session, in shadow and live alike. Simulated only.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from math import sqrt
from pathlib import Path
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from packages.backtesting.global_strategies import _last_closed, centre_strike
from packages.backtesting.published_strategies import Book, Market, spot_from_closed_bars
from packages.backtesting.rich_premium import SessionMove, session_moves, sessions_until_expiry
from packages.database.recorder_models import MarketSnapshot1m
from packages.database.session import build_session_factory
from packages.recorder.universe import INDIA_VIX_KEY

IST = ZoneInfo("Asia/Kolkata")
EVENTS = Path("work/learning/events.json")
VIX = Path("work/learning/vix.json")
REALISED_SESSIONS = 5
SKEW_DISTANCE = 0.02
_STRADDLE_PER_MOVE = 0.8
_SESSIONS_A_YEAR = 252
FEATURES = ("realised_vol", "vix", "term_gap", "skew", "event_today", "event_next")
Features = dict[str, float | bool | None]


@dataclass(frozen=True)
class Context:
    """What features read besides bars: scheduled event days and India VIX closes."""

    events: frozenset[date] = frozenset()
    vix: Mapping[date, float] = field(default_factory=dict)

    @classmethod
    def load(cls, database_url: str | None = None, events: Path = EVENTS,
             vix: Path = VIX) -> "Context":
        """Events from the owner's file; VIX closes from an optional file, then the recorder."""
        days = frozenset(date.fromisoformat(row["date"]) for row in json.loads(
            events.read_text(encoding="utf-8"))) if events.exists() else frozenset()
        closes = {date.fromisoformat(day): float(value) for day, value in json.loads(
            vix.read_text(encoding="utf-8")).items()} if vix.exists() else {}
        if database_url is not None:
            closes.update(recorded_vix(database_url))
        return cls(days, closes)


def recorded_vix(database_url: str) -> dict[date, float]:
    """Each recorded day's last India VIX reading, which the recorder already subscribes to."""
    closes: dict[date, float] = {}
    with build_session_factory(database_url)() as session:
        for day, value in session.execute(
                select(MarketSnapshot1m.trading_day, MarketSnapshot1m.ltp_close)
                .where(MarketSnapshot1m.instrument_key == INDIA_VIX_KEY,
                       MarketSnapshot1m.ltp_close.is_not(None))
                .order_by(MarketSnapshot1m.bucket_start)):
            closes[day] = float(value)
    return closes


def _implied_vol(book: Book, decided_at: datetime) -> float | None:
    """Annualised volatility the at-the-money straddle implies, from bars closed by then."""
    spot = spot_from_closed_bars(book, decided_at)
    if spot is None:
        return None
    strike = centre_strike(book, spot)
    call = _last_closed(book, strike, "CE", decided_at)
    put = _last_closed(book, strike, "PE", decided_at)
    sessions = sessions_until_expiry(book, decided_at)
    if call is None or put is None or sessions <= 0:
        return None
    move = (call + put) / (_STRADDLE_PER_MOVE * spot)
    return move / sqrt(sessions / _SESSIONS_A_YEAR)


def _skew(book: Book, decided_at: datetime) -> float | None:
    """A put 2% below spot over a call 2% above; above 1 means downside protection is dear."""
    spot = spot_from_closed_bars(book, decided_at)
    if spot is None:
        return None
    put = _last_closed(book, centre_strike(book, spot * (1 - SKEW_DISTANCE)), "PE", decided_at)
    call = _last_closed(book, centre_strike(book, spot * (1 + SKEW_DISTANCE)), "CE", decided_at)
    return put / call if put and call else None


def features_at(
    near: Book, following: Book | None, decided_at: datetime, past: Sequence[SessionMove],
    next_session: date | None, context: Context,
) -> Features:
    day = decided_at.date()
    recent = [move.variance for move in past if move.session < day][-REALISED_SESSIONS:]
    earlier = [moment for moment in sorted(context.vix) if moment < day]
    near_vol = _implied_vol(near, decided_at)
    next_vol = _implied_vol(following, decided_at) if following is not None else None
    return {
        "realised_vol": (sqrt(mean(recent) * _SESSIONS_A_YEAR)
                         if len(recent) == REALISED_SESSIONS else None),
        "vix": context.vix[earlier[-1]] if earlier else None,
        "term_gap": near_vol / next_vol if near_vol and next_vol else None,
        "skew": _skew(near, decided_at),
        "event_today": day in context.events,
        "event_next": None if next_session is None else next_session in context.events,
    }


def passes(filters: Sequence[Mapping[str, Any]], values: Features) -> bool:
    """Every filter must hold; a missing value fails closed."""
    for rule in filters:
        value = values.get(rule["feature"])
        if value is None:
            return False
        if "equals" in rule and value != rule["equals"]:
            return False
        if "min" in rule and float(value) < rule["min"]:
            return False
        if "max" in rule and float(value) > rule["max"]:
            return False
    return True


def validate(filters: Sequence[Mapping[str, Any]]) -> None:
    for rule in filters:
        if rule.get("feature") not in FEATURES or not {"equals", "min", "max"} & set(rule):
            raise ValueError(f"Bad filter {rule}")


def session_features(market: Market, entry: time, context: Context) -> dict[date, Features]:
    """Features at `entry` on each session in the market, from what was known by then."""
    moves = session_moves(market)
    found: dict[date, Features] = {}
    sessions = market.sessions
    for index, day in enumerate(sessions):
        live = [book for book in market.books
                if book.expiry >= day and book.moments_by_day.get(day)]
        if not live:
            continue
        following = live[1] if len(live) > 1 else None
        later = sessions[index + 1] if index + 1 < len(sessions) else None
        found[day] = features_at(live[0], following, datetime.combine(day, entry, IST), moves,
                                 later, context)
    return found
