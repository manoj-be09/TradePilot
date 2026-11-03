"""One error budget for every forward test the loop will ever run.

Backtests only reject ideas, so they spend nothing. A forward test is fresh evidence, but running
many and promoting the best is its own search, so each forward hypothesis gets a slice of a single
5% family-wise budget the moment it enters the shadow stage: the k-th takes 1% of what the first
k-1 left. The slices sum to under 5% however many follow, so the chance that any promotion is a
false one stays below 5% (a union bound over always-valid tests). Early ideas get the most room;
the bar tightens as the search grows, so testing more ideas cannot buy a promotion.
"""

FAMILY_WISE_ALPHA = 0.05
SPEND_SHARE = 0.01


def alpha_for(sequence: int) -> float:
    """The slice for the hypothesis that entered the shadow stage `sequence`-th (from 1)."""
    if sequence < 1:
        raise ValueError("Budget sequence numbers start at 1")
    return FAMILY_WISE_ALPHA * SPEND_SHARE * (1 - SPEND_SHARE) ** (sequence - 1)


def remaining_after(spent: int) -> float:
    """Budget left once `spent` hypotheses have taken their slices."""
    return FAMILY_WISE_ALPHA * (1 - SPEND_SHARE) ** spent
