$ErrorActionPreference = "Stop"

$repo = (Get-Location).Path
docker run --rm `
  --mount "type=bind,source=$repo,target=/src,readonly" `
  --workdir /src `
  -e PYTHONPATH=/src `
  -e DATABASE_URL=sqlite:// `
  -e ENVIRONMENT=development `
  -e LIVE_TRADING_ENABLED=false `
  python:3.12.14-slim `
  sh -c "pip install --no-cache-dir -r requirements-dev.lock && python -m pytest tests/unit/test_chronological_validation.py tests/unit/test_equity_policy_trials.py tests/unit/test_backtesting.py tests/integration/test_equity_deployments.py tests/integration/test_option_deployments.py tests/integration/test_worker_and_entries.py tests/integration/test_expiry_settlement.py -q -p no:cacheprovider --basetemp=/tmp/pytest"
