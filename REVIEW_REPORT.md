# Algorithmic Trading Platform — Code, Progress and Plan Review

Reviewed: 2026-09-26. Owner preference: personal trading first, commercial expansion later.

## Recommendation

**Continue the current codebase, with a structured refactor and new trading-core modules. Do not do a full restart.**

This is a useful NIFTY options research and manual paper-trading prototype. It has real functionality, coherent domain calculations, server-side checks, persistent records, and a passing test foundation. The most important missing capability is a durable strategy runtime that can run the same versioned strategy in historical, paper and live modes. Adding more templates or AI before that foundation would increase surface area without solving the core problem.

The existing plan has the right broad ambitions but is not yet an adequate engineering delivery plan. It places some prerequisites too late, groups too much into “MVP”, and leaves acceptance criteria, operational failure behavior, data requirements and live launch controls underspecified. The revised [PROJECT_PLAN.md](PROJECT_PLAN.md) turns it into a dependency-ordered plan and preserves all 33 original requests.

## Scope and evidence limits

The implementation is located inside the supplied folder at:

`Codex/2026-09-23/files-pasted-by-the-user-master`

Reviewed the project/architecture/readme/security/trading-safety documents, backend routes and schemas, domain packages, dashboard source, migrations, tests, CI and development launch configuration. The source/test/migration inventory contained 73 Python/TypeScript/TSX/CSS files after excluding generated/dependency directories. The nearby `o-x20` directory contained no additional files returned by the file inventory. The project's `outputs` directory also contained no deliverables to review.

There is no `.git` directory in the supplied project. Therefore this review establishes the state of the supplied snapshot, not its commit history, prior conversations, an upstream repository, or a deployed service. Cached test artifacts are not treated as fresh evidence. No broker account, production data feed, or live trading deployment was exercised.

The `.env` file and local database were not opened or copied. Application source was copied to a clean review workspace, excluding credentials, dependency folders, caches and local data. No application implementation changes were made to the supplied source for this review. Review probes were added only to the isolated copy.

## Fresh validation results

| Check | Result | Meaning / limit |
|---|---|---|
| Existing pytest suite | **60 passed** | Current tests pass using local/mocked fixtures |
| Backend statement coverage | **84%**, 2,367 statements, 377 missed | Includes API and domain packages; not branch, browser, migration or operational coverage |
| `mypy apps/api packages` | **Passed**, 40 source files | Declared static types checked |
| Ruff on apps/packages/alembic/tests | **Passed** | Original source and tests; review-only probes excluded |
| Bandit on apps/api/packages | **No findings reported** | Static scanner only; not a security audit |
| Next.js production build | **Passed**, including type validation | Buildable frontend; no interactive browser acceptance testing performed |
| Four review-only diagnostic probes | **4 passed confirming four gaps** | These deliberately assert current problematic behavior; they are not release tests |

Environment: Windows, Python 3.12.14. Dependencies were freshly resolved from the project's declared ranges, including FastAPI 0.141.1, SQLAlchemy 2.1.1, Pydantic 2.13.5 and pytest 8.4.2. Frontend was installed with the available pnpm 11.25.0 and built with Next.js 15.5.26, React 19.3.0 and TypeScript 5.9.3. Package lifecycle install scripts were disabled; the build succeeded. This is not proof that the original exported `node_modules` or Python dependency folder is portable.

The original bundled Python dependencies failed import under the available Python 3.12 runtime with a missing Pydantic native module. The export includes Python 3.14 cache artifacts. Fresh compatible installation resolved the test environment; this was an environment mismatch, not an application test failure.

Pytest emitted a Starlette TestClient deprecation warning concerning its httpx integration. Track it in dependency maintenance. The fresh resolver selected versions different from some exported packages, illustrating why a supported runtime and committed lockfiles matter.

Not tested: real Upstox connectivity/entitlement, exchange-time quality, broker orders, PostgreSQL concurrency and migrations, Docker startup, restore drills, latency/load, accessibility, browser workflows, vulnerability-database audit, or strategy profitability. Passing these checks does not establish live readiness.

### Reproduction

From a clean copy with Python 3.12 and the declared dependencies installed, run:

