# Paper trading runbook

What to do when something goes wrong during unattended NIFTY paper trading (roadmap Phase 6). Everything here is paper only; live orders are forced off and nothing below can enable them.

## Daily routine

| When (IST) | Check |
|---|---|
| Before 09:15 | Status row: **Exit worker: running**, **Market data: connected**, **Entries on** (or deliberately paused). The recorder log says it is waiting for or recording the session. |
| During the session | Home shows open positions and today's P&L; the exit worker chip stays green. |
| After 15:30 | Trade → More → **Account and risk** → **Check ledger now** shows "Saved ledger matches". No position shows *Expired · settlement pending* unless it is an expiry day. |

Local processes (Windows, from the repo root), each in its own terminal:

```bash
powershell -File scripts/start-api-local.ps1
powershell -File scripts/start-paper-worker-local.ps1
powershell -File scripts/start-web-local.ps1
```

```bash
DATABASE_URL="sqlite:///./work/nifty-local.db" PYTHONPATH=. .venv/Scripts/python.exe -u -m apps.recorder.main --data-dir work/recorder-data
```

Keep the machine awake and on power for the whole session; a sleeping laptop stops all of them.

Or start everything with one command. The supervisor runs the API, web app, worker, forward runner, recorder, alert relay and nightly job, restarts any that crash (waiting 5 seconds, then longer, up to 5 minutes), and writes each one's log to `work/logs/`. Stop the separately started copies first, or the API and web app will find their ports in use.

```bash
powershell -File scripts/start-all-local.ps1
```

To have it start whenever you sign in, run this once yourself (add `-Remove` to undo):

```bash
powershell -File scripts/install-autostart.ps1
```

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
- Demotion and probation apply to futures templates as they do to options. There is no futures backtest baseline yet, so a futures template cannot finish probation on its own; it stays on one lot until you reinstate it after review.
- A position that could not be squared off shows **Needs settlement** under Running on Trade. Record the settlement there against the official NSE close; nothing settles automatically.

## Holidays and special sessions

Session open and close come from Upstox's scheduled exchange timings for that day; there is no local holiday table. On a holiday no session is scheduled, so nothing enters or exits. For a special session (for example Muhurat trading), the worker follows whatever session Upstox publishes; check the first cycle after the session opens.

## Database problems

- SQLite (local): the file is `work/nifty-local.db`. The nightly job makes a verified copy in `work/backups/` after each close and keeps the last 3. It refuses to run before 16:30 IST to keep the load off the machine during the session. To back up by hand, stop the API, worker and recorder before copying the file. If it is corrupted, restore the latest copy; trades recorded after that copy are lost, so reconcile against the order log before resuming.
- Postgres (Docker): `docker compose ps` should show the database healthy before starting the API and worker.
- Any database error during an entry rolls the transaction back; no partial trade is saved.

## Crash in the middle of an entry or exit

- **Entry:** paper entries use an idempotency key. If the browser lost the response, Home or Trade shows **A paper entry needs confirmation**; retrying with the saved key returns the existing trade instead of opening a second.
- **Worker exit:** each cycle holds the lease and fences its writes with the lease generation, so a restarted or second worker cannot double-close a position. After a crash, the next cycle re-evaluates every open position from fresh quotes.

## Ledger mismatch

**Check ledger now** compares saved cash with every simulated order and trade. A mismatch pauses new paper cash actions. Do not edit the database by hand. Note the listed issues, keep entries paused, and fix the cause in code with a test before resuming.

## A strategy was paused automatically

The worker checks closed paper trades every cycle, and each entry checks them again. A strategy is paused after 5 consecutive losses, when its closed paper drawdown passes 2× its backtest baseline (6% of capital per lot without a baseline), or when a closed trade has no usable P&L. While paused it can't be armed or entered and ranks as blocked; open positions keep their exits, and armed entries stay waiting without filling. The pause is listed at the top of the app. Research lists every demotion and promotion with its reason and time.

It comes back by rule:

1. **Cooling-off:** 7 days after the pause (`PAPER_PROMOTION_COOLDOWN_DAYS`).
2. **Probation:** one lot, one open position or armed entry at a time. Arm it from Trade, or let the forward paper runner arm its 1-lot version for the next expiry. The ranking shows it as blocked and never selects it.
3. **Promotion:** at least 5 trades opened during probation have closed (`PAPER_PROMOTION_MIN_TRADES`), their total P&L per lot is positive, and a saved backtest baseline exists. Without a baseline it stays on probation: save one with `--save-baseline` in `scripts/run-history-backtest.py`.
4. **Failure:** 3 consecutive losses (`PAPER_PROMOTION_LOSS_STREAK`), drawdown past 1× the baseline, or missing P&L during probation restarts the cooling-off.

**Reinstate** still lifts a pause or probation at once; use it only after reviewing why the strategy lost more than its history suggested. It is recorded as an owner action. These rules decide eligibility only: they don't change a strategy's own rules or the 2% and 10% risk limits.

A pause for missing P&L means a saved trade is incomplete. Treat it like a ledger mismatch: don't edit the database by hand, and fix the cause in code with a test.

## Forward paper period

The runner arms each frozen strategy version once per expiry, for the nearest expiry after today, at the strike nearest the index. It goes through the same checks as the web app, and the worker rechecks entry rules and risk limits before any simulated fill. A strategy you cancel stays cancelled for that expiry.

Save fresh 1-lot rules from current quotes with `--propose` (during market hours; this arms nothing), then freeze a period once (this refuses to overwrite `work/forward-paper.json`; the file is private and not committed). A restart means archiving that file and freezing again:

```bash
DATABASE_URL="sqlite:///./work/nifty-local.db" PYTHONPATH=. .venv/Scripts/python.exe -m apps.forward_runner.main --freeze --user-email <account email> --start 2026-11-02 --weeks 6
```

Then keep it running alongside the worker:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/start-forward-runner-local.ps1
```

Its log shows one line per cycle: `OK`, `NOT_STARTED`, `CLOSED`, `PAUSED`, `NO_DATA` or `FINISHED`. Pausing entries stops it arming anything new. A strategy that is paused automatically is skipped and retried each cycle, while the others are still armed.

After each close the nightly job writes `work/forward-reports/<date>.md`: per-strategy armed, open, closed, won, realized P&L and worst drawdown for the frozen strategies only, plus the ledger status. Figures are simulated. Run it by hand with `DATABASE_URL="sqlite:///./work/nifty-local.db" PYTHONPATH=. .venv/Scripts/python.exe -m apps.nightly.main --once`.

## Strategy ranking on the Trade page
The Trade page sorts option strategies by results in `work/bt/rules-*.json`, written by
`scripts/run-template-rule-backtest.py`, which replays each template's real entry and exit rules.
It falls back to `work/bt/wf-*.json` from `scripts/run-walk-forward.py`. These files are built from private data and stay out of git. If
they are missing or belong to an older template version, a strategy shows "No past test" and
sorts last. Ordering is display only: nothing is started, sized or blocked because of it.

## Comparing exit rules
`scripts/run-exit-rule-comparison.py` closes each entry from the template replay under other exit
rules (`packages/backtesting/exit_rules.py`) and writes `work/bt/exits-*.json`. It is research only:
paper trading, the forward test and the Trade page do not read these files, which stay out of git
because they are built from private data. The earlier half of the expiries picks a rule set for each
strategy and the later half judges it. A result only counts if the later half makes money and beats
the current rules with a paired t-value of at least 2. Run it from the main checkout so that it uses
the local database.
