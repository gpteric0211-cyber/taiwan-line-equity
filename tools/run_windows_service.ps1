$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw 'Run setup.cmd before starting the service.' }
$logRoot = Join-Path $projectRoot 'var/services'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
Set-Location -LiteralPath $projectRoot
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$serviceProcess = Start-Process -FilePath $pythonPath -ArgumentList "-u", "-m", "equity", "run" -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot "supervisor-$stamp.out") -RedirectStandardError (Join-Path $logRoot "supervisor-$stamp.err") -PassThru -Wait
exit $serviceProcess.ExitCode