```text
python -m pytest -q --ignore=tests/integration/test_review_diagnostics.py \
  --cov=apps/api --cov=packages --cov-report=term
python -m mypy apps/api packages
python -m ruff check apps packages alembic tests --exclude test_review_diagnostics.py
python -m bandit -r apps/api packages -q
pnpm run build   # from apps/web
```

The line continuation above is presentation notation; use a single line on Windows. To reproduce the four review probes, copy [review_diagnostics.py](review_diagnostics.py) to `tests/integration/test_review_diagnostics.py` in a disposable review copy and run that file with pytest. It uses the project's existing fixtures and a fake provider. The inherited fixture uses expiry dates in October 2026, so freeze the review date or update the fixture coherently when reproducing later. Do not interpret these probes' passing status as expected corrected behavior; regression tests should invert the relevant assertions after fixes.

## Progress reconstructed from the source

| Area | Implemented evidence | Assessment |
|---|---|---|
| Foundation/security | `auth.py`, settings, schema validation, users/roles, administrator tooling | Useful foundation, not deployment-complete security |
| Persistence | Users, effective-dated instruments, audit, accounts, paper trades/orders, notifications, replay runs, presets; seven migrations | Worth retaining; production migration proof absent |
| Market access | Read-only Upstox REST, normalized contracts and quality reports | Useful single-provider research path; no stream or durable history |
| Quantitative utilities | Indicators, Black–Scholes Greeks, payoff, OI/IV features, regime rules | Reusable pure calculations with tests; numerical/market assumptions still need broader validation |
| Options and entry rules | Nine defined-risk templates; custom 1–4 legs; server re-evaluation of ANDed conditions | Real construction workflow; not a general no-code algo engine |
| Paper ledger/risk | Bid/ask simulated fills, caps, per-user persistence, marks, close, notifications | Useful manual simulator; limited fill/accounting/risk model |
| Exit rules | Persistent trailing peak and stop/target logic, monitor API | Implemented evaluator; runtime depends on the browser |
| Historical research | Prior-close strike selection and daily open/close option replay; saved per-user runs | One-position research replay, not strategy backtesting |
| Presets | Backend schemas/routes/model/migration and integration test | No preset references/workflow found in the current dashboard source; not strategy version control |
| Frontend | Authentication, chain/analytics, builder, paper positions and replay UI in a single page | Functional source/build; needs separation and browser-level tests |
| Deployment | Development Compose, local launchers, CI definitions | Local development setup; no production operating evidence |

The migration order documents a sensible progression: core schema → paper ledger → exits → entry rules → notifications → backtest history → strategy presets. It cannot establish when or how reliably those features ran in practice.

## Confirmed findings and remediation

Priorities below are engineering priorities for this prototype. No real-money loss is alleged; there is no live order path in the inspected code.

### F1 — Duplicate entry commands create multiple positions (high)

Evidence: `apps/api/main.py:827` (`open_paper_trade`) and `PaperOrder`/`PaperTrade` models. There is no persistent entry-command identity or deduplication contract. A review probe submitted the same entry twice with the same `Idempotency-Key`; both returned HTTP 201 with different trade IDs and two open positions.

Impact: a retry after a lost response, a repeated signal, or duplicate worker dispatch can consume risk twice. The paper cap can limit the total but does not prevent duplicate intent execution.

Required fix: store a user/account-scoped idempotency key plus request hash with a unique constraint, commit intent and reservation atomically, and return the original outcome on replay. Reject reuse of the key with different content. Test concurrent retries and timeout/restart recovery, not just sequential requests.

### F2 — Stale exchange-time data can still execute a paper trade (high)

Evidence: `packages/market_data/quality.py:56` makes `STALE_SNAPSHOT` a warning; `apps/api/main.py` rejects only FAIL quality at entry, and `_require_quote` checks price/spread/size but not age. A one-hour-old exchange-timestamped snapshot returned HTTP 201 with a warning in the review probe.

Impact: paper performance can appear executable when the information is stale. The current real adapter already labels chain exchange timestamps unverified; this is a known limitation, not freshness proof.

Required fix: separate descriptive research quality from execution eligibility. Define per-session/per-feed quote-age policy, fail new entries closed when freshness is inadequate, and define exit degradation/escalation separately. A disclosed educational stale-data mode must not qualify a strategy for live activation.

