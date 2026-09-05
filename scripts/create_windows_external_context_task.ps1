param(
    [string]$Time = "06:15",
    [string[]]$AdditionalTimes = @("09:00", "11:30", "14:00", "18:00"),
    [string]$TaskName = "Taiwan Stock External Analysis Context",
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$Runner = Join-Path $RepoRoot "scripts\run_external_analysis_context_update.bat"

if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) {
    throw "Runner not found: $Runner"
}

$RunnerArguments = '/d /c ""{0}""' -f $Runner
$AllTimes = @($Time) + @($AdditionalTimes) | Select-Object -Unique
if ($WhatIf) {
    Write-Host "WhatIf: would register scheduled task '$TaskName'"
    Write-Host "WhatIf: daily at $($AllTimes -join ', ')"
    Write-Host "WhatIf: action $env:ComSpec $RunnerArguments"
    exit 0
}

$Action = New-ScheduledTaskAction `
    -Execute $env:ComSpec `
    -Argument $RunnerArguments `
    -WorkingDirectory $RepoRoot
$Triggers = @(
    foreach ($ScheduleTime in $AllTimes) {
        New-ScheduledTaskTrigger -Daily -At $ScheduleTime
    }
)
$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Triggers `
    -Settings $Settings `
    -Description "Refresh persisted global market, TAIFEX night, MOPS events, official revenue, policy RSS, and configured licensed feeds outside request paths." `
    -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName' daily at $($AllTimes -join ', ')."
