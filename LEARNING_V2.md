# Learning loop v2

Merged and live since 3 November 2026: the night runner reads its rules from the v2 ledger. Every result here is simulated and none of it is investment advice.

## Owner decisions (3 November 2026)

- A family-wise error budget of 5% across every forward test the loop will ever run.
- Machine-proposed hypotheses (GRID, LLM) enter shadow without the owner's approval.
- Overrides stay allowed. They are recorded as overrides and never count as promotions.
- The probation account keeps ₹10 lakh of paper capital.

## What changes from v1

| v1 | v2 |
|---|---|
| A fixed t ≥ 4.47 over 12,500 backtest trials | Always-valid betting e-process (`packages/learning/evidence.py`), safe to check every night |
| Corrects for backtest trials | Corrects for forward hypotheses. Each gets a slice of one 5% budget as it enters shadow; backtests spend nothing (`budget.py`) |
| Every rule enters shadow | A power check first: an idea that could not show its effect within about 125 sessions is refused without spending budget (`power.py`) |
| Rules hard-coded in `ENGINE_RULES` | Hypotheses read from a private file, `work/learning/hypotheses.json`, so expected effects stay out of git |
| Shadow → paper approved | Shadow → probation (paper, 1 lot) → approved. Approval needs 10 paper trades that do not trail shadow by more than half its average |
| Drift t ≤ −2 | Futility e-process (evidence of losing ≥ 10), hard limits, and a 5-loss streak |
| No baseline | Optional `baseline_key`: the rule must beat the same trade taken without its filter |

Kept from v1: frozen versions, the append-only decision trail, champion/challenger within a family, rollback, and the ₹20,000 per-trade and ₹60,000 drawdown limits.

## Phase 0 fixes

- The nightly job imports newly expired expiries and far strikes (`import_recent_history`), so the shadow ledger no longer depends on manual runs.
- The night runner sends phone alerts through the relay account when it opens a trade, is blocked, skips for missing data, or fails its cache import.
- Closed override paper trades become PAPER observations (`link_paper_trades`), and each check reports `paper_vs_shadow` on the same sessions.
- The night runner logs each leg's last price, mid and quote age next to its decision.

## Measured on simulated data (tests/unit/test_learning_stats.py)

- **No edge:** checked after every trade for 120 trades, the false promotion rate stays below 5%.
- **A large edge (₹2,000 a trade):** found within 150 trades in most runs.
- **A ₹3,000-a-trade loser:** dropped within 100 trades in most runs.
- **Speed:** forward evidence is slow at these effect sizes. That is the cost of not fooling ourselves.

## Built

1. **Probation runner** (`apps/night_runner`, `tradable_rules` in `engine.py`): once the hypothesis file exists, the night runner reads its rules each morning from the v2 ledger. It trades rules in probation or approved, and the owner's overrides until evidence stops them, in the same separate paper account (₹10 lakh). Non-override trades are named "Probation". A rule stopped by either loop is not traded.
2. **Forward bars from the recorder** (`forward_bars.py`): recorder minutes fold into 5-minute IST bars. They fill sessions the history imports lack, and imported bars always win, so nightly shadow scoring makes no API calls. The recorder now keeps ±40 strikes.
3. **Point-in-time features and an event calendar** (`features.py`):
   - realised vol over the last 5 sessions;
   - the previous session's India VIX close, from the recorder;
   - the gap between weekly and next-expiry implied vol;
   - 2% put/call skew;
   - event days from the owner's private `work/learning/events.json`, a list of `{"date", "name"}`. Nothing is pre-filled.

   Filters are frozen in a hypothesis's params. They apply alike in shadow scoring and the live runner, and an unreadable feature means no trade.
4. **Idea generator** (`ideas.py`):
   - GRID declares one-step neighbours of shadow rules with 20+ positive forward trades. It drops a batch whose backtests overfit, and pre-registers half the parent's average.
   - Every Friday, an LLM proposes up to 3 ideas: Claude (`claude-opus-5-5`) when the `llm` extra and `ANTHROPIC_API_KEY` are set, otherwise a free local Ollama model (`OLLAMA_MODEL`, default `qwen2.5:7b`; pull it once with `ollama pull qwen2.5:7b`). It is sent only the parameter space and aggregate shadow results, and each proposal is range-checked.
   - New ideas go to the private `work/learning/generated.json` and are frozen from the next session.
5. **Backtest screen, reject only** (`screen.py`): applies to machine ideas only. It checks 30+ trades, purged folds (positive in 4 of 5), the deflated Sharpe ratio against the family's trials, a placebo of random baseline days, and a CSCV overfitting probability for GRID batches. A rejected idea spends no budget.
6. **Allocation** (`allocation.py`): rules due together are ordered by a Thompson draw. Each condor opens only if the whole book stays inside:
   - a net delta cap;
   - a net vega cap;
   - a 5% overnight gap stress test at 10% of capital.

   Unknown greeks fail closed. The open function's per-trade, portfolio, daily-loss and kill-switch checks still apply.
7. **Execution learning** (`execution.py`): each Friday, the median paper-minus-shadow gap becomes a new version in the private `work/learning/cost-model.json`. New ideas freeze that slippage and pay it on every shadow trade, so counted evidence never changes.
8. **Research page panel and weekly report**:
   - `LearningLoop` on the research page reads `/research/learning`, which now includes the cost model.
   - Every Friday, `report.py` writes `work/learning/reports/YYYY-MM-DD.md`.

## Known limits

- **One traded family.** The generator and runner cover only `rich_condor` night holds, which the next-session exit supports. A second kind, `far_put_spread`, is scored in shadow only: a 5-delta put sold about three days before expiry with a bought put a fixed distance below, held to the expiry-day square-off. The runner cannot trade it yet.
- **VIX coverage.** VIX comes only from the recorder, so sessions before recording began have no VIX. A VIX filter fails closed on those sessions, which only affects backtests.
- **Shadow results wait for the expiry.** The sessions left to expiry are counted from the book's own bars, so a session is scored only once its expiry's bars run to expiry day. Results for an expiry that is still trading arrive up to a week late. A session missing from the book next to a weekend or holiday is still not counted.
- **Greek caps are fixed.** The caps (delta 40 index points, vega ₹3,000 a point) are starting values, not fitted ones.
- **LLM data sharing.** With a key, LLM ideas send aggregate simulated results to the Anthropic API; the local Ollama model keeps them on this machine. With neither, LLM ideas are off.

## How v2 was applied

1. Merge the branch. Migration `0041_learning_v2` adds three tables and changes nothing existing.
2. Write `work/learning/hypotheses.json` with the two condors:
   - keyed like the v1 rules (`very_rich_night_condor`, `very_rich_night_condor_920`);
   - with `override: true`, their pre-registered effects, and an every-day baseline.
3. Optionally:
   - write `work/learning/events.json`;
   - install the `llm` extra and set `ANTHROPIC_API_KEY` for Claude's weekly ideas.
4. Restart the stack outside market hours. The night runner then trades from the v2 ledger instead of the v1 list, in the same account.
