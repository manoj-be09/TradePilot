"""Public Upstox standard-plan NFO option charge scenario, never account cash."""

from decimal import ROUND_HALF_UP, Decimal
from typing import Sequence

from packages.options.models import PayoffRequest

MODEL_ID = "upstox-public-nfo-options-2026-10-scenario-v1"
SOURCE_URL = "https://upstox.com/brokerage-charges/"
_CENT = Decimal("0.01")
_BROKERAGE = Decimal("20")  # One separate executed order per leg; Plus/promos may differ.
_STT_SELL = Decimal("0.0015")  # 0.15% of option premium from 1 April 2026.
_TRANSACTION = Decimal("0.0003553")  # NSE options from 1 March 2026.
_STAMP_BUY = Decimal("0.00003")
_SEBI = Decimal("0.000001")  # Rs10 per crore.
_GST = Decimal("0.18")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _order_cost(turnover: Decimal, side: str) -> Decimal:
    transaction = _money(turnover * _TRANSACTION)
    stt = _money(turnover * _STT_SELL) if side == "SELL" else Decimal(0)
    stamp = _money(turnover * _STAMP_BUY) if side == "BUY" else Decimal(0)
    sebi = _money(turnover * _SEBI)
    gst = _money((_BROKERAGE + transaction) * _GST)
    return _BROKERAGE + transaction + stt + stamp + sebi + gst


def upstox_option_cost_scenario(request: PayoffRequest) -> dict[str, object] | None:
    """Estimate entry and unchanged-premium exit charges for caller-supplied lots.

    The exit side reverses the entry side. Actual exit prices, account plan,
    execution grouping and expiry exercise fees are unknown here.
    """
    if (
        request.underlying != "NIFTY"
        or request.multiplier != request.multiplier.to_integral_value()
    ):
        return None
    entry = exit_ = Decimal(0)
    for leg in request.legs:
        turnover = abs(leg.quantity) * request.multiplier * leg.entry_premium
        side = "BUY" if leg.quantity > 0 else "SELL"
        entry += _order_cost(turnover, side)
        exit_ += _order_cost(turnover, "SELL" if side == "BUY" else "BUY")
    return {
        "model_id": MODEL_ID,
        "source_url": SOURCE_URL,
        "entry_charge": str(_money(entry)),
        "unchanged_premium_exit_charge": str(_money(exit_)),
        "round_trip_scenario_charge": str(_money(entry + exit_)),
        "currency": "INR",
        "assumed_brokerage_per_separate_leg_order": str(_BROKERAGE),
        "complete_broker_costs": False,
        "limitations": (
            "Public standard-plan benchmark as reviewed 2026-10-23; account plan, "
            "promotions, multi-leg discounts and exact tax rounding are unverified. "
            "Exit assumes unchanged premiums; exercise/settlement, slippage and "
            "financing are excluded. Reporting only, not a paper cash charge or "
            "an after-cost profit forecast."
        ),
    }


def upstox_option_two_price_scenario(
    request: PayoffRequest, exit_premiums: Sequence[Decimal],
) -> dict[str, object] | None:
    """Illustrate a round trip at supplied prices; do not treat prices as fills.

    Prices have no quote-side, book-time, size, rights or policy provenance here.
    This calculation is excluded from qualified ranking and paper cash.
    """
    if (
        request.underlying != "NIFTY"
        or request.multiplier != request.multiplier.to_integral_value()
        or len(exit_premiums) != len(request.legs)
        or any(not price.is_finite() or price <= 0 for price in exit_premiums)
    ):
        return None
    entry_flow = exit_flow = entry_charge = exit_charge = Decimal(0)
    for leg, exit_price in zip(request.legs, exit_premiums, strict=True):
        if not leg.entry_premium.is_finite() or leg.entry_premium <= 0:
            return None
        units = abs(leg.quantity) * request.multiplier
        entry_turnover = _money(units * leg.entry_premium)
        exit_turnover = _money(units * exit_price)
        entry_side = "BUY" if leg.quantity > 0 else "SELL"
        exit_side = "SELL" if entry_side == "BUY" else "BUY"
        entry_flow += -entry_turnover if entry_side == "BUY" else entry_turnover
        exit_flow += -exit_turnover if exit_side == "BUY" else exit_turnover
        entry_charge += _order_cost(entry_turnover, entry_side)
        exit_charge += _order_cost(exit_turnover, exit_side)
    gross = _money(entry_flow + exit_flow)
    charges = _money(entry_charge + exit_charge)
    return {
        "model_id": MODEL_ID,
        "source_url": SOURCE_URL,
        "evidence_origin": "SUPPLIED_PRICE_SCENARIO",
        "entry_charge": str(_money(entry_charge)),
        "exit_charge": str(_money(exit_charge)),
        "round_trip_scenario_charge": str(charges),
        "gross_scenario_pnl": str(gross),
        "net_scenario_pnl": str(_money(gross - charges)),
        "currency": "INR",
        "complete_broker_costs": False,
        "promotion_eligible": False,
        "profit_probability": None,
        "limitations": (
            "Public standard-plan benchmark only; supplied prices are not verified "
            "bid/ask fills. Quote timing, displayed size, complete account charges, "
            "execution policy and source rights are unreviewed. Reporting only; "
            "never paper cash or a qualified after-cost ranking."
        ),
    }
