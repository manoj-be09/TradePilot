"""The backtest screen: it can only reject a hypothesis, never promote one. Simulated only.

A new machine or human idea runs on the sessions before it was frozen. It is rejected, without
spending any of the error budget, when the backtest shows it is very likely noise:

- too few trades to judge;
- purged folds: the sessions split into five contiguous blocks, one session dropped at each edge
  so a night hold never leaks across, and the average must be positive in at least four;
- the deflated Sharpe ratio (Bailey and López de Prado): the chance its Sharpe beats the best of
  the family's trials by luck, after skew and fat tails, must reach 95%;
- a placebo: the same number of trades drawn at random from its baseline (the trade taken every
  day) must beat it less than 5% of the time;
- the probability of backtest overfitting (combinatorially symmetric cross-validation) across a
  batch of variants, used by the idea generator before it keeps the batch's best.

Passing proves nothing; only forward evidence moves a hypothesis.
"""

import random
from collections.abc import Sequence
from datetime import date
from itertools import combinations
from math import e, log, sqrt
from statistics import NormalDist, mean, pstdev

MIN_BACKTEST_TRADES = 30
FOLDS = 5
FOLDS_POSITIVE = 4
EMBARGO_SESSIONS = 1
DSR_BAR = 0.95
PLACEBO_DRAWS = 2_000
PLACEBO_P = 0.05
PBO_BLOCKS = 8
PBO_BAR = 0.5
_NORMAL = NormalDist()
_EULER_GAMMA = 0.5772156649


def purged_folds(trades: Sequence[tuple[date, float]], sessions: Sequence[date]) -> list[float]:
    """Each fold's average, dropping trades on the sessions that touch a fold edge."""
    ordered = sorted(sessions)
    size = len(ordered) // FOLDS
    averages = []
    for fold in range(FOLDS):
        block = ordered[fold * size:(fold + 1) * size if fold < FOLDS - 1 else len(ordered)]
        inner = set(block[EMBARGO_SESSIONS:len(block) - EMBARGO_SESSIONS])
        values = [value for day, value in trades if day in inner]
        if values:
            averages.append(mean(values))
    return averages


def _moments(values: Sequence[float]) -> tuple[float, float, float]:
    centre, spread = mean(values), pstdev(values)
    if spread == 0:
        return centre, 0.0, 3.0
    skew = mean(((value - centre) / spread) ** 3 for value in values)
    kurt = mean(((value - centre) / spread) ** 4 for value in values)
    return centre / spread, skew, kurt


def deflated_sharpe(values: Sequence[float], trials: int) -> float:
    """Probability the per-trade Sharpe beats the expected best of `trials` noise strategies."""
    sharpe, skew, kurt = _moments(values)
    count = len(values)
    if count < 3:
        return 0.0
    # Under noise each trial's Sharpe has variance about 1/T.
    spread = sqrt(1 / (count - 1))
    best = 0.0 if trials <= 1 else spread * (
        (1 - _EULER_GAMMA) * _NORMAL.inv_cdf(1 - 1 / trials)
        + _EULER_GAMMA * _NORMAL.inv_cdf(1 - 1 / (trials * e)))
    denominator = 1 - skew * sharpe + (kurt - 1) / 4 * sharpe ** 2
    if denominator <= 0:
        return 0.0
    return _NORMAL.cdf((sharpe - best) * sqrt(count - 1) / sqrt(denominator))


def placebo_p(values: Sequence[float], baseline: Sequence[float], seed: int = 0) -> float:
    """Share of random same-size draws from the baseline whose average is at least as good."""
    if not baseline or len(values) > len(baseline):
        return 1.0
    # A reproducible placebo draw, not security.
    rng = random.Random(seed)  # nosec B311
    target = mean(values)
    wins = sum(mean(rng.sample(list(baseline), len(values))) >= target
               for _ in range(PLACEBO_DRAWS))
    return (wins + 1) / (PLACEBO_DRAWS + 1)


def overfit_probability(matrix: Sequence[Sequence[float]]) -> float:
    """CSCV: rows are variants, columns the same sessions' results (0 on no trade).

    Over every half of the session blocks, pick the best variant in-sample; the share of splits
    where it lands in the bottom half out-of-sample is the probability of overfitting.
    """
    if len(matrix) < 2 or len(matrix[0]) < PBO_BLOCKS:
        return 1.0
    width = len(matrix[0]) // PBO_BLOCKS
    blocks = [[sum(row[block * width:(block + 1) * width]) for block in range(PBO_BLOCKS)]
              for row in matrix]
    lost = splits = 0
    for chosen in combinations(range(PBO_BLOCKS), PBO_BLOCKS // 2):
        inside = [sum(row[index] for index in chosen) for row in blocks]
        outside = [sum(row) - value for row, value in zip(blocks, inside, strict=True)]
        best = max(range(len(matrix)), key=inside.__getitem__)
        rank = sum(value <= outside[best] for value in outside) / (len(matrix) + 1)
        splits += 1
        lost += log(rank / (1 - rank)) <= 0
    return lost / splits


def screen(trades: Sequence[tuple[date, float]], sessions: Sequence[date], trials: int,
           baseline: Sequence[float] | None = None) -> list[str]:
    """Reasons to reject, empty when the backtest does not rule the idea out."""
    values = [value for _, value in trades]
    if len(values) < MIN_BACKTEST_TRADES:
        return [f"{len(values)} backtest trades, fewer than {MIN_BACKTEST_TRADES}"]
    reasons = []
    folds = purged_folds(trades, sessions)
    if sum(value > 0 for value in folds) < FOLDS_POSITIVE:
        reasons.append(f"positive in {sum(value > 0 for value in folds)} of {len(folds)} folds")
    dsr = deflated_sharpe(values, trials)
    if dsr < DSR_BAR:
        reasons.append(f"deflated Sharpe {dsr:.2f} against {trials} trial(s)")
    if baseline is not None and (p := placebo_p(values, baseline)) >= PLACEBO_P:
        reasons.append(f"random baseline days do as well (p {p:.3f})")
    return reasons
