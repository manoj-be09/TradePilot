"""Load imported option candles into per-expiry replay inputs.

Only COMPLETE contracts are used; an expiry whose contracts report different lot sizes
is left out rather than guessed.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.backtesting.history_backtest import Candle, ExpiryHistory, OptionKind
from packages.database.history_models import HistoricalImportContract, HistoricalOptionBar


def load_histories(
    session: Session, *, interval: str, start: date, end: date
) -> tuple[list[ExpiryHistory], list[tuple[date, str]]]:
    contracts = session.scalars(select(HistoricalImportContract).where(
        HistoricalImportContract.interval == interval,
        HistoricalImportContract.status == "COMPLETE",
        HistoricalImportContract.expiry >= start,
        HistoricalImportContract.expiry <= end,
    )).all()
    by_expiry: dict[date, list[HistoricalImportContract]] = defaultdict(list)
    for contract in contracts:
        by_expiry[contract.expiry].append(contract)

    histories: list[ExpiryHistory] = []
    rejected: list[tuple[date, str]] = []
    for expiry, rows in sorted(by_expiry.items()):
        lot_sizes = {row.lot_size for row in rows}
        if len(lot_sizes) != 1:
            rejected.append((expiry, f"inconsistent lot sizes {sorted(lot_sizes)}"))
            continue
        keys = {row.instrument_key: row for row in rows}
        bars: dict[tuple[Decimal, OptionKind], list[Candle]] = defaultdict(list)
        for bar in session.scalars(select(HistoricalOptionBar).where(
            HistoricalOptionBar.interval == interval,
            HistoricalOptionBar.instrument_key.in_(list(keys)),
        ).order_by(HistoricalOptionBar.bar_start)):
            kind: OptionKind = "CE" if bar.option_type == "CE" else "PE"
            bars[(bar.strike, kind)].append(Candle(start=bar.bar_start, close=bar.close))
        histories.append(ExpiryHistory(expiry=expiry, lot_size=lot_sizes.pop(), bars=dict(bars)))
    return histories, rejected
