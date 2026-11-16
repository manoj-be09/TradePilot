"""The forecast pieces are exact on known inputs and never use the day they forecast."""

from math import exp, isclose, log

from packages.forecasting.volatility import (
    LogModel,
    daily_variance,
    har_row,
    implied_daily_variance,
    newey_west_t,
    qlike,
    solve,
)


def test_daily_variance_is_gap_squared_plus_garman_klass():
    flat = daily_variance((100.0, 100.0, 100.0, 100.0), 100.0)
    assert flat == 0.0
    gap_only = daily_variance((101.0, 101.0, 101.0, 101.0), 100.0)
    assert isclose(gap_only, log(1.01) ** 2)
    ranged = daily_variance((100.0, 102.0, 99.0, 100.0), 100.0)
    assert isclose(ranged, 0.5 * log(102 / 99) ** 2)


def test_solve_recovers_exact_coefficients():
    rows = [[1.0, x, x * x % 7] for x in range(1, 30)]
    ys = [2 - 0.5 * r[1] + 3 * r[2] for r in rows]
    beta = solve(rows, ys)
    assert all(isclose(a, b, abs_tol=1e-9) for a, b in zip(beta, [2, -0.5, 3], strict=True))


def test_log_model_forecast_matches_a_noise_free_fit():
    rows = [[1.0, log(v)] for v in (1e-4, 2e-4, 4e-4, 8e-4)]
    model = LogModel.fit(rows, [exp(0.1 + 0.9 * r[1]) for r in rows])
    assert isclose(model.residual_variance, 0, abs_tol=1e-18)
    assert isclose(model.forecast([1.0, log(3e-4)]), exp(0.1 + 0.9 * log(3e-4)))


def test_har_row_needs_22_days_and_uses_only_the_past():
    past = [1e-4] * 21
    assert har_row(past) is None
    row = har_row([*past, 4e-4], vix=16)
    assert row == [1.0, log(4e-4), log(sum([1e-4] * 4 + [4e-4]) / 5),
                   log((21e-4 + 4e-4) / 22), log(implied_daily_variance(16))]


def test_qlike_is_zero_when_exact_and_newey_west_handles_constant_gain():
    assert qlike(2e-4, 2e-4) == 0 and qlike(1e-4, 2e-4) > 0
    assert newey_west_t([1.0] * 50, 5) is None
    assert newey_west_t([1.0, 2.0] * 50, 5) > 10
