param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "Python virtual environment not found. Run: py -3.12 -m venv .venv; .\.venv\Scripts\python.exe -m pip install --no-deps -r requirements-dev.lock; .\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e ."
}

$portProbe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try {
    $portProbe.Start()
} catch {
    throw "Port $Port is already in use. Stop the older API in its PowerShell window with Ctrl+C, then run this script again."
} finally {
    $portProbe.Stop()
}

Set-Location $repoRoot
$null = New-Item -ItemType Directory -Force -Path (Join-Path $repoRoot "work")
$env:DATABASE_URL = "sqlite:///./work/nifty-local.db"

& $pythonExe -m apps.api.init_local_db
if ($LASTEXITCODE -ne 0) {
    throw "Local database initialization failed with exit code $LASTEXITCODE."
}

& $pythonExe -m uvicorn apps.api.main:app --host 127.0.0.1 --port $Port --reload
exit $LASTEXITCODE
