param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 3000,
    [string]$ApiUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$webRoot = Join-Path $repoRoot "apps\web"
if (-not (Test-Path -LiteralPath (Join-Path $webRoot "package.json") -PathType Leaf)) {
    throw "The web application was not found at '$webRoot'."
}

$portProbe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try {
    $portProbe.Start()
} catch {
    throw "Port $Port is already in use. Stop the older web server in its PowerShell window with Ctrl+C, then run this script again."
} finally {
    $portProbe.Stop()
}

Set-Location $webRoot
$env:NEXT_PUBLIC_API_URL = $ApiUrl.TrimEnd("/")
npm run dev -- --hostname 127.0.0.1 --port $Port
exit $LASTEXITCODE
