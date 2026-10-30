# TradePilot status

Short current state. The forward plan is `PLATFORM_ROADMAP.md`; dated history is `BUILD_PROGRESS.md`; how live trading would be added is `LIVE_TRADING.md`. Updated 29 October 2026.

Scope: NIFTY 50 futures and options, **paper trading only**. Live orders are forced off.

## What works

**Paper trading**
- Defined-risk NIFTY option strategies (26 templates plus custom legs) and NIFTY futures, entered from live Upstox quotes into a simulated account.
- Four NIFTY futures templates (intraday and positional, long and short) start with one click from Trade, wait for their rules and are exit-managed by the worker. No evidence of an edge yet; forward paper results will be the first test.
- Risk limits enforced under the account row lock: 2% of starting capital per trade, 10% aggregate open maximum loss, daily loss budget, kill switch (pause entries) on every page.
- Strategies can be armed to wait for entry conditions; the independent paper worker checks entries and exits (stop, target, trailing stop, expiry square-off) under a lease with fencing.
- SPAN-style margin estimate with exposure and expiry-day margin; within ±5% of Upstox's margin calculator on seven sample positions (29 Oct 2026).
- Versioned charge model (brokerage, STT, exchange, SEBI, stamp duty, GST) applied to fills.
- Automatic demotion: a strategy's new entries pause when its closed paper drawdown passes 2× its backtest baseline (6% of capital per lot without one).
- Ledger reconciliation check; a mismatch pauses new paper cash actions.

**Data and research**
- Expired NIFTY option candles imported from Upstox (2 years, 5-minute) into private local storage.
- Feed V3 recorder for live quotes (private, never redistributed).
- History backtester over the imported candles with spread calibration from recorded quotes, charges, margin, return on capital, folds and drawdown baselines.
- Strategy ranking (four specialists plus an after-cost selector) that abstains with a reason when evidence is missing.

**Web app**
- Five pages: Home, Trade, Chart, Backtest, Research; one status row (paper, exit worker, market data, entries on/paused); phone bottom tab bar.

## What's next

1. Calibrate backtest spreads from the first full recorded session and rerun all 26 strategies with drawdown baselines.
2. Phase 0 cleanup: remove backend equity routes and services; drop the `Equity*` tables only after the owner decides whether to export them.
3. Walk-forward evaluation by expiry (roadmap Phase 4) so strategies are judged on unseen periods.
4. Unattended paper operations (Phase 6): scheduler, alerts (Telegram needs `TELEGRAM_BOT_TOKEN` from the owner), runbooks and fault-injection tests.
5. Six weeks of forward paper trading, then a decision on a 1-lot live pilot (`LIVE_TRADING.md`).

## Known limits

- Backtests use 5-minute candle closes with a modelled spread, not historical bid/ask; exits are checked on bar closes, so stops can be overshot.
- Two years of history, hold-to-expiry rules and one entry time per strategy: too few independent samples to claim an edge. On that history most strategies lose after costs (simulated).
- Feed V3 has no exchange-origin depth timestamp; paper fills from unverified quote times are allowed only by the local educational setting.
- Margin is an estimate from configured parameters, not NSE's daily SPAN file; checked on one evening and one expiry.
- Postgres-only integration tests run in CI, not on the local SQLite setup.
- Nothing here is investment advice. All results are simulated.
