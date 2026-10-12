from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from packages.database.models import Instrument

_EXCHANGE_TIMEZONE = ZoneInfo("Asia/Kolkata")


def exchange_date() -> date:
    return datetime.now(_EXCHANGE_TIMEZONE).date()


def register_instrument(session: Session, values: dict[str, Any]) -> Instrument:
    existing = session.scalar(
        select(Instrument).where(
            Instrument.exchange == values["exchange"],
            Instrument.instrument_token == values["instrument_token"],
            Instrument.effective_from == values["effective_from"],
        )
    )
    if existing is not None:
        raise ValueError("An instrument record already exists for this token and effective_from")
    instrument = Instrument(**values)
    session.add(instrument)
    session.flush()
    return instrument


def list_instruments(
    session: Session,
    underlying: str | None = None,
    expiry: date | None = None,
    *,
    exchange: str | None = None,
    instrument_type: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Instrument]:
    now = datetime.now(timezone.utc)
    query = select(Instrument).where(
        Instrument.status == "ACTIVE",
        Instrument.effective_from <= now,
        or_(Instrument.effective_to.is_(None), Instrument.effective_to > now),
        or_(Instrument.expiry.is_(None), Instrument.expiry >= exchange_date()),
    )
    if underlying:
        query = query.where(Instrument.underlying == underlying.upper())
    if expiry:
        query = query.where(Instrument.expiry == expiry)
    if exchange:
        query = query.where(Instrument.exchange == exchange.upper())
    if instrument_type:
        query = query.where(Instrument.instrument_type == instrument_type)
    if search:
        escaped = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        term = f"%{escaped}%"
        query = query.where(
            or_(
                Instrument.trading_symbol.ilike(term, escape="\\"),
                Instrument.underlying.ilike(term, escape="\\"),
            )
        )
    return list(
        session.scalars(
            query.order_by(
                Instrument.exchange,
                Instrument.instrument_type,
                Instrument.expiry,
                Instrument.strike,
                Instrument.option_type,
                Instrument.trading_symbol,
                Instrument.id,
            ).limit(limit).offset(offset)
        )
    )


def available_expiries(
    session: Session, underlying: str = "NIFTY", on_date: date | None = None
) -> list[date]:
    as_of = on_date or exchange_date()
    now = datetime.now(timezone.utc)
    query = (
        select(Instrument.expiry)
        .where(
            Instrument.underlying == underlying.upper(),
            Instrument.instrument_type == "OPTION",
            Instrument.status == "ACTIVE",
            Instrument.effective_from <= now,
            or_(Instrument.effective_to.is_(None), Instrument.effective_to > now),
            Instrument.expiry >= as_of,
        )
        .distinct()
        .order_by(Instrument.expiry)
    )
    return list(session.scalars(query))


def available_strikes(session: Session, underlying: str, expiry: date) -> list[Decimal]:
    if expiry < exchange_date():
        return []
    now = datetime.now(timezone.utc)
    query = (
        select(Instrument.strike)
        .where(
            Instrument.underlying == underlying.upper(),
            Instrument.instrument_type == "OPTION",
            Instrument.status == "ACTIVE",
            Instrument.effective_from <= now,
            or_(Instrument.effective_to.is_(None), Instrument.effective_to > now),
            Instrument.expiry == expiry,
        )
        .distinct()
        .order_by(Instrument.strike)
    )
    return list(session.scalars(query))
