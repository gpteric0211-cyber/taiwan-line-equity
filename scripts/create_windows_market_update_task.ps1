param(
    [string]$Time = "15:00",
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
$TaskName = "Taiwan50 Market Foundation Daily Update"
$BatPath = Join-Path $RepoRoot "scripts\run_daily_market_foundation_update.bat"

if ($WhatIf) {
    Write-Host "WhatIf: would register scheduled task '$TaskName'"
    Write-Host "WhatIf: trigger weekdays at $Time; verified market calendar gate remains authoritative"
    Write-Host "WhatIf: action $BatPath"
    exit 0
}

$Action = New-ScheduledTaskAction -Execute $BatPath -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $Time
$Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Run the official Taiwan market update on weekdays; the verified exchange calendar gate skips closures." -Force | Out-Null
Write-Host "Registered scheduled task '$TaskName' on weekdays at $Time"
