"""Refuse forward tests that could not finish in a useful time, before they spend any budget.

With the betting e-process the log stake grows by about half the squared signal-to-noise ratio per
trade, so reaching 1/alpha takes about 2 ln(1/alpha) / (mean / sd)^2 trades. The mean and spread
are on the scale the bet uses, the trade's result over its worst loss.
"""

from dataclasses import dataclass
from math import log

MAX_SESSIONS = 125  # About six months of NSE sessions.


@dataclass(frozen=True)
class PowerCheck:
    trades_needed: float
    sessions_needed: float
    feasible: bool


def power_check(
    expected_mean: float, expected_sd: float, max_loss: float, trades_per_session: float,
    alpha: float, max_sessions: float = MAX_SESSIONS,
) -> PowerCheck:
    """Expected sample needed if the pre-registered effect is real; infeasible past max_sessions."""
    if expected_sd <= 0 or max_loss <= 0 or not 0 < trades_per_session <= 10 or not 0 < alpha < 1:
        raise ValueError("Pre-registration needs a positive spread, loss bound and trade rate")
    if expected_mean <= 0:
        return PowerCheck(float("inf"), float("inf"), False)
    signal = (expected_mean / max_loss) / (expected_sd / max_loss)
    trades = 2 * log(1 / alpha) / (signal * signal)
    sessions = trades / trades_per_session
    return PowerCheck(trades, sessions, sessions <= max_sessions)
