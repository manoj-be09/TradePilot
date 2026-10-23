# TradePilot daily delivery plan — 21 October 2026

This is a **five-hour-per-day work plan**, not a promised completion date. Default block: **09:30–14:30 Asia/Kolkata daily**. On non-trading days, use the block for source review, isolated implementation and testing; never describe those checks as an open-market acceptance. If a quota or host-access gate stops work, record actual time and resume after reset. Do not spend money or weaken a safety gate to keep the schedule.

## Dated high-level roadmap and finish forecast

Start **22 October 2026**. Twenty five-hour blocks are **100 planned hours**, with a **10 November software release-candidate target** if host access and quotas permit. This target covers the scoped NIFTY paper/research software and honest abstention when factors are missing; it does **not** certify exchange-timed fills, final settlement, a profitable strategy or calibrated probability. The earliest *conditional* operational paper acceptance review is **11–20 November** if a licensed source proves per-leg executable-book timing, a genuine normal-session strategy signal occurs, and final clearing settlement is independently reviewed. Otherwise those gates remain blocked without a defensible finish date. Evidence-qualified ranking/POP requires later independent policy-matched after-cost and prospective observations; its date cannot be fixed before data coverage and sample sufficiency are measured.

| Day/date | Five-hour focus | End-of-day evidence or honest blocker |
| --- | --- | --- |
| 1 — 22 Oct | Publish pending commits with host access; verify exact API/web revision and signed-in screens. | Remote commit, deployment health, or recorded access blocker. |
| 2 — 23 Oct | Diagnose degraded worker cycle and reconcile account-safe logs, health and paper ledger. | Cause/fix and focused checks, or a bounded unknown; no historical mutation. |
| 3 — 24 Oct | Review Upstox/DhanHQ capabilities, cost, rights and exact NIFTY instrument/expiry mapping. | Provider capability and licensing matrix; no subscription. |
| 4 — 25 Oct | Specify source event, exchange-book, last-trade, response and server-receipt time separately. | Timestamp acceptance contract and missing-data decisions. |
| 5 — 26 Oct | Run a bounded read-only normal-session feed probe **only if the exchange is open**; otherwise build deterministic fixtures. | Raw source/timing observations or `NOT OBSERVED`; never promote a response time to book time. |
| 6 — 27 Oct | Bind retained chain/bar source identity, event/availability/receipt time, revisions and digests. | Point-in-time artifact and late/corrected-data tests. |
| 7 — 28 Oct | Capture expiry, lot, underlying, volume, OI, IV/Greeks, bid/ask quantities and depth-order counts where sourced. | Per-field provenance and unavailable markers; order counts are not unique people. |
| 8 — 29 Oct | Define derived volatility and support/resistance features using only completed, available bars. | Frozen definitions, lookback/availability tests and explicit insufficient-history states. |
| 9 — 30 Oct | Assess timestamped news source rights/coverage and missing-factor behavior; do not infer narratives. | Reviewed source or `UNAVAILABLE`, with no backfill into old fills. |
| 10 — 31 Oct | Freeze incumbent, NO_TRADE and at most two challenger NIFTY policies before outcomes. | Versioned entry/exit/stop/size/capital/review contract. |
| 11 — 1 Nov | Freeze cost assumptions and exact source/data/cost artifact bindings. | Reviewed charge coverage or blocked readiness; no retrofitted historical charges. |
| 12 — 2 Nov | Enforce canonical parent search accounting across copied/renamed studies. | Concurrent denial/bypass tests and retained failed attempts. |
| 13 — 3 Nov | Restrict holdout access and record an independent validation account. | Access audit and leakage/failure tests; no repeated hidden tuning. |
| 14 — 4 Nov | Evaluate policy-matched chronological entry/exit with conservative spread, fees and nonfill. | Reproducible after-cost replay labeled separately from daily-close illustration. |
| 15 — 5 Nov | Compare qualified policies with NO_TRADE under capital/tail-risk gates and uncertainty. | Deterministic ranking only where matched evidence qualifies; otherwise abstention. No invented POP. |
| 16 — 6 Nov | Persist prospective recommendation/abstention and unresolved/rejected/no-fill outcomes. | Account-scoped, idempotent, time-bounded records with no order/promotion side effect. |
| 17 — 7 Nov | Run security, isolation, migration, lease/recovery and failure-path regression. | Focused and full test results with skip reasons. |
| 18 — 8 Nov | Verify desktop/phone Research and F&O presentation plus cost, source and uncertainty labels. | Signed-in UI evidence or exact bridge/deployment blocker. |
| 19 — 9 Nov | Attempt open-session paper lifecycle acceptance only if per-leg book-time gate passed; otherwise finish independent operational QA. | Natural signal entry/exit with ledger or honest waiting/no-signal evidence. |
| 20 — 10 Nov | Review final software candidate, deployment and gate ledger; review expiry proof without changing the saved trade unless independently justified. | Release-candidate decision and named remaining external gates, never an arbitrary percentage. |

