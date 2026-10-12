from decimal import Decimal

from packages.options.models import PayoffPoint, PayoffRequest, PayoffResult

_ZERO = Decimal(0)


def _pnl_at(request: PayoffRequest, spot: Decimal) -> Decimal:
    total = _ZERO
    for leg in request.legs:
        intrinsic = (
            max(spot - leg.strike, _ZERO)
            if leg.option_type == "CE"
            else max(leg.strike - spot, _ZERO)
        )
        total += (intrinsic - leg.entry_premium) * leg.quantity * request.multiplier
    return total


def _breakevens(request: PayoffRequest, knots: list[Decimal]) -> list[Decimal]:
    values = [_pnl_at(request, point) for point in knots]
    roots: set[Decimal] = {point for point, value in zip(knots, values, strict=True) if value == 0}
    for index in range(len(knots) - 1):
        left_value, right_value = values[index], values[index + 1]
        if left_value * right_value < 0:
            root = knots[index] - left_value * (knots[index + 1] - knots[index]) / (
                right_value - left_value
            )
            roots.add(root)
    tail_slope = (
        sum((leg.quantity for leg in request.legs if leg.option_type == "CE"), 0)
        * request.multiplier
    )
    if tail_slope != 0 and knots:
        tail_root = knots[-1] - values[-1] / tail_slope
        if tail_root > knots[-1]:
            roots.add(tail_root)
    return sorted(roots)


def calculate_expiry_payoff(request: PayoffRequest) -> PayoffResult:
    strikes = sorted({leg.strike for leg in request.legs})
    knots = sorted({_ZERO, *strikes})
    knot_values = [_pnl_at(request, point) for point in knots]
    tail_slope = (
        sum((leg.quantity for leg in request.legs if leg.option_type == "CE"), 0)
        * request.multiplier
    )
    maximum_profit_unbounded = tail_slope > 0
    maximum_loss_unbounded = tail_slope < 0
    maximum_profit = None if maximum_profit_unbounded else max(knot_values)
    maximum_loss = None if maximum_loss_unbounded else max(_ZERO, -min(knot_values))

    step = (request.price_max - request.price_min) / Decimal(request.grid_points - 1)
    points = [
        PayoffPoint(
            underlying_price=request.price_min + step * index,
            profit_loss=_pnl_at(request, request.price_min + step * index),
        )
        for index in range(request.grid_points)
    ]
    return PayoffResult(
        underlying=request.underlying,
        multiplier=request.multiplier,
        breakeven_prices=_breakevens(request, knots),
        maximum_profit=maximum_profit,
        maximum_profit_unbounded=maximum_profit_unbounded,
        maximum_loss=maximum_loss,
        maximum_loss_unbounded=maximum_loss_unbounded,
        points=points,
        assumptions=[
            "Expiry payoff only; profit/loss equals intrinsic value less entry premium.",
            "Quantities are signed lots; multiplier is caller-supplied units per lot.",
            "Commissions, taxes, slippage, margin, and early exits are excluded.",
            "Maximum P&L considers all non-negative prices; curve points use the requested range.",
        ],
    )
