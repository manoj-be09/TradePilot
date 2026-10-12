# Architecture

The monorepo contains a FastAPI service in `apps/api`, an independent paper exit worker in
`apps/paper_worker`, a Next.js dashboard in `apps/web`, and domain packages under `packages/`.
The current path is:

```text
Upstox read-only REST
        ↓
Normalized market-data contracts
        ↓
Option-chain data-quality report
        ↓
Indicators, chain features, option analytics, and regime classification
        ↓
Authenticated research dashboard → manual paper entry → risk controls
                                                  ↓
Per-user paper ledger ← simulated bid/ask fills → audit/order history
          ↑
Independent paper exit worker → quote and exit checks
```

The Upstox adapter currently supports NIFTY 50 option contracts, option chains, quotes, and
historical candles, including expired option contracts and candles where the account's Upstox
entitlement permits them. It uses the account's Analytics Token only for GET requests. The token is
loaded from server configuration and is never returned to the browser. WebSocket streaming and
secondary-provider failover are not implemented. Upstox expired-option history currently requires
its Plus entitlement; the API returns a clear access error when the account lacks it.

Every option-chain response includes a quality report. It checks expiry and contract consistency,
duplicate instrument keys and strike sides, missing quote fields, crossed books, and wide spreads.
The Upstox option-chain response has no per-contract exchange timestamp, so the report marks
freshness as unverified instead of treating API receipt time as exchange time. Invalid prices are
rejected by the normalized Pydantic contracts. The paper execution gate is stricter than the
research quality report: it rejects unverified quote time by default. The current Upstox chain
therefore cannot open or close paper positions unless an operator explicitly sets
`PAPER_ALLOW_UNVERIFIED_QUOTES=true` for educational simulation. That override does not establish
exchange freshness or unattended readiness.

The analytics package calculates trailing price indicators from provider historical bars and
chain-level OI, volume, IV-skew, and spread features from a supplied snapshot. IV rank, IV percentile,
and term structure remain unavailable until historical option-chain snapshots are stored. The
options package provides explicit-input European Black-Scholes calculations and multi-leg expiry
payoffs. Regime labels use documented rule thresholds and include reasons; they are not probabilities
or trading signals.

The dashboard requires local app authentication. Its bearer token is kept in browser session
storage. CORS allows the two local dashboard origins by default and can be configured with
`CORS_ORIGINS`.

Paper trade entry requires a per-user `Idempotency-Key`, records the request hash and response, and
replays an identical retry without opening another position. A changed request under the same key
is rejected. Entry re-fetches the selected expiry's option contracts and chain server-side. The API
supports nine capped-loss templates plus custom one-to-four-leg same-expiry structures. It sizes
from the provider's lot size, calculates each structure's maximum expiry loss, applies quote and
data-quality checks, and enforces per-trade and aggregate loss limits while locking the account row.
Opening and closing fill snapshots, quote times, warnings, and account state changes are stored.
Optional no-code entry filters use current chain PCR, IV skew, OI concentration, relative spread, or
spot. The API re-evaluates them on its fresh snapshot and stores both conditions and actual values;
an unavailable metric cannot silently pass a filter.
Current open P&L is calculated from available close-side quotes and omitted when a safe mark is not
available. The kill switch pauses new paper entries; it does not pause exits.

Paper stop loss, profit target, and trailing stop values are rupee P&L thresholds for the whole
position. The trailing stop persists the highest observed paper P&L and begins only after profit.
The independent worker evaluates rules from current chain quotes, skips failed or unusable data with
a warning, and records triggered closes. It runs without a browser or JWT, acquires a database lease,
and fences off an expired worker before account mutation. A restart can resume checking open trades;
the signed-in `/paper/worker` endpoint exposes heartbeat and last success. The dashboard refreshes
portfolio display separately. This is still immediate bid/ask paper simulation, without partial fills
or exchange-grade execution assurance.

PostgreSQL and Alembic remain the production database path; SQLite is provided only for local
exploration. Redis is configured but not yet used for rate limiting or event delivery.

The backtesting package currently supports one historical replay of a registered strategy template
against a manually selected expired NIFTY expiry. It chooses strikes from the prior session's NIFTY
close, uses each option's entry-day open and exit-day close, and calculates gross P&L and theoretical
maximum expiry loss. Successful runs and their assumptions are stored per user and can be listed or
reopened from the dashboard. It does not execute repeated strategy signals, replay custom
structures, model bid/ask or costs, or establish executable fills. Repeated signal backtesting
remains a separate planned capability.

## Planned boundaries

Market-data adapters → validation and quality → indicators and features → option strategy selection
→ deterministic risk approval → paper execution → separately reviewed live execution. Current paper
strategy catalog contains nine defined-risk templates plus a custom multi-leg builder. The no-code
filters are entry gates on current-chain metrics; reusable indicator-driven signal construction is
not implemented. Persistent in-app notifications are implemented for paper events; external delivery
channels are not. Historical position replay is implemented with the limitations above, while
repeated signal-based backtesting is not. Strategies and any future AI component must never call a
broker directly. Live execution remains forced off regardless of environment configuration. Paper
stop, target, and trailing checks run in the independent paper worker when it is deployed and healthy.
Scheduled strategy entries and a reusable strategy runtime are not implemented.
