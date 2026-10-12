# Trading safety gates

- `LIVE_TRADING_ENABLED` defaults to false.
- The API reports live trading as disabled unconditionally in this phase, even if the variable is set to true.
- Paper account, trade, and order endpoints are strictly simulated; there is no broker order adapter or live execution endpoint.
- Paper capital is an isolated per-user ledger. A paper fill never sends an order to a broker.
- Paper entries use server-fetched Upstox option-chain data, simulated buys at ask and sells at bid, and preserve quote/fill snapshots in the order log.
- Paper entry and exit require exchange-verified quote time by default and reject quotes older than `PAPER_QUOTE_MAX_AGE_SECONDS` (60 seconds by default), quotes more than five seconds in the future, crossed or invalid selected quotes, wide selected spreads, insufficient displayed size, and failed-quality chains.
- **The current Upstox option-chain adapter uses API receipt time, not exchange quote time. With the safe default `PAPER_ALLOW_UNVERIFIED_QUOTES=false`, real Upstox paper entries and exits are blocked.** Setting it to `true` is an explicit educational simulation override. It cannot validate exchange freshness or establish readiness for unattended trading. It does not enable live orders.
- `POST /paper/trades` requires a per-user `Idempotency-Key`. A retry with the same key and request returns the recorded position; a changed request under that key is rejected. Browser session storage retains an uncertain entry for a controlled retry.
- Default paper risk limits are 2% of starting capital for one trade and 10% for aggregate open max loss; the account kill switch blocks new entries but permits closing existing paper positions.
- Paper execution does not model partial fills, queue priority, transaction charges, tax, margin, or slippage. Positions are marked from current bid/ask quotes when available. API receipt timestamps cannot establish how current the provider's quote was.
- The independent paper exit worker runs without a browser or app JWT and uses a database lease and fencing to prevent a stale worker from committing an exit. Its status is visible at authenticated `GET /paper/worker`. If data is unavailable or unusable, it skips the check rather than inventing an exit. The operator must inspect degraded, error, or stale status and open exposure.
- No recommendation or performance result is fabricated by the application.
- Without `UPSTOX_ANALYTICS_TOKEN`, the market-data adapter reports unconfigured and data requests fail closed.
- With a token, only documented read-only Upstox market-data GET endpoints are used; no order endpoint is called.
- Option-chain responses report data-quality checks. The published [Upstox option-chain response schema](https://upstox.com/developer/api-documentation/get-pc-option-chain/) has no per-contract bid/ask event timestamp. The [V3 full-quote API](https://upstox.com/developer/api-documentation/get-full-market-quote-v3/) defines `data.timestamp` as response-generation time. We infer that neither field proves exchange freshness of the bid/ask used for a simulated fill. This is why the strict default blocks this adapter.

Live trading requires later work for broker authorization and capability verification, independent risk review, real-order state management, reconciliation, failure-mode testing, operational monitoring, and an explicit reviewed release gate. No real orders should be sent from this foundation.
