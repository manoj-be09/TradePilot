# Controlled successors for frozen equity research

Implementation target for priority 2. This is a design, not implemented behavior.

## Problem verified in current code

`EquityValidationStudy` permits only one record per account/provider/instrument through `uq_equity_validation_scope`. Chronological review returns that saved record on every later request. Cost/source guards correctly reject a new policy trial against incompatible assumptions, but there is no supported successor-study route. Deleting or overwriting the first study would lose evidence and must not be the recovery path.

## Minimal first increment

1. Add a generation integer (existing rows become generation 1) and a nullable parent-study reference. Replace the scope uniqueness constraint with account/provider/instrument/generation uniqueness. Preserve all study IDs, report/dataset digests, stored trials and foreign keys. Test the PostgreSQL migration against isolated schemas and SQLite migration compatibility. Do not downgrade after multiple generations exist unless it explicitly refuses rather than deleting evidence.
2. Keep the existing chronological-review route returning generation 1. Add an explicit account-scoped successor route addressed by parent study ID; never make ordinary refresh silently generate a new experiment.
3. A source-update successor initially reuses the parent's exact preserved candles, allocation, risk and explicit costs. It retains the parent's training, validation and reserved boundaries. Reject missing/invalid parent manifests and missing cost assumptions; do not guess historical fees. Return the existing child on retry, and use generation uniqueness to fence concurrent requests. Store the current declared calculation-source identity and a reviewed reason for the successor.
4. Replay only the same earlier windows. The newest reserved window stays outside the replay input. Verify all boundary dates match the parent before saving. A successor cannot pull new provider history, roll the reserved window forward, evaluate old reserved outcomes, change a running strategy or activate orders.
5. Record the lineage, successor request, failures and returned-report identity in audit history. Treat another code candidate as another research trial; retain unsuccessful attempts. Show generation and parent linkage in expandable details without promoting it to an independent untouched holdout.

## Required tests

- Migration preserves existing study/trial IDs and data, and permits a second generation.
- Cross-account parent access is rejected; a child cannot attach to another instrument/provider.
- Repeated and concurrent successor requests return one child without changing the original report.
- Exact parent candles, costs, capital and boundaries are retained; reserved candles never reach replay.
- Unavailable source, missing cost assumptions, invalid datasets and insufficient diagnostics cannot create qualified evidence or affect a wallet/deployment.
- Existing generation-1 routes remain compatible; source/cost mismatch still blocks comparisons against the wrong generation.

## Separate future work

Fresh-data successors, final-holdout release, licensed point-in-time universe/adjustment manifests, complete dependency environments and champion/challenger promotion need separate protocols. Do not extend this first route with those concerns. A chain of successors is not proof of profitability and does not erase prior trial exposure.
