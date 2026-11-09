param(
    [switch]$Uninstall
)

# Starts ChanAnalyzer (API + Worker) in the background whenever the current user logs on,
# so the weekday daily-preparation schedule runs without opening the app by hand.
# A run missed while the computer was off is queued as soon as the Worker starts.

$ErrorActionPreference = 'Stop'
$taskName = 'ChanAnalyzer Local Autostart'
$projectRoot = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $PSScriptRoot 'start-local-hidden.ps1'

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Output "Removed scheduled task '$taskName'."
    } else {
        Write-Output "Scheduled task '$taskName' is not installed."
    }
    return
}

if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "Launcher not found: $launcher"
}

$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcher`" -NoBrowser" `
    -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
# Give the network a moment after logon; the launcher itself waits for the API to become ready.
$trigger.Delay = 'PT1M'
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $taskName `
    -Description 'Start the ChanAnalyzer API and Worker in the background at logon.' `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Output "Installed scheduled task '$taskName' for $user (runs at logon, 1 minute delay)."
Write-Output "Remove it with: powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Uninstall"
