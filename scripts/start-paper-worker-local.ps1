param([switch]$Once)
$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "Python virtual environment not found. Create and install .venv using the README instructions."
}
Set-Location $repoRoot
$null = New-Item -ItemType Directory -Force -Path (Join-Path $repoRoot "work")
$env:DATABASE_URL = "sqlite:///./work/nifty-local.db"
& $pythonExe -m apps.api.init_local_db
if ($LASTEXITCODE -ne 0) { throw "Initialize the database before starting the worker." }
if ($Once) { & $pythonExe -m apps.paper_worker.main --once }
else { & $pythonExe -m apps.paper_worker.main }
exit $LASTEXITCODE
