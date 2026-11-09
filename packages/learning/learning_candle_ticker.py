"""The candle that is forming right now, built from one quote a second, for the arena page.

Prices come only from quotes; a missing or failed quote leaves the field empty (the page shows a
dash) rather than reusing an old value. A candle joined part-way through is marked partial until
it is seeded from the provider's 1-minute candles.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from packages.learning.learning_candle import IST


def bucket(moment: datetime) -> datetime:
    local = moment.astimezone(IST)
    return local.replace(minute=local.minute - local.minute % 5, second=0, microsecond=0)


@dataclass
class Forming:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    partial: bool
    volume: float | None = None  # futures contracts traded in this candle so far
    path: list[list[float]] = field(default_factory=list)  # [seconds into candle, price]


class Ticker:
    def __init__(self) -> None:
        self.candle: Forming | None = None
        self._base: float | None = None  # cumulative futures volume when this candle began
        self.volumes: deque[float] = deque(maxlen=12)  # finished candles' futures volume

    def seed(self, start: datetime, open_: float, high: float, low: float,
             volume_so_far: float | None, cumulative: float | None) -> None:
        """Start mid-candle from the provider's 1-minute candles, so the open is the real one."""
        self.candle = Forming(start, open_, high, low, open_, partial=False, volume=volume_so_far)
        self._base = (cumulative - volume_so_far
                      if cumulative is not None and volume_so_far is not None else None)

    def update(self, now: datetime, price: float, cumulative: float | None) -> Forming:
        start = bucket(now)
        if self.candle is None or self.candle.start != start:
            if self.candle is not None and self.candle.volume is not None and (
                    start - self.candle.start == timedelta(minutes=5)):
                self.volumes.append(self.candle.volume)
            late = (now - start).total_seconds() > 3  # joined after the open: open unknown
            self.candle = Forming(start, price, price, price, price, partial=late)
            self._base = cumulative
        candle = self.candle
        candle.high, candle.low = max(candle.high, price), min(candle.low, price)
        candle.close = price
        if cumulative is not None and self._base is None:
            self._base = cumulative
        if cumulative is not None and self._base is not None and cumulative >= self._base:
            candle.volume = cumulative - self._base
        else:
            candle.volume = None  # no volume, or the count went backwards: show none, not a guess
        candle.path = [*candle.path, [round((now - start).total_seconds(), 1), price]][-300:]
        return candle


def _num(item: dict[str, Any] | None, *path: str) -> float | None:
    value: Any = item
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return float(value) if isinstance(value, (int, float)) and value == value else None


def tick_state(ticker: Ticker, nifty: dict[str, Any] | None, vix: dict[str, Any] | None,
               fut: dict[str, Any] | None, now: datetime, symbol: str | None) -> dict[str, Any]:
    """Everything the page shows for this second. Missing quote fields stay None."""
    candle = ticker.candle
    price, change = _num(nifty, "last_price"), _num(nifty, "net_change")
    buy, sell = _num(fut, "total_buy_quantity"), _num(fut, "total_sell_quantity")
    oi, prev_oi = _num(fut, "oi"), _num(fut, "previous_oi")
    fut_price, vwap = _num(fut, "last_price"), _num(fut, "average_price")
    avg = sum(ticker.volumes) / len(ticker.volumes) if ticker.volumes else None
    return {
        "updated": now.astimezone(IST).isoformat(),
        "price": price, "change": change,
        "prev_close": price - change if price is not None and change is not None else None,
        "day": {k: _num(nifty, "ohlc", k) for k in ("open", "high", "low")},
        "vix": _num(vix, "last_price"), "vix_change": _num(vix, "net_change"),
        "fut": {"symbol": symbol, "price": fut_price, "volume": _num(fut, "volume"),
                "vwap": vwap, "oi": oi,
                "oi_change": oi - prev_oi if oi is not None and prev_oi is not None else None,
                "buy_qty": buy, "sell_qty": sell,
                "basis": fut_price - price if fut_price is not None and price is not None
                else None},
        "candle": None if candle is None else {
            "start": candle.start.isoformat(), "open": candle.open, "high": candle.high,
            "low": candle.low, "close": candle.close, "partial": candle.partial,
            "volume": candle.volume, "avg_volume": avg, "path": candle.path},
    }
