param(
    [switch]$IncludeDb,
    [switch]$IncludeEnv,
    [switch]$Clean,
    [string]$OutputDir,
    [string]$PythonExe = "python",
    [switch]$SkipInstall,
    [string]$PythonRuntimeDir,
    [string]$PythonEmbedZip,
    [switch]$UseEmbeddedPython
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
if (-not $OutputDir) {
    $OutputDir = Join-Path $RepoRoot "dist\Taiwan50Dashboard_Portable_Windows"
}
$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)
$AppDir = Join-Path $OutputDir "app"
$VenvDir = Join-Path $OutputDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$PythonRuntimeTarget = Join-Path $OutputDir "python"
$EmbeddedPython = Join-Path $PythonRuntimeTarget "python.exe"
$RequirementsSource = Join-Path $RepoRoot "review_src\requirements.txt"
$RequirementsTarget = Join-Path $OutputDir "requirements.txt"
$LauncherBatName = [string]::Concat([char]0x555f, [char]0x52d5, [char]0x53f0, [char]0x80a1, [char]0x5206, [char]0x6790, [char]0x7cfb, [char]0x7d71, ".bat")
$ReadmeUseName = [string]::Concat("README_", [char]0x4f7f, [char]0x7528, [char]0x65b9, [char]0x5f0f, ".txt")

function Copy-Tree {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
    }
    Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
}

function Remove-PortableExcludedItems {
    param([Parameter(Mandatory = $true)][string]$Root)

    $excludedDirs = @(".git", ".venv", "venv", "env", "dist", "node_modules", "__pycache__")
    foreach ($name in $excludedDirs) {
        Get-ChildItem -LiteralPath $Root -Recurse -Force -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -eq $name } |
            ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
    }

    Get-ChildItem -LiteralPath $Root -Recurse -Force -File -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -like "*.pyc" -or
            $_.Name -like "*.pyo" -or
            $_.Name -like "*.log" -or
            $_.Name -like "*.zip" -or
            $_.Name -like "*.7z" -or
            $_.Name -like "*.tar.gz" -or
            ((-not $IncludeEnv) -and ($_.Name -eq ".env" -or $_.Name -like ".env.*")) -or
            ((-not $IncludeDb) -and ($_.Name -like "*.db" -or $_.Name -like "*.sqlite" -or $_.Name -like "*.sqlite3" -or $_.Name -like "*.db-shm" -or $_.Name -like "*.db-wal"))
        } |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
}

function Test-DashboardDependencies {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$SuccessToken
    )
    & $PythonPath -c "import sys; print(sys.executable); import uvicorn; import fastapi; print('$SuccessToken')"
    if ($LASTEXITCODE -ne 0) {
        throw "Portable dependency check failed for $PythonPath"
    }
}

function Test-DashboardAppImport {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$AppCwd,
        [Parameter(Mandatory = $true)][string]$SuccessToken
    )
    Push-Location -LiteralPath $AppCwd
    try {
        & $PythonPath -c "import sys; print(sys.executable); import app; print('$SuccessToken')"
        if ($LASTEXITCODE -ne 0) {
            throw "Portable app import check failed for $PythonPath in $AppCwd"
        }
    } finally {
        Pop-Location
    }
}

