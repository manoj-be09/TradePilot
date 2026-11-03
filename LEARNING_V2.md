# Learning loop v2

Branch `feat/learning-v2`. It is not merged and not running: the live system keeps v1 (the research rule engine plus the night condor override) until the owner says to apply v2. Every result here is simulated and none of it is investment advice.

## Owner decisions (3 November 2026)

- A family-wise error budget of 5% across every forward test the loop will ever run.
- Machine-proposed hypotheses (GRID, LLM) enter shadow without the owner's approval.
- Overrides stay allowed. They are recorded as overrides and never count as promotions.
- The probation account keeps ₹10 lakh of paper capital.

## What changes from v1

| v1 (live) | v2 (this branch) |
|---|---|
| A fixed t ≥ 4.47 over 12,500 backtest trials | Always-valid betting e-process (`packages/learning/evidence.py`), safe to check every night |
| Corrects for backtest trials | Corrects for forward hypotheses. Each gets a slice of one 5% budget as it enters shadow; backtests spend nothing (`budget.py`) |
| Every rule enters shadow | A power check first: an idea that could not show its effect within about 125 sessions is refused without spending budget (`power.py`) |
| Rules hard-coded in `ENGINE_RULES` | Hypotheses read from a private file, `work/learning/hypotheses.json`, so expected effects stay out of git |
| Shadow → paper approved | Shadow → probation (paper, 1 lot) → approved. Approval needs 10 paper trades that do not trail shadow by more than half its average |
| Drift t ≤ −2 | Futility e-process (evidence of losing ≥ 10), hard limits, and a 5-loss streak |
| No baseline | Optional `baseline_key`: the rule must beat the same trade taken without its filter |

Kept from v1: frozen versions, the append-only decision trail, champion/challenger within a family, rollback, and the ₹20,000 per-trade and ₹60,000 drawdown limits.

## Phase 0 fixes (also on this branch)

- The nightly job imports newly expired expiries and far strikes (`import_recent_history`), so the shadow ledger no longer depends on manual runs.
- The night runner sends phone alerts through the relay account when it opens a trade, is blocked, skips for missing data, or fails its cache import.
- Closed override paper trades become PAPER observations (`link_paper_trades`), and each check reports `paper_vs_shadow` on the same sessions.
- The night runner logs each leg's last price, mid and quote age next to its decision.

## Measured on simulated data (tests/unit/test_learning_stats.py)

- **No edge:** checked after every trade for 120 trades, the false promotion rate stays below 5%.
- **A large edge (₹2,000 a trade):** found within 150 trades in most runs.
- **A ₹3,000-a-trade loser:** dropped within 100 trades in most runs.
- **Speed:** forward evidence is slow at these effect sizes. That is the cost of not fooling ourselves.

## Still to build

1. **Probation runner:** generalise `apps/night_runner` to trade any PROBATION or override hypothesis from its definition, in the probation account.
2. **Recorder → 5-minute forward bars:** shadow scoring from the private recorder data, with a wider strike range, so hundreds of candidates cost no API calls.
3. **Point-in-time features and an event calendar:** India VIX, the weekly-vs-monthly IV gap, skew, realised vol; RBI, budget, CPI and Fed days.
4. **Idea generator:**
   - GRID: pre-declared neighbours of the rules doing well in shadow.
   - LLM: written hypotheses with a rationale.
   - Each enters with a pre-registration, and its rationale is stored.
5. **Backtest screen (reject only):** purged cross-validation, probability of backtest overfitting, deflated Sharpe, and a placebo run on shuffled data.
6. **Allocation across approved rules:** Thompson sampling inside the 2% and 10% limits, with portfolio Greek caps and a −5% gap stress test for night holds.
7. **Execution learning:** weekly recalibration of the spread and slippage model from paper fills, with pinned cost-model versions.
8. **Research page panel:** for `/research/learning`, and a weekly evidence report.

## Applying v2 later

1. Merge this branch. Migration `0041_learning_v2` adds three tables and changes nothing existing.
2. Write `work/learning/hypotheses.json` with the two condors, keyed like the v1 rules (`very_rich_night_condor`, `very_rich_night_condor_920`), with `override: true`, their pre-registered effects, and an every-day baseline.
3. Restart the stack outside market hours. v1 keeps running beside v2 until the owner retires it.
