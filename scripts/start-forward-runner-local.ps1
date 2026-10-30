param([switch]$Once)
$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "Python virtual environment not found. Create and install .venv using the README instructions."
}
Set-Location $repoRoot
$env:DATABASE_URL = "sqlite:///./work/nifty-local.db"
if ($Once) { & $pythonExe -m apps.forward_runner.main --once }
else { & $pythonExe -m apps.forward_runner.main }
exit $LASTEXITCODE
