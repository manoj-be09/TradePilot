"""Cash intrinsic settlement for an expired NIFTY options paper position."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

_CENT = Decimal("0.01")


@dataclass(frozen=True)
class SettlementLeg:
    instrument_key: str
    option_type: str
    side: str
    strike: Decimal
    quantity_units: int
    intrinsic_value: Decimal
    cash_flow: Decimal


@dataclass(frozen=True)
class ExpirySettlement:
    legs: tuple[SettlementLeg, ...]
    settlement_cash_flow: Decimal
    realized_pnl: Decimal


def calculate_expiry_settlement(
    saved_legs: object, entry_cash_flow: Decimal, final_index_value: Decimal
) -> ExpirySettlement:
    """Value each long/short leg at intrinsic; no quote or broker fill is implied."""
    if (
        not final_index_value.is_finite()
        or final_index_value <= 0
        or final_index_value != final_index_value.quantize(_CENT)
        or not entry_cash_flow.is_finite()
        or not isinstance(saved_legs, list)
        or not saved_legs
    ):
        raise ValueError("Invalid expiry settlement inputs")
    results: list[SettlementLeg] = []
    total = Decimal(0)
    for raw in saved_legs:
        if not isinstance(raw, dict):
            raise ValueError("Saved option leg is malformed")
        leg: dict[str, Any] = raw
        option_type = leg.get("option_type")
        side = leg.get("side")
        instrument_key = leg.get("instrument_key")
        quantity = leg.get("quantity_units")
        try:
            strike = Decimal(str(leg.get("strike")))
        except (ValueError, ArithmeticError):
            raise ValueError("Saved option strike is invalid") from None
        if (
            option_type not in {"CE", "PE"}
            or side not in {"BUY", "SELL"}
            or not isinstance(instrument_key, str)
            or not instrument_key
            or not isinstance(quantity, int)
            or isinstance(quantity, bool)
            or quantity <= 0
            or not strike.is_finite()
            or strike <= 0
        ):
            raise ValueError("Saved option leg is invalid")
        intrinsic = max(
            (final_index_value - strike) if option_type == "CE" else
            (strike - final_index_value),
            Decimal(0),
        ).quantize(_CENT, rounding=ROUND_HALF_UP)
        cash_flow = (intrinsic * quantity * (1 if side == "BUY" else -1)).quantize(
            _CENT, rounding=ROUND_HALF_UP
        )
        total += cash_flow
        results.append(SettlementLeg(
            instrument_key=instrument_key,
            option_type=option_type,
            side=side,
            strike=strike,
            quantity_units=quantity,
            intrinsic_value=intrinsic,
            cash_flow=cash_flow,
        ))
    total = total.quantize(_CENT, rounding=ROUND_HALF_UP)
    return ExpirySettlement(
        legs=tuple(results),
        settlement_cash_flow=total,
        realized_pnl=(entry_cash_flow + total).quantize(_CENT, rounding=ROUND_HALF_UP),
    )
