param(
    [string]$OutputDir,
    [Parameter(Mandatory = $true)][string]$PythonRuntimeDir,
    [string]$SitePackagesDir,
    [string]$CloudflaredExe,
    [string]$BuildPython,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$DistRoot = Join-Path $RepoRoot "dist"
if (-not $OutputDir) {
    $OutputDir = Join-Path $DistRoot "TaiwanStock_PostClose_Portable_Windows"
}
$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)
$DistPrefix = [System.IO.Path]::GetFullPath($DistRoot).TrimEnd('\') + '\'
if (-not $OutputDir.StartsWith($DistPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "For safety, OutputDir must stay under the repository dist folder: $DistRoot"
}

$PythonRuntimeDir = [System.IO.Path]::GetFullPath($PythonRuntimeDir)
$PythonSourceExe = Join-Path $PythonRuntimeDir "python.exe"
if (-not (Test-Path -LiteralPath $PythonSourceExe -PathType Leaf)) {
    throw "Python runtime does not contain python.exe: $PythonRuntimeDir"
}
if (-not $SitePackagesDir) {
    $SitePackagesDir = Join-Path $RepoRoot "review_src\.venv\Lib\site-packages"
}
$SitePackagesDir = [System.IO.Path]::GetFullPath($SitePackagesDir)
if (-not (Test-Path -LiteralPath $SitePackagesDir -PathType Container)) {
    throw "Tested site-packages folder not found: $SitePackagesDir"
}
if (-not $BuildPython) {
    $BuildPython = Join-Path $RepoRoot "review_src\.venv\Scripts\python.exe"
}
$BuildPython = [System.IO.Path]::GetFullPath($BuildPython)
if (-not (Test-Path -LiteralPath $BuildPython -PathType Leaf)) {
    throw "Build Python not found: $BuildPython"
}

$OllamaSource = Join-Path $RepoRoot "runtime\ollama"
$OllamaExeSource = Join-Path $OllamaSource "ollama.exe"
$OllamaLibSource = Join-Path $OllamaSource "lib"
$ModelSource = Join-Path $RepoRoot "models\ollama"
$ModelManifestSource = Join-Path $ModelSource "manifests\registry.ollama.ai\library\taiwan-stock-qwen\latest"
$DatabaseSource = Join-Path $RepoRoot "review_src\data\taiwan50.db"
foreach ($required in @($OllamaExeSource, $OllamaLibSource, $ModelSource, $ModelManifestSource, $DatabaseSource)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required full-stack artifact not found: $required"
    }
}

function Invoke-RobocopyTree {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [string[]]$ExcludeDirectories = @(),
        [string[]]$ExcludeFiles = @()
    )
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $arguments = @(
        $Source,
        $Destination,
        "/E",
        "/COPY:DAT",
        "/DCOPY:DAT",
        "/R:2",
        "/W:1",
        "/NFL",
        "/NDL",
        "/NJH",
        "/NJS",
        "/NP"
    )
    if ($ExcludeDirectories.Count -gt 0) {
        $arguments += "/XD"
        $arguments += $ExcludeDirectories
    }
    if ($ExcludeFiles.Count -gt 0) {
        $arguments += "/XF"
        $arguments += $ExcludeFiles
    }
    & robocopy.exe @arguments
    $code = $LASTEXITCODE
    if ($code -ge 8) {
        throw "Robocopy failed with exit code $code while copying $Source"
    }
}

function Copy-RequiredFile {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "Required file not found: $Source"
    }
    $parent = Split-Path -Parent $Destination
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
}

