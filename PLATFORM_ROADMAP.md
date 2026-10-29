# TradePilot platform roadmap: self-learning NIFTY F&O platform

Prepared 27 October 2026 from a critical review of commit `453ccc2`. This is the single forward plan. Where `PROJECT_PLAN.md` or `STRATEGY_INTELLIGENCE_PLAN.md` conflict with it on order or scope, this document wins; their safety rules and research principles still apply.

## 1. Goal and what "done" means

**Goal:** an automated, self-learning platform that researches, selects, paper-trades and — only after explicit gates — live-trades **NIFTY 50 index futures and options** through a SEBI-compliant broker path.

"Self-learning" is defined narrowly and measurably:

- The platform records every market observation, decision and outcome.
- On a schedule it re-evaluates strategies, parameters, cost/slippage models and strategy-selection policy on new data.
- Better candidates are promoted only through frozen evaluation, forward shadow evidence and an approval gate; worse ones are demoted automatically.
- It never changes risk limits, never bypasses the risk engine, and never promotes itself to live.

**Done (end state):**

1. 12+ months of licensed, point-in-time NIFTY options/futures data (recorded + purchased history).
2. One decision core used identically by backtest, paper and live runtimes, with a parity test proving it.
3. Realistic simulation: bid/ask + depth-limited fills, slippage, leg risk, versioned costs, SPAN+exposure margin, expiry settlement.
4. A strategy library of versioned NIFTY F&O strategies including futures and hedged structures.
5. A running champion/challenger loop with documented promotions, demotions and their evidence.
6. 3+ months of unattended forward paper trading whose results are within tolerance of what replay predicted.
7. A live pilot only after the compliance, security and risk gates in Phase 7 pass.

## 2. Guiding principles

- **Data before models, one engine before many strategies, forward evidence before live.** Phases are ordered by that rule.
- **Deterministic core, learning at the edges.** Code computes fills, costs, margin, P&L and risk. Learning chooses *which approved strategy and parameters* to run, never whether risk rules apply.
- **Fail closed** everywhere (unchanged from today).
- **Small, shippable increments.** Each workstream ends with code + tests + one short status line, not a long record entry.
- **Delete before adding.** Out-of-scope code is removed, not maintained.

## 3. Weakness → fix map

| # | Weakness (review finding) | Fixed in |
|---|---|---|
| W1 | No stored market data; backtests depend on daily option candles | Phase 1 |
| W2 | Backtest and paper use different engines and assumptions | Phases 2–3 |
| W3 | Entry and risk logic embedded in a ~600-line API handler; 4,031-line `main.py`; 3,825-line `page.tsx` | Phase 2 |
| W4 | No futures, no margin model, no slippage/partial/leg fills | Phase 3 |
| W5 | Process overhead: 31% "Record/Document" commits, 1,649-line `BUILD_PROGRESS.md` | Phase 0 |
| W6 | Out-of-scope equity and multi-index code still present | Phase 0 |
| W7 | "Self-improving" has nothing to learn from yet | Phases 4–5 |
| W8 | Feed observer swallows errors, calls private provider API | Phases 0–1 |
| W9 | Security gaps (token in sessionStorage, no MFA/revocation/rate limits, thin `SECURITY.md`) | Phases 6–7 |
| W10 | Repo hygiene: ~80 `.pytest-*` dirs, `work/` patch scripts | Phase 0 |
| W11 | Recorded data could not be used for research until retention was clarified (now allowed for private Feed V3 use) | Phase 1 |
| W12 | No evidence of an edge after costs | Phases 4–6 |

## 4. Phases

Durations assume one developer working with AI coding agents. They are estimates, not promises; Phase 6 is bounded by calendar time in the market, not by coding speed.

### Phase 0 — Reset scope and hygiene (1 week)

Goal: a smaller, clearer codebase that only does NIFTY F&O.

