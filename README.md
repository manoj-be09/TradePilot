# TradePilot

NIFTY 50 futures and options research and **paper-trading** platform: authenticated Upstox market data, 26 defined-risk option strategy templates plus NIFTY futures, a simulated account with risk limits and a kill switch, a server-side paper worker for entries and exits, a SPAN-style margin estimate, versioned charges, a history backtester over imported option candles, and a Next.js dashboard.

## Safety status

Live broker execution is forced off. Paper trades fill at the current quote (buys at ask, sells at bid) and apply modelled charges and margin; they do not model queue priority or partial fills. Paper execution requires exchange-verified quote time by default; the Upstox chain supplies API receipt time, so local paper fills rely on the explicit educational override described below. Nothing in the dashboard is investment advice, and all results are simulated.

See [STATUS.md](STATUS.md) for what works now and what's next, [PLATFORM_ROADMAP.md](PLATFORM_ROADMAP.md) for the forward plan, and [BUILD_PROGRESS.md](BUILD_PROGRESS.md) for dated history.

## Mobile web shell

The responsive dashboard includes a web-app manifest, icon, theme color, and mobile safe-area spacing. Market data and account actions require a live API connection; the app does not cache authenticated data for offline trading. A native mobile application remains a later milestone.

## Account security

Signed-in users can change their app password under **Account security**. The API requires the current password, applies the same attempt limiter as sign-in, revokes every active app session, and requires a fresh sign-in. The same panel shows recent security activity scoped to that account, without exposing audit metadata. The Upstox Analytics Token remains separate and server-side.

## Development

Use Python 3.12, Node 24, and pnpm 11.25. Copy `.env.example` to `.env`, then run `docker compose up --build`. The API applies Alembic migrations before serving; the separate `paper-worker` starts after the API health check passes. API: `http://localhost:8000/health`; web: `http://localhost:3000`. `/health` reports API liveness only. `/ready` returns 200 only when the database and configured authentication limiters are usable; it returns a sanitized, non-cacheable 503 otherwise. Compose uses `/ready` before starting dependent services. Sign in and check `/paper/worker` or the dashboard for exit-worker status. The example PostgreSQL password is for a local development stack; if you change `POSTGRES_PASSWORD`, update `DATABASE_URL` to match.

The Python runtime and test dependencies are pinned in `requirements.lock` and `requirements-dev.lock`; the web uses `pnpm-lock.yaml`. Docker and CI install from those files. The local PowerShell scripts use a `.venv` made with Python 3.12.

### Local API without Docker

For API exploration without PostgreSQL, open PowerShell in the repository root and install the pinned development environment once:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-deps -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
.\.venv\Scripts\python.exe -m pip check
```

Then start the API:

```powershell
.\scripts\start-api-local.ps1
```

The launcher uses `.venv`, initializes the local SQLite schema without deleting existing data,
and starts the current API at `127.0.0.1:8000`. If an older API
already owns that port, stop it with **Ctrl+C** in its original PowerShell window before running
the launcher. Use `-Port 8001` to start on another free port.

`init_local_db` creates missing tables and adds new paper-trading columns without deleting existing
users, instruments, paper trades, or backtest runs.

Then use `http://127.0.0.1:8000/docs`. SQLite is only a local exploration mode; it is not used for production migrations.

Start the independent paper exit worker in a second PowerShell window:

```powershell
.\scripts\start-paper-worker-local.ps1
```

The worker checks open positions every 15 seconds by default and continues when the dashboard is closed or the app JWT expires. `GET /ready/paper-worker` is a separate, non-cacheable readiness probe for worker supervision: it returns 200 for a recent healthy cycle or a valid active lease, and 503 for missing, stale, expired, degraded, or failed worker state. It does not return account or provider details. For a single diagnostic cycle, use `-Once`; that process then exits. The worker shares the local SQLite database with the API. PostgreSQL is the deployment and concurrency-test path. The signed-in `GET /paper/worker` response reports its last heartbeat, last successful check, and status (`HEALTHY`, `DEGRADED`, `ERROR`, or `STALE`, among others). Review warnings and open positions whenever it is not healthy. An outage or unusable quote skips an exit check; it never fabricates a fill.

### Market data recorder

`docker compose up -d recorder` records every NSE F&O session through Upstox Market Data Feed V3 (one of the two Feed V3 connections Upstox allows). Each day it subscribes to NIFTY 50, India VIX, the three NIFTY futures and NIFTY options for the nearest three weekly expiries plus the monthly expiries, ATM ±20 strikes (about 415 instruments), and widens the strike window if NIFTY moves.

