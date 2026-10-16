"""Prepare bounded chronological research plans; never evaluate or promote rules.

Pattern reference: Qlib 0.9.7 RollingGen (MIT), not copied implementation.
Unlike Qlib's moving test segment, our final holdout stays fixed and unused.
"""

from datetime import date

from packages.regime.candidate_registry import TrialManifest


def rolling_manifests(
    base: TrialManifest, sessions: list[date], *, step_sessions: int,
    purge_sessions: int, count: int,
) -> list[TrialManifest]:
    """Slide train/validation windows on supplied sessions, preserving holdout.

    All six base endpoints must be exact supplied session dates. Purge is an
    explicit minimum gap at both boundaries, not a guarantee that variable
    holding-period labels are isolated. The evaluator must exclude crossing
    trades separately. No external calendar, data, database or network access.
    """
    for value, minimum in ((step_sessions, 1), (purge_sessions, 0), (count, 1)):
        if type(value) is not int or value < minimum:
            raise ValueError("Rolling counts must be integers within their bounds")
    if count > base.declared_search_budget - base.trial_number + 1:
        raise ValueError("Rolling plan exceeds the declared remaining search budget")
    if not sessions or any(type(day) is not date for day in sessions):
        raise ValueError("Use exact session dates")
    if any(a >= b for a, b in zip(sessions, sessions[1:], strict=False)):
        raise ValueError("Sessions must be unique and increasing")
    names = ("train_start", "train_end", "validation_start", "validation_end",
             "holdout_start", "holdout_end")
    indices = {day: index for index, day in enumerate(sessions)}
    try:
        start, end, val_start, val_end, hold_start, _ = (
            indices[getattr(base, name)] for name in names
        )
    except KeyError:
        raise ValueError("Every manifest endpoint must be a supplied session") from None
    if val_start - end - 1 < purge_sessions:
        raise ValueError("Training/validation gap is smaller than the requested purge")
    if val_end + (count - 1) * step_sessions + purge_sessions >= hold_start:
        raise ValueError("Rolling validation would enter the protected holdout gap")
    plans = []
    for offset in range(count):
        shift = offset * step_sessions
        changes = dict(zip(names[:4], (
            sessions[index + shift] for index in (start, end, val_start, val_end)
        ), strict=True))
        # Revalidate rather than model_copy(update=...), which skips validation.
        payload = base.model_dump() | changes | {"trial_number": base.trial_number + offset}
        plans.append(TrialManifest.model_validate(payload))
    return plans
