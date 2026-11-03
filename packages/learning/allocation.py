"""Which rules trade when several are due: Thompson ordering inside portfolio caps. Simulated only.

One condor lot already sits near the 2% per-trade cap, so allocation is about which trades go
ahead, not their size. Rules due together are ordered by a draw from each one's posterior average
(Thompson sampling), so better-evidenced rules usually go first while weaker ones still get
evidence. Each trade is then admitted only if the whole book (open trades plus this one) stays
inside a net delta cap, a net vega cap, and a gap stress test: a 5% move either way overnight,
valued at intrinsic, must not lose more than 10% of capital. Unknown greeks fail closed. The open
function's own per-trade, portfolio, daily-loss and kill-switch checks still apply after this.
"""

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from math import sqrt
from statistics import mean, pstdev

GAP_SHOCK = 0.05
GAP_LIMIT_SHARE = 0.10
# Index points of exposure (delta × units): about ₹40 per point of NIFTY.
MAX_NET_DELTA = 40.0
# Rupees per IV point across the book, about five condor lots short.
MAX_NET_VEGA = 3_000.0
# Before any results: a wide prior around zero, in rupees per trade.
PRIOR_SD = 5_000.0


@dataclass(frozen=True)
class Leg:
    option_type: str
    side: str
    strike: Decimal
    units: int
    entry: float

    @property
    def sign(self) -> int:
        return 1 if self.side == "BUY" else -1


def saved_legs(items: Sequence[Mapping[str, object]]) -> list[Leg]:
    """Legs as the paper trade stores them (`legs["items"]`)."""
    return [Leg(str(item["option_type"]), str(item["side"]), Decimal(str(item["strike"])),
                int(str(item["quantity_units"])), float(str(item["entry_price"])))
            for item in items]


def stress_loss(legs: Sequence[Leg], spot: float) -> float:
    """The worse of a 5% gap down or up, at intrinsic value; positive is a loss."""
    worst = 0.0
    for moved in (spot * (1 - GAP_SHOCK), spot * (1 + GAP_SHOCK)):
        value = 0.0
        for leg in legs:
            strike = float(leg.strike)
            intrinsic = max(moved - strike, 0.0) if leg.option_type == "CE" else max(
                strike - moved, 0.0)
            value += leg.sign * leg.units * (intrinsic - leg.entry)
        worst = max(worst, -value)
    return worst


def net_greek(legs: Sequence[Leg], greeks: Mapping[tuple[Decimal, str], Mapping[str, object]],
              name: str) -> float | None:
    total = 0.0
    for leg in legs:
        value = greeks.get((leg.strike, leg.option_type), {}).get(name)
        if value is None:
            return None
        total += leg.sign * leg.units * float(str(value))
    return total


def thompson_draw(values: Sequence[float], rng: random.Random) -> float:
    if len(values) < 2:
        return rng.gauss(0.0, PRIOR_SD)
    return rng.gauss(mean(values), max(pstdev(values), 1.0) / sqrt(len(values)))


def refusal(new: Sequence[Leg], held: Sequence[Leg], spot: float,
            greeks: Mapping[tuple[Decimal, str], Mapping[str, object]],
            capital: float) -> str | None:
    """Why the book with this trade added breaks a cap; None when it fits."""
    book = [*held, *new]
    delta, vega = net_greek(book, greeks, "delta"), net_greek(book, greeks, "vega")
    if delta is None or vega is None:
        return "greeks unknown for a leg; no trade"
    if abs(delta) > MAX_NET_DELTA:
        return f"net delta {delta:,.0f} over {MAX_NET_DELTA:,.0f}"
    if abs(vega) > MAX_NET_VEGA:
        return f"net vega ₹{vega:,.0f} over ₹{MAX_NET_VEGA:,.0f}"
    loss = stress_loss(book, spot)
    if loss > GAP_LIMIT_SHARE * capital:
        return f"a 5% gap would lose ₹{loss:,.0f}, over {GAP_LIMIT_SHARE:.0%} of capital"
    return None
