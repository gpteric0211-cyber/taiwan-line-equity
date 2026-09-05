param(
    [string]$Time = "15:00",
    [string]$WindowEnd = "17:50",
    [string]$TaskName = "Taiwan Stock Fugle Full-Market Capture",
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

$RunnerArguments = '/d /c ""{0}" --stage capture --window-end {1} --max-retries 5 --lock-wait-seconds 10200"' -f $Runner, $WindowEnd
$Now = Get-Date
$ScheduledTime = [TimeSpan]::Parse($Time, [System.Globalization.CultureInfo]::InvariantCulture)
$ScheduledToday = $Now.Date.Add($ScheduledTime)
$WindowEndTime = [TimeSpan]::Parse($WindowEnd, [System.Globalization.CultureInfo]::InvariantCulture)
$WindowEndToday = $Now.Date.Add($WindowEndTime)
if ($WindowEndToday -le $ScheduledToday) {
    throw "WindowEnd must be later than the capture Time on the same day."
}
$IsWeekday = $Now.DayOfWeek -in @(
    [System.DayOfWeek]::Monday,
    [System.DayOfWeek]::Tuesday,
    [System.DayOfWeek]::Wednesday,
    [System.DayOfWeek]::Thursday,
    [System.DayOfWeek]::Friday
)
$PreviousLastRunTime = [DateTime]::MinValue
$PreviousTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $PreviousTask) {
    $PreviousTaskInfo = $PreviousTask | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue
    if ($null -ne $PreviousTaskInfo -and $null -ne $PreviousTaskInfo.LastRunTime) {
        $PreviousLastRunTime = $PreviousTaskInfo.LastRunTime
    }
}
$ShouldRunImmediateCatchUp = (
    -not $SkipImmediateCatchUp `
    -and $IsWeekday `
    -and $Now -ge $ScheduledToday `
    -and $Now -le $WindowEndToday `
    -and $PreviousLastRunTime -lt $ScheduledToday
)

if ($WhatIf) {
    Write-Host "WhatIf: would register scheduled task '$TaskName'"
    Write-Host "WhatIf: weekdays at $Time"
    Write-Host "WhatIf: capture-only runner; no TWSE/TPEx finalization or scoring"
    Write-Host "WhatIf: retryable capture failures stop by $WindowEnd"
    Write-Host "WhatIf: lock wait is bounded by the same capture deadline"
    Write-Host "WhatIf: wake the computer and allow execution on battery"
    Write-Host "WhatIf: immediate post-close catch-up required: $ShouldRunImmediateCatchUp"
    Write-Host "WhatIf: official stock_master full-market universe"
    Write-Host "WhatIf: action $env:ComSpec $RunnerArguments"
    exit 0
}

$Action = New-ScheduledTaskAction `
    -Execute $env:ComSpec `
    -Argument $RunnerArguments `
    -WorkingDirectory $RepoRoot
$DailyTrigger = New-ScheduledTaskTrigger `
    -Weekly `
    -WeeksInterval 1 `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At $Time
$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 12)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $DailyTrigger `
    -Settings $Settings `
    -Description "At $Time capture licensed Fugle trades and price-volume rows for the official stock universe. Rows remain unverified and excluded from scoring until the later official task reconciles them. Retryable capture failures stop by $WindowEnd; a project-local lock prevents overlap." `
    -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName' for weekdays at $Time (capture only)."

if ($ShouldRunImmediateCatchUp) {
    $RegisteredTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    if ($RegisteredTask.State -ne "Running") {
        Start-ScheduledTask -TaskName $TaskName
        Write-Host "Started immediate post-close catch-up because today's $Time trigger had not run before registration."
    } else {
        Write-Host "Immediate post-close catch-up was not duplicated because the task is already running."
    }
}
