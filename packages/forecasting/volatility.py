"""NIFTY realized-volatility forecasts from daily candles, scored as forecasts.

A day's variance is the overnight gap squared plus the Garman-Klass range estimate for the
session. The HAR model regresses the log of the next `horizon` days' mean variance on the logs of
the last 1, 5 and 22 days' means, optionally with the log of India VIX's implied daily variance.
Fits are ordinary least squares on data strictly before the forecast day, so a walk-forward score
never sees its own target. Research only: it places no trades and makes no claim about profit.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from math import exp, log, sqrt

Candle = tuple[float, float, float, float]  # open, high, low, close
TRADING_DAYS = 252


def daily_variance(candle: Candle, previous_close: float) -> float:
    """Gap squared plus the Garman-Klass session variance, never negative."""
    open_, high, low, close = candle
    gap = log(open_ / previous_close)
    session = 0.5 * log(high / low) ** 2 - (2 * log(2) - 1) * log(close / open_) ** 2
    return gap * gap + max(session, 0.0)


def variances(candles: Sequence[Candle]) -> list[float]:
    """One variance per candle after the first."""
    return [daily_variance(candles[at], candles[at - 1][3]) for at in range(1, len(candles))]


def har_row(past: Sequence[float], vix: float | None = None) -> list[float] | None:
    """Regressors for the day after `past` ends: 1, log mean of last 1, 5, 22 days (and VIX)."""
    if len(past) < 22:
        return None
    means = [sum(past[-n:]) / n for n in (1, 5, 22)]
    if min(means) <= 0:
        return None
    row = [1.0, *(log(value) for value in means)]
    if vix is not None:
        if vix <= 0:
            return None
        row.append(log(implied_daily_variance(vix)))
    return row


def implied_daily_variance(vix: float) -> float:
    return (vix / 100) ** 2 / TRADING_DAYS


def target(future: Sequence[float]) -> float | None:
    """Mean daily variance over the horizon; None if any day is zero (log undefined)."""
    if not future or min(future) <= 0:
        return None
    return sum(future) / len(future)


def solve(rows: Sequence[Sequence[float]], ys: Sequence[float]) -> list[float]:
    """Least squares by the normal equations and Gaussian elimination with pivoting."""
    k = len(rows[0])
    a = [[sum(r[i] * r[j] for r in rows) for j in range(k)] for i in range(k)]
    b = [sum(r[i] * y for r, y in zip(rows, ys, strict=True)) for i in range(k)]
    for col in range(k):
        pivot = max(range(col, k), key=lambda i: abs(a[i][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError("Regressors are collinear")
        a[col], a[pivot] = a[pivot], a[col]
        b[col], b[pivot] = b[pivot], b[col]
        for i in range(col + 1, k):
            factor = a[i][col] / a[col][col]
            for j in range(col, k):
                a[i][j] -= factor * a[col][j]
            b[i] -= factor * b[col]
    beta = [0.0] * k
    for i in reversed(range(k)):
        beta[i] = (b[i] - sum(a[i][j] * beta[j] for j in range(i + 1, k))) / a[i][i]
    return beta


@dataclass(frozen=True)
class LogModel:
    """log(target) = beta . row; forecasts in variance units with the lognormal bias fix."""

    beta: tuple[float, ...]
    residual_variance: float

    @classmethod
    def fit(cls, rows: Sequence[Sequence[float]], targets: Sequence[float]) -> "LogModel":
        ys = [log(value) for value in targets]
        beta = solve(rows, ys)
        residuals = [y - sum(b * x for b, x in zip(beta, r, strict=True))
                     for r, y in zip(rows, ys, strict=True)]
        dof = max(len(ys) - len(beta), 1)
        return cls(tuple(beta), sum(e * e for e in residuals) / dof)

    def forecast(self, row: Sequence[float]) -> float:
        return exp(sum(b * x for b, x in zip(self.beta, row, strict=True))
                   + self.residual_variance / 2)


def qlike(actual: float, forecast: float) -> float:
    """Patton's robust loss for variance forecasts; zero when the forecast is exact."""
    ratio = actual / forecast
    return ratio - log(ratio) - 1


def newey_west_t(values: Sequence[float], lags: int) -> float | None:
    """t of the mean with Bartlett-weighted autocovariances, for overlapping targets."""
    n = len(values)
    if n < lags + 2:
        return None
    mean = sum(values) / n
    centered = [v - mean for v in values]
    variance = sum(c * c for c in centered) / n
    for lag in range(1, lags + 1):
        cov = sum(centered[i] * centered[i - lag] for i in range(lag, n)) / n
        variance += 2 * (1 - lag / (lags + 1)) * cov
    if variance <= 0:
        return None
    return mean / sqrt(variance / n)
