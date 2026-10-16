# Isolated research runtime demonstration

This is SYNTHETIC DEMO ONLY, not market data, paper-account results, calibrated probability or evidence that any strategy is profitable. No owner database, settings, token, network or provider is accessed. A temporary SQLite database under work/ is created and removed by the script; normal account database configuration is never read.

Run with the project Python environment:

    .venv/Scripts/python.exe scripts/demo-research-evaluator.py

The script generates 900 invented daily bars with a fixed 30-day price pattern and an artificial calendar. It registers one synthetic hypothesis and trial, freezes the existing momentum adapter, reserves a one-slot budget, evaluates only through the validation end and retries the same trial. All metrics come from the existing runtime engine; no fills/P&L are supplied as answers. It asserts one consumed slot, idempotent result identity and an untouched operator-result column.

Observed 16 October 2026:

| Check | Runtime result |
|---|---|
| Engine execution | COMPLETED |
| Sample qualification | INSUFFICIENT_SAMPLE |
| Closed training trades | 33 |
| Closed validation trades | 12 |
| Reviewed daily bars | 720 |
| Holdout evaluated | false |
| Budget slots consumed after retry | 1 |
| Operator result overwritten | false |
| Promotion eligible | false |
| Profit probability | unavailable |

Twelve validation closes fail the minimum twenty-close requirement. The fixture was not changed to manufacture an acceptance pass. The replay cost model remains illustrative; descriptive replay win frequencies are not future profit probabilities.

Engine result SHA-256: fb03363432b262c4d55d0065d80d97ff8c0bdf694af84caa19f4b16f51ed1921

Calculation source digest: 8469ec83cc57b85134b6dc8298b3b5b78831c407d3a73d66ea75c9a9264f0a9d

Dataset byte digest: d0d41b04e26cbe90b4983e1f272b4510cab01b9fa515d1b25f25f240ae47421b

Full generated JSON is in work/research-demo-20261016.json (ignored diagnostic artifact); rerunning produces a current calculation fingerprint if source changes. The script and evidence assertions are committed. This proves a disposable SQLite service flow only, not deployed API/PostgreSQL/concurrent-worker acceptance, licensed source/data quality, real bid/ask freshness, NIFTY expiry settlement or automatic daily recommendation readiness.