if ($Clean -and (Test-Path -LiteralPath $OutputDir)) {
    Remove-Item -LiteralPath $OutputDir -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $OutputDir, $AppDir | Out-Null

Copy-Tree -Source (Join-Path $RepoRoot "review_src") -Destination (Join-Path $AppDir "review_src")
Copy-Tree -Source (Join-Path $RepoRoot "docs") -Destination (Join-Path $AppDir "docs")
Copy-Tree -Source (Join-Path $RepoRoot "scripts") -Destination (Join-Path $AppDir "scripts")

Copy-Item -LiteralPath (Join-Path $RepoRoot "AGENTS.md") -Destination (Join-Path $AppDir "AGENTS.md") -Force
Copy-Item -LiteralPath (Join-Path $RepoRoot "start_dashboard.py") -Destination (Join-Path $OutputDir "start_dashboard.py") -Force
Copy-Item -LiteralPath (Join-Path $RepoRoot $LauncherBatName) -Destination (Join-Path $OutputDir $LauncherBatName) -Force
Copy-Item -LiteralPath $RequirementsSource -Destination $RequirementsTarget -Force

Remove-PortableExcludedItems -Root $AppDir

$runtimeMode = "venv-based"
if ($PythonRuntimeDir) {
    Copy-Tree -Source $PythonRuntimeDir -Destination $PythonRuntimeTarget
}
if ($PythonEmbedZip) {
    if (Test-Path -LiteralPath $PythonRuntimeTarget) {
        Remove-Item -LiteralPath $PythonRuntimeTarget -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $PythonRuntimeTarget | Out-Null
    Expand-Archive -LiteralPath $PythonEmbedZip -DestinationPath $PythonRuntimeTarget -Force
}
if ($UseEmbeddedPython -and -not (Test-Path -LiteralPath $EmbeddedPython)) {
    throw "UseEmbeddedPython requires PythonRuntimeDir or PythonEmbedZip that provides python\python.exe."
}
if (Test-Path -LiteralPath $EmbeddedPython) {
    $runtimeMode = "true-self-contained"
}

$installStatus = "skipped"
if ($runtimeMode -eq "true-self-contained") {
    if (-not $SkipInstall) {
        $installStatus = "ok"
        & $EmbeddedPython -m pip install --upgrade pip
        if ($LASTEXITCODE -ne 0) { $installStatus = "pip upgrade failed" }
        if ($installStatus -eq "ok") {
            & $EmbeddedPython -m pip install -r $RequirementsTarget
            if ($LASTEXITCODE -ne 0) { $installStatus = "requirements install failed" }
        }
        if ($installStatus -ne "ok") {
            throw "Package install did not complete: $installStatus"
        }
    }
    Test-DashboardDependencies -PythonPath $EmbeddedPython -SuccessToken "PORTABLE_PYTHON_OK"
    Test-DashboardAppImport -PythonPath $EmbeddedPython -AppCwd (Join-Path $AppDir "review_src") -SuccessToken "PORTABLE_APP_IMPORT_OK"
} elseif ($SkipInstall) {
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        throw "SkipInstall was requested, but $VenvPython does not exist."
    }
    Test-DashboardDependencies -PythonPath $VenvPython -SuccessToken "PORTABLE_VENV_OK"
    Test-DashboardAppImport -PythonPath $VenvPython -AppCwd (Join-Path $AppDir "review_src") -SuccessToken "PORTABLE_APP_IMPORT_OK"
} else {
    & $PythonExe -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "Failed to create virtual environment." }

    $installStatus = "ok"
    & $VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { $installStatus = "pip upgrade failed" }
    if ($installStatus -eq "ok") {
        & $VenvPython -m pip install -r $RequirementsTarget
        if ($LASTEXITCODE -ne 0) { $installStatus = "requirements install failed" }
    }
    if ($installStatus -ne "ok") {
        throw "Package install did not complete: $installStatus"
    }
    Test-DashboardDependencies -PythonPath $VenvPython -SuccessToken "PORTABLE_VENV_OK"
    Test-DashboardAppImport -PythonPath $VenvPython -AppCwd (Join-Path $AppDir "review_src") -SuccessToken "PORTABLE_APP_IMPORT_OK"
}

$readme = @"
Taiwan50 Dashboard Portable - Windows

How to run:
1. Double-click the dashboard .bat launcher.
2. Open http://127.0.0.1:8000 if the browser does not open automatically.

Notes:
- This package uses python\python.exe when provided, otherwise the .venv folder created during build.
- Runtime startup does not install packages.
- Local DB and .env files are included only when the build used IncludeDb or IncludeEnv.
"@
$readme | Set-Content -LiteralPath (Join-Path $OutputDir $ReadmeUseName) -Encoding UTF8

$gitHash = ""
try {
    $gitHash = (& git -C $RepoRoot rev-parse --short HEAD).Trim()
} catch {
    $gitHash = "unknown"
}

$buildInfo = @"
Name: Taiwan50Dashboard_Portable_Windows
BuiltAt: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz")
GitHash: $gitHash
PythonExe: $PythonExe
RuntimeMode: $runtimeMode
InstallStatus: $installStatus
IncludeDb: $IncludeDb
IncludeEnv: $IncludeEnv
"@
$buildInfo | Set-Content -LiteralPath (Join-Path $OutputDir "BUILD_INFO.txt") -Encoding UTF8

if ($runtimeMode -eq "true-self-contained") {
    & $EmbeddedPython (Join-Path $OutputDir "start_dashboard.py") --dry-run
} elseif (Test-Path -LiteralPath $VenvPython) {
    & $VenvPython (Join-Path $OutputDir "start_dashboard.py") --dry-run
}

Write-Host "Portable package created:"
Write-Host "  $OutputDir"
