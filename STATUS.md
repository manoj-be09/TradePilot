# TradePilot status

Short current state. The forward plan is `PLATFORM_ROADMAP.md`; dated history is `BUILD_PROGRESS.md`; how live trading would be added is `LIVE_TRADING.md`. Updated 1 November 2026.

Scope: NIFTY 50 futures and options, **paper trading only**. Live orders are forced off.

## What works

**Paper trading**
- Defined-risk NIFTY option strategies (26 templates plus custom legs) and NIFTY futures, entered from live Upstox quotes into a simulated account.
- Four NIFTY futures templates (intraday and positional, long and short) start with one click from Trade, wait for their rules and are exit-managed by the worker. No evidence of an edge yet; forward paper results will be the first test.
- Risk limits enforced under the account row lock: 2% of starting capital per trade, 10% aggregate open maximum loss, daily loss budget, kill switch (pause entries) on every page.
- Strategies can be armed to wait for entry conditions; the independent paper worker checks entries and exits (stop, target, trailing stop, expiry square-off) under a lease with fencing.
- SPAN-style margin estimate with exposure and expiry-day margin; within ±5% of Upstox's margin calculator on seven sample positions (29 Oct 2026).
- Versioned charge model (brokerage, STT, exchange, SEBI, stamp duty, GST) used by backtests, futures fills and the nightly forward report. Option trades on Trade show a smaller illustrative 0.10% of premium instead, and neither is deducted from paper option cash.
- Automatic demotion: a strategy's new entries pause on 5 consecutive closed paper losses, when its closed paper drawdown passes 2× its backtest baseline (6% of capital per lot without one), or when a closed trade has no usable P&L. It then ranks as blocked and can't be armed.
- Rule-based promotion: after 7 days a paused strategy goes on one-lot probation; 5 closed probation trades with a positive total, inside tighter limits and with a saved baseline, make it eligible again. Every step is listed on Research. The forward runner skips a paused strategy and keeps arming the others.
- Research rule engine (`packages/backtesting/rule_engine.py`): each night the two forward-only night condors are scored on imported sessions from 2 November 2026 into a shadow ledger. A version is promoted only past a strict bar (20 trades, 90 days, positive after charges, study loss limits, t of at least 4.47, the one-in-twenty line across 12,500 rules tried). It is demoted on a loss streak, a limit breach or drift below its shadow record, and a demoted newer version rolls back to the one it replaced. Every decision is listed on Research. Promotion clears a rule for a separate paper account only.
- Template rules (10 November 2026): every same-expiry paper template, with its own entry and exit rules, is also a rule in the engine, frozen from 10 November 2026. Each night it is replayed over newly imported expiries (armed 7 days before expiry at 09:20, the account's per-trade limit and recorded spreads) into the same shadow ledger, under the same promotion bar. Calendars and diagonals cannot be replayed on history and are not scored. A promoted template is armed by the `promoted-runner` in its own paper account, only while the engine holds it promoted (`work/forward-promoted-paper.json`, frozen with `--promoted-only`).
- IV against realized volatility (14 November 2026): two entry metrics, `realized_vol_10d_pct` (NIFTY's annualized close-to-close volatility over the 10 sessions before today, from the provider's daily candles, read only when a rule uses it and once a day) and `iv_minus_rv_points` (at-the-money IV less it). Unknown when a candle is missing, so the entry waits. `work/forward-ivrv-paper.json` runs the kept long-volatility templates only when IV is at or below realized volatility, and the range condor only when IV is well above it, beside the unfiltered accounts. History replay doesn't compute these metrics.
- Extra forward accounts: the supervisor runs a forward runner for each of `work/forward-range-paper.json` (range condor and call calendar), `work/forward-multi-paper.json` (put calendar, diagonals, double diagonal, inverse iron butterfly) and `work/forward-promoted-paper.json` once the file exists. All simulated paper; none can place a broker order.
- Night condor override (`apps/night_runner/main.py`): on 3 November 2026 the owner chose to paper trade both night condors now, before the engine's bar is met. The night runner reads richness at 09:20 and 09:30 from the live chain and past 5-minute candles with the research code, and opens one lot through the guarded paper path in its own account, named "not promoted". The worker exits at 15:10 the next session (exit policy `nifty-option-next-session-v1`) or on expiry day. A version the engine demotes, retires or replaces is not traded. Paper only; it places no broker orders.
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
2. Import new expiries and their far strikes (`RUNBOOK.md`); the nightly job's rule engine then scores the two forward-only research rules. They are not in paper trading. The first of them failed a rougher test on 2019 to 2024 daily prices, so expect less from it. Backtest spreads are calibrated and drawdown baselines are saved, so promotion can now run on its own rules.
3. After the period: a decision on a 1-lot live pilot (`LIVE_TRADING.md`).
4. Deferred until after that decision: removing backend equity code and tables, and splitting `main.py` and `page.tsx`.
5. Owner steps: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and `TELEGRAM_ALERT_USER_EMAIL` for alerts; `scripts/install-autostart.ps1` to start at sign-in.

## Known limits

- Local services stop if the machine sleeps or the process that started the supervisor ends.
- The 26 forward templates cannot all be open together. The 5% daily budget counts open maximum loss, so it limits open risk before the 10% aggregate cap does, and long-premium templates often exceed the 2% per-trade cap on a full-week expiry. Blocked entries keep waiting and may fill later in the week, closer to expiry, in an order that follows arming order and freed risk. Compare templates with that in mind; the nightly report lists each armed entry with no trade and why.
- Proposed exit rules are exploratory rules of thumb, and their rupee amounts stay fixed for a whole forward period.
- Not every strategy can be started with one click from Trade: only those the ranking shows. The rest go through the strategy builder.

- Backtests use 5-minute candle closes with a modelled spread, not historical bid/ask; exits are checked on bar closes, so stops can be overshot.
- Two years of history, hold-to-expiry rules and one entry time per strategy: too few independent samples to claim an edge. On that history most strategies lose after costs (simulated).
- Feed V3 has no exchange-origin depth timestamp; paper fills from unverified quote times are allowed only by the local educational setting.
- Margin is an estimate from configured parameters, not NSE's daily SPAN file; checked on one evening and one expiry.
- Postgres-only integration tests run in CI, not on the local SQLite setup.
- Nothing here is investment advice. All results are simulated.
