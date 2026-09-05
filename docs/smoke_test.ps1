param(
    [string]$BaseUrl = $env:BASE_URL,
    [string]$SmokeStockCode = $env:SMOKE_STOCK_CODE,
    [string]$PythonBin = $env:PYTHON
)

if (-not $BaseUrl) { $BaseUrl = "http://localhost:8000" }
if (-not $SmokeStockCode) { $SmokeStockCode = "2330" }
if (-not $PythonBin) { $PythonBin = "python" }

$Failures = 0

function Pass($Message) {
    Write-Host "PASS $Message"
}

function Fail($Message) {
    Write-Host "FAIL $Message"
    $script:Failures += 1
}

function Check-PyCompile {
    Write-Host "Checking py_compile: review_src/app.py"
    & $PythonBin -m py_compile review_src/app.py
    if ($LASTEXITCODE -eq 0) {
        Pass "python -m py_compile review_src/app.py"
    } else {
        Fail "python -m py_compile review_src/app.py"
    }
}

function Test-HttpGet {
    param(
        [string]$Path,
        [bool]$ExpectJson,
        [string]$Label
    )

    $Url = "$BaseUrl$Path"
    try {
        $Response = Invoke-WebRequest -UseBasicParsing -Uri $Url -Method GET -TimeoutSec 30
        $Status = [int]$Response.StatusCode
    } catch {
        $Resp = $_.Exception.Response
        if ($Resp -and $Resp.StatusCode) {
            $Status = [int]$Resp.StatusCode
            if ($Status -eq 400 -or $Status -eq 404) {
                Fail "$Label - HTTP $Status (route or parameter issue)"
                return
            }
            if ($Status -ge 500) {
                Fail "$Label - HTTP $Status (server error)"
                return
            }
            Fail "$Label - HTTP $Status"
            return
        }
        Fail "$Label - server unreachable or request failed: $($_.Exception.Message)"
        return
    }

    if ($Status -lt 200 -or $Status -ge 300) {
        Fail "$Label - HTTP $Status"
        return
    }

    if ($ExpectJson) {
        try {
            $null = $Response.Content | ConvertFrom-Json
            Pass "$Label - HTTP $Status JSON parsed"
        } catch {
            Fail "$Label - HTTP $Status but response is not JSON"
        }
    } else {
        Pass "$Label - HTTP $Status"
    }
}

Write-Host "Smoke test baseline"
Write-Host "BASE_URL=$BaseUrl"
Write-Host "SMOKE_STOCK_CODE=$SmokeStockCode"
Write-Host ""

Check-PyCompile
Test-HttpGet -Path "/" -ExpectJson:$false -Label "GET /"
Test-HttpGet -Path "/api/quotes?mode=watchlist" -ExpectJson:$true -Label "GET /api/quotes?mode=watchlist"
Test-HttpGet -Path "/api/quotes?mode=tw50" -ExpectJson:$true -Label "GET /api/quotes?mode=tw50"
Test-HttpGet -Path "/api/stock/$SmokeStockCode/detail" -ExpectJson:$true -Label "GET /api/stock/$SmokeStockCode/detail"

Write-Host ""
if ($Failures -eq 0) {
    Pass "smoke test completed"
    exit 0
}

Fail "smoke test completed with $Failures failure(s)"
exit 1

