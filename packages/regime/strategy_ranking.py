"""Versioned replay ordering with bounded, separately recorded paper feedback."""

from typing import Any

RANKING_VERSION = "profit-review-v6"
RANKING_METHOD = (
    "Ranking starts with historical replay. Verified closed live-paper outcomes adjust the score "
    "within the replay evidence tier; they are not added to replay trades or treated as "
    "independent validation. "
    "Candidates are grouped first: positive mean net rupee and R returns with 20+ closed trades; "
    "positive with fewer trades; non-positive; then unavailable. "
    "Within each group, score = n/(n+20) * [mean net R + "
    "0.25 * (smoothed historical profit probability - 0.5) - 0.10 * closed-trade drawdown in R] "
    "+ 0.10 * market-fit score / 4 + bounded paper feedback. R is net P&L divided "
    "by that trade's original "
    "modeled stop loss including costs, never its later trailing stop. "
    "The probability is a Beta(2,2)-smoothed historical replay win frequency, shown "
    "with sample depth; it is not a calibrated next-trade profit probability. "
    "Paper feedback uses only current-version automated close snapshots with original "
    "risk and exchange-timestamped quotes whose saved bid/ask reproduces paper P&L. "
    "Its adjustment is at most 0.2 in score and shrinks to near zero "
    "for tiny samples. Weights and the 20-trade threshold are design choices, not "
    "validated profit predictors. "
    "Three comparisons remain visible even if all results are weak. "
    "Current signal, quote, session and risk checks independently govern entries."
)


def paper_feedback(paper: dict[str, Any] | None) -> tuple[float, float | None, int]:
    """Return a bounded score adjustment from separately verified paper outcomes."""
    if not paper:
        return 0.0, None, 0
    n = int(paper["closed_trades"])
    average_r = paper.get("average_r")
    win_rate = paper.get("win_rate")
    if n <= 0 or average_r is None or win_rate is None or paper.get("risk_recorded_trades") != n:
        return 0.0, None, 0
    probability = ((float(win_rate) / 100 * n) + 2) / (n + 4)
    bounded_r = max(-1.0, min(1.0, float(average_r)))
    adjustment = n / (n + 20) * (0.15 * bounded_r + 0.10 * (probability - 0.5))
    return round(adjustment, 6), round(probability * 100, 2), n


def profit_review(
    evidence: dict[str, Any], fit_score: int, risk_budget: float,
    paper: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rank a single replay sample using each trade's original modeled risk."""
    if risk_budget <= 0:
        raise ValueError("Planned risk must be positive")
    adjustment, paper_probability, paper_count = paper_feedback(paper)
    n = int(evidence["closed_trades"])
    average = evidence["average_net_pnl"]
    win_rate = evidence["win_rate"]
    expectancy_r = evidence.get("average_r")
    drawdown_r = evidence.get("closed_trade_drawdown_r")
    if (n == 0 or average is None or win_rate is None
            or expectancy_r is None or drawdown_r is None):
        return {
            "tier": 0, "score": round(0.1 * fit_score / 4 + adjustment, 6),
            "label": "Insufficient replay evidence",
            "summary": "Replay ranking unavailable: no closed trades or original risk is unknown.",
            "sample_weight": 0.0,
            "estimated_profit_probability_pct": None,
            "validated_profit_probability_pct": None,
            "probability_confidence": "unavailable",
            "adaptive_parameter_note": "No closed replay sample yet; keep default parameters.",
            "paper_feedback_score": adjustment,
            "paper_profit_probability_pct": paper_probability,
            "paper_feedback_trades": paper_count,
        }
    weight = n / (n + 20)
    # Smoothed win probability: Beta(2,2) prior prevents tiny samples from showing
    # false certainty. This estimates historical replay win frequency, not the next
    # trade's guaranteed chance of profit.
    smoothed_probability = ((float(win_rate) / 100 * n) + 2) / (n + 4) * 100
    probability_confidence = "high" if n >= 50 else "medium" if n >= 20 else "low"
    score = weight * (expectancy_r + 0.25 * (smoothed_probability / 100 - 0.5)
                      - 0.1 * drawdown_r) + 0.1 * fit_score / 4 + adjustment
    positive = float(expectancy_r) > 0 and float(average) > 0
    tier = 3 if positive and n >= 20 else 2 if positive else 1
    label = (
        "Positive replay · 20+ trades" if tier == 3
        else "Positive replay · small sample" if tier == 2
        else "No positive replay edge"
    )
    return {
        "tier": tier, "score": round(score, 6), "label": label,
        "summary": (
            f"Replay net average ₹{float(average):,.2f}/trade ({float(expectancy_r):.2f}R); "
            f"{float(win_rate):.1f}% raw wins, {smoothed_probability:.1f}% smoothed "
            f"historical replay win estimate across {n} trades. "
            "Ranking uses replay only, adjusted for sample size, initial risk, "
            f"drawdown and market fit; {paper_count} verified paper outcomes "
            "contribute a bounded, separate score adjustment."
        ),
        "sample_weight": round(weight, 4),
        "estimated_profit_probability_pct": round(smoothed_probability, 2),
        "validated_profit_probability_pct": None,
        "probability_confidence": probability_confidence,
        "paper_feedback_score": adjustment,
        "paper_profit_probability_pct": paper_probability,
        "paper_feedback_trades": paper_count,
        "adaptive_parameter_note": (
            "Historical replay supports current parameters; live-paper evidence can now "
            "confirm or weaken this ranking."
            if tier == 3 else
            "Keep default parameters until more closed trades confirm the setup."
            if tier == 2 else
            "Do not tighten risk from this sample; prefer waiting or reducing allocation."
        ),
    }
