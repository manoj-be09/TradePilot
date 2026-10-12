# Friend testing package

This package runs the current paper-trading preview on another Windows, macOS, or Linux computer with Docker Desktop. It does not contain an Upstox token, user database, passwords, or other local secrets. Live broker orders are disabled in code.

## Start

1. Install and start Docker Desktop.
2. Extract the ZIP to a new folder.
3. Open PowerShell in that folder and run:

   ```powershell
   .\scripts\prepare-friend-env.ps1
   ```

4. Open the generated `.env` in a text editor. Put the friend's own read-only Upstox Analytics Token after `UPSTOX_ANALYTICS_TOKEN=`. Do not send the token through chat or commit `.env`.
5. Start the platform:

   ```powershell
   docker compose up --build
   ```

6. Wait until the API and worker are ready, then open [http://localhost:3000](http://localhost:3000). Create a new app account; accounts are local to that computer.

Stop with `Ctrl+C`, then run `docker compose down`. Use `docker compose down -v` only when the tester intentionally wants to delete all local accounts, trades, and PostgreSQL data.

## What to test

- Create account, sign in, sign out, and sign in again.
- Start on **Equities & ETFs**; search an NSE equity, save it, request a quote, and open its chart.
- Switch to **Futures & Options**; check the provider connection and load an expiry.
- Review all nine strategy scorecards. Close paper trades to build the user-specific observed win rate and probability estimate.
- Select a strategy, strike, and lot count and confirm maximum expiry profit/loss appears.
- Confirm live-order capability stays **Disabled**.

`PAPER_ALLOW_UNVERIFIED_QUOTES=false` is the default. If Upstox supplies only API-receipt timing, paper entries remain safety-blocked. A tester may set it to `true` only for explicitly educational simulation, then restart `api` and `paper-worker`. This does not make the data exchange-verified.

## Troubleshooting

Run `docker compose ps` and `docker compose logs api paper-worker web`. API readiness is at `http://localhost:8000/ready`; API documentation is at `http://localhost:8000/docs`.
