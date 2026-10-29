"""Dated, versioned NSE F&O charge schedules for simulated fills and expiry exercise.

Every result carries the schedule id and whether all rates it used were verified from a
cited source. Never recompute stored results with a newer schedule; add a new schedule
with its own effective date instead. Components are rounded to paise per order
(ROUND_HALF_UP); brokers may round differently, so totals are estimates.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

_CENT = Decimal("0.01")
_LAKH = Decimal("100000")

Instrument = Literal["FUT", "OPT"]
Side = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class Rate:
    value: Decimal
    source: str
    verified: bool


@dataclass(frozen=True)
class SegmentRates:
    stt_sell: Rate  # fraction of turnover (premium for options)
    transaction: Rate
    ipft_per_lakh: Rate  # rupees per lakh of turnover
    stamp_buy: Rate


@dataclass(frozen=True)
class ChargeSchedule:
    schedule_id: str
    effective_from: date
    futures: SegmentRates
    options: SegmentRates
    exercise_stt: Rate  # fraction of settlement value of exercised long options
    sebi_per_crore: Rate
    gst: Rate


@dataclass(frozen=True)
class BrokeragePlan:
    plan_id: str
    option_per_order: Decimal
    future_per_order: Decimal
    future_pct_cap: Decimal  # brokerage is min(per-order, pct of turnover)


@dataclass(frozen=True)
class ChargeBreakdown:
    schedule_id: str
    plan_id: str
    verified: bool
    turnover: Decimal
    brokerage: Decimal
    stt: Decimal
    transaction: Decimal
    ipft: Decimal
    stamp: Decimal
    sebi: Decimal
    gst: Decimal

    @property
    def total(self) -> Decimal:
        return (self.brokerage + self.stt + self.transaction + self.ipft + self.stamp
                + self.sebi + self.gst)


UPSTOX_PAGE = "https://upstox.com/brokerage-charges/ (read 2026-10-27)"
REPO_REVIEW = "COST_MODEL.md public Upstox scenario (reviewed 2026-10-23)"
UNVERIFIED = "not verified against an official source; confirm before relying on it"


def _rate(value: str, source: str, verified: bool = True) -> Rate:
    return Rate(Decimal(value), source, verified)


SCHEDULE_2024_10 = ChargeSchedule(
    schedule_id="nse-fo-2024-10-01",
    effective_from=date(2024, 10, 1),
    futures=SegmentRates(
        stt_sell=_rate("0.0002", UPSTOX_PAGE),
        transaction=_rate("0.0000173", UPSTOX_PAGE),
        ipft_per_lakh=_rate("0.10", UPSTOX_PAGE),
        stamp_buy=_rate("0.00002", UPSTOX_PAGE),
    ),
    options=SegmentRates(
        stt_sell=_rate("0.001", UPSTOX_PAGE),
        transaction=_rate("0.0003503", UPSTOX_PAGE),
        ipft_per_lakh=_rate("0.50", UPSTOX_PAGE),
        stamp_buy=_rate("0.00003", UPSTOX_PAGE),
    ),
    exercise_stt=_rate("0.00125", UNVERIFIED, verified=False),
    sebi_per_crore=_rate("10", UPSTOX_PAGE),
    gst=_rate("0.18", UPSTOX_PAGE),
)

SCHEDULE_2026_04 = ChargeSchedule(
    schedule_id="nse-fo-2026-04-01",
    effective_from=date(2026, 4, 1),
    futures=SegmentRates(
        stt_sell=_rate("0.0005", UNVERIFIED, verified=False),
        transaction=_rate("0.0000173", UPSTOX_PAGE + "; 2026 revision unknown", verified=False),
        ipft_per_lakh=_rate("0.10", UPSTOX_PAGE),
        stamp_buy=_rate("0.00002", UPSTOX_PAGE),
    ),
    options=SegmentRates(
        stt_sell=_rate("0.0015", REPO_REVIEW),
        transaction=_rate("0.0003553", REPO_REVIEW),
        ipft_per_lakh=_rate("0.50", UPSTOX_PAGE),
        stamp_buy=_rate("0.00003", UPSTOX_PAGE),
    ),
    exercise_stt=_rate("0.00125", UNVERIFIED, verified=False),
    sebi_per_crore=_rate("10", UPSTOX_PAGE),
    gst=_rate("0.18", UPSTOX_PAGE),
)

SCHEDULES: tuple[ChargeSchedule, ...] = (SCHEDULE_2024_10, SCHEDULE_2026_04)

UPSTOX_STANDARD = BrokeragePlan(
    "upstox-standard", option_per_order=Decimal("20"), future_per_order=Decimal("20"),
    future_pct_cap=Decimal("0.0005"),
)
UPSTOX_PLUS = BrokeragePlan(
    "upstox-plus-max", option_per_order=Decimal("30"), future_per_order=Decimal("30"),
    future_pct_cap=Decimal("0.0005"),
)


def schedule_for(trade_date: date, schedules: Sequence[ChargeSchedule] = SCHEDULES
                 ) -> ChargeSchedule:
    eligible = [s for s in schedules if s.effective_from <= trade_date]
    if not eligible:
        raise ValueError(f"No charge schedule covers {trade_date}")
    return max(eligible, key=lambda s: s.effective_from)


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def order_charges(
    *,
    instrument: Instrument,
    side: Side,
    price: Decimal,
    quantity: int,
    trade_date: date,
    plan: BrokeragePlan = UPSTOX_STANDARD,
    schedule: ChargeSchedule | None = None,
) -> ChargeBreakdown:
    """Charges for one executed order. `quantity` is in units (lots × lot size)."""
    if price < 0 or quantity <= 0:
        raise ValueError("Price must be non-negative and quantity positive")
    sched = schedule or schedule_for(trade_date)
    rates = sched.futures if instrument == "FUT" else sched.options
    turnover = price * quantity
    if instrument == "FUT":
        brokerage = min(plan.future_per_order, _money(turnover * plan.future_pct_cap))
    else:
        brokerage = plan.option_per_order
    stt = _money(turnover * rates.stt_sell.value) if side == "SELL" else Decimal(0)
    stamp = _money(turnover * rates.stamp_buy.value) if side == "BUY" else Decimal(0)
    transaction = _money(turnover * rates.transaction.value)
    ipft = _money(turnover / _LAKH * rates.ipft_per_lakh.value)
    sebi = _money(turnover / Decimal("10000000") * sched.sebi_per_crore.value)
    gst = _money((brokerage + transaction + ipft) * sched.gst.value)
    used = [rates.transaction, rates.ipft_per_lakh, sched.sebi_per_crore, sched.gst,
            rates.stt_sell if side == "SELL" else rates.stamp_buy]
    return ChargeBreakdown(
        schedule_id=sched.schedule_id, plan_id=plan.plan_id,
        verified=all(rate.verified for rate in used),
        turnover=_money(turnover), brokerage=brokerage, stt=stt, transaction=transaction,
        ipft=ipft, stamp=stamp, sebi=sebi, gst=gst,
    )


def exercise_charges(
    *, settlement_price: Decimal, quantity: int, trade_date: date,
    schedule: ChargeSchedule | None = None,
) -> ChargeBreakdown:
    """STT on a long in-the-money option exercised at expiry (settlement value basis)."""
    if settlement_price < 0 or quantity <= 0:
        raise ValueError("Settlement price must be non-negative and quantity positive")
    sched = schedule or schedule_for(trade_date)
    value = settlement_price * quantity
    zero = Decimal(0)
    return ChargeBreakdown(
        schedule_id=sched.schedule_id, plan_id="exercise", verified=sched.exercise_stt.verified,
        turnover=_money(value), brokerage=zero, stt=_money(value * sched.exercise_stt.value),
        transaction=zero, ipft=zero, stamp=zero, sebi=zero, gst=zero,
    )
