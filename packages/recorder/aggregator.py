"""Fold ticks into one row per instrument per UTC minute (LTP OHLC + last state)."""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from packages.recorder.frames import Tick


def minute_of(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(second=0, microsecond=0)


@dataclass
class MinuteBar:
    instrument_key: str
    bucket_start: datetime
    last: Tick
    ltp_open: Decimal | None = None
    ltp_high: Decimal | None = None
    ltp_low: Decimal | None = None
    message_count: int = 0
    _merged: dict[str, Any] = field(default_factory=dict)

    def add(self, tick: Tick) -> None:
        self.message_count += 1
        if tick.ltp is not None:
            if self.ltp_open is None:
                self.ltp_open = tick.ltp
            self.ltp_high = tick.ltp if self.ltp_high is None else max(self.ltp_high, tick.ltp)
            self.ltp_low = tick.ltp if self.ltp_low is None else min(self.ltp_low, tick.ltp)
        # Feed V3 may omit fields in an update; keep the latest non-empty value of each.
        updates = {name: getattr(tick, name) for name in _CARRIED if getattr(tick, name)}
        self._merged.update(updates)
        self.last = replace(tick, **self._merged)

    def row(self, trading_day: Any) -> dict[str, Any]:
        tick = self.last
        return {
            "instrument_key": self.instrument_key,
            "bucket_start": self.bucket_start,
            "trading_day": trading_day,
            "received_at": tick.received_at,
            "provider_ts": tick.provider_ts,
            "last_trade_time": tick.last_trade_time,
            "ltp_open": self.ltp_open,
            "ltp_high": self.ltp_high,
            "ltp_low": self.ltp_low,
            "ltp_close": tick.ltp,
            "last_trade_qty": tick.last_trade_qty,
            "prev_close": tick.prev_close,
            "bid": tick.bid,
            "ask": tick.ask,
            "bid_qty": tick.bid_qty,
            "ask_qty": tick.ask_qty,
            "depth": {"levels": [list(level) for level in tick.depth]} if tick.depth else None,
            "volume": tick.volume,
            "open_interest": tick.open_interest,
            "iv": tick.iv,
            "delta": tick.delta,
            "gamma": tick.gamma,
            "theta": tick.theta,
            "vega": tick.vega,
            "rho": tick.rho,
            "avg_traded_price": tick.avg_traded_price,
            "total_buy_qty": tick.total_buy_qty,
            "total_sell_qty": tick.total_sell_qty,
            "message_count": self.message_count,
        }


_CARRIED = (
    "ltp", "last_trade_time", "last_trade_qty", "prev_close", "bid", "ask", "bid_qty",
    "ask_qty", "depth", "volume", "open_interest", "iv", "delta", "gamma", "theta", "vega",
    "rho", "avg_traded_price", "total_buy_qty", "total_sell_qty",
)


class MinuteAggregator:
    def __init__(self) -> None:
        self._bars: dict[tuple[str, datetime], MinuteBar] = {}

    def add(self, tick: Tick) -> None:
        bucket = minute_of(tick.received_at)
        key = (tick.instrument_key, bucket)
        bar = self._bars.get(key)
        if bar is None:
            bar = MinuteBar(tick.instrument_key, bucket, tick)
            self._bars[key] = bar
        bar.add(tick)

    def drain_completed(self, now: datetime, *, grace_seconds: float = 2.0) -> list[MinuteBar]:
        """Return bars whose minute ended at least `grace_seconds` before `now`."""
        cutoff = now.astimezone(timezone.utc) - timedelta(seconds=grace_seconds)
        done = [key for key in self._bars if key[1] + timedelta(minutes=1) <= cutoff]
        return [self._bars.pop(key) for key in sorted(done, key=lambda k: (k[1], k[0]))]

    def drain_all(self) -> list[MinuteBar]:
        bars = [self._bars[key] for key in sorted(self._bars, key=lambda k: (k[1], k[0]))]
        self._bars.clear()
        return bars