### F3 — Automatic exit audit metadata can refer to another expiry (medium)

Evidence: `apps/api/main.py:1327–1328` uses the outer `chain` variable when writing `market_as_of` and quality, although fills were calculated from `trade_chain`. With two open expiries, the review probe confirmed that the first expiry's exit recorded the second expiry's `market_as_of`, while per-fill quote timestamps belonged to the first expiry.

Impact: audit evidence is internally inconsistent and cannot reliably reconstruct the snapshot used. This probe establishes the metadata bug; it does not establish that fill prices use the wrong expiry.

Required fix: persist metadata from `trade_chain`, preferably a single immutable snapshot ID shared by decision, risk evaluation and fill. Add a regression with two expiries and distinct timestamp/quality values.

### F4 — Browser DELETE preflight is rejected (medium)

Evidence: `apps/api/main.py:101` allows GET/POST/PUT/OPTIONS but not DELETE, while a DELETE preset route exists. A preflight from the configured dashboard origin for DELETE returned HTTP 400. Direct API integration tests do not emulate that browser restriction.

Impact: a browser client using the preset-delete API from the supported separate origin cannot complete the call. The current dashboard has no preset workflow, so this is an API/browser integration defect rather than a proven broken visible button.

Required fix: align CORS with supported routes and headers, test preflight/authorization, and add the missing preset UI as a separate feature. Future idempotency headers also need an explicit CORS policy.

## Important gaps and risks from static inspection

### G1 — Browser-owned monitoring is the largest immediate product gap

`apps/web/app/page.tsx:999` starts a 15-second timer. The API has a monitor endpoint but no independent worker. Closing the browser, losing connectivity, background timer throttling, or session expiry removes reliable exit checking. Stop/target/trailing features must remain labeled partial until a persistent server runtime is delivered.

### G2 — Concurrency claims need PostgreSQL proof

The service uses `with_for_update`, which is a good intention, but tests use SQLite and do not prove PostgreSQL locking behavior. `packages/database/session.py:15` uses `expire_on_commit=False`; account/trade objects are loaded, committed, provider calls awaited, and then selected under locks. Previously loaded ORM identity-map values may not be refreshed merely by issuing another SELECT. Audit this before relying on current cash, kill-switch state, or an OPEN status during concurrent requests.

This is a source-identified concurrency risk, not a reproduced PostgreSQL incident. Add deterministic two-session tests for concurrent entries, kill-switch/entry races, simultaneous manual/automatic closes and fresh balance/state reload. Use appropriate transaction scopes and explicit refresh/populate-existing or equivalent design.

### G3 — Expiry and unavailable exit data can strand simulated positions

Quality rejects past-expiry chains; closes depend on usable positive two-sided quotes and sufficient size. There is no settlement worker or explicit zero-value expiry close. Holding a paper position through expiry can therefore leave it OPEN with no normal quote-based close path. Add exchange-calendar cutoffs, settlement prices/provenance, zero-value handling and an auditable unresolved-state workflow.

### G4 — Current risk caps and paper accounting are intentionally narrow

Per-trade and aggregate risk use initial cash (`main.py:1022–1025`), not current equity/daily loss. There is no broker margin model, pending-order reservation, concentration/Greek limits or full cost ledger. Cash plus premium and theoretical expiry max loss are insufficient for a live spread book. Simultaneous bid/ask leg fills do not model hedge failure, queueing or interim margin.

### G5 — Backtest capabilities are overstated by the broad roadmap label

`packages/backtesting/replay.py` selects a template from a prior NIFTY close and uses entry-day open/exit-day close. This avoids using future spot for the reference price, but does not establish point-in-time strike availability, intraday fill sequencing or executable multi-leg prices. There is no repeated strategy signal evaluation, stop/target replay, cost/margin engine, equity curve or held-out experiment workflow. Saved results lack a complete dataset/code/engine manifest.

### G6 — Extensibility exists but orchestration is too concentrated

The API file is 1,538 lines and the dashboard page is 1,730 lines. The domain packages are relatively separable, so a rewrite is unnecessary. Extract services and UI components incrementally. Paper entry also requires the concrete Upstox provider type and uses provider-specific contract classes, so a provider protocol alone does not yet make the trading flow broker-neutral.

