# TradePilot status

Short current state. The forward plan is `PLATFORM_ROADMAP.md`; dated history is `BUILD_PROGRESS.md`; how live trading would be added is `LIVE_TRADING.md`. Updated 30 October 2026.

Scope: NIFTY 50 futures and options, **paper trading only**. Live orders are forced off.

## What works

**Paper trading**
- Defined-risk NIFTY option strategies (26 templates plus custom legs) and NIFTY futures, entered from live Upstox quotes into a simulated account.
- Four NIFTY futures templates (intraday and positional, long and short) start with one click from Trade, wait for their rules and are exit-managed by the worker. No evidence of an edge yet; forward paper results will be the first test.
- Risk limits enforced under the account row lock: 2% of starting capital per trade, 10% aggregate open maximum loss, daily loss budget, kill switch (pause entries) on every page.
- Strategies can be armed to wait for entry conditions; the independent paper worker checks entries and exits (stop, target, trailing stop, expiry square-off) under a lease with fencing.
- SPAN-style margin estimate with exposure and expiry-day margin; within ±5% of Upstox's margin calculator on seven sample positions (29 Oct 2026).
- Versioned charge model (brokerage, STT, exchange, SEBI, stamp duty, GST) applied to fills.
- Automatic demotion: a strategy's new entries pause on 5 consecutive closed paper losses, when its closed paper drawdown passes 2× its backtest baseline (6% of capital per lot without one), or when a closed trade has no usable P&L. It then ranks as blocked and can't be armed.
- Rule-based promotion: after 7 days a paused strategy goes on one-lot probation; 5 closed probation trades with a positive total, inside tighter limits and with a saved baseline, make it eligible again. Every step is listed on Research. The forward runner skips a paused strategy and keeps arming the others.
- Ledger reconciliation check; a mismatch pauses new paper cash actions.
- Supervisor: one command starts all local paper processes and restarts any that crash (`scripts/start-all-local.ps1`).
- Forward paper runner: keeps a frozen set of saved strategy versions armed once per expiry for a fixed period, through the same checks as the web app (`scripts/start-forward-runner-local.ps1`).

**Data and research**
- Expired NIFTY option candles imported from Upstox (2 years, 5-minute) into private local storage.
- Feed V3 recorder for live quotes (private, never redistributed).
- History backtester over the imported candles with spread calibration from recorded quotes, charges, margin, return on capital, folds and drawdown baselines.
- Strategy ranking (four specialists plus an after-cost selector) that abstains with a reason when evidence is missing.

**Web app**
- Five pages: Home, Trade, Chart, Backtest, Research; one status row (paper, exit worker, market data, entries on/paused); phone bottom tab bar.

## What's next

1. Forward paper period: all 26 option templates frozen, 2 November to 14 December 2026. The first period (30 October) was stopped the same day because exits fired within minutes; the exit rules were fixed and it restarts on 2 November.
2. Calibrate backtest spreads from recorded sessions and save drawdown baselines. Promotion stays on hold until baselines exist.
3. After the period: a decision on a 1-lot live pilot (`LIVE_TRADING.md`).
4. Deferred until after that decision: removing backend equity code and tables, and splitting `main.py` and `page.tsx`.
5. Owner steps: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and `TELEGRAM_ALERT_USER_EMAIL` for alerts; `scripts/install-autostart.ps1` to start at sign-in.

## Known limits

- Local services stop if the machine sleeps or the process that started the supervisor ends.
- Proposed exit rules are exploratory rules of thumb, and their rupee amounts stay fixed for a whole forward period.
- Not every strategy can be started with one click from Trade: only those the ranking shows. The rest go through the strategy builder.

- Backtests use 5-minute candle closes with a modelled spread, not historical bid/ask; exits are checked on bar closes, so stops can be overshot.
- Two years of history, hold-to-expiry rules and one entry time per strategy: too few independent samples to claim an edge. On that history most strategies lose after costs (simulated).
- Feed V3 has no exchange-origin depth timestamp; paper fills from unverified quote times are allowed only by the local educational setting.
- Margin is an estimate from configured parameters, not NSE's daily SPAN file; checked on one evening and one expiry.
- Postgres-only integration tests run in CI, not on the local SQLite setup.
- Nothing here is investment advice. All results are simulated.
