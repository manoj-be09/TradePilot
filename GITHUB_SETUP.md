# GitHub export setup

Copy `.env.example` to `.env`, set strong `POSTGRES_PASSWORD` and `JWT_SECRET`, add `UPSTOX_ANALYTICS_TOKEN` only on the server, and keep `LIVE_TRADING_ENABLED=false`. Run `docker compose up --build` and open `http://localhost:3000`.

Secrets, local databases, generated builds, caches, dependency folders, logs, and the friend-testing ZIP are excluded.
