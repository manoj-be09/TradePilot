# TradePilot

NIFTY options research and **paper-trading** platform: FastAPI API (`apps/api`), independent paper exit worker (`apps/paper_worker`), feed observer (`apps/feed_observer`), Next.js dashboard (`apps/web`), domain packages in `packages/`, Alembic migrations in `alembic/`. Read `ARCHITECTURE.md` for the data flow and `TRADING_SAFETY.md` for the gates.

## Rules that must never break
- Live trading stays off. Never enable `LIVE_TRADING_ENABLED`, never set `PAPER_ALLOW_UNVERIFIED_QUOTES=true` in committed config, never add broker order/execution calls. Only read-only market-data GET endpoints, plus the owner-approved (2026-10-29) margin calculation POST `/v2/charges/margin`, which places nothing; `UpstoxMarketDataProvider._send` refuses any other non-GET path. `LIVE_TRADING.md` describes how live trading would be added; nothing in it is built yet.
- Never read, print or commit `.env` or any credential. No broker credentials in this workspace.
- Fail closed: missing, stale, unverified or failed-quality data rejects or skips — never default, forward-fill or fabricate a price, fill, exit or metric.
- Strategies and AI components never call a broker or the paper ledger directly; deterministic code owns calculations, gates, fills, costs and P&L.
- Preserve idempotency keys, account-row locking, worker lease/fencing and risk limits (2% per trade, 10% aggregate) on any paper entry/exit path.
- No investment advice or performance promises in user-facing text; simulated results are labelled as simulated.
- Schema changes need a numbered Alembic migration and must keep `init_local_db` (SQLite) working.

A PreToolUse hook (`.claude/hooks/trading_guard.py`) blocks edits that break the first two rules. If it blocks you, stop and tell the user — do not work around it.

## Commands (Windows, from repo root)
- Python: `.venv\Scripts\python.exe -m pytest -q` (or a `tests/unit` / `tests/integration` subset)
- Lint/type/security, as in CI: `ruff check apps packages alembic tests`, `mypy apps/api apps/paper_worker packages`, `bandit -q -r apps/api apps/paper_worker packages`
- Web (`apps/web`): `pnpm run lint` (tsc), `pnpm run build`
- Local stack: `docker compose up --build` (API :8000, web :3000, Postgres :5432). Without Docker: `scripts/start-api-local.ps1`, `scripts/start-paper-worker-local.ps1`, `scripts/start-web-local.ps1`
- Don't create new `.pytest-*` directories in the repo root; use pytest's default temp location.

## Skills and tools
Project skills in `.claude/skills/` cover each role — pick the matching one before working in its area: `senior-software-engineer`, `software-architect`, `senior-tester`, `risk-manager`, `fno-trader`, `financial-expert`, `quant-researcher`, `market-data-engineer`, `ai-self-improvement-expert`, `security-expert`, `ui-expert`, `devops-sre`, `compliance-sebi`. Any change to trading behaviour also gets `risk-manager`.
- `tradepilot-db` MCP: read-only access to the local Docker Postgres for inspecting paper trades, worker state and research records. Use it to look, never as a reason to change data by other means.
- `context7` MCP: current library docs (Next.js 15, React 19, FastAPI, SQLAlchemy 2, Pydantic v2) — check it before using an API you are unsure of.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- A committed graphify-out/graph.json is rewritten whole on every build, so a large diff to it in a pull request is expected. Do not flag it as churn or suggest skipping the graph rebuild or its hooks.
