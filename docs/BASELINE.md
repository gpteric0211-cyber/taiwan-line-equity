# BASELINE.md — Phase 0 驗證基準

本文件記錄目前專案的最低啟動與 API smoke test 基準。  
本基準只驗證 HTTP、啟動、JSON 基本可用性，不驗證股票分析結果是否正確。

## app.py 行數

重新確認結果：

- `review_src/app.py`: 6001 行

確認方式：

```powershell
(Get-Content -LiteralPath review_src\app.py | Measure-Object -Line).Lines
```

## App 啟動方式

目前可用的啟動方式依現有專案檔案：

```powershell
Set-Location <repo-root>\review_src
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

或使用現有啟動檔：

```powershell
Set-Location <repo-root>\review_src
.\啟動台股分析系統.cmd
```

`docs/smoke_test.sh` 與 `docs/smoke_test.ps1` **不會自動啟動 server**。  
請先啟動 server，再執行 smoke test。

## Smoke test 使用方式

### Bash

```bash
./docs/smoke_test.sh
```

### PowerShell

```powershell
.\docs\smoke_test.ps1
```

## 預設設定

- 預設 `BASE_URL`: `http://localhost:8000`
- 預設 `SMOKE_STOCK_CODE`: `2330`

使用 `2330` 作為預設股票代號，是因為台積電是台灣市場最常見、最穩定的測試標的之一，通常較容易具備歷史價量、法人、估值與詳細頁資料。

## 覆蓋設定

### Bash

```bash
BASE_URL=http://127.0.0.1:8034 SMOKE_STOCK_CODE=2317 ./docs/smoke_test.sh
```

或只改股票：

```bash
SMOKE_STOCK_CODE=2317 ./docs/smoke_test.sh
```

### PowerShell

```powershell
$env:BASE_URL="http://127.0.0.1:8034"
$env:SMOKE_STOCK_CODE="2317"
.\docs\smoke_test.ps1
```

## Smoke test 檢查項目

Smoke test 至少檢查：

```bash
python -m py_compile review_src/app.py
GET /
GET /api/quotes?mode=watchlist
GET /api/quotes?mode=tw50
GET /api/stock/${SMOKE_STOCK_CODE}/detail
```

## PASS / FAIL 判斷標準

Smoke test 只驗證：

- app 可被 Python 編譯
- endpoint 可回應
- HTTP status code 是否正常
- JSON endpoint 的 response 是否可解析為 JSON

Smoke test 不驗證：

- 股票分析結果是否正確
- 推薦結論是否正確
- 分數是否正確
- 資料是否完整
- 外部資料源是否已更新

### PASS

- `python -m py_compile review_src/app.py` 成功。
- `GET /` 回傳 HTTP 2xx。
- JSON endpoint 回傳 HTTP 2xx 且 response 可解析為 JSON。
- `GET /api/stock/${SMOKE_STOCK_CODE}/detail` 若 HTTP 200 且 JSON 可解析，即使 response 內為 `ok: false`，仍算 smoke test PASS。

### FAIL

- server 無法連線。
- HTTP 5xx。
- JSON endpoint 回傳非 JSON。
- 指令本身失敗。
- HTTP 400 或 404。這通常代表 route 或參數可能有問題，需進一步檢查。

## 關於 detail API 的 ok: false

`GET /api/stock/${SMOKE_STOCK_CODE}/detail` 如果 HTTP 200，但 response 裡是：

```json
{"ok": false}
```

仍可視為 smoke test PASS。

原因是 `ok: false` 可能代表：

- DB 缺資料
- 資料源尚未更新
- 該股票代號目前沒有足夠資料
- 本機 `.env` 或資料庫內容不足

這不一定代表程式壞掉。Smoke test 只判斷 endpoint 是否可回應與 JSON 是否可解析。

## 目前已知可能需要資料庫或環境變數的 endpoint

以下 endpoint 可能受資料庫內容、`.env`、資料源 token、外部 API 可用性或市場時間影響：

- `GET /api/quotes?mode=watchlist`
- `GET /api/quotes?mode=tw50`
- `GET /api/stock/${SMOKE_STOCK_CODE}/detail`

若這些 endpoint HTTP 200 且 JSON 可解析，但資料內容不完整，通常屬於資料或環境狀態，不一定是程式錯誤。

## 目前已知 GET 副作用風險

依 `docs/ARCHITECTURE.md` 盤點，目前某些 GET route 可能仍含 `enqueue_missing=True` 或背景補資料風險。Phase 0 只建立基準驗證，不修正該問題。  
GET 副作用清理規劃在 Phase 2。

## 2026-08-29 LINE 本地模型規格 v2 受保護分析基準

本節是 LINE／本地模型後續階段的防誤改基準，不代表目前工作樹等同 Git HEAD，也不宣稱所有
既存公式已完成獨立金融驗證。建立時工作樹已有大量使用者未提交內容，因此不能用單一 commit
充當目前行為；本基準改以實際工作樹中受保護分析檔的 SHA-256 為準。

- 基準時間：`2026-08-29T20:07:15+08:00`
- Git HEAD：`6d9e34b2875058394004800a605509f049b45ea2`
- 機器可讀 manifest：`docs/LINE_MODEL_SPEC_V2_BASELINE.json`
- 基準類型：working-tree protected-file hashes
- 本規格階段修改 runtime／模型／環境／DB：`0`
- 規格完成後受保護檔案 hash mismatch：`0`

受保護範圍包含：scoring、technical、practical/referee、support/resistance、estimated chip cost、
next-day outlook、advisory/recommendation safety、low-zone entry、price-volume service 與集中式
data quality。後續 ModelFactPacket、request planner、research、queue 或 LINE runtime 階段開始前，
必須重新計算 manifest；任何差異都要停止並另案說明，不得混在模型／網搜實作中。

### 規格階段測試狀態

- 2026-08-29 targeted rework 後最新完整測試：`455 passed in 23.43s`。
- 舊的 blanket-ban 測試已由三個明確契約取代：deadline 准入時允許模型解釋基本面缺失；模型補造
  的財務數字必須被 validator 擋下；deadline 准入失敗時必須跳過模型並回誠實 fallback。
- 三個契約測試、LINE gateway 58 項測試、skill validator 與 protected hash 複核均通過；本次沒有
  修改 runtime、模型設定、DB 或受保護金融分析檔。
- 下一階段仍須先取得使用者對 context/profile、cache、conflict 與官方來源條款的逐行簽核，之後
  才能開始 `ModelFactPacketV2 + token preflight + observability` shadow mode。
