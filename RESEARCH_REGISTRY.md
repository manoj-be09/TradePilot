# Research registry — foundation

This slice records hypotheses and evaluation bookkeeping. It does not discover sources, execute rule text, run a backtest, score profitability, train a probability model, schedule daily evaluations or promote anything into paper/live execution.

Authenticated routes:
- POST /research/candidates freezes a typed definition (family, market, hypothesis, public HTTPS source, declared license, entry/exit rule text and limitations). Source/license remain UNREVIEWED. Same account and identical canonical definition return the same candidate; changes create a new definition rather than overwriting old evidence.
- GET /research/candidates returns account-scoped history, with bounded limit/offset pagination and no-store.
- POST /research/candidates/{id}/trials records a manifest before evaluation. It pins dataset and calculation digests, a cost-model ID, experiment family, seed, declared trial number/budget and non-overlapping chronological training/validation/holdout periods. Replaying the exact manifest returns its original attempt, not a new independent experiment.
- GET /research/candidates/{id}/trials retains unfinished, failed and completed records, account scoped and paginated.
- POST /research/trials/{id}/result finalizes once using an atomic null-result condition. Exact replay is idempotent; different evidence conflicts. FAILED, ERROR and INSUFFICIENT outcomes are retained. Completion requires a result-artifact digest. Error reason codes are bounded; arbitrary provider errors or claimed probability fields are rejected.

All results are explicitly OPERATOR_RECORDED and promotion_eligible=false. A digest is identity, not proof that an artifact exists, the data are licensed, an evaluation was correct or a strategy is profitable. Records are write-once through this service, not database-admin-proof audit storage. Credentials, query strings and fragments are forbidden in source references; the server never fetches these URLs.

Declared budgets are recorded and a trial number cannot exceed its declared budget. A durable experiment-family budget counter, cross-candidate duplicate-search accounting, verified source/licensing review, dataset/artifact storage, runnable typed rule compilation and evaluator-owned results remain next steps. The registry alone does not enforce a global search budget or protect an untouched holdout against repeated external use. Do not use it to rank or promote candidates until these controls and forward-paper evidence exist.

Storage: two additive tables via0025_research_registry; no old table/row updates or backfills. No delete/update-definition route. Normal deployment needs Alembic upgrade through the existing API startup command. Tests use isolated SQLite only; do not invoke registry writes against the owner's account merely for verification.

Validated: definition/trial identity, failed-result retention and conflict/replay behavior, cross-owner access rejection, chronological/hash/budget-number validation, source credential rejection, required authentication dependency wiring, additive migration preserving an existing user, and static checks. HTTP transport/PostgreSQL migration and deployed UI remain unverified. No research UI was added in this backend foundation.
