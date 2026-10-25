# TradePilot task checklist

Source: [DAILY_DELIVERY_PLAN.md](DAILY_DELIVERY_PLAN.md) and [PROJECT_PLAN.md](PROJECT_PLAN.md). Status checked against [BUILD_PROGRESS.md](BUILD_PROGRESS.md) on **25 October 2026**. Dates are work targets, not proof of completion. A checked box means the stated bounded task has evidence; it does not qualify a paper fill, final settlement, profitability, or a calibrated probability.

## Working rule

Complete the tasks below in order. After a task meets its stated evidence gate, check it off and move directly to the next one. Do not pause for routine confirmation. If a task is blocked by missing access, external evidence or an unsafe dependency, leave it unchecked, record the blocker and continue with the next independent task. Return to blocked tasks when their evidence becomes available. Keep paper-only safety controls and the evidence rules in the linked plans in force.

## Dated work

- [x] **Day 1 — 22 Oct: Publish and verify the local build.** Confirm the pushed source, healthy API/worker/web and signed-in desktop and phone F&O views.
- [x] **Day 2 — 23 Oct: Diagnose the worker cycle.** Check the account-safe worker warning and reconcile the observed paper ledger without changing historical positions.
- [x] **Day 3 — 24 Oct: Decide on a NIFTY data provider.** Compare Upstox/DhanHQ capabilities, published costs and use limits; verify the observed NIFTY instrument, expiry and lot mapping. Keep Upstox observation-only while rights and book timing are unreviewed.
- [x] **Day 4 — 25 Oct: Define the timestamp contract.** Distinguish source event, executable-book, last-trade, provider-response and server-receipt times; specify missing, stale and future-data behavior. Provider proof of exchange-origin book time remains open under Day 5 and the external gates below.
- [ ] **Day 5 — 26 Oct: Probe a normal-session feed when the exchange is open.**
  - [x] Make the read-only probe and deterministic fixtures report missing provider tick time as unknown.
  - [ ] Retain bounded raw observations and decide whether every selected leg has a usable exchange-origin executable-book time. Record `NOT OBSERVED` if no eligible session is available.
- [ ] **Day 6 — 27 Oct: Retain point-in-time NIFTY chain and bar artifacts.**
  - [x] Validate declared event, availability and receipt times, source identity, revisions, exact bytes and late corrections in offline fixtures.
  - [ ] Review actual source rights and retained raw chain/bar artifacts for the owner study.
- [ ] **Day 7 — 28 Oct: Record available market fields with provenance.**
  - [x] Track returned-chain expiry, underlying, volume/OI/IV/Greek and bid/ask quantity coverage; keep partial totals and missing depth-order counts explicit.
  - [ ] Establish licensed depth-order data if available. Do not label order counts as distinct buyers or sellers.
- [ ] **Day 8 — 29 Oct: Freeze derived volatility and price-level features.**
  - [x] Require continuous current-session bars for the 20-return realized-volatility heuristic; withhold values on insufficient history.
  - [ ] Review source availability and validation for the descriptive support/resistance heuristic before research use.
- [x] **Day 9 — 30 Oct: Resolve news as a missing factor.** Mark historical NIFTY news `UNAVAILABLE_FOR_RESEARCH` without backfilling old fills or inventing narratives. Reopen only if a suitable timestamped source and its rights are reviewed.
- [ ] **Day 10 — 31 Oct: Freeze a real NIFTY policy set before outcomes.**
  - [x] Implement the immutable incumbent, `NO_TRADE` and at-most-two-challenger policy-set contract.
  - [ ] Review and freeze the actual owner's entry, exit, stop, size, capital and review settings before collecting outcomes.
- [ ] **Day 11 — 1 Nov: Bind reviewed costs and inputs.**
  - [x] Compute labeled offline Upstox public-plan entry/exit charge scenarios from supplied prices.
  - [ ] Review complete account-specific charges and exact source, dataset and cost artifacts; do not retrofit missing historical costs.
- [ ] **Day 12 — 2 Nov: Enforce canonical parent search accounting.**
  - [x] Reserve numbered slots under the exact NIFTY dataset digest and block new slots for differently hashed owned studies with overlapping declared dates; focused checks and CI passed on `74d34c5`.
  - [ ] Review actual observational lineage across copied/rehashed datasets, concurrency around newly frozen studies, and retention/counting of every failed or rejected search.
