param(
    [string]$TaskName = "Taiwan Stock Single Track V3 Scheduler",
    [string]$DatabasePath = "",
    [string]$PythonPath = "",
    [string]$SourcePolicyVersion = "SourceAuthorityPolicyV1",
    [switch]$Install,
    [switch]$ReplaceExisting
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$Runner = Join-Path $ScriptDir "run_single_track_v3_scheduler.py"

if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) {
    throw "Scheduler runner source is missing."
}

if ([string]::IsNullOrWhiteSpace($DatabasePath)) {
    $DatabasePath = Join-Path $RepoRoot "review_src\data\taiwan50.db"
} elseif (-not [System.IO.Path]::IsPathRooted($DatabasePath)) {
    $DatabasePath = Join-Path $RepoRoot $DatabasePath
}
$DatabasePath = [System.IO.Path]::GetFullPath($DatabasePath)

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $VenvPython = Join-Path $RepoRoot "review_src\.venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
        $PythonPath = $VenvPython
    } else {
        $PythonCommand = Get-Command python -ErrorAction Stop
        $PythonPath = $PythonCommand.Source
    }
}
$PythonPath = [System.IO.Path]::GetFullPath($PythonPath)
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Configured Python executable does not exist."
}
if (-not (Test-Path -LiteralPath $DatabasePath -PathType Leaf)) {
    throw "Configured scheduler database does not exist."
}
$LocalTimeZoneId = [System.TimeZoneInfo]::Local.Id
$TaipeiTimeZoneReady = $LocalTimeZoneId -eq "Taipei Standard Time"

$PreflightText = & $PythonPath $Runner `
    --preflight `
    --database $DatabasePath 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) {
    throw "Scheduler preflight command failed: $PreflightText"
}
try {
    $Preflight = $PreflightText | ConvertFrom-Json
} catch {
    throw "Scheduler preflight did not return valid JSON."
}

$ActionArguments = @(
    ('"{0}"' -f $Runner),
    "--run",
    "--allow-write",
    "--database",
    ('"{0}"' -f $DatabasePath),
    "--source-policy-version",
    ('"{0}"' -f $SourcePolicyVersion)
) -join " "

if (-not $Install) {
    [pscustomobject]@{
        mode = "what_if"
        task_name = $TaskName
        schedule = "from local midnight, repeat every 15 minutes for the maximum supported duration, plus startup catch-up"
        local_time_zone_id = $LocalTimeZoneId
        taipei_time_zone_ready = $TaipeiTimeZoneReady
        start_when_available = $true
        wake_to_run = $true
        multiple_instances = "IgnoreNew"
        database_exists = $true
        preflight_install_ready = [bool]$Preflight.install_ready
        preflight_reason_codes = @($Preflight.reason_codes)
        task_registered = $false
    } | ConvertTo-Json -Depth 5
    exit 0
}

if (-not [bool]$Preflight.install_ready) {
    $Reasons = @($Preflight.reason_codes) -join ", "
    throw "Fail-closed: scheduler installation preflight is not ready ($Reasons)."
}
if (-not $TaipeiTimeZoneReady) {
    throw "Fail-closed: Windows local time zone must be Taipei Standard Time."
}

$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $ExistingTask -and -not $ReplaceExisting) {
    throw "Task already exists; pass -ReplaceExisting for an explicit replacement."
}

$Action = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument $ActionArguments `
    -WorkingDirectory $RepoRoot
$QuarterHourTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -RepetitionDuration ([TimeSpan]::MaxValue)
$StartupTrigger = New-ScheduledTaskTrigger -AtStartup
$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -WakeToRun `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 14) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger @($QuarterHourTrigger, $StartupTrigger) `
    -Settings $Settings `
    -Description "Durable Single-Track V3 TPE scheduler tick and zero-GPU outbox producer; retrieval runs remain governed by persisted policy and calendar revisions." `
    -Force | Out-Null

[pscustomobject]@{
    mode = "installed"
    task_name = $TaskName
    schedule = "from local midnight, repeat every 15 minutes for the maximum supported duration, plus startup catch-up"
    task_registered = $true
} | ConvertTo-Json -Depth 3
