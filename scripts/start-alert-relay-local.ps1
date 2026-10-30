$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "Python virtual environment not found. Create and install .venv using the README instructions."
}
Set-Location $repoRoot
$env:DATABASE_URL = "sqlite:///./work/nifty-local.db"
# Idle unless TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID and TELEGRAM_ALERT_USER_EMAIL are set in .env.
& $pythonExe -m apps.alert_relay.main
exit $LASTEXITCODE
