"""Always-valid evidence for a rule's average trade, safe to check every night.

A betting e-process: each trade either grows or shrinks a stake, and the stake's size is the
evidence. Under the null hypothesis the stake is a supermartingale, so by Ville's inequality the
chance it ever reaches 1/alpha, however often it is checked and whenever the check stops, is at most
alpha. A fixed t-test is only valid at one sample size chosen in advance.

Every rule here is defined risk, so each trade's worst loss and best gain are known at entry. The
bet on a trade is scaled by those bounds, which keeps the stake positive without any assumption
about the shape of returns. The bet size follows the running mean and variance seen so far
(aGRAPA, Waudby-Smith and Ramdas 2023), using only earlier trades.
"""

from collections.abc import Sequence
from dataclasses import dataclass

# At most half the stake is ever at risk on one trade, so a single loss cannot end the test.
MAX_BET = 0.5
# A weak prior: zero mean, the variance of a fair coin on the scaled range, worth one trade.
_PRIOR_MEAN, _PRIOR_VARIANCE, _PRIOR_WEIGHT = 0.0, 0.25, 1.0


@dataclass(frozen=True)
class Trade:
    net_pnl: float
    # Largest possible loss and gain of this trade, both positive, known when it was opened.
    max_loss: float
    max_gain: float

    def __post_init__(self) -> None:
        if not (self.max_loss > 0 and self.max_gain > 0):
            raise ValueError("A trade needs positive loss and gain bounds")
        if not -self.max_loss - 1e-6 <= self.net_pnl <= self.max_gain + 1e-6:
            raise ValueError("A trade's result lies outside its own bounds")


def _process(scaled: Sequence[float]) -> float:
    """Stake after betting on each value in [-1, ...]; null: every value has mean at most 0."""
    stake = 1.0
    total, count = _PRIOR_MEAN * _PRIOR_WEIGHT, _PRIOR_WEIGHT
    variance_sum = _PRIOR_VARIANCE * _PRIOR_WEIGHT
    for value in scaled:
        mean = total / count
        variance = variance_sum / count
        bet = min(MAX_BET, max(0.0, mean / (variance + mean * mean)))
        stake *= 1.0 + bet * max(value, -1.0)
        total += value
        count += 1.0
        variance_sum += (value - mean) ** 2
    return stake


def growth_evidence(trades: Sequence[Trade]) -> float:
    """Evidence against "the average trade loses or breaks even"; large means a real edge."""
    return _process([trade.net_pnl / trade.max_loss for trade in trades])


def futility_evidence(trades: Sequence[Trade]) -> float:
    """Evidence against "the average trade gains or breaks even"; large means it loses money."""
    return _process([-trade.net_pnl / trade.max_gain for trade in trades])
