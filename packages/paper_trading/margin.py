"""SPAN-style scenario margin for NIFTY futures and options paper positions.

This follows the shape of NSE's SPAN method (16 price/volatility scenarios, a short
option minimum charge, plus exposure margin) using configurable parameters and our own
Black-Scholes repricing. It does not read exchange SPAN files or a broker margin API, so
it is an estimate to size simulated cash, never a broker requirement.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from packages.options.pricing import _black_scholes_price

MARGIN_MODEL_ID = "nifty-span-style-scenarios-v1"
MARGIN_DISCLAIMER = (
    "Estimated margin: SPAN-style price and volatility scenarios plus exposure margin, "
    "using configured parameters rather than NSE's daily SPAN file. Not a broker "
    "requirement; real margin can be higher, especially near expiry or in volatile sessions."
)
FALLBACK_VOLATILITY_PCT = Decimal(20)
_MIN_VOLATILITY = 0.01
_YEAR_SECONDS = 31_536_000.0
_EXPIRY_CLOSE = time(15, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
_CENT = Decimal("0.01")
# Fractions of the price scan range in the 14 regular scenarios; each runs with
# volatility up and down. Two extreme moves add at a reduced weight.
_PRICE_FRACTIONS = (0.0, 1 / 3, -1 / 3, 2 / 3, -2 / 3, 1.0, -1.0)

Instrument = Literal["FUT", "CE", "PE"]


@dataclass(frozen=True)
class MarginParameters:
    price_scan_pct: Decimal
    volatility_scan_points: Decimal
    exposure_pct: Decimal
    short_option_minimum_pct: Decimal
    extreme_move_multiple: Decimal = Decimal(2)
    extreme_move_weight: Decimal = Decimal("0.35")

    def snapshot(self) -> dict[str, str]:
        return {
            "price_scan_pct": str(self.price_scan_pct),
            "volatility_scan_points": str(self.volatility_scan_points),
            "exposure_pct": str(self.exposure_pct),
            "short_option_minimum_pct": str(self.short_option_minimum_pct),
            "extreme_move_multiple": str(self.extreme_move_multiple),
            "extreme_move_weight": str(self.extreme_move_weight),
        }


@dataclass(frozen=True)
class MarginLeg:
    """One position leg; quantity is signed (+ long, - short) in units."""

    instrument: Instrument
    quantity_units: int
    underlying_price: Decimal
    strike: Decimal | None = None
    years_to_expiry: float = 0.0
    volatility_pct: Decimal | None = None


@dataclass(frozen=True)
class MarginEstimate:
    scan_risk: Decimal
    short_option_minimum: Decimal
    net_option_value: Decimal
    exposure: Decimal
    total: Decimal
    worst_scenario: str
    parameters: MarginParameters

    @property
    def span(self) -> Decimal:
        """Risk requirement less long option value already paid (plus short value held)."""
        return max(
            Decimal(0), max(self.scan_risk, self.short_option_minimum) - self.net_option_value
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "model_id": MARGIN_MODEL_ID,
            "scan_risk": str(self.scan_risk),
            "short_option_minimum": str(self.short_option_minimum),
            "net_option_value": str(self.net_option_value),
            "span": str(self.span),
            "exposure": str(self.exposure),
            "total": str(self.total),
            "worst_scenario": self.worst_scenario,
            "parameters": self.parameters.snapshot(),
            "disclaimer": MARGIN_DISCLAIMER,
        }


def money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def years_to_expiry(expiry: date, now: datetime | None = None) -> float:
    """Years from now to the NSE close on the expiry date; zero once it has passed."""
    moment = now or datetime.now(timezone.utc)
    close = datetime.combine(expiry, _EXPIRY_CLOSE)
    return max(0.0, (close - moment).total_seconds() / _YEAR_SECONDS)


def _leg_value(leg: MarginLeg, move: float, volatility_shift: float) -> float:
    price = float(leg.underlying_price) * (1.0 + move)
    if leg.instrument == "FUT":
        return leg.quantity_units * price
    if leg.strike is None or leg.volatility_pct is None:
        raise ValueError("Option margin legs need a strike and a volatility")
    volatility = max(_MIN_VOLATILITY, float(leg.volatility_pct) / 100 + volatility_shift)
    premium = _black_scholes_price(
        leg.instrument, price, float(leg.strike), leg.years_to_expiry, volatility, 0.0, 0.0
    )
    return leg.quantity_units * premium


def estimate_position_margin(
    legs: list[MarginLeg], parameters: MarginParameters
) -> MarginEstimate:
    """SPAN-style requirement plus exposure margin.

    As in SPAN, the larger of the worst scenario loss and the short option minimum is
    reduced by the net option value: long premium is already paid, while the value of
    short options stays held against the position.
    """
    if not legs:
        raise ValueError("Margin needs at least one leg")
    for leg in legs:
        if leg.underlying_price <= 0 or leg.quantity_units == 0:
            raise ValueError("Margin legs need a positive price and a non-zero quantity")
    scan = float(parameters.price_scan_pct) / 100
    vol_scan = float(parameters.volatility_scan_points) / 100
    base = sum(_leg_value(leg, 0.0, 0.0) for leg in legs)

    scenarios: list[tuple[str, float]] = []
    for fraction in _PRICE_FRACTIONS:
        for direction, shift in (("up", vol_scan), ("down", -vol_scan)):
            value = sum(_leg_value(leg, fraction * scan, shift) for leg in legs)
            label = f"price {fraction * scan:+.2%}, volatility {direction}"
            scenarios.append((label, base - value))
    extreme = float(parameters.extreme_move_multiple) * scan
    weight = float(parameters.extreme_move_weight)
    for move in (extreme, -extreme):
        value = sum(_leg_value(leg, move, 0.0) for leg in legs)
        label = f"extreme price {move:+.2%} at {weight:.0%} weight"
        scenarios.append((label, (base - value) * weight))
    worst_label, worst_loss = max(scenarios, key=lambda item: item[1])

    options = [leg for leg in legs if leg.instrument != "FUT"]
    net_short_units = max(0, -sum(leg.quantity_units for leg in options))
    reference = max((leg.underlying_price for leg in options), default=Decimal(0))
    short_minimum = money(
        reference * net_short_units * parameters.short_option_minimum_pct / Decimal(100)
    )
    exposed_notional = sum(
        (
            leg.underlying_price * abs(leg.quantity_units)
            for leg in legs
            if leg.instrument == "FUT" or leg.quantity_units < 0
        ),
        Decimal(0),
    )
    exposure = money(exposed_notional * parameters.exposure_pct / Decimal(100))
    scan_risk = money(Decimal(str(max(0.0, worst_loss))))
    net_option_value = money(Decimal(str(sum(_leg_value(leg, 0.0, 0.0) for leg in options))))
    span = max(Decimal(0), max(scan_risk, short_minimum) - net_option_value)
    return MarginEstimate(
        scan_risk=scan_risk,
        short_option_minimum=short_minimum,
        net_option_value=net_option_value,
        exposure=exposure,
        total=money(span + exposure),
        worst_scenario=worst_label if worst_loss > 0 else "no scenario loss",
        parameters=parameters,
    )


def futures_margin(
    price: Decimal, quantity_units: int, parameters: MarginParameters
) -> MarginEstimate:
    """Margin for an outright futures position; direction does not change the amount."""
    if price <= 0 or quantity_units <= 0:
        raise ValueError("Margin needs a positive price and quantity")
    return estimate_position_margin(
        [MarginLeg(instrument="FUT", quantity_units=quantity_units, underlying_price=price)],
        parameters,
    )