- Raw frames are stored losslessly in `data/feed_v3/<date>/<HH>.frames.gz`, with subscribed contract metadata in `universe.jsonl`.
- One row per instrument per minute goes to `market_snapshots_1m` (LTP OHLC, last bid/ask, 5-level depth, volume, OI, IV, Greeks), plus `recorded_instruments`, `recorder_events` and `recorder_day_summaries`.
- If the database is unavailable or not migrated, raw capture continues. Afterwards, rebuild a day's rows with `python -m apps.recorder.main --data-dir data --rebuild YYYY-MM-DD`.
- Timestamps are local receipt time and Upstox message time; neither is an exchange book time, so recordings do not unblock strict paper fills.
- Upstox allows this private retention for replay and backtesting but **not redistribution**: keep `data/` private and out of Git, shared drives and friend-testing environments.

### Connect Upstox market data

If the dashboard shows **Unavailable**, use **Check connection** after confirming the API is running. A response with `configured: true` and `Could not connect to Upstox` means the API host could not complete an outbound HTTPS request; it is distinct from an HTTP 401/403 token rejection. In restricted development environments, grant outbound access and restart the API process, then refresh the chain. Keep the token in the server environment.

The API can use an Upstox Analytics Token for read-only NIFTY option contracts, option chain,
quotes, and historical candles. The token stays in the API process and is never returned by an
endpoint. No Upstox order API is used, and live trading remains disabled.

1. In the Upstox developer portal, create an Analytics Token with market-data access. Keep it
   private; do not paste it into chat, Swagger, or a source file.
2. Open the repository's `.env` file and set `UPSTOX_ANALYTICS_TOKEN` to the token value. The
   `.env.example` file shows the variable name. If `.env` does not exist, copy `.env.example` to
   `.env` first.
3. Restart the API from the repository root so it loads the new value, and restart the paper worker if it is running:

   ```powershell
   .\scripts\start-api-local.ps1
   ```

4. In `/docs`, register or log in through `/auth/register` or `/auth/login`. Click **Authorize**
   and enter the app's returned `access_token` (not the Upstox token).
5. In the signed-in dashboard, click **Check connection** to request `/market-data/status`. A configured token is reported separately from a successful Upstox response. You can also call `/market-data/option-contracts` to see available expiry dates
   and contract keys. Copy an expiry and call `/market-data/option-chain?expiry_date=YYYY-MM-DD`.
   The chain response includes call/put quotes, Greeks, comparison fields, and a data-quality
   report. `/market-data/quote` and
   `/market-data/historical` accept an Upstox `instrument_key` from the contracts response.

The option-chain `as_of` value is when this API received the response; Upstox's chain response
does not provide a per-contract exchange timestamp. WebSocket streaming is not implemented in this
adapter yet. `/market-data/status` makes a read-only NIFTY option-contract request to check token
validity and connectivity. It returns only a configured/healthy result and a sanitized
message; it never returns the token. This check can take up to 15 seconds when Upstox
is unreachable. Because API receipt time cannot prove quote freshness, the current adapter cannot pass the default paper execution gate. For an explicitly labeled educational simulation only, set `PAPER_ALLOW_UNVERIFIED_QUOTES=true` in the server `.env` and restart the API and worker. The 60-second `PAPER_QUOTE_MAX_AGE_SECONDS` check, spread/size checks, and other risk gates still apply, but the source quote time remains unverified. Leave the override off for normal operation.

### Shared instrument catalog and live discovery

The authenticated `GET /instruments` endpoint now searches effective-dated INDEX, EQUITY, ETF,
FUTURE, and OPTION records. Filter by `instrument_type`, `exchange`, `underlying`, `expiry`, or a
symbol/underlying `search`; `limit` is 1–100 (default 50) and `offset` is bounded. Administrators
can register records through `POST /instruments`. Spot instruments omit expiry/strike/option type;
futures require expiry; options require expiry, strike, and CE/PE. The dashboard has a collapsible
**Market instrument catalog** with search and type filters. This is reference data, not evidence
that quotes or trading are available for every listed instrument. Live data and paper execution
still cover the NIFTY options slice. Migration `0011_multi_asset_instruments` updates database
checks and refuses an existing future without expiry so its contract metadata can be repaired
before upgrade.

