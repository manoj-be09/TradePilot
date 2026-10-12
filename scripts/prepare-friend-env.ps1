param([switch]$Force)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$target = Join-Path $repoRoot ".env"
if ((Test-Path -LiteralPath $target) -and -not $Force) {
    throw ".env already exists. Use -Force only if you intend to replace this computer's local configuration."
}

function New-RandomText([int]$ByteCount) {
    $bytes = New-Object byte[] $ByteCount
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToHexString($bytes).ToLowerInvariant()
}

$databasePassword = New-RandomText 18
$jwtSecret = New-RandomText 32
$content = Get-Content -LiteralPath (Join-Path $repoRoot ".env.example") -Raw
$content = $content.Replace("local-only-change-me", $databasePassword)
$content = $content.Replace("replace-with-a-random-secret-before-deployment", $jwtSecret)
[System.IO.File]::WriteAllText($target, $content, [System.Text.UTF8Encoding]::new($false))
Write-Host "Created .env with fresh local database and JWT secrets."
Write-Host "Add the tester's read-only UPSTOX_ANALYTICS_TOKEN in .env, then run: docker compose up --build"
