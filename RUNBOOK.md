# Paper trading runbook

What to do when something goes wrong during unattended NIFTY paper trading (roadmap Phase 6). Everything here is paper only; live orders are forced off and nothing below can enable them.

## Daily routine

| When (IST) | Check |
|---|---|
| Before 09:15 | Status row: **Exit worker: running**, **Market data: connected**, **Entries on** (or deliberately paused). The recorder log says it is waiting for or recording the session. |
| During the session | Home shows open positions and today's P&L; the exit worker chip stays green. |
| After 15:30 | Trade → **Account, risk and ledger** → **Check ledger now** shows "Saved ledger matches". No position shows *Expired · settlement pending* unless it is an expiry day. |

Local processes (Windows, from the repo root), each in its own terminal:

```bash
powershell -File scripts/start-api-local.ps1
powershell -File scripts/start-paper-worker-local.ps1
powershell -File scripts/start-web-local.ps1
```

```bash
DATABASE_URL="sqlite:///./work/nifty-local.db" PYTHONPATH=. .venv/Scripts/python.exe -u -m apps.recorder.main --data-dir work/recorder-data
```

Keep the machine awake and on power for the whole session; a sleeping laptop stops all four.

## The exit worker is not running or shows warnings

The worker (`apps/paper_worker`) checks stops, targets, trailing stops, expiry square-off and armed-strategy entries every 15 seconds under a 120-second lease.

- **Exit worker: not started / stale.** Start it with `scripts/start-paper-worker-local.ps1`. Until it runs, armed strategies never enter and open positions are not exited automatically. Only one worker holds the lease, so starting a second copy is harmless; it reports `BUSY`.
- **Exit worker: warnings (DEGRADED).** Open the chip for the first reason. Common causes: a quote failed quality checks, or an expired position awaits settlement. Individual exits still run in a degraded cycle.
- **ERROR cycles.** The log shows only the exception type (never provider text or credentials). Restart the worker; if it repeats, pause entries (below) and check the API and database.

## Pause everything new

Press **Pause** in the status row (any page). This is the kill switch: it blocks new entries, including armed strategies, and never blocks exits. Press **Resume** when the cause is fixed.

## Market data is down or the token expired

Status row shows **Market data: not verified**; the worker logs quote or provider warnings.

- Entries fail closed: no fill is recorded without a fresh, quality-checked quote. Exits that cannot be priced are retried on the next cycle, not filled at a guess.
- Fix: renew the Upstox analytics token in the server `.env` yourself (never paste it into chat or a commit), then restart the API and worker.
- If the outage lasts into the expiry-day square-off window, the affected positions stay open and appear as *Expired · settlement pending* the next day; settle them as below.

## Expiry day

- Short-dated positions are squared off `PAPER_EXPIRY_SQUARE_OFF_MINUTES_BEFORE_CLOSE` (default 20) minutes before the scheduled close. Entries into an expiring contract are refused inside that window.
- Short options expiring today also carry the 2% additional exposure margin.
- A position that could not be squared off shows **Expired paper positions needing settlement** on Trade. Preview the settlement against the official NSE index close, review the archive fingerprint, then record it. Nothing settles automatically.

## Futures strategies

Started from Trade → **NIFTY futures strategies**. Each uses the nearest contract more than 2 calendar days from expiry and checks the 5-minute regime, open-interest PCR and futures spread; missing data means it keeps waiting.

- **Intraday** templates stop entering and square off `PAPER_EXPIRY_SQUARE_OFF_MINUTES_BEFORE_CLOSE` minutes before every close (`SESSION_SQUARE_OFF`).
- **Positional** templates hold overnight. Inside the 2-day window the position closes (`ROLLOVER`) and the strategy waits again on the next contract. It re-enters only when its rules and all risk checks pass, so it can stay flat; the notification says whether it re-armed. Pausing entries does not stop a rollover exit.
- **Margin** is blocked at fill and returned on exit. Overnight gaps can lose more than the stop-loss; the stop is the risk budget used for the limits, not a guarantee.
- A contract lot-size change rejects a waiting strategy instead of rescaling its stop. Start it again.
- A position that could not be squared off shows *Expired · settlement pending* in Your positions. Record the settlement there against the official NSE close; nothing settles automatically.

## Holidays and special sessions

Session open and close come from Upstox's scheduled exchange timings for that day; there is no local holiday table. On a holiday no session is scheduled, so nothing enters or exits. For a special session (for example Muhurat trading), the worker follows whatever session Upstox publishes; check the first cycle after the session opens.

## Database problems

- SQLite (local): the file is `work/nifty-local.db`. Stop the API, worker and recorder before copying it for a backup. If it is corrupted, restore the latest copy; trades recorded after that copy are lost, so reconcile against the order log before resuming.
- Postgres (Docker): `docker compose ps` should show the database healthy before starting the API and worker.
- Any database error during an entry rolls the transaction back; no partial trade is saved.

## Crash in the middle of an entry or exit

- **Entry:** paper entries use an idempotency key. If the browser lost the response, Home or Trade shows **A paper entry needs confirmation**; retrying with the saved key returns the existing trade instead of opening a second.
- **Worker exit:** each cycle holds the lease and fences its writes with the lease generation, so a restarted or second worker cannot double-close a position. After a crash, the next cycle re-evaluates every open position from fresh quotes.

## Ledger mismatch

**Check ledger now** compares saved cash with every simulated order and trade. A mismatch pauses new paper cash actions. Do not edit the database by hand. Note the listed issues, keep entries paused, and fix the cause in code with a test before resuming.

## A strategy was paused automatically

Strategies whose closed paper drawdown passes 2× their backtest baseline (6% of capital per lot without a baseline) stop taking new entries; open positions are unaffected. The pause is listed at the top of the app. Re-enable only after reviewing why it lost more than its history suggested.
