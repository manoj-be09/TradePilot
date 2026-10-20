# Probability research acceptance

Status: evidence foundation only; no predictive percentages published.

19 October 2026: [NIFTY_STRATEGY_RANKING_DESIGN.md](NIFTY_STRATEGY_RANKING_DESIGN.md) defines the proposed policy-specific probability target, fills versus opportunities, same-horizon conservative ranking, selected-subset calibration, dependence-aware uncertainty and explicit abstention. The current PCR/paper-frequency heuristic is not this model. The proposed policy constants and acceptance thresholds have not been evaluated or deployed.

## Outcome and sample
A win means realized P&L strictly positive after a declared versioned cost model under the exact saved entry/exit policy. Loss and break-even are separate classes; loss is not automatically100%-win. Keep account/strategy/version/policy, entry and close timestamps, expiry, premium fills, source quality and cost model in every labeled row. Existing paper P&L is before costs and cannot be silently reused as this label. Exclude test-only/manual/incomplete outcomes and isolate synthetic fixtures from actual observations. Never relabel old entries with newly fetched features.

## Inputs
Existing immutable entry context preserves OI/volume ratios, ATM IV and spreads. New entries additionally preserve underlying spot, observation time and expiry. This is not a price-trend history. News and trend features are not integrated; news needs timestamped historical publication/availability evidence. Missing inputs remain missing. Current UI snapshots are informational only, and may be outside a verified open session.

## Next implementation gates
1. Produce a read-only eligible dataset with explicit exclusions, validated cost assumptions and event-time feature availability. Report periods and counts by exact strategy policy; do not pool unlike rules.
2. Freeze a baseline and walk-forward time splits. Purge overlapping open-trade/expiry intervals across split boundaries. Fit preprocessing, model and calibration on training/calibration periods only.
3. Compare untouched later-period Brier score, log loss and reliability bins against a historical base-rate baseline. Report per-class counts, periods, missingness and uncertainty. No fixed trade count alone proves calibration. Sparse single-session trades do not provide independent validation.
4. Store a versioned model/evaluation artifact before wiring a read-only probability endpoint. Withhold estimates for stale/missing/unqualified inputs, unseen policy or failed validation. Never feed estimates into execution without a separate authorized change.

## Current limits
The existing smoothed qualified-paper frequency is descriptive before-cost history, not a calibrated forecast. No after-cost validated sample, out-of-sample model, validation period or news integration exists. Strict exchange-origin book timing remains unproven. The UI explicitly withholds win/loss forecasts.

References: https://scikit-learn.org/stable/modules/calibration.html and https://scikit-learn.org/stable/modules/cross_validation.html (time-series validation).
