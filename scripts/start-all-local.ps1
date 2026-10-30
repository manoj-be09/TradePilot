$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "Python virtual environment not found. Create and install .venv using the README instructions."
}
Set-Location $repoRoot
$null = New-Item -ItemType Directory -Force -Path (Join-Path $repoRoot "work\logs")
$logStream = Join-Path $repoRoot "work\logs\supervisor.log"
& $pythonExe -u -m apps.supervisor.main *>> $logStream
exit $LASTEXITCODE