### G7 — Security and operations remain development-grade

The code has useful authentication and isolation patterns, but no login rate limiting, session revocation/refresh system, MFA, production secret-store integration or independent worker monitoring. The shared server market-data credential is acceptable as a personal design choice only within provider rights; it is not commercial per-tenant broker authorization. `/health` is liveness, not dependency readiness. Redis is configured but not an implemented event/rate-limit service.

The archive includes `.env` and generated dependency/runtime artifacts despite Git ignore rules. Ignoring files in Git does not prevent them being included in a ZIP/export. A clean source release should explicitly exclude credentials and personal data. No claim is made about the contents or exposure of the uninspected `.env`.

### G8 — CI does not cover the risks the product will depend on

Existing CI runs tests, Ruff, type checking and Bandit, but explicitly targets `apps/api` for the latter two rather than also targeting all domain packages. Integration tests use in-memory SQLite. There are no demonstrated PostgreSQL migration/lock tests or browser acceptance tests. Time-sensitive fixtures include a hard-coded October 1, 2026 expiry, so tests can expire with the wall clock. Broad dependency ranges and an unpinned install make future builds variable.

Coverage has useful breadth but limited depth: the adapter had 59% statement coverage, and admin/password-reset command modules were unexercised. This is a reason for targeted tests, not for treating a higher coverage percentage as a safety guarantee.

### G9 — Future market expansion needs contract-model changes

The current instrument constraint requires non-options to have no expiry, even though FUTURE is an allowed type. That cannot represent a conventional dated future accurately. Fix the instrument invariants before claiming futures support. Freeze quantities and other broker constraints also need normalization and enforcement before live order slicing.

## Continue versus refactor versus restart

| Choice | Benefit | Cost/risk | Decision |
|---|---|---|---|
| Keep extending as-is | Fast visible additions | More route/UI coupling; fragile runtime and unproven execution semantics remain | Reject |
| Preserve foundation, refactor boundaries, add missing core | Retains tested work; permits incremental correctness and migration | Requires deliberate interfaces and temporary compatibility code | **Recommended** |
| Full rewrite | Freedom to redesign everything | Recreates authentication, models, analytics and tests; does not solve data/market/operational unknowns by itself | Not justified by current evidence |

Keep: normalized contracts, payoff/pricing/indicator functions, strategy catalog, authentication foundation, schema history, useful UI flows and existing tests. Review their assumptions as coverage grows.

Refactor: API orchestration, provider coupling, ledger/risk calculations, browser request/state management, error/notification flow and persistence boundaries.

Build new behind those boundaries: strategy AST/version store, scheduler/worker/checkpoints, durable history, chronological simulator, order state machine, reconciliation and operational controls. Do not represent all these as a minor extension of an `EXECUTED`-only paper order record.

Before building a large simulator from scratch, run a bounded feasibility comparison with an established engine such as LEAN: Indian option data/broker fit, license, operational burden, strategy-definition translation and deterministic parity. QuantConnect documents separable [reality models](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/key-concepts); that is useful architecture inspiration, not evidence that this project can adopt it without adaptation. Keep this as an architecture decision, not a mandate to replace the stack.

## What changed in the plan

- Preserved all 33 original capabilities with explicit milestone mapping.
- Separated research/paper delivery from the controlled live pilot.
- Moved strategy versioning, operational monitoring, realistic simulation, basic holdouts and applicable compliance ahead of live activation.
- Added data contracts/rights/quality, durable workers, idempotency, order lifecycle, reconciliation, ledger, margin, expiry handling and fault testing.
- Added acceptance criteria, ownership roles, failure behavior, proposed operational targets and evidence-based launch gates.
- Incorporated complementary product ideas from QuantConnect, TradingView, Tradetron, Sensibull, AmiBroker, NinjaTrader and MetaTrader, with primary-source links in the plan.
- Kept AI, marketplace, commercial multi-account features and sophisticated multi-broker execution later.

The next step is M0: fix the concrete defects, establish reproducible builds and PostgreSQL tests, and extract services. Then deliver one versioned strategy end to end through credible backtesting and unattended paper operation. That sequence makes the existing work useful while controlling the most expensive future risks.
