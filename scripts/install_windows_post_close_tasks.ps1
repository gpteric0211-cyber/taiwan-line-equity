param(
    [string]$PostCloseTime = "15:00",
    [string]$OfficialTime = "18:10",
    [string]$FinalOfficialTime = "23:40",
    [string]$CatchUpTime = "06:45",
    [string]$ExternalContextTime = "06:15",
    [switch]$IncludeFugleSupplemental,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$ExternalContextInstaller = Join-Path $RepoRoot "scripts\create_windows_external_context_task.ps1"
$FugleInstaller = Join-Path $RepoRoot "scripts\create_windows_fugle_watchlist_task.ps1"
$CaptureInstaller = Join-Path $RepoRoot "scripts\create_windows_full_market_database_task.ps1"
$OfficialInstaller = Join-Path $RepoRoot "scripts\create_windows_official_reconciliation_task.ps1"
$CaptureTaskName = "Taiwan Stock Fugle Full-Market Capture"
$OfficialTaskName = "Taiwan Stock Official EOD Reconciliation"

foreach ($RequiredInstaller in @($CaptureInstaller, $OfficialInstaller)) {
    if (-not (Test-Path -LiteralPath $RequiredInstaller -PathType Leaf)) {
        throw "Installer not found: $RequiredInstaller"
    }
}

if ($WhatIf) {
    & $CaptureInstaller -Time $PostCloseTime -TaskName $CaptureTaskName -WhatIf
    & $OfficialInstaller -Time $OfficialTime -FinalFallbackTime $FinalOfficialTime -CatchUpTime $CatchUpTime -TaskName $OfficialTaskName -WhatIf
    & $ExternalContextInstaller -Time $ExternalContextTime -WhatIf
    if ($IncludeFugleSupplemental) {
        & $FugleInstaller -WhatIf
    }
    exit 0
}

$LegacyTaskNames = @(
    "Taiwan Stock Full-Market Database Update",
    "Taiwan Full Market Daily Database Update",
    "Taiwan50 Market Foundation Daily Update"
)
foreach ($LegacyTaskName in $LegacyTaskNames) {
    $LegacyTask = Get-ScheduledTask -TaskName $LegacyTaskName -ErrorAction SilentlyContinue
    if ($null -eq $LegacyTask) {
        continue
    }
    $BelongsToThisRepo = $false
    foreach ($LegacyAction in $LegacyTask.Actions) {
        $ActionText = "{0} {1} {2}" -f $LegacyAction.Execute, $LegacyAction.Arguments, $LegacyAction.WorkingDirectory
        if ($ActionText.IndexOf($RepoRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            $BelongsToThisRepo = $true
            break
        }
    }
    if ($BelongsToThisRepo) {
        if ($LegacyTask.State -eq "Running") {
            Disable-ScheduledTask -TaskName $LegacyTaskName | Out-Null
            Write-Host "Disabled legacy task '$LegacyTaskName'; its current run was left untouched."
        } else {
            Unregister-ScheduledTask -TaskName $LegacyTaskName -Confirm:$false
            Write-Host "Removed duplicate legacy task '$LegacyTaskName'."
        }
    } else {
        Write-Warning "Legacy task '$LegacyTaskName' was preserved because it does not point to this project root."
    }
}

& $CaptureInstaller -Time $PostCloseTime -TaskName $CaptureTaskName

& $OfficialInstaller `
    -Time $OfficialTime `
    -FinalFallbackTime $FinalOfficialTime `
    -CatchUpTime $CatchUpTime `
    -TaskName $OfficialTaskName

& $ExternalContextInstaller -Time $ExternalContextTime
if ($IncludeFugleSupplemental) {
    & $FugleInstaller
}

Write-Host "Registered post-close tasks from project root: $RepoRoot"
Write-Host "Fugle capture: weekdays at $PostCloseTime"
Write-Host "Official reconciliation: weekdays at $OfficialTime and $FinalOfficialTime; Monday-Saturday T+1 catch-up at $CatchUpTime"
Write-Host "Fugle supplemental task included: $IncludeFugleSupplemental"
