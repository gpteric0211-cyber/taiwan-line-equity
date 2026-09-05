param(
    [string]$Time = "18:10",
    [string]$FinalFallbackTime = "23:40",
    [string]$CatchUpTime = "06:45",
    [string]$TaskName = "Taiwan Stock Official EOD Reconciliation",
    [switch]$SkipImmediateCatchUp,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$Runner = Join-Path $RepoRoot "scripts\run_full_market_daily_database_update.bat"

if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) {
    throw "Runner not found: $Runner"
}

$RunnerArguments = '/d /c ""{0}" --stage finalize --window-end 23:59 --max-retries 18 --lock-wait-seconds 3600"' -f $Runner
$Now = Get-Date
$IsPrimaryDay = $Now.DayOfWeek -in @(
    [System.DayOfWeek]::Monday,
    [System.DayOfWeek]::Tuesday,
    [System.DayOfWeek]::Wednesday,
    [System.DayOfWeek]::Thursday,
    [System.DayOfWeek]::Friday
)
$IsCatchUpDay = $Now.DayOfWeek -in @(
    [System.DayOfWeek]::Monday,
    [System.DayOfWeek]::Tuesday,
    [System.DayOfWeek]::Wednesday,
    [System.DayOfWeek]::Thursday,
    [System.DayOfWeek]::Friday,
    [System.DayOfWeek]::Saturday
)
$PreviousLastRunTime = [DateTime]::MinValue
$PreviousTaskWasRunning = $false
$PreviousTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $PreviousTask) {
    $PreviousTaskWasRunning = $PreviousTask.State.ToString() -eq "Running"
    $PreviousTaskInfo = $PreviousTask | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue
    if ($null -ne $PreviousTaskInfo -and $null -ne $PreviousTaskInfo.LastRunTime) {
        $PreviousLastRunTime = $PreviousTaskInfo.LastRunTime
    }
}
$DueTimes = @()
if ($IsPrimaryDay) {
    $DueTimes += @($Time, $FinalFallbackTime) | ForEach-Object {
        $Now.Date.Add([TimeSpan]::Parse($_, [System.Globalization.CultureInfo]::InvariantCulture))
    } | Where-Object { $_ -le $Now }
}
$PostCloseBoundary = $Now.Date.Add([TimeSpan]::Parse("15:00", [System.Globalization.CultureInfo]::InvariantCulture))
if ($IsCatchUpDay -and $Now -lt $PostCloseBoundary) {
    $CatchUpDue = $Now.Date.Add(
        [TimeSpan]::Parse($CatchUpTime, [System.Globalization.CultureInfo]::InvariantCulture)
    )
    if ($CatchUpDue -le $Now) {
        $DueTimes += $CatchUpDue
    }
}
$LatestDueTime = $DueTimes | Sort-Object -Descending | Select-Object -First 1
$ShouldRunImmediateCatchUp = (
    -not $SkipImmediateCatchUp `
    -and -not $PreviousTaskWasRunning `
    -and $null -ne $LatestDueTime `
    -and $PreviousLastRunTime -lt $LatestDueTime
)

if ($WhatIf) {
    Write-Host "WhatIf: would register scheduled task '$TaskName'"
    Write-Host "WhatIf: weekdays at $Time and final fallback at $FinalFallbackTime"
    Write-Host "WhatIf: Monday-Saturday official-only catch-up at $CatchUpTime"
    Write-Host "WhatIf: finalize never calls Fugle and retries only exit codes 4 and 5"
    Write-Host "WhatIf: immediate official catch-up required: $ShouldRunImmediateCatchUp"
    Write-Host "WhatIf: action $env:ComSpec $RunnerArguments"
    exit 0
}

$Action = New-ScheduledTaskAction `
    -Execute $env:ComSpec `
    -Argument $RunnerArguments `
    -WorkingDirectory $RepoRoot
$PrimaryTrigger = New-ScheduledTaskTrigger `
    -Weekly `
    -WeeksInterval 1 `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At $Time
$FinalFallbackTrigger = New-ScheduledTaskTrigger `
    -Weekly `
    -WeeksInterval 1 `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At $FinalFallbackTime
$CatchUpTrigger = New-ScheduledTaskTrigger `
    -Weekly `
    -WeeksInterval 1 `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday,Saturday `
    -At $CatchUpTime
$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger @($PrimaryTrigger, $FinalFallbackTrigger, $CatchUpTrigger) `
    -Settings $Settings `
    -Description "At $Time run official TWSE/TPEx reconciliation after Fugle capture. Retry delayed official sources through 23:59; $FinalFallbackTime protects against an earlier fatal exit, and Monday-Saturday $CatchUpTime performs official-only T+1 catch-up, including Friday data on Saturday. This task never recaptures historical Fugle trades." `
    -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName' at $Time, $FinalFallbackTime, and $CatchUpTime."

if ($ShouldRunImmediateCatchUp) {
    $RegisteredTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    if ($RegisteredTask.State -ne "Running") {
        Start-ScheduledTask -TaskName $TaskName
        Write-Host "Started immediate official catch-up because the latest due trigger had not run."
    } else {
        Write-Host "Immediate official catch-up was not duplicated because the task is already running."
    }
}