From **11 November onward**, use each five-hour block for the next open evidence gate: licensed book-timing proof, a naturally triggered normal-session paper round trip, independently reviewed final expiry/cost settlement, and prospective policy outcomes. Report daily; repeat observations until the predeclared coverage and calibration criteria pass. If a provider cannot supply executable-book timestamps, keep F&O fills blocked and decide on an alternative provider only after a reviewed cost/rights/technical assessment. A month of elapsed time does not turn insufficient evidence into a qualified prediction.

## One daily work block (five hours)

| Time (IST) | Work |
| --- | --- |
| 09:30–10:00 | Check both Codex quota windows, repository status, service health and yesterday's open gates; choose one deliverable and its acceptance test. |
| 10:00–12:30 | Implement or review the day's highest-priority independent software/evidence task. During a normal NSE session, use a bounded read-only feed observation when it is the selected task. |
| 12:30–13:30 | Run focused unit/integration checks, account-isolation and failure-path checks; inspect safe migration/runtime impact where applicable. |
| 13:30–14:00 | Reconcile retained evidence with its source, event time, receipt time, provider, instrument, exact policy and cost version. Mark missing or unreviewed inputs explicitly. |
| 14:00–14:30 | Update the gate ledger below and BUILD_PROGRESS.md with artifact/commit/test links, actual time worked, blockers, assumptions and the next action. Report the daily delta to the owner. |

The daily report must say **PASS, PARTIAL, BLOCKED or NOT STARTED** for each changed gate, with a reason and a link or exact test result. No percentage is reported unless the same published denominator and acceptance rule are used. Never turn a green synthetic test into market-data, final-settlement or profitability acceptance.

## Ordered delivery queue

The day numbers are work packages, not guaranteed calendar dates. Advance to the next package when its acceptance checks pass; independent work may move ahead when an external gate is blocked. Reassess scope at the end of each block.

| Package | Work within five-hour blocks | Exit evidence |
| --- | --- | --- |
| 1. Publish and diagnose | Push/rebuild the pending local provider-identity change when host access permits; recheck authenticated F&O/Research desktop and phone; inspect the shared degraded worker cycle without mutating the account. | Git remote contains the exact reviewed commit; API/web revision and health are identified; UI evidence and worker cause or explicit unknown are recorded. |
| 2. Data-source decision | Compare Upstox and DhanHQ against exact NIFTY contract identity, source rights, point-in-time availability, volume/OI/IV, depth order counts, per-leg book-update time, gaps and retention. Do not subscribe or make paid calls. | Reviewed capability matrix, documented timestamp semantics and a provider acceptance/abstention decision. Order counts are never called unique buyers/sellers. |
| 3. Point-in-time artifacts | Retain or explicitly mark unavailable the raw chain/bar context, provider/instrument, event/receipt/availability times, correction history, content digest, expiry/lot, bid/ask depth and missing factors. Keep news and support/resistance separate unless timestamped, sourced and predeclared. | Reproducible artifact identity plus tests for late arrival, revised data, stale/missing depth and mismatched providers; no backfill into historical fills. |
| 4. Frozen NIFTY study | Freeze one actual incumbent, NO_TRADE and at most two challenger policies with entry, exit, stop, target, size, capital, costs, review cadence and uncertainty before inspecting outcomes. | Account-scoped immutable study and policy identities; reviewed source/data/cost rights, or an explicit UNKNOWN/ABSTAIN state. |
| 5. Parent budget and protected validation | Tie overlapping observations to one canonical search parent, enforce the bounded slot budget and restrict holdout access. Retain every rejected, failed, nonfilled and unresolved attempt. | Concurrency and bypass tests, auditable holdout access, retained failures; no copied dataset or renamed study silently resets the budget. |
| 6. Policy-matched evaluation | Evaluate the exact frozen entry/exit policy on chronological data with conservative spread, fees, size and nonfill behavior. Keep daily option-close replay separately labeled indicative. | Reproducible after-cost outcomes and uncertainty for matching policies, independent validation account, NO_TRADE comparison and explicit insufficiency when evidence is thin. No POP from win frequency. |
| 7. Prospective recommendations | Record time-bounded candidate review or abstention with exact policy/data/cost identities and reason codes. Keep any scheduler off until its runtime contract is separately accepted. | Account-scoped immutable records, stale-input rejection, idempotency, desktop/phone readability and no order/promotion side effect. |
| 8. Open-session paper acceptance | Only after documented executable-book update timing, observe normal-session quote/depth changes and a naturally triggered NIFTY paper entry/managed exit. | Per-leg fresh exchange-origin book evidence, saved decision/fill/cost trail, rejected/no-fill cases and matched position/order/cash ledger. If no legitimate signal occurs, remain waiting. |
| 9. Expiry acceptance | Independently check the final clearing settlement/corrections and applicable costs for the existing 19 October TEST ONLY 22,950 CE. | Reviewed source and explicit operator action followed by matched ledger; otherwise keep `EXPIRED SETTLEMENT PENDING` untouched. |
| 10. Release review | Run focused and full regressions, PostgreSQL recovery/concurrency checks when contracts change, security/account-isolation checks, local deployment and Edge desktop/phone review. | Gate-by-gate evidence in PROJECT_PLAN.md and BUILD_PROGRESS.md. Declare operational paper readiness only when its runtime and settlement gates pass; declare predictive ranking only after independent after-cost evidence and calibration. |

