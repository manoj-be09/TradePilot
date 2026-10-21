# TradePilot daily delivery plan — 21 October 2026

This is a **five-hour-per-day work plan**, not a promised completion date. Default block: **09:30–14:30 Asia/Kolkata daily**. On non-trading days, use the block for source review, isolated implementation and testing; never describe those checks as an open-market acceptance. If a quota or host-access gate stops work, record actual time and resume after reset. Do not spend money or weaken a safety gate to keep the schedule.

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

The data provider and exchange control some evidence. The rational assumption is to continue on independent software and review work while those gates remain blocked; it is **not** an assumption that missing data are accurate, licensed or predictive. Preserve live-order and additional-index disablement, user positions/volumes, strategy versions, the friend-testing ZIP, and replay/synthetic/paper/operator separation throughout.