1. **Remove equity scope.** Delete `apps/api/equity_routes.py`, `apps/api/services/equity_runtime.py`, `packages/paper_trading/equity*.py`, `packages/regime/equity_policy_trials.py`, `apps/web/app/EquityStrategies.tsx` and their tests; add one Alembic migration dropping the 5 `Equity*` tables (export their data first if it matters). Remove NSE stock search from the chart.
2. **Remove multi-index paths.** Hard-scope option execution to NIFTY; delete `paper_index_execution_enabled` branches and index-switching UI.
3. **Rename** `packages/regime` → `packages/research` (keep `classifier.py`/`outlook.py` as `packages/research/regime/`).
4. **Repo hygiene.** Delete all `.pytest-*` directories and `work/`; add them to `.gitignore`; configure pytest `--basetemp` under one ignored folder. Add a pre-commit hook for ruff and file-size warnings (>800 lines).
5. **Process reset.** Replace `BUILD_PROGRESS.md` with a ≤100-line `STATUS.md` (what works, what's next, known limits). Move history to `docs/archive/`. Commit rule: code changes only, evidence lives in CI artifacts; no "Record …" commits.
6. **Feed observer quick fix.** Log (sanitized) and count exceptions; use a public provider method instead of `_get`.

Exit gate: CI green; line count reduced; no equity routes in OpenAPI; `STATUS.md` exists.

### Phase 1 — Data foundation (3–4 weeks build, recording continues forever)

Goal: licensed, point-in-time, replayable data. **This is the highest-leverage phase; start the licensing step on day 1.**

**1a. Data rights (resolved for recording) and history purchase.**
- **Recording is permitted.** Upstox's published community guidance (see `DATA_SOURCE_DECISION.md`, 25 October 2026 section) allows private, non-commercial storage of **Market Data Feed V3** data for replay and backtesting with no retention limit, plus retained derived analysis — provided the data is never redistributed to third parties and WebSocket connection/subscription limits are respected. Record through **Feed V3**, not REST polling, because that is the endpoint the guidance covers.
- Consequences: raw data stays private (never in the public Git repo, never shared with friends/testers or exposed in a shared UI); check Feed V3 subscription limits against the instrument count below.
- Still unresolved and unchanged: Feed V3 has no exchange-origin depth timestamp, so recorded bid/ask carries Upstox message time only. Store it as observed time; the strict paper-fill freshness gate stays as designed.
- Decide budget for **purchased history**: recording starts from zero, and ~50 weekly expiries per year is too few for statistically meaningful learning. Evaluate NSE-authorised historical vendors (e.g. TrueData, Global Datafeeds, NSE Data & Analytics) for 2–3 years of NIFTY options/futures with bid/ask at 1-minute or better.

**1b. Recorder service (`apps/recorder`).**
- Subscribe via Feed V3 and persist every message (or 1-second/1-minute conflation, configurable) during market hours: full NIFTY chain for the nearest 3 weekly expiries + current monthly, strikes ATM ±20; NIFTY spot; NIFTY futures (near, next); India VIX.
- Each record: `instrument_key`, `event_time` (exchange time if provided, else null), `received_at`, `provider`, `revision`, bid/ask/size/LTP/volume/OI, and a batch id.
- Market calendar aware (NSE sessions, holidays, special sessions); detects and stores gaps explicitly.
- Health metrics and alert on missed snapshots.

**1c. Storage.**
- PostgreSQL + TimescaleDB hypertables (fits existing stack) for snapshots; compressed after 7 days. Parquet export per day for research and backup.
- Revision-aware: corrections create new rows with their own `received_at`; never update in place.
- Instrument master history: lot size, tick size, expiry dates per contract over time (lot sizes have changed — never assume constants).
- Corporate/calendar data: expiry calendar, holidays, event calendar (RBI policy, budget, major results), all with availability time.

**1d. Data quality.** Reuse and extend the existing chain quality report: crossed/wide books, missing strikes, stale quotes, outliers, OI/volume sanity. Daily quality report per session; bad sessions flagged, not deleted.

**1e. Historical import.** Loader for purchased data into the same schema, tagged with source and licence.

Exit gate: 10 consecutive sessions recorded with <1% missing snapshots; historical import of ≥1 year loaded and passing quality checks; licence documented in `DATA_SOURCE_DECISION.md`.

### Phase 2 — One decision core (3–4 weeks, can overlap Phase 1)

Goal: extract the trading brain out of the web layer so backtest, paper and live share it.

**Target package layout (`packages/core`):**

```text
events.py        MarketEvent (chain snapshot, quote, bar, futures tick), TimerEvent, FillEvent
strategy.py      Strategy protocol: on_event(ctx) -> list[OrderIntent]; versioned, deterministic
intents.py       OrderIntent (legs, qty in lots, limit/market, tag, strategy_version)
risk.py          RiskEngine.evaluate(intent, PortfolioState, MarketState, Limits) -> RiskDecision (pure)
execution.py     ExecutionModel protocol: simulate(intent, MarketState) -> list[Fill]
portfolio.py     Positions, cash, margin used, marked P&L (pure state transitions)
ledger.py        Append-only event log: intents, decisions, fills, marks, settlements
clock.py         Clock protocol: replay clock vs wall clock (IST, session-aware)
```

Steps:
1. Write **golden tests** first: capture today's `/paper/trades` behaviour (accepted, rejected, reasons, cash flows) for ~30 scenarios.
2. Move the risk checks from `_open_paper_trade_with_provider` (`apps/api/main.py:2471` onward) into `RiskEngine` unchanged; handler calls it. Golden tests must stay green.
3. Move exit rules and settlement from `paper_monitor.py` / `packages/paper_trading/exits.py` into the core; the worker becomes a thin runtime.
4. Split `main.py` into routers (`auth`, `market_data`, `paper`, `research`, `ops`), each <600 lines. Business logic lives only in packages.
5. Split `apps/web/app/page.tsx` into feature components (chain, entry form, portfolio, worker status, research) with shared API hooks.
6. Append-only ledger table for every intent/decision/fill, enabling exact replay of any paper day.

Exit gate: no risk or ledger logic in `apps/`; golden tests green; `main.py` and `page.tsx` each <800 lines; architecture test (import-linter) forbids `apps → broker` and `strategy → ledger` imports.

### Phase 3 — Realistic simulation, futures and margin (4–6 weeks)

Goal: a simulator honest enough that backtests predict paper results.

1. **Execution model v1:** fills at bid/ask only up to displayed size; remainder unfilled or worse price per configurable slippage; legs filled sequentially with per-leg latency (leg risk); limit orders with time-in-force. Calibrate later from forward data (Phase 6).
2. **Costs:** versioned charge model (brokerage, STT incl. exercise STT on ITM options, exchange charges, SEBI fee, stamp duty, GST) applied to every fill; stored version on each result.
3. **Margin model:** SPAN + exposure margin using NSE SPAN risk-parameter files (or broker margin API as cross-check); margin blocks capital; strategy returns reported on margin used. Include expiry-day additional margin rules (verify current SEBI/NSE rules).
4. **NIFTY futures:** tradable instrument (near/next month), mark-to-market, rollover handling, margin; enables hedged structures and directional futures strategies.
5. **Settlement:** cash settlement at official final settlement price; ITM exercise; physical-style edge cases not applicable to index.
6. **Event-driven backtester** that replays stored events through the same core and runtime clock. Delete the old daily-open/close replay once parity is shown.
7. **Parity test:** replaying a recorded paper day must reproduce that day's paper decisions and fills within tolerance. Runs in CI on a stored fixture day.

Exit gate: parity test green; margin within ±5% of broker figures on sample positions; futures and options both tradable in paper.

### Phase 4 — Strategy library and research engine (4–6 weeks)

Goal: candidate strategies evaluated honestly on the new data.

1. **Feature store (point-in-time):** IV per strike, ATM IV, IV rank/percentile, skew, term structure, PCR, OI change, max-pain-style concentration, realized vol (intraday excluding overnight gap), futures basis, India VIX, event proximity, days to expiry, time of day. Every feature has `as_of` and is computed only from data available then.
2. **Strategy library (versioned code, parameters separate):**
   - Premium selling with defined risk: iron condor, iron fly, credit spreads (delta- or premium-targeted strikes).
   - Directional: debit spreads, futures with option hedge, futures trend/momentum intraday.
   - Event/volatility: long straddle/strangle before scheduled events (with strict cost model).
   - Expiry-day specific variants as separate strategies (different regime).
   - Every strategy includes explicit exits, max holding time, and a no-trade condition.
3. **Research engine:** walk-forward evaluation (train/test windows by expiry), purged and embargoed splits, protected final holdout (existing holdout machinery reused here), full trial accounting.
4. **Metrics:** net P&L after costs, return on margin, max drawdown on marked equity, CVaR/tail loss, win rate *and* payoff ratio, per-regime breakdown, deflated Sharpe and probability of backtest overfitting with stated assumptions.
5. **Kill bad ideas fast:** a strategy that fails net-of-cost walk-forward on purchased history is rejected, regardless of how good one window looks.

Exit gate: ≥3 strategies pass walk-forward on ≥1 year of data net of costs and margin, with results reproducible from a frozen study id. If none pass, stop and revisit strategies before Phase 5 — do not proceed to learning on top of no edge.

### Phase 5 — Self-learning loop (4–6 weeks)

Goal: the platform improves its choices from evidence, under control.

**What learns (in order of safety):**

1. **Cost and fill calibration** — slippage, fill probability and leg latency fitted from forward paper observations (and later live fills).
2. **Exclusion rules** — conditions under which a strategy must not trade (e.g. VIX spike, wide spreads, event day), learned from losses and validated walk-forward.
3. **Parameter re-tuning** — scheduled walk-forward re-optimisation within pre-declared bounds; new parameters are a new strategy version.
4. **Strategy selection policy** — a conservative contextual selector (start with rule-based scoring; then a constrained contextual bandit) that chooses among *approved* strategies or **no trade**, given the feature vector.

**Loop mechanics:**
- Nightly: ingest day's data → quality check → recompute features → evaluate challengers on newly available data → update forward shadow results.
- Weekly: promotion review report; promotion requires frozen evaluation pass + minimum forward shadow period + human approval (state machine already designed: `DRAFT → … → APPROVED_PAPER`).
- Automatic **demotion** (no approval needed) on drawdown breach, drift alarm, data-quality failure or cost-model breach.
- Drift monitoring: feature distribution shift, prediction calibration, realised vs expected P&L, slippage vs model.
- Model registry with pinned versions; every decision records exact strategy, parameter, selector and cost-model versions.

**LLM role (bounded):** generate research hypotheses and strategy variants as *proposals* for the research engine, write daily journals and explanations, summarise promotion evidence. Never sees holdout data, never computes metrics, never approves.

Exit gate: one full learning cycle demonstrated end to end (challenger proposed → evaluated → shadowed → promoted or rejected) with an audit trail; automatic demotion tested by fault injection.

### Phase 6 — Unattended paper operations and forward evidence (≥3 months calendar)

Goal: prove the system runs itself and that forward results match expectations.

1. **Strategy runtime/scheduler:** session-aware orchestration (pre-open checks, entry windows, exit windows, end-of-day settlement), using the core and the worker lease pattern.
2. **Monitoring & alerting:** metrics (Prometheus or equivalent), dashboards, alerts to phone (Telegram/email) for: data gaps, worker stale, risk rejections spike, drawdown thresholds, margin utilisation, kill switch.
3. **Reconciliation:** daily ledger vs positions vs cash; any mismatch halts new entries.
4. **Runbooks:** provider outage, DB failure, mid-trade crash, expiry day, holiday changes.
5. **Fault-injection tests:** kill the worker mid-exit, drop the feed, delay quotes, corrupt a snapshot.
6. **Security hardening (W9):** httpOnly secure cookie sessions instead of sessionStorage bearer, refresh + revocation, MFA for admin, distributed rate limits in Redis, secrets in a vault/OS keystore, CORS locked down, dependency scanning in CI, backups with restore test.
7. **Forward track record:** ≥60 trading sessions (target 3 months) unattended paper with ≥95% scheduled-run success.

Exit gate: forward net P&L, drawdown and slippage within pre-registered tolerance of replay predictions; zero unreconciled ledger days; all runbooks exercised once.

### Phase 7 — Live readiness and pilot (gated, after Phase 6)

Live trading stays forced off until every item passes and the owner signs off in writing.

1. **Compliance:** confirm current SEBI retail algorithmic trading framework requirements with the chosen broker (algo registration/identification, API access conditions, static IP or other controls); confirm the private, personal-use posture (no advice to others, no signal sharing).
2. **Broker adapter:** separate `packages/broker_<name>` with order state machine (new → acknowledged → partial → filled/cancelled/rejected), idempotent client order ids, reconciliation with broker positions, rate limits, reconnect handling. Only the core's execution runtime may call it.
3. **Pre-trade controls at two layers:** platform `RiskEngine` and broker-side limits (max order value, max lots, freeze quantity, price bands).
4. **Independent review:** security review, risk review and a dry run in the broker's sandbox (if available) or with minimal quantity.
5. **Pilot:** 1 lot, single strategy, capped daily loss, 4–8 weeks; compare live fills to simulation; scale only by an explicit decision.
6. **Ops:** hard kill switch (one action flattens or blocks), alerting on every live order, daily P&L and position reconciliation with the broker contract notes.

## 5. Timeline overview

| Weeks | Work |
|---|---|
| 1 | Phase 0; start the Feed V3 recorder immediately; request vendor history quotes |
| 2–5 | Phase 1 (recorder, storage, import) ∥ Phase 2 golden tests and risk-engine extraction |
| 5–8 | Finish Phase 2; start Phase 3 |
| 8–14 | Phase 3 (execution, costs, margin, futures, backtester, parity) |
| 14–20 | Phase 4 (features, strategies, research engine) |
| 20–26 | Phase 5 (learning loop) ∥ Phase 6 runtime and monitoring |
| 26–39 | Phase 6 forward paper evidence (calendar-bound) |
| 40+ | Phase 7 only if all gates pass |

Realistic total to a live pilot: **9–12 months**, dominated by data acquisition and forward evidence. Coding faster does not shorten the forward-evidence period.

## 6. Success metrics

- Data: snapshot completeness ≥99% per session; quality-flagged sessions <5%.
- Engineering: no file >800 lines in `apps/`; parity test green; CI <10 min; zero risk logic outside `packages/core`.
- Research: every reported result reproducible from a study id; full trial counts recorded.
- Trading (paper, forward): positive net-of-cost expectancy with confidence interval reported; max drawdown within declared budget; slippage within model ±25%.
- Operations: ≥95% scheduled runs succeed; mean time to alert <2 minutes; zero unreconciled days.

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Upstox changes its data-use guidance, or Feed V3 limits are too tight for the full chain | Keep a dated copy of the guidance; record only within limits; purchased vendor history as a second source |
| No strategy shows an edge after costs | Phase 4 gate stops the plan; that is a valid, money-saving outcome |
| Overfitting through many AI-generated variants | Full trial accounting, deflated Sharpe/PBO, protected holdout, forward shadow |
| Regulatory changes (lot size, expiry days, margins, algo rules) | Instrument master history, versioned cost/margin models, compliance check before Phase 7 |
| Refactor breaks safety behaviour | Golden tests before moving code; guard hook; import-linter rules |
| Single-person bus factor and agent churn | Short `STATUS.md`, architecture tests, weekly review of direction against this roadmap |

## 8. Decisions needed from the owner

1. Data budget and vendor for 2–3 years of NIFTY options/futures history.
2. Confirm recorded Feed V3 data will stay strictly private (no sharing with friend testers), as Upstox's guidance requires.
3. Confirm deleting equity code and tables (export first?).
4. Storage choice: TimescaleDB on the existing Postgres (recommended) vs Parquet-only.
5. Intended broker for eventual live trading (affects margin API and adapter).
6. Capital and risk budget for an eventual pilot.
7. Hosting for unattended operation (home machine vs VPS in India for latency and static-IP needs).