if ($Clean -and (Test-Path -LiteralPath $OutputDir)) {
    Remove-Item -LiteralPath $OutputDir -Recurse -Force
}
if (Test-Path -LiteralPath $OutputDir) {
    throw "Output folder already exists. Use -Clean or choose another OutputDir: $OutputDir"
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

Write-Host "[1/8] Copying current application source without secrets, DB files, caches, or virtual environments..."
$ReviewSource = Join-Path $RepoRoot "review_src"
$ReviewTarget = Join-Path $OutputDir "review_src"
Invoke-RobocopyTree `
    -Source $ReviewSource `
    -Destination $ReviewTarget `
    -ExcludeDirectories @(
        (Join-Path $ReviewSource ".venv"),
        (Join-Path $ReviewSource "data\backups"),
        (Join-Path $ReviewSource "data\yfinance_cache"),
        "__pycache__"
    ) `
    -ExcludeFiles @(".env", ".env.*", "*.db", "*.db-*", "*.sqlite", "*.sqlite3", "*.pyc", "*.pyo", "*.log")
Copy-RequiredFile (Join-Path $ReviewSource ".env.example") (Join-Path $ReviewTarget ".env.example")
Copy-RequiredFile (Join-Path $ReviewSource ".env.line_bot.example") (Join-Path $ReviewTarget ".env.line_bot.example")

Invoke-RobocopyTree (Join-Path $RepoRoot "scripts") (Join-Path $OutputDir "scripts") -ExcludeDirectories @("__pycache__") -ExcludeFiles @("*.pyc", "*.pyo", "*.log")
New-Item -ItemType Directory -Force -Path (Join-Path $OutputDir "docs") | Out-Null
Copy-RequiredFile (Join-Path $RepoRoot "docs\smoke_test.ps1") (Join-Path $OutputDir "docs\smoke_test.ps1")
foreach ($name in @(
    "AGENTS.md",
    "run_fugle_all_from_xlsx_progress.py",
    "start_dashboard.py",
    "啟動台股分析系統.bat",
    "啟動LINE股票機器人.cmd",
    "設定LINE機器人.cmd"
)) {
    Copy-RequiredFile (Join-Path $RepoRoot $name) (Join-Path $OutputDir $name)
}
$MaintenanceTarget = Join-Path $OutputDir "維護工具"
New-Item -ItemType Directory -Force -Path $MaintenanceTarget | Out-Null
foreach ($name in @(
    "啟動Cloudflare臨時Tunnel.cmd",
    "安裝盤後自動更新排程.cmd"
)) {
    Copy-RequiredFile `
        (Join-Path $RepoRoot (Join-Path "維護工具" $name)) `
        (Join-Path $MaintenanceTarget $name)
}
Copy-RequiredFile (Join-Path $ReviewSource "requirements.txt") (Join-Path $OutputDir "requirements.txt")

Write-Host "[2/8] Creating a consistent SQLite snapshot..."
$DatabaseTarget = Join-Path $ReviewTarget "data\taiwan50.db"
& $BuildPython (Join-Path $RepoRoot "scripts\create_sqlite_snapshot.py") $DatabaseSource $DatabaseTarget
if ($LASTEXITCODE -ne 0) {
    throw "SQLite snapshot creation failed."
}

Write-Host "[3/8] Copying independent Python runtime and tested site-packages..."
$PythonTarget = Join-Path $OutputDir "python"
Invoke-RobocopyTree $PythonRuntimeDir $PythonTarget -ExcludeDirectories @("__pycache__") -ExcludeFiles @("*.pyc", "*.pyo")
Invoke-RobocopyTree $SitePackagesDir (Join-Path $PythonTarget "Lib\site-packages") -ExcludeDirectories @("__pycache__") -ExcludeFiles @("*.pyc", "*.pyo")
$PortablePython = Join-Path $PythonTarget "python.exe"
& $PortablePython -c "import fastapi, uvicorn, requests, dotenv, pandas, numpy, yfinance; from zoneinfo import ZoneInfo; ZoneInfo('Asia/Taipei'); ZoneInfo('America/New_York'); print('PORTABLE_PYTHON_DEPENDENCIES_OK')"
if ($LASTEXITCODE -ne 0) {
    throw "Portable Python dependency check failed."
}

Write-Host "[4/8] Copying Ollama runtime..."
$OllamaTarget = Join-Path $OutputDir "runtime\ollama"
New-Item -ItemType Directory -Force -Path $OllamaTarget | Out-Null
Copy-RequiredFile $OllamaExeSource (Join-Path $OllamaTarget "ollama.exe")
Invoke-RobocopyTree $OllamaLibSource (Join-Path $OllamaTarget "lib")

Write-Host "[5/8] Copying registered Qwen Ollama model store without duplicating the GGUF..."
$ModelTarget = Join-Path $OutputDir "models\ollama"
Invoke-RobocopyTree $ModelSource $ModelTarget -ExcludeDirectories @("__pycache__") -ExcludeFiles @("*.tmp", "*.partial")
$manifest = Get-Content -LiteralPath $ModelManifestSource -Raw | ConvertFrom-Json
$modelLayer = $manifest.layers | Where-Object { $_.mediaType -eq "application/vnd.ollama.image.model" } | Select-Object -First 1
if (-not $modelLayer -or -not $modelLayer.digest.StartsWith("sha256:")) {
    throw "Cannot resolve the model blob digest from the Ollama manifest."
}
$modelDigest = $modelLayer.digest.Substring(7)
$ModelBlobTarget = Join-Path $ModelTarget ("blobs\sha256-" + $modelDigest)
if (-not (Test-Path -LiteralPath $ModelBlobTarget -PathType Leaf)) {
    throw "Copied Ollama model blob not found: $ModelBlobTarget"
}
$modelHash = (Get-FileHash -LiteralPath $ModelBlobTarget -Algorithm SHA256).Hash.ToLowerInvariant()
if ($modelHash -ne $modelDigest.ToLowerInvariant()) {
    throw "Ollama model SHA256 validation failed."
}
$LineTemplate = Join-Path $ReviewTarget ".env.line_bot.example"
$relativeModelBlob = "models/ollama/blobs/sha256-$modelDigest"
(Get-Content -LiteralPath $LineTemplate) `
    -replace '^QWEN_MODEL_FILE=.*$', "QWEN_MODEL_FILE=$relativeModelBlob" |
    Set-Content -LiteralPath $LineTemplate -Encoding UTF8

Write-Host "[6/8] Copying Cloudflare Tunnel runtime when provided..."
$cloudflaredIncluded = $false
if ($CloudflaredExe) {
    $CloudflaredExe = [System.IO.Path]::GetFullPath($CloudflaredExe)
    if (-not (Test-Path -LiteralPath $CloudflaredExe -PathType Leaf)) {
        throw "Cloudflared executable not found: $CloudflaredExe"
    }
    Copy-RequiredFile $CloudflaredExe (Join-Path $OutputDir "runtime\cloudflared\cloudflared.exe")
    $cloudflaredIncluded = $true
}

Write-Host "[7/8] Writing deployment instructions and build metadata..."
$readme = @"
Taiwan Stock Post-Close Analysis - Portable Windows

This folder contains:
- independent Python runtime and tested dependencies
- current dashboard, all-market post-close updater, and LINE stock bot
- a consistent SQLite database snapshot
- Ollama runtime and registered taiwan-stock-qwen model store
- relative-path launchers and Windows scheduled-task installer
- no real API keys, LINE secrets, tokens, or local .env files

Target server prerequisites:
1. 64-bit Windows on compatible CPU architecture.
2. Current NVIDIA display driver for GPU acceleration. CUDA Toolkit is not required by this folder unless the installed Ollama build specifically requires it.
3. At least 30 GB free disk after extraction; more space is recommended for logs and future DB/model growth.
4. Outbound HTTPS access for official market updates, LINE Messaging API, news/global context, and Cloudflare Tunnel.

First setup after the folder reaches its final location:
1. Run 設定LINE機器人.cmd.
2. Fill LINE_CHANNEL_SECRET and LINE_CHANNEL_ACCESS_TOKEN in .env.line_bot at this folder's root.
3. Optional provider credentials belong in review_src\.env. Never place secrets in source files.
4. Run 啟動LINE股票機器人.cmd; it starts the temporary Tunnel and updates/verifies LINE Webhook automatically.
5. Run 維護工具\安裝盤後自動更新排程.cmd as Administrator after the folder is in its final location.

Dashboard:
- Run 啟動台股分析系統.bat.
- Open http://127.0.0.1:8000.

LINE local endpoints:
- Qwen/Ollama: http://127.0.0.1:8020/v1
- read-only market API: http://127.0.0.1:8010
- LINE webhook gateway: http://127.0.0.1:8021/line/webhook

Post-close automation:
- Taiwan Stock Full-Market Database Update: weekdays at 15:00.
- The task first captures the full-market Fugle price-volume distribution, then updates official TWSE/TPEx OHLCV, margin financing, short selling, and securities-lending balances.
- Official sources that have not yet published the requested trading date are retried until the end of the post-close window; older data is never relabelled as today's data.
- StartWhenAvailable is enabled. If the computer was off at 15:00, Windows runs the missed task after the computer is started and the user signs in.
- Taiwan Stock External Analysis Context: weekdays at 06:15.
- Exchange-calendar checks remain authoritative on holidays and closure days.

Migration rules:
- Do not copy an actively changing SQLite file manually. This package already contains a verified snapshot.
- Windows scheduled tasks contain the final absolute installation path and must be registered again after moving the folder.
- A Windows package cannot be moved to Linux/macOS. Build a separate package on that operating system.
"@
$readme | Set-Content -LiteralPath (Join-Path $OutputDir "README_部署與啟動.txt") -Encoding UTF8

$gitHash = "unknown"
$gitWorkingTreeDirty = "unknown"
try {
    $gitHash = (& git -C $RepoRoot rev-parse --short HEAD).Trim()
    $gitWorkingTreeDirty = if ((& git -C $RepoRoot status --porcelain)) { "true" } else { "false" }
} catch {
    $gitHash = "unknown"
    $gitWorkingTreeDirty = "unknown"
}
$pythonVersion = (& $PortablePython -c "import sys; print(sys.version.split()[0])").Trim()
$dbInfo = & $PortablePython -c "import sqlite3, pathlib; p=pathlib.Path(r'$DatabaseTarget'); c=sqlite3.connect('file:'+p.as_posix()+'?mode=ro', uri=True); print(c.execute('select max(date) from history_price').fetchone()[0]); print(c.execute('pragma quick_check').fetchone()[0]); c.close()"
$dbLatest = $dbInfo[0]
$dbQuickCheck = $dbInfo[1]
$buildInfo = @"
Name: TaiwanStock_PostClose_Portable_Windows
BuiltAt: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz")
GitHash: $gitHash
WorkingTreeDirty: $gitWorkingTreeDirty
PythonVersion: $pythonVersion
RuntimeMode: true-self-contained-folder
DatabaseLatestTradingDate: $dbLatest
DatabaseQuickCheck: $dbQuickCheck
OllamaModelId: taiwan-stock-qwen
OllamaModelSha256: $modelHash
CloudflaredIncluded: $cloudflaredIncluded
SecretsIncluded: false
"@
$buildInfo | Set-Content -LiteralPath (Join-Path $OutputDir "BUILD_INFO.txt") -Encoding UTF8

Write-Host "[8/8] Running package-relative verification..."
Push-Location -LiteralPath ([System.IO.Path]::GetTempPath())
try {
    & $PortablePython (Join-Path $OutputDir "start_dashboard.py") --dry-run --no-browser --no-preload
    if ($LASTEXITCODE -ne 0) {
        throw "Portable dashboard dry-run failed."
    }
    & $PortablePython -c "import sys; sys.path.insert(0, r'$(Join-Path $OutputDir "review_src")'); import bot_app, line_bot_app; print('PORTABLE_LINE_IMPORT_OK')"
    if ($LASTEXITCODE -ne 0) {
        throw "Portable LINE application import check failed."
    }
} finally {
    Pop-Location
}
$GeneratedBlankEnv = Join-Path $ReviewTarget ".env"
if (Test-Path -LiteralPath $GeneratedBlankEnv -PathType Leaf) {
    Remove-Item -LiteralPath $GeneratedBlankEnv -Force
}
$GeneratedYfinanceCache = Join-Path $ReviewTarget "data\yfinance_cache"
if (Test-Path -LiteralPath $GeneratedYfinanceCache -PathType Container) {
    Remove-Item -LiteralPath $GeneratedYfinanceCache -Recurse -Force
}
Get-ChildItem -LiteralPath $ReviewTarget -Recurse -Force -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }

Write-Host "Portable post-close package created:"
Write-Host "  $OutputDir"
