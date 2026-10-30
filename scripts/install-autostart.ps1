# Run this yourself once. It adds a Windows task that starts TradePilot's local paper
# processes when you sign in, and needs no administrator rights. Remove it with -Remove.
param([switch]$Remove)
$ErrorActionPreference = "Stop"
$taskName = "TradePilot local paper"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    "Removed the task '$taskName'."
    exit 0
}
$script = (Resolve-Path (Join-Path $PSScriptRoot "start-all-local.ps1")).Path
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings | Out-Null
"Added the task '$taskName'. It starts at your next sign-in."
