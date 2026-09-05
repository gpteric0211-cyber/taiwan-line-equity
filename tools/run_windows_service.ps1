[CmdletBinding()]
param(
    [switch]$Interactive,
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$ServiceArguments
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$logRoot = Join-Path $projectRoot 'var/services'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
try {
    Add-Type -Path (Join-Path $PSScriptRoot 'WindowsServiceJob.cs')
    [Equity.WindowsServiceJob]::Attach()
    $pythonPath = Join-Path $projectRoot 'python/python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
        $pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
    }
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw 'Run setup.cmd before starting the service.' }
    Set-Location -LiteralPath $projectRoot
    if ($Interactive) {
        & $pythonPath -u -m equity run @ServiceArguments
        exit $LASTEXITCODE
    }
    $arguments = @('-u', '-m', 'equity', 'run')
    if ($ServiceArguments) { $arguments += $ServiceArguments }
    $serviceProcess = Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot "supervisor-$stamp.out") -RedirectStandardError (Join-Path $logRoot "supervisor-$stamp.err") -PassThru -Wait
    exit $serviceProcess.ExitCode
} catch {
    $_ | Out-String | Set-Content -LiteralPath (Join-Path $logRoot "launcher-$stamp.err") -Encoding UTF8
    Write-Error $_ -ErrorAction Continue
    exit 1
}
