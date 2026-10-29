# Live trading: design and switch-on plan

Status: **design only. No code in this repository can place, modify or cancel a real order.**
`Settings.effective_live_trading_enabled` returns `False` regardless of configuration, and the
Upstox adapter refuses every non-GET call except the margin calculation. This document exists
so that, once the paper evidence and the owner decide to go live, the remaining work is a short,
reviewed build rather than a redesign. It expands Phase 7 of `PLATFORM_ROADMAP.md`.

## 1. What goes live first

The first live step is a pilot, not a switch-over:

- One registered strategy whose forward paper record passed the Phase 6 exit gate.
- 1 lot, NIFTY options only (futures later), defined-risk structures only.
- A capped daily loss and a capped number of orders per day (section 4).
- Paper keeps running beside it for the same signals, so live fills can be compared with simulated ones.
- 4–8 weeks, and a scale-up only by an explicit written decision.

## 2. Architecture

```
strategy / deployment ──► decision core (same as paper) ──► RiskEngine (platform limits)
                                                              │
                                     ┌────────────────────────┴──────────────────┐
                                     ▼                                           ▼
                          paper ledger (unchanged)              live execution runtime (new)
                                                                   │  pre-trade checks
                                                                   ▼
                                                          packages/broker_upstox (new)
                                                                   │  Upstox order API
                                                                   ▼
                                                          order state machine + reconciliation
```

- **One decision path.** Live entries are produced by the same code that produces paper entries, so
  paper results remain evidence for live behaviour. Strategies, AI components and the web layer
  never call the broker package; only the live execution runtime may.
- **`packages/broker_upstox`** is a separate package from `packages/market_data`. It owns the
  order-scoped token, which is a different credential from the read-only analytics token and is never
  loaded by the API process that serves the dashboard.
- **Order state machine:** `NEW → SENT → ACKNOWLEDGED → PARTIAL → FILLED | CANCELLED | REJECTED`,
  plus `UNKNOWN` when a send times out. `UNKNOWN` is resolved only by reading the broker's order
  book. It is never re-sent blindly.
- **Idempotency:** every order carries a client order id (`tag`) derived from the deployment, leg
  and attempt. A restart reads open orders by tag before sending anything.
- **Multi-leg entries:** legs are sent hedge-first (buy legs before sell legs) so a partial failure
  never leaves naked short exposure. If any leg fails, the runtime flattens the legs that did fill
  and pauses the deployment.
- **Lease and fencing:** the live runtime uses the same database lease and fencing token as the
  paper worker, so a stale process cannot send or cancel orders.

## 3. Switch-on sequence

Each step is a separate, reviewed pull request. None changes paper behaviour.

| Step | Work | Ready when |
|---|---|---|
| L1 | Compliance check with Upstox: SEBI's retail algo framework (algo id/registration, static IP, API order limits), and a personal-use-only posture | Owner has written confirmation of what applies |
| L2 | `packages/broker_upstox`: order models, state machine, order-book/positions/trades reads, a mock-broker test suite. **No send path yet** | State machine fully tested against recorded broker responses |
| L3 | Read-only reconciliation: daily comparison of broker positions, funds and contract notes against what TradePilot believes | 10 consecutive sessions with zero unexplained differences |
| L4 | Pre-trade controls (section 4) and the hard kill switch (section 5) | Every control has a failing-path test |
| L5 | Send path behind three locks: config flag, a per-deployment owner arm, and a signed release record naming the strategy, lots and loss caps | Security and risk review passed |
| L6 | Dry run: a far-OTM 1-lot order placed and cancelled during market hours, then one round trip | Fills, charges and reconciliation match |
| L7 | Pilot (section 1) | Phase 7 gates in `PLATFORM_ROADMAP.md` |

## 4. Pre-trade controls (two layers)

Platform, checked inside the account lock before every send:

- The existing 2% per-trade and 10% aggregate max-loss limits, measured on **live** capital.
- Daily realized + unrealized loss cap. Breaching it blocks entries and alerts.
- Maximum lots per order and per day, and maximum orders per minute (under Upstox's API rate limits
  and SEBI's orders-per-second threshold for unregistered algos).
- NSE freeze quantity, price bands and tick size. Limit orders only, priced from a fresh quote with a
  bounded slippage allowance. Never market orders.
- Broker margin check: `/v2/charges/margin` for the basket must be within available funds. This
  endpoint is already integrated, read-only.
- Quote freshness: the strict exchange-time rule stays. No `PAPER_ALLOW_UNVERIFIED_QUOTES`-style
  override exists for live.

Broker side, configured in the Upstox account by the owner: order value, quantity and loss limits
wherever Upstox offers them, as a backstop if the platform layer fails.

## 5. Kill switch and monitoring

- **One action** (dashboard button, CLI command and Telegram command) sets a database flag that the
  runtime reads before every send. It cancels all open orders and, if chosen, exits all live positions
  with limit orders.
- The runtime **stops on its own** on a lost lease, unknown order state, a reconciliation mismatch, a
  stale feed, or a breached daily loss.
- An alert for every order event (sent, filled, rejected, cancelled), a daily P&L and positions
  summary, and an alert when the runtime stops.

## 6. What the owner provides at switch-on

- Confirmation of the regulatory steps in L1, including any static IP the broker requires.
- An Upstox app with order permissions and its daily login flow. The order token is kept in a
  separate secret store, never in `.env` alongside the analytics token.
- Live capital and loss caps, written into the release record in L5.
- Sign-off on the security and risk reviews.

## 7. Open questions

- Does Upstox offer a sandbox or paper endpoint for orders? If not, L6 uses minimal real quantity.
- Whether multi-leg baskets can be placed atomically (they are treated as non-atomic here).
- How to handle expiry-day physical settlement risk for stock options (not applicable to NIFTY
  index options, which are cash-settled).
