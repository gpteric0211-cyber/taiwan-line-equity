param(
    [string]$Time = "13:35",
    [string]$TaskName = "Taiwan50 Fugle Watchlist Price Volume Update",
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$Runner = Join-Path $RepoRoot "scripts\run_fugle_watchlist_update.bat"
$TriggerClock = [DateTime]::ParseExact($Time, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)
$WindowStart = $TriggerClock.AddMinutes(-4).ToString("HH:mm")
$WindowEnd = $TriggerClock.AddMinutes(35).ToString("HH:mm")
$RunnerArguments = '/d /c ""{0}" --capture-phase post_close --window-start {1} --window-end {2}"' -f $Runner, $WindowStart, $WindowEnd

if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) {
    throw "Runner not found: $Runner"
}

if ($WhatIf) {
    Write-Host "WhatIf: would register scheduled task '$TaskName'"
    Write-Host "WhatIf: weekdays at $Time"
    Write-Host "WhatIf: mode watchlist"
    Write-Host "WhatIf: runner capture window $WindowStart-$WindowEnd"
    Write-Host "WhatIf: action $env:ComSpec $RunnerArguments"
    exit 0
}

$Action = New-ScheduledTaskAction -Execute $env:ComSpec -Argument $RunnerArguments -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $Time
$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "At $Time on weekdays after the close, capture Fugle supplemental price-volume for the local watchlist only inside $WindowStart-$WindowEnd; official same-day EOD volume must reconcile before use." `
    -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName' for weekdays at $Time (watchlist mode)."
