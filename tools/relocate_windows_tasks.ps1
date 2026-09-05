[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$SourceRoot, [switch]$Apply)
$ErrorActionPreference = 'Stop'
$sourcePath = (Resolve-Path -LiteralPath $SourceRoot).Path.TrimEnd('\','/')
$destinationPath = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path.TrimEnd('\','/')
if ($sourcePath -eq $destinationPath) { throw 'Source and destination must differ.' }
$pattern = [regex]::Escape($sourcePath) + '(?=[\\/]|["\s]|$)'
function Convert-ProjectPath([string]$Value) {
    if (-not $Value) { return $Value }
    $translated = [regex]::Replace($Value, $pattern, [System.Text.RegularExpressions.MatchEvaluator]{ param($m) $destinationPath }, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    $oldEnvironment = Join-Path $destinationPath 'review_src/.venv'
    $newEnvironment = Join-Path $destinationPath '.venv'
    return [regex]::Replace($translated, [regex]::Escape($oldEnvironment), [System.Text.RegularExpressions.MatchEvaluator]{ param($m) $newEnvironment }, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
}
$tasks = @(Get-ScheduledTask | Where-Object { @($_.Actions | Where-Object { ($_.Execute + ' ' + $_.Arguments + ' ' + $_.WorkingDirectory) -match $pattern }).Count })
$backupPath = Join-Path $destinationPath ('var/migration/windows-tasks-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$report = @()
foreach ($task in $tasks) {
    $actions = @($task.Actions | ForEach-Object {
        $options = @{Execute=(Convert-ProjectPath $_.Execute)}
        if ($_.Arguments) { $options.Argument = Convert-ProjectPath $_.Arguments }
        if ($_.WorkingDirectory) { $options.WorkingDirectory = Convert-ProjectPath $_.WorkingDirectory }
        New-ScheduledTaskAction @options
    })
    $report += [pscustomobject]@{ Name=$task.TaskName; State=[string]$task.State; Actions=@($actions | Select-Object Execute,Arguments,WorkingDirectory); Applied=[bool]$Apply }
    if ($Apply) {
        New-Item -ItemType Directory -Path $backupPath -Force | Out-Null
        $safeName = $task.TaskName -replace '[^\p{L}\p{N} ._-]', '_'
        Export-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath | Set-Content -LiteralPath (Join-Path $backupPath ($safeName + '.xml')) -Encoding UTF8
        Set-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath -Action $actions | Out-Null
        if ($task.State -eq 'Disabled') { Disable-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath | Out-Null }
    }
}
$report | ConvertTo-Json -Depth 6
