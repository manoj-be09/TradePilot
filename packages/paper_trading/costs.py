"""Versioned illustrative reporting charges; never changes fills, cash or exit decisions."""
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

MODEL_ID = "option-premium-turnover-illustrative-v1"
RATE = Decimal("0.001")
CENT = Decimal("0.01")
DISCLAIMER = (
    "Illustrative 0.10% per filled leg on absolute premium turnover, rounded per leg. "
    "Not a broker tariff: excludes taxes, statutory/exchange charges, fixed brokerage, "
    "market impact and additional slippage. Spread is already reflected in simulated fills. "
    "Reporting only; paper cash and risk rules are unchanged."
)


def cost_snapshot(fills: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Pin the assumptions and leg-level amounts at the time of a new order."""
    if not fills:
        raise ValueError("Costs require fills")
    legs = []
    total = Decimal(0)
    for fill in fills:
        try:
            price = Decimal(str(fill["fill_price"]))
            units = Decimal(str(fill["quantity_units"]))
        except (KeyError, InvalidOperation, ValueError) as error:
            raise ValueError("Invalid cost inputs") from error
        if (not price.is_finite() or price < 0 or not units.is_finite()
                or units <= 0 or units != units.to_integral_value()):
            raise ValueError("Invalid cost inputs")
        turnover = price * units
        charge = (turnover * RATE).quantize(CENT, rounding=ROUND_HALF_UP)
        total += charge
        legs.append({"instrument_key": str(fill.get("instrument_key", "")),
                     "quantity_units": str(units), "fill_price": str(price),
                     "premium_turnover": str(turnover), "modeled_charge": str(charge)})
    return {"model_id": MODEL_ID, "currency": "INR", "rate": str(RATE),
            "basis": "absolute_premium_turnover_per_leg", "rounding": "HALF_UP_PER_LEG_0.01",
            "total_charge": str(total.quantize(CENT)), "legs": legs,
            "complete_broker_costs": False, "disclaimer": DISCLAIMER}


def summarize_costs(status: str, realized_pnl: Decimal | None,
                    orders: Sequence[Any]) -> dict[str, Any]:
    """Never reconstruct missing old snapshots or call a partial model all-in costs."""
    valid = []
    for order in orders:
        snapshot = order.fill_snapshot if isinstance(order.fill_snapshot, dict) else {}
        saved = snapshot.get("modeled_costs")
        try:
            expected = cost_snapshot(snapshot.get("fills", []))
        except (ValueError, TypeError, InvalidOperation):
            continue
        if order.status == "EXECUTED" and saved == expected:
            valid.append(order)
    complete = (status == "CLOSED" and len(orders) == len(valid) == 2
                and {order.action for order in valid} == {"OPEN", "CLOSE"})
    total = sum((Decimal(order.fill_snapshot["modeled_costs"]["total_charge"])
                 for order in valid), Decimal(0))
    flows = [order.net_cash_flow for order in orders]
    reconciled = (realized_pnl is not None and realized_pnl.is_finite()
                  and all(flow.is_finite() for flow in flows)
                  and sum(flows, Decimal(0)).quantize(CENT) == realized_pnl.quantize(CENT))
    adjusted = ((realized_pnl - total).quantize(CENT, rounding=ROUND_HALF_UP)
                if complete and reconciled and realized_pnl is not None else None)
    return {"coverage": "COMPLETE" if complete else "PARTIAL" if valid else "MISSING",
            "model_id": MODEL_ID, "orders_with_costs": len(valid), "order_count": len(orders),
            "recorded_modeled_charges": str(total.quantize(CENT)) if valid else None,
            "pnl_after_modeled_charges": str(adjusted) if adjusted is not None else None,
            "complete_broker_costs": False, "disclaimer": DISCLAIMER}