## Daily gate ledger (update only when evidence changes)

| Gate | 21 October baseline | Next evidence |
| --- | --- | --- |
| Core paper safety and accounting software | PASS for bounded software scope | Regression only after relevant change |
| PostgreSQL research reservation/lease safety | PASS for bounded isolated scope; unattended scheduler off | New acceptance only if scheduling contract changes |
| Current deployment and signed-in UI | PARTIAL; earlier build/empty states checked, pending local source not deployed, worker banner degraded | Package 1 |
| Executable NIFTY option book timing | BLOCKED by unverified per-leg exchange-origin depth time | Packages 2–3 and normal-session probe |
| Genuine normal-session NIFTY paper round trip | BLOCKED until book timing passes | Package 8 |
| Actual 19 October expiry settlement | BLOCKED pending independent final clearing/cost review | Package 9 |
| Qualified NIFTY ranking/POP | BLOCKED pending reviewed artifacts and independent policy-matched outcomes | Packages 3–7; calibrated POP may need a longer prospective sample |

**23 October checkpoint:** Day 1 source/deployment and signed-in F&O layout are PASS for the bounded local development stack. The owner pushed `9ef76ca`; the rebuilt API and paper-worker containers were healthy and each exposed `org.opencontainers.image.revision=9ef76ca8214db5bc801c722b6c90c05957473011`. The bind-mounted web returned HTTP 200 and the signed-in Edge F&O Strategies view showed the new batch-policy review control on desktop and an approximately 390 px phone viewport. No strategy was saved, armed or filled for this check. The full isolated regression passed 587 tests with 12 skipped. The revision label records the declared clean tracked checkout at build time; it does not certify market-data provenance or an immutable web image. Day 2 worker diagnosis is PASS for the observed cycle: the stored lease had one warning, the expired open option awaiting settlement; the signed-in ledger matched ₹10,14,686.75 across 27 orders. Final clearing settlement, exchange-origin NIFTY option book timing, a genuine normal-session paper round trip and evidence-qualified ranking remain BLOCKED. Actual work time was not measured; see BUILD_PROGRESS.md for scope and limits.

**Day 3 early review, 23 October — PARTIAL:** Public Upstox/DhanHQ contract, chain, feed, price and use terms are compared in `DATA_SOURCE_DECISION.md`. Dhan's support guidance does not support live-data paper trading with virtual funds; do not procure its Data API for this paper account. Upstox contract binding now rejects missing/wrong underlying, wrong requested expiry and duplicate option keys; 28 focused tests and the full 590-pass isolated regression passed. No authenticated current master/chain/lot acceptance, Upstox study-rights grant or per-leg exchange-book timestamp proof exists. Day 4 timestamp work may proceed independently, but the provider and paper-fill evidence gates remain open. Actual work time was not measured.

The data provider and exchange control some evidence. The rational assumption is to continue on independent software and review work while those gates remain blocked; it is **not** an assumption that missing data are accurate, licensed or predictive. Preserve live-order and additional-index disablement, user positions/volumes, strategy versions, the friend-testing ZIP, and replay/synthetic/paper/operator separation throughout.