- [ ] **Day 13 — 3 Nov: Protect an independent holdout.**
  - [x] Exclude declared holdout rows from an offline fixed-equity evaluation input.
  - [x] Audit current source paths and limits in [HOLDOUT_ACCESS_AUDIT.md](HOLDOUT_ACCESS_AUDIT.md).
  - [ ] Verify an authentic, separately controlled NIFTY holdout, access logs and an independent validation account with leakage/failure tests.
- [ ] **Day 14 — 4 Nov: Evaluate the exact frozen policy after costs.**
  - [x] Screen supplied option quote paths for contract identity, timing, side, spread, displayed size, expiry and explicit unresolved/no-fill outcomes.
  - [ ] Run reproducible policy-matched chronological entry and exit on reviewed data with full costs and conservative nonfill; label daily-close illustrations separately.
- [ ] **Day 15 — 5 Nov: Compare qualified policies with `NO_TRADE`.** Apply capital, tail-risk and uncertainty gates; otherwise abstain. Publish no invented probability of profit.
- [ ] **Day 16 — 6 Nov: Retain prospective decisions and outcomes.**
  - [x] Keep existing time-bounded account-scoped recommendation/abstention records separate from orders and promotion.
  - [x] Bind newly recorded shadow no-fill and rejection events to the saved frozen policy artifact; preserve idempotent replay of earlier records.
  - [ ] Record policy-matched unresolved, rejected and no-fill observations prospectively and idempotently.
- [ ] **Day 17 — 7 Nov: Complete release security and recovery checks.**
  - [x] Run full CI on `74d34c5` (passed), including the configured isolated PostgreSQL test service.
  - [x] Run the later isolated local suite after the parent-lock change: 609 passed, 13 skipped, with two existing deprecation warnings; Ruff, mypy and Bandit passed.
  - [x] Suppress a research evaluation wrapper's saved result when its lease outcome is not COMPLETED; focused lease/evaluator checks passed.
  - [x] Roll back failed or stale research task transactions before lease status/recovery handling; focused regression checks passed.
  - [x] Run the new PostgreSQL study-freeze/reservation race test in CI with `TEST_POSTGRES_URL`; backend and frontend jobs passed on `05d41c0`.
  - [x] Audit the 13 local skip reasons: 11 PostgreSQL-only cases require the CI test database and two historical-settlement cases require an explicit opt-in NSE archive date.
  - [ ] Finish final migration, account-isolation, lease/recovery and failure-path review against the release candidate and verify the deployed revision.
- [ ] **Day 18 — 8 Nov: Verify the final desktop and phone screens.**
  - [x] Inspect earlier signed-in F&O desktop and approximately 390 px phone layouts.
  - [x] Show the frozen study's declared cost artifact status and uncertainty note in the Research summary; frontend type check passed.
  - [ ] Verify current populated Research/F&O views, cost/source/uncertainty labels and accessible abstention states.
- [ ] **Day 19 — 9 Nov: Attempt normal-session paper lifecycle acceptance only after the book-time gate passes.** Observe a natural policy-pinned signal, entry, exit, rejected/no-fill paths and ledger reconciliation. If no qualified feed or signal exists, record the waiting evidence; do not force a trade.
- [ ] **Day 20 — 10 Nov: Decide on the bounded software release candidate.** Review exact deployment revision, test/skip results and the gate ledger; review final expiry evidence without changing the saved trade unless independently justified.

## Open external acceptance gates after Day 20

- [ ] Prove licensed, per-leg exchange-origin executable bid/ask book timing for NIFTY paper fills.
- [ ] Observe a genuine normal-session, policy-pinned paper round trip when a natural signal occurs.
- [ ] Independently review the final clearing settlement and applicable costs for the existing **TEST ONLY NIFTY22950CE, expiry 19 Oct**; retain its current state until then.
- [ ] Collect sufficient independent, prospective, policy-matched after-cost outcomes and validate uncertainty/calibration before claiming strategy advantage or probability of profit.

Live orders, additional-index execution and automatic strategy promotion remain disabled throughout this checklist. No subscription or paid data call is authorized by a task box.
