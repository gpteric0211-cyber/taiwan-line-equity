[CmdletBinding()]
param([switch]$Install, [switch]$Start, [switch]$ReplaceExisting, [string]$TaskName = 'Taiwan Line Equity')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$entrypoint = Join-Path $PSScriptRoot 'run_windows_service.ps1'
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw 'Run setup.cmd first.' }
$powerShell = (Get-Command powershell -ErrorAction Stop).Source
$userName = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$arguments = '-NoLogo -NoProfile -WindowStyle Hidden -File "' + $entrypoint + '"'
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing -and -not $ReplaceExisting -and -not (@($existing.Actions | Where-Object { $_.Arguments -like ('*' + $entrypoint + '*') }).Count)) {
    throw 'The task name belongs to another project; choose a different -TaskName or use -ReplaceExisting after verifying the old project.'
}
if (-not $Install) {
    [pscustomobject]@{ TaskName=$TaskName; Project=$projectRoot; User=$userName; Trigger='AtLogOn'; Install=$false } | ConvertTo-Json
    return
}
if ($existing) {
    $backupDir = Join-Path $projectRoot 'var/migration'
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    $backupPath = Join-Path $backupDir ('service-task-before-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.xml')
    Export-ScheduledTask -TaskName $TaskName | Set-Content -LiteralPath $backupPath -Encoding UTF8
}
$action = New-ScheduledTaskAction -Execute $powerShell -Argument $arguments -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userName
$principal = New-ScheduledTaskPrincipal -UserId $userName -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Portable Taiwan stock web, LINE webhook, local model and data scheduler.' -Force | Out-Null
if ($Start) { Start-ScheduledTask -TaskName $TaskName }
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName,State | ConvertTo-Json