The catalog also shows **What this build can do**, backed by authenticated
`GET /market-data/capabilities`. For index, equity, ETF, future and option rows it separates
read-only discovery, snapshot quotes and daily history from paper entries and live orders.
“Requestable” means the route and server token are configured; it does not verify connectivity,
subscription entitlement, coverage or quote freshness. Strict option paper entries remain safety
blocked, and live orders are disabled for every asset class.

The same panel can [search Upstox instruments](https://upstox.com/developer/api-documentation/instrument-search/)
by symbol/name and request a [full market quote](https://upstox.com/developer/api-documentation/get-full-market-quote-v3/)
for a selected result. The authenticated API endpoint is `GET /market-data/instrument-search`
with a 3–50 character `query`, `exchange` (NSE, BSE, MCX) and `segment` (EQ, FUT, OPT, INDEX).
It returns at most 20 normalized first-page results. `/market-data/quote` accepts a selected
`instrument_key`; it was smoke-tested with a read-only NSE equity quote. Provider results are
temporary discovery data, not imported catalog records or trading entitlements. Quotes are
snapshots, not a streaming feed or execution approval. The Upstox token remains on the server.
The quote view labels the provider's response timestamp separately from its last-trade time.
If either source field is absent, it is labeled as an API receipt time or unavailable; a recent
response alone is not proof that the last trade or order-book prices are fresh.

Signed-in users can save a discovered instrument to **Your watchlist**. The server repeats the
bounded provider search before saving its metadata, limits each account to 100 unique items, and
returns an existing item on duplicate submission. `GET /watchlist`, `POST /watchlist` and
`DELETE /watchlist/{item_id}` are account-scoped; deletion never addresses another user's item.
Saved instruments can request fresh read-only quotes. Watchlist membership is not an order intent
and does not authorize trading. Migration `0012_watchlist_items` adds the database record.

Selecting a discovery result or watchlist item also opens a read-only daily closing-price chart
and recent OHLCV table from `/market-data/historical`. The chart shows the actual dates and number
of candles returned within the requested 45-day window. Missing history or provider entitlement
is reported in the panel; no prices are estimated or substituted. This view is for research and
does not enable an equity or futures order workflow.

### Analytics (read-only)

The dashboard calculates put/call OI and volume ratios, nearest-strike IV skew, OI concentration,
and spread/liquidity observations from the loaded chain. These use the same provider snapshot; the
app sends it to `POST /analytics/options/summary` for calculation. `GET /analytics/options` can
fetch and summarize a chain directly in Swagger.

`GET /analytics/indicators` and `GET /analytics/regime` use `/market-data/historical` data. Supply
an instrument key, `start`, `end`, and interval (`1m`, `5m`, `15m`, `30m`, `1h`, or `1d`). Indicators
include SMA, EMA, RSI, MACD, ATR, ADX, Bollinger Bands, session VWAP, momentum, rate of change, and
20-return annualized realized volatility. Short-history values are returned as `null` with their
names in `unavailable`. Regime labels expose their rules and reasons; they are heuristics, not
probabilities or trade signals.

`POST /analytics/greeks` accepts a European Black-Scholes input with explicit spot, strike, expiry,
volatility, continuously compounded rate, and dividend yield. Volatility and rates are decimals
(`0.20` means 20%). It returns theoretical price, Greeks, and optional implied volatility when a
market price is supplied. `POST /analytics/payoff` accepts signed multi-leg positions, entry
premiums, the actual contract multiplier, and a requested price range. It returns expiry P&L,
breakevens, and bounded or unbounded maximum profit/loss. These calculations exclude costs, slippage,
margin, and early exits.

IV rank and IV percentile require stored option-chain history, and term structure requires matching
snapshots across expiries. Those values remain unavailable until that data is collected; the API
does not synthesize them.

### Historical options replay

The dashboard's **Replay one NIFTY options position** panel can replay one of the nine registered
strategy templates against a manually selected expired NIFTY expiry, entry date, exit date, and lot
count. The API uses the previous NIFTY daily close to choose an available reference strike, buys or
sells each option leg at its entry-day daily open, and values the exit at its exit-day daily close.
The result includes per-leg P&L, gross position P&L, theoretical maximum expiry loss at entry, and illustrative net P&L after declared flat per-leg fees and per-unit slippage on both sides. Older saved reports show “not modeled” for net figures because they did not capture cost assumptions. New single-expiry reports also store a SHA-256 fingerprint of the selected spot and option observations, strategy template version, lot count, dates, and declared costs; a matching fingerprint is a drift check, not an immutable market-data archive.
Successful runs are saved to the signed-in user's account and appear in recent replay history after
refresh; opening a saved run displays its original assumptions and warnings.

The **Replay a template across expiries** panel runs the selected template and lot count across up to four past expiries. It enters a fixed number of NIFTY trading sessions before each expiry and exits on expiry. The run saves per-expiry gross and estimated net P&L, cumulative net P&L, a closed-trade drawdown, declared fee/slippage inputs, and a fingerprint of the selected observed inputs. Missing sessions or option candles fail the complete run. This is a fixed-schedule research replay, not a strategy signal backtest. The fingerprint does not preserve an immutable market-data snapshot.

This first replay slice requires Upstox expired-option historical data access (currently an Upstox
Plus entitlement). Without it, the panel reports the provider's access error instead of fabricating
history. Upstox documents its [expired option contracts](https://upstox.com/developer/api-documentation/get-expired-option-contracts/),
[expired candles](https://upstox.com/developer/api-documentation/get-expired-historical-candle-data/),
and [backtesting access](https://upstox.com/developer/api-documentation/backtesting/). The replay
supports a single position or a bounded fixed-expiry schedule, not a repeated entry/exit signal
backtest; it does not yet support custom structures. Candle OHLC prices are theoretical observations, not confirmed fills, and the result
excludes actual broker charges, taxes, spread dynamics, margin, market impact, and intraday execution in both modes. Both modes accept illustrative fee and slippage estimates; these do not establish executable prices. It is for research and paper
practice, not a performance guarantee.

### Authentication attempt limits

Sign-in and account creation are limited separately by client address and normalized email. Exceeded limits return HTTP 429 with a Retry-After header. Development uses a local process limiter; non-development deployments require Redis and fail those requests closed when it is unavailable. Configure `LOGIN_*` and `REGISTRATION_*` limits in the server environment. The reverse proxy must provide a trustworthy client address to the API; do not trust arbitrary forwarded headers from clients.

### Dashboard

The web dashboard signs in to the local API and displays the selected NIFTY expiry's option chain
and the paper-trading workspace. It keeps the app access token in the browser session only. While the tab is active, it renews the 30-minute app token through `/auth/renew`; a session has an absolute 12-hour limit and then requires sign-in. A temporary API outage does not erase the saved session token. Manual sign-out calls `/auth/logout` to revoke the active server session; tokens issued before the revocable-session upgrade are exchanged on renewal and otherwise expire within their existing short lifetime. Upstox credentials remain server-side. Start the API first, then open a second PowerShell window in the repository root and run:

```powershell
.\scripts\start-web-local.ps1
```

Install pnpm with `npm install -g pnpm@11.25.0`, then run `pnpm install --frozen-lockfile` in `apps/web` once if the web dependencies are not installed. The launcher starts
Next.js at `127.0.0.1:3000`; use `-Port 3001` if that port is already occupied.

Open `http://127.0.0.1:3000`, sign in with the local app account, choose an expiry, select a strategy,
reference strike, and lot count, optionally set a stop loss, profit target, or trailing stop, then
choose **Open paper trade**. To build your own position, choose **Custom multi-leg**, add one to four
legs, select each call or put, buy or sell side, strike, and ratio. Open positions can be marked from
current quotes and closed from the paper portfolio. Optional entry conditions can filter on PCR,
ATM IV skew, OI concentration, median spread, and NIFTY spot. All selected conditions must pass, and
the API evaluates them again from its own fresh option-chain snapshot before recording a paper trade.
The selected rules and their values are saved in the trade history. The independent paper worker checks configured exits every 15 seconds by default, including while the browser is closed. The dashboard refreshes portfolio data separately; it is not an exchange-grade order monitor. **Pause entries** is the paper kill switch; closing an existing position remains
available while entries are paused. The **Saved presets** panel stores a named template strategy and up to five entry conditions for the signed-in user. Loading a preset restores only those fields; recheck the current expiry, strike, lots, and exit rules before opening a paper trade. Custom multi-leg structures are not supported by this preset format yet.

Exit amounts are total-position rupee P&L thresholds. A trailing stop follows the highest paper P&L
seen and begins protecting only after the position becomes profitable. A failed-quality or unusable
quote skips that check and is reported; these paper rules do not guarantee a fill at the selected
threshold.

New paper entries are rejected once their contract expiry date has passed in Asia/Kolkata. If an existing paper position remains open after that date, the worker and portfolio report it as unresolved, skip new quote-based marks or exits, and leave the trade and cash ledger unchanged. A manual close is also blocked after expiry. This is a safety hold, not expiry settlement; reconcile the contract outcome manually until a tested settlement workflow is implemented. Paper fills and quote-based marks also require an open NFO schedule from the [Upstox Market Timings API](https://upstox.com/developer/api-documentation/get-market-timings/). Missing, closed, or invalid timing data blocks fills. The schedule check does not detect unscheduled intraday halts; a real-time exchange-status feed and settlement workflow remain pending.

Paper accounts start with `INITIAL_PAPER_CAPITAL` (default ₹10,00,000). Risk defaults limit one
trade to 2% of starting capital, aggregate open risk to 10%, open positions to 10, and the daily loss budget to 5%. New entries are blocked when Asia/Kolkata-day realized losses plus open theoretical maximum loss and the proposed trade risk exceed that daily budget. Profits do not increase the budget. Configure `PAPER_MAX_TRADE_RISK_PCT`, `PAPER_MAX_PORTFOLIO_RISK_PCT`, `PAPER_DAILY_LOSS_LIMIT_PCT`, and `PAPER_MAX_OPEN_TRADES` before starting the API. This budget omits open mark-to-market losses, costs and settlement effects; it is only one conservative entry gate. The order log records every simulated opening and closing fill. The Updates bell
shows persistent, account-scoped notices for paper position opens/closes, automatic exit triggers,
and kill-switch changes; unread notices can be marked read there.

Paper entries require a configured Upstox market-data token and an `Idempotency-Key` header (8–128 ASCII letters, digits, `_`, or `-`). Reuse the same key and identical request body after an uncertain response; the API returns the original recorded trade rather than opening another. Reusing a key with a changed body returns `409`. The dashboard retains an uncertain entry in browser session storage and offers a retry. The server re-fetches the chain and contract master for each new trade; it blocks failed-quality chains, unverified/stale or invalid/crossed quotes, wide selected-contract spreads, insufficient displayed size, and risk-cap breaches. See the educational override above for the current Upstox chain timestamp limitation.

For a different web origin, set `CORS_ORIGINS` in
the API `.env` to the comma-separated list of allowed origins before restarting the API.

Provision the first administrator with `python -m apps.api.create_admin` after applying `alembic upgrade head` and setting `DATABASE_URL`. Public registration only creates `VIEWER` users.

Backend checks from the repository root: `.\.venv\Scripts\python.exe -m pytest -q`, `.\.venv\Scripts\ruff.exe check apps packages alembic tests`, `.\.venv\Scripts\mypy.exe apps/api apps/paper_worker packages`, and `.\.venv\Scripts\bandit.exe -q -r apps/api apps/paper_worker packages`. Frontend: `pnpm run lint` and `pnpm run build` from `apps/web`. CI also runs PostgreSQL migration and separate-session concurrency tests via `TEST_POSTGRES_URL`.

Immutable template strategy versions are available through `GET/POST /strategies/versions` and in the paper-trading dashboard. A saved revision captures the registered template version and legs, lots, entry filters, and stop/target/trailing amounts; edits create a numbered version. The dashboard compares changed fields between two saved versions and exports a selected definition as JSON for review. An exported JSON file can be loaded as an editable, unpinned draft; saving that draft creates a new server-validated version. Imports are restricted to the current registered template version and never become trusted or active automatically. A paper entry can supply `strategy_version_id`, in which case the API checks that the current request and template still match the saved definition and records its ID and SHA-256 hash in the entry snapshot. Existing limited presets remain separate. Expiry and reference strike are chosen at entry time. Historical single-expiry and scheduled replay can attach a saved version only when it has no entry filters or automatic exits. The API verifies the template, legs, and lots and writes the version ID/hash into the saved result. These candle replays cannot evaluate saved filters, stops, targets, or trailing exits, and the paper worker does not yet run a shared strategy-entry engine.

Database evolution is managed by Alembic. Apply `alembic upgrade head` to add the paper account,
trade, order, notification, backtest history, idempotency-command, worker-lease, auth-session, and strategy-version tables. Local SQLite exploration can use
`python -m apps.api.init_local_db` to create the current ORM schema.

See [ARCHITECTURE.md](ARCHITECTURE.md), [SECURITY.md](SECURITY.md), and [TRADING_SAFETY.md](TRADING_SAFETY.md).
