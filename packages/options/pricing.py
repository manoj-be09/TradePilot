from datetime import timezone
from decimal import Decimal
from math import erf, exp, log, pi, sqrt

from packages.options.models import OptionGreeksInput, OptionGreeksResult

_YEAR_SECONDS = Decimal("31536000")


def _cdf(value: float) -> float:
    return (1.0 + erf(value / sqrt(2.0))) / 2.0


def _pdf(value: float) -> float:
    return exp(-(value**2) / 2.0) / sqrt(2.0 * pi)


def _black_scholes_price(
    option_type: str,
    spot: float,
    strike: float,
    years: float,
    volatility: float,
    rate: float,
    dividend_yield: float,
) -> float:
    if years <= 0:
        intrinsic = max(spot - strike, 0.0) if option_type == "CE" else max(strike - spot, 0.0)
        return intrinsic
    root_time = sqrt(years)
    d1 = (log(spot / strike) + (rate - dividend_yield + volatility**2 / 2) * years) / (
        volatility * root_time
    )
    d2 = d1 - volatility * root_time
    spot_discount = exp(-dividend_yield * years)
    strike_discount = exp(-rate * years)
    if option_type == "CE":
        return spot * spot_discount * _cdf(d1) - strike * strike_discount * _cdf(d2)
    return strike * strike_discount * _cdf(-d2) - spot * spot_discount * _cdf(-d1)


def _implied_volatility(
    option_type: str,
    market_price: float,
    spot: float,
    strike: float,
    years: float,
    rate: float,
    dividend_yield: float,
) -> float | None:
    if years <= 0:
        return None
    lower = 1e-6
    upper = 5.0
    low_price = _black_scholes_price(option_type, spot, strike, years, lower, rate, dividend_yield)
    high_price = _black_scholes_price(option_type, spot, strike, years, upper, rate, dividend_yield)
    if market_price < low_price - 1e-8 or market_price > high_price + 1e-8:
        return None
    for _ in range(100):
        middle = (lower + upper) / 2
        price = _black_scholes_price(option_type, spot, strike, years, middle, rate, dividend_yield)
        if abs(price - market_price) < 1e-8:
            return middle
        if price < market_price:
            lower = middle
        else:
            upper = middle
    return (lower + upper) / 2


def calculate_greeks(request: OptionGreeksInput) -> OptionGreeksResult:
    valuation = request.valuation_time.astimezone(timezone.utc)
    expiration = request.expiration_time.astimezone(timezone.utc)
    years = max(0.0, float((expiration - valuation).total_seconds()) / float(_YEAR_SECONDS))
    spot = float(request.underlying_price)
    strike = float(request.strike)
    volatility = float(request.volatility)
    rate = float(request.risk_free_rate)
    dividend_yield = float(request.dividend_yield)
    price = _black_scholes_price(
        request.option_type, spot, strike, years, volatility, rate, dividend_yield
    )
    implied_volatility = (
        _implied_volatility(
            request.option_type,
            float(request.market_price),
            spot,
            strike,
            years,
            rate,
            dividend_yield,
        )
        if request.market_price is not None
        else None
    )

    delta = gamma = theta_per_day = vega_per_point = rho_per_point = None
    if years > 0:
        root_time = sqrt(years)
        d1 = (log(spot / strike) + (rate - dividend_yield + volatility**2 / 2) * years) / (
            volatility * root_time
        )
        d2 = d1 - volatility * root_time
        spot_discount = exp(-dividend_yield * years)
        strike_discount = exp(-rate * years)
        pdf = _pdf(d1)
        gamma = spot_discount * pdf / (spot * volatility * root_time)
        vega = spot * spot_discount * pdf * root_time
        vega_per_point = vega / 100
        if request.option_type == "CE":
            delta = spot_discount * _cdf(d1)
            theta_annual = (
                -spot * spot_discount * pdf * volatility / (2 * root_time)
                - rate * strike * strike_discount * _cdf(d2)
                + dividend_yield * spot * spot_discount * _cdf(d1)
            )
            rho_per_point = strike * years * strike_discount * _cdf(d2) / 100
        else:
            delta = spot_discount * (_cdf(d1) - 1)
            theta_annual = (
                -spot * spot_discount * pdf * volatility / (2 * root_time)
                + rate * strike * strike_discount * _cdf(-d2)
                - dividend_yield * spot * spot_discount * _cdf(-d1)
            )
            rho_per_point = -strike * years * strike_discount * _cdf(-d2) / 100
        theta_per_day = theta_annual / 365

    iv_status = (
        "not_requested"
        if request.market_price is None
        else "solved"
        if implied_volatility is not None
        else "market_price_outside_model_bounds_or_expired"
    )
    return OptionGreeksResult(
        option_type=request.option_type,
        model_price=Decimal(str(price)),
        delta=Decimal(str(delta)) if delta is not None else None,
        gamma=Decimal(str(gamma)) if gamma is not None else None,
        theta_per_calendar_day=(Decimal(str(theta_per_day)) if theta_per_day is not None else None),
        vega_per_volatility_point=(
            Decimal(str(vega_per_point)) if vega_per_point is not None else None
        ),
        rho_per_rate_point=Decimal(str(rho_per_point)) if rho_per_point is not None else None,
        implied_volatility=(
            Decimal(str(implied_volatility)) if implied_volatility is not None else None
        ),
        implied_volatility_status=iv_status,
        assumptions=[
            "European Black-Scholes model with continuous risk-free rate and dividend yield.",
            "Volatility input is annualized decimal volatility; 0.20 means 20%.",
            "Vega and rho are per one percentage-point move; theta is per calendar day.",
            "Model values exclude brokerage, taxes, bid-ask spread, and early-exercise effects.",
        ],
    )
