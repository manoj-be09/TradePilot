# GitHub export setup

Copy `.env.example` to `.env`, set strong `POSTGRES_PASSWORD` and `JWT_SECRET`, add `UPSTOX_ANALYTICS_TOKEN` only on the server, and keep `LIVE_TRADING_ENABLED=false`. Run `docker compose up --build` and open `http://localhost:3000`.

Secrets, local databases, generated builds, caches, dependency folders, logs, and the friend-testing ZIP are excluded.

## Validate a running stack

In PowerShell, from the repository root:

```powershell
docker compose ps
Invoke-RestMethod http://localhost:8000/ready
Invoke-RestMethod http://localhost:8000/ready/paper-worker
```

Both readiness requests should return `ready`. This proves dependency/worker health,
not that a strategy has entered a trade or that paper results predict profit.
Open `http://localhost:3000` and select **Equity & ETF → Algo Trading**. Before
selecting a stock, expand a strategy in the library to see its source and rules.
Save and select an NSE share to load that stock's replay and top three candidates.
No history or a data error must not be presented as a measured win probability.

## Read-only market-session probe

The bounded feed probe is included in the API image. After pulling a version that
adds or updates it, rebuild once with `docker compose build api`. Run:

```powershell
docker compose run --rm --no-deps --entrypoint python api scripts/probe-upstox-v3-feed.py
```

The script reads the server-side Analytics Token already configured in `.env`;
do not paste credentials into a command or report. It makes no orders or database
writes. Outside `NORMAL_OPEN`, it stops without subscribing. During an open session,
it observes RELIANCE for up to 45 seconds, then reports price/depth changes,
receipt gaps, tick/trade times and clock-offset limitations. No configured Upstox
token produces an explicit no-stream message. Provider errors are redacted.
Observed changes do not prove exchange-origin book timestamps, F&O quote quality,
or a complete entry/stop/exit lifecycle. Strict quote gates remain unchanged.

## Preserve local data when troubleshooting

Do not use `docker compose down -v` to fix application startup: it deletes the
paper database volume. Changing `.env` does not reset the database's existing
password. Use a database backup and an intentional password change if needed.
Use `docker compose logs --since=5m api` for recent startup failures; never share
raw environment/configuration output containing tokens or database passwords.
