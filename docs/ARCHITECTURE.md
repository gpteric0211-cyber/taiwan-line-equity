# ARCHITECTURE.md — 台灣股票分析系統現況盤點

> 2026-08-31 現況入口：[PROJECT_MAINTENANCE_PLAN.md](PROJECT_MAINTENANCE_PLAN.md)第5節。
> 本檔下方為歷史基線，含當時「未找到api/repository/data_quality」及舊行號，不能當成目前不存在這些模組。
> 新盤點已確認現有分層及入口；只規劃逐批整理，尚未刪除程式、重構或部署。

> 2026-08-23 增量：全市場資料層已新增 `adapter/`、`services/`、`repository/`、`api/` 分層，並加入 `stock_master`、`daily_technical_snapshot`、精確日期 TWSE/TPEx 全市場日線回補與唯讀 Bot history API。最新資料流與 schema 請以 `docs/FULL_MARKET_DATABASE_ARCHITECTURE.md` 為準；下方舊盤點保留作重構前基線。

> 2026-08-28 增量：LINE 連續對話使用獨立的 encrypted SQLite。資料流為 `LINE webhook → ConversationMemoryService → LineConversationRepository → local Qwen context builder/compactor`；市場 FACTS 仍只從唯讀 Bot API 進入，記憶不得成為金融資料或主結論來源。每個 Channel／使用者／chat scope 以 HMAC 隔離，原文加密，並支援重送冪等、亂序保護、撤回失效、unfollow／使用者刪除及跨程序 compaction lease。逐字問答保存 24 小時，有限 session 與壓縮摘要保存 30 天。

> 本文件依照 `docs/WORKFLOW.md` 要求建立。內容只描述目前原始碼中可驗證的事實與重構建議，不代表已執行任何重構。

## 0. 查證方式

本次盤點以 `review_src/` 作為實際程式根目錄。已使用下列搜尋與檢查：

```bash
rg -n "route|@app|APIRouter|CREATE TABLE|sqlite|def |class |FinMind|TWSE|TAIFEX|Fugle|Yahoo|enqueue|background|cache" .
rg -n "enqueue_missing|background_tasks|asyncio.create_task|threading.Thread" .
rg -n "^@app\.(get|post|delete|patch)|^@router\.(get|post|delete|patch)|include_router|FastAPI|StaticFiles" app.py auth\router.py
rg -n "CREATE TABLE IF NOT EXISTS|CREATE INDEX IF NOT EXISTS|ALTER TABLE" core\db.py auth\models.py
rg -n "def fetch_us_quote|Yahoo Finance|yfinance|configure_yfinance_cache|query1.finance.yahoo|yahoo_tw_symbols|upsert_yahoo|TWSE_|MIS|FUGLE|FINMIND|TAIFEX|Fugle|FinMind" core\config.py app.py market\futures.py us_relations.py start_dashboard.py
rg -n "uvicorn|app:app|StaticFiles|index\.html|detail\.html|fetch\(|/api/" app.py start_dashboard.py static\index.html static\detail.html "啟動台股分析系統.cmd"
rg -n "_cache|CACHE|cache" app.py market\futures.py static\index.html
rg -n "def .*score|def .*status|def .*rsi|def .*support|def .*resistance|def .*cost|def .*price_volume|def .*futures|def .*outlook|class |score_stock|classify_practical_status|calc_rsi|calc_support|chip_cost|price_volume|futures_night|synthesize_next_day" app.py scoring.py chip_cost_engine.py price_volume.py market\futures.py us_relations.py
```

`app.py` 實際行數使用：

```powershell
(Get-Content -LiteralPath app.py | Measure-Object -Line).Lines
```

結果：`review_src/app.py` 目前為 **6001 行**。

## 1. 目前專案目錄結構摘要

專案根目錄目前可見：

- `AGENTS.md`
- `docs/`
- `.agents/`
- `review_src/`

實際應用程式主要位於 `review_src/`，目前可見主要檔案與目錄：

- `review_src/app.py`
- `review_src/scoring.py`
- `review_src/chip_cost_engine.py`
- `review_src/price_volume.py`
- `review_src/us_relations.py`
- `review_src/start_dashboard.py`
- `review_src/auth/`
- `review_src/core/`
- `review_src/market/`
- `review_src/static/`
- `review_src/data/`

目前未看到正式分層目錄：

- `api/`: Unverified / Not found in current source
- `service/`: Unverified / Not found in current source
- `repository/`: Unverified / Not found in current source
- `adapter/`: Unverified / Not found in current source
- `analysis/`: Unverified / Not found in current source
- `model/`: Unverified / Not found in current source
- `schema/`: Unverified / Not found in current source
- `task/`: Unverified / Not found in current source

## 2. 主要入口檔案

### 後端入口

- FastAPI app 建立於 `review_src/app.py:139`
- lifespan 定義於 `review_src/app.py:134`
- static mount 定義於 `review_src/app.py:140`
- auth router 掛載於 `review_src/app.py:141`
- auth router 來源為 `review_src/auth/router.py`，其 route 定義見 `review_src/auth/router.py:67`

### 啟動方式

- `review_src/start_dashboard.py` 使用 uvicorn 啟動 `app:app`：`review_src/start_dashboard.py:85`、`review_src/start_dashboard.py:86`
- Windows 啟動檔檢查 uvicorn：`review_src/啟動台股分析系統.cmd:22`

### 前端入口

- 首頁由 `GET /` 回傳 `static/index.html`：`review_src/app.py:4652`、`review_src/app.py:4654`
- 股票 detail 頁由 `/stock/{code}` 與 `/detail/{code}` 回傳 `static/detail.html`：`review_src/app.py:4657`、`review_src/app.py:4659`、`review_src/app.py:4662`、`review_src/app.py:4664`
- 首頁主要 API 呼叫位於 `review_src/static/index.html:816`、`review_src/static/index.html:825`、`review_src/static/index.html:834`、`review_src/static/index.html:953`、`review_src/static/index.html:954`
- 詳細頁讀取股票 detail API：`review_src/static/detail.html:58`

### main.py

- `main.py`: Unverified / Not found in current source

## 3. app.py 行數與責任混雜情況

`review_src/app.py` 存在，實際行數為 **6001 行**。它是目前最大架構風險之一。

目前 `app.py` 同時包含下列責任：

- FastAPI app / route：`review_src/app.py:139`、`review_src/app.py:4652`
- 靜態檔案掛載：`review_src/app.py:140`
- 全域 cache：`review_src/app.py:148`、`review_src/app.py:152`、`review_src/app.py:157`、`review_src/app.py:163`、`review_src/app.py:165`
- Fugle rate limiter class：`review_src/app.py:170`
- 資料修復隊列：`review_src/app.py:468`
- TWSE MIS 即時報價抓取與 daemon：`review_src/app.py:558`、`review_src/app.py:684`、`review_src/app.py:795`
- TWSE OpenAPI 資料抓取：`review_src/app.py:826`、`review_src/app.py:872`、`review_src/app.py:974`
- Fugle quote / 分價量抓取：`review_src/app.py:1008`、`review_src/app.py:1020`、`review_src/app.py:1045`
- FinMind 抓取與寫入：`review_src/app.py:1707`、`review_src/app.py:1731`
- Yahoo / yfinance 台股歷史與估值補資料：`review_src/app.py:1885`、`review_src/app.py:1891`、`review_src/app.py:1958`、`review_src/app.py:2013`
- 支撐 / 賣壓計算：`review_src/app.py:3085`、`review_src/app.py:3198`
- 籌碼成本相關 orchestration：`review_src/app.py:2385`
- practical status / referee：`review_src/app.py:3798`、`review_src/app.py:3945`
- 列表 row 組裝與 row cache：`review_src/app.py:4237`、`review_src/app.py:4505`
- 背景更新任務：`review_src/app.py:4547`、`review_src/app.py:4574`、`review_src/app.py:4581`
- 美股 / 期貨 / 籌碼 / 技術多因子隔日展望：`review_src/app.py:5339`、`review_src/app.py:5374`、`review_src/app.py:5406`、`review_src/app.py:5500`、`review_src/app.py:5565`
- API route：`review_src/app.py:4652` 到 `review_src/app.py:6418`

## 4. API endpoints 清單

### app.py routes

| Method | Path | Location |
|---|---|---|
| GET | `/` | `review_src/app.py:4652` |
| GET | `/stock/{code}` | `review_src/app.py:4657` |
| GET | `/detail/{code}` | `review_src/app.py:4662` |
| GET | `/api/config` | `review_src/app.py:4667` |
| GET | `/api/status` | `review_src/app.py:4705` |
| GET | `/api/watchlist` | `review_src/app.py:4710` |
| POST | `/api/watchlist` | `review_src/app.py:4717` |
| DELETE | `/api/watchlist/{code}` | `review_src/app.py:4743` |
| POST | `/api/update/eod` | `review_src/app.py:4751` |
| POST | `/api/update/chip` | `review_src/app.py:4758` |
| POST | `/api/update/ensure-complete` | `review_src/app.py:4776` |
| POST | `/api/update/price-volume` | `review_src/app.py:4791` |
| GET | `/api/debug/price-volume/{code}` | `review_src/app.py:4806` |
| GET | `/api/debug/volume_units` | `review_src/app.py:4829` |
| GET | `/api/debug/rsi_check/{code}` | `review_src/app.py:4837` |
| GET | `/api/debug/cost/{code}` | `review_src/app.py:4871` |
| GET | `/api/stock/{code}/detail` | `review_src/app.py:5811` |
| GET | `/api/debug/data-readiness` | `review_src/app.py:6311` |
| GET | `/api/debug/us-relations/coverage` | `review_src/app.py:6322` |
| GET | `/api/debug/ui-completeness` | `review_src/app.py:6328` |
| GET | `/api/quotes/watchlist` | `review_src/app.py:6413` |
| GET | `/api/quotes` | `review_src/app.py:6418` |

### auth router routes

`auth_router` 掛載於 `review_src/app.py:141`。

| Method | Path | Location |
|---|---|---|
| GET | `/auth/security-check` | `review_src/auth/router.py:67` |
| POST | `/auth/register` | `review_src/auth/router.py:78` |
| POST | `/auth/verify-email` | `review_src/auth/router.py:90` |
| POST | `/auth/resend-verification` | `review_src/auth/router.py:98` |
| POST | `/auth/login` | `review_src/auth/router.py:110` |
| POST | `/auth/logout` | `review_src/auth/router.py:128` |
| GET | `/auth/me` | `review_src/auth/router.py:135` |
| POST | `/auth/forgot-password` | `review_src/auth/router.py:140` |
| POST | `/auth/reset-password` | `review_src/auth/router.py:152` |
| GET | `/me/watchlist` | `review_src/auth/router.py:160` |
| POST | `/me/watchlist` | `review_src/auth/router.py:165` |
| DELETE | `/me/watchlist/{code}` | `review_src/auth/router.py:173` |
| PATCH | `/me/watchlist/reorder` | `review_src/auth/router.py:179` |

## 5. 外部資料源清單

| Source | Purpose currently visible | Location |
|---|---|---|
| TWSE MIS `getStockInfo.jsp` | 盤中即時報價、快照、快照保存 | endpoint config `review_src/core/config.py:147`; parser/cache `review_src/app.py:558`; batch fetch `review_src/app.py:684`; daemon `review_src/app.py:795` |
| TWSE OpenAPI `STOCK_DAY_ALL` | 全市場盤後收盤資料 | config `review_src/core/config.py:149`; fetch `review_src/app.py:826` |
| TWSE `STOCK_DAY` by code | 個股日 K 補資料 | config `review_src/core/config.py:150`; fetch `review_src/app.py:872` |
| TWSE `BWIBBU_ALL` | PE / PB / 殖利率 | config `review_src/core/config.py:151`; fetch `review_src/app.py:974` |
| TWSE 除權息 CSV | 除權息資料 | config `review_src/core/config.py:153`; fetch text `review_src/app.py:3412` |
| FinMind | 股價、法人、融資融券、外資持股等 | config `review_src/core/config.py:148`; token config `review_src/core/config.py:124`; generic fetch `review_src/app.py:1707`; upsert `review_src/app.py:1731` |
| Fugle intraday quote | 盤中 quote fallback / background refresh | config `review_src/core/config.py:146`; headers `review_src/app.py:1004`; cached quote `review_src/app.py:1008`; network quote `review_src/app.py:1020` |
| Fugle intraday volumes | 分價量資料來源 | network fetch `review_src/app.py:1045`; upsert profile `review_src/app.py:1191` |
| Yahoo Finance chart | 台股歷史 fallback / 美股報價 | direct chart `review_src/app.py:1891`; chart URL `review_src/app.py:1903`; quote `review_src/app.py:4921` |
| yfinance | 台股歷史、估值 fallback / 美股報價 fallback | cache config `review_src/core/config.py:97`; history fallback `review_src/app.py:1958`; valuation fallback `review_src/app.py:2013`; quote fallback `review_src/app.py:5012` |
| TAIFEX OpenAPI | 期貨夜盤 / 盤後期貨參考 | config `review_src/core/config.py:152`; cache/fetch `review_src/market/futures.py:15`、`review_src/market/futures.py:25`; signal `review_src/market/futures.py:149` |

## 6. DB tables / schema / SQL 定義位置

主要 DB 連線與初始化：

- `db()`：`review_src/core/db.py:10`
- `init_db()`：`review_src/core/db.py:18`
- `app.py` startup 呼叫 `init_db()`：`review_src/app.py:4623`

### market / analysis tables

| Table | Location |
|---|---|
| `watchlist` | `review_src/core/db.py:26` |
| `eod_price` | `review_src/core/db.py:32` |
| `valuation` | `review_src/core/db.py:48` |
| `history_price` | `review_src/core/db.py:60` |
| `institution_daily` | `review_src/core/db.py:74` |
| `margin_daily` | `review_src/core/db.py:84` |
| `lending_daily` | `review_src/core/db.py:95` |
| `fetch_status` | `review_src/core/db.py:104` |
| `corporate_actions` | `review_src/core/db.py:110` |
| `foreign_shareholding` | `review_src/core/db.py:121` |
| `stock_state_history` | `review_src/core/db.py:129` |
| `mis_quote_snapshot` | `review_src/core/db.py:142` |
| `price_volume_profile_daily` | `review_src/core/db.py:160` |
| `price_volume_score_daily` | `review_src/core/db.py:180` |
| `next_day_outlook_daily` | `review_src/core/db.py:214` |

Schema migrations / ALTER:

- `valuation.eps`：`review_src/core/db.py:246`
- `valuation.eps_source`：`review_src/core/db.py:248`
- `history_price.amount`：`review_src/core/db.py:251`
- `history_price.volume_unit`：`review_src/core/db.py:253`

### auth tables

Auth SQL 被 `core/db.py` 引入：`review_src/core/db.py:7`，並在 `review_src/core/db.py:242` 執行。

| Table | Location |
|---|---|
| `users` | `review_src/auth/models.py:5` |
| `email_verifications` | `review_src/auth/models.py:16` |
| `user_watchlist` | `review_src/auth/models.py:26` |
| `login_attempts` | `review_src/auth/models.py:37` |
| `auth_sessions` | `review_src/auth/models.py:46` |

DB migrations framework:

- Unverified / Not found in current source

## 7. background task / cache / repair job 位置

### Background threads

| Task | Location |
|---|---|
| data repair worker thread | `review_src/app.py:484` |
| MIS quote daemon thread | `review_src/app.py:822` |
| startup EOD update thread | `review_src/app.py:4639` |
| startup watchlist chip update thread | `review_src/app.py:4644` |
| startup TW50 chip update thread | `review_src/app.py:4647` |
| watchlist add prefetch complete-data thread | `review_src/app.py:4739` |
| manual EOD update thread | `review_src/app.py:4754` |
| manual chip update thread | `review_src/app.py:4770` |
| manual ensure-complete thread | `review_src/app.py:4787` |
| manual price-volume update thread | `review_src/app.py:4802` |
| auto repair from readiness | `review_src/app.py:6276`、`review_src/app.py:6280` |

### Repair queue

- `enqueue_data_repair()`：`review_src/app.py:468`
- `_data_repair_worker()`：`review_src/app.py:487`
- `data_readiness_for_items()`：`review_src/app.py:6063`
- `enqueue_missing` inside readiness can start background repair：`review_src/app.py:6276`、`review_src/app.py:6280`

### Caches

| Cache | Location |
|---|---|
| `_score_cache` | `review_src/app.py:148` |
| `_practical_cache` | `review_src/app.py:152` |
| `_row_cache` | `review_src/app.py:157` |
| `_fugle_quote_cache` | `review_src/app.py:163` |
| `_mis_quote_cache` | `review_src/app.py:165` |
| `prune_timed_cache()` | `review_src/app.py:213` |
| `prune_mis_quote_cache()` | `review_src/app.py:234` |
| `prune_compute_caches()` | `review_src/app.py:253` |
| TAIFEX cache | `review_src/market/futures.py:13`、`review_src/market/futures.py:15` |
| frontend session cache prefix | `review_src/static/index.html:709` |
| frontend read/write cache | `review_src/static/index.html:743`、`review_src/static/index.html:744` |

## 8. 目前分析邏輯位置

| Domain | Location |
|---|---|
| scoring main entry | `review_src/scoring.py:321` |
| scoring indicators | `review_src/scoring.py:167` |
| Wilder RSI shared implementation | `review_src/scoring.py:119` |
| app-level `calc_rsi()` wrapper | `review_src/app.py:2245` |
| practical status / referee | `review_src/app.py:3798` |
| practical status cache | `review_src/app.py:3945` |
| support/resistance detail | `review_src/app.py:3085` |
| support/resistance compact display | `review_src/app.py:3198` |
| 5/10/20/60 support-resistance periods | `review_src/app.py:2606` |
| public chip cost orchestration | `review_src/app.py:2385` |
| chip cost engine dataclass | `review_src/chip_cost_engine.py:12` |
| foreign cost estimate | `review_src/chip_cost_engine.py:225` |
| trust buy cost estimate | `review_src/chip_cost_engine.py:301` |
| POC60 estimate | `review_src/chip_cost_engine.py:356` |
| chip cost entry | `review_src/chip_cost_engine.py:415` |
| price-volume evaluate profile | `review_src/price_volume.py:203` |
| price-volume score compute | `review_src/app.py:1385` |
| Fugle price-volume upsert | `review_src/app.py:1191` |
| futures night signal | `review_src/market/futures.py:149` |
| app-level futures factor | `review_src/app.py:5374` |
| US relations table lookup | `review_src/us_relations.py:413` |
| relation coverage | `review_src/us_relations.py:423` |
| next-day US factor | `review_src/app.py:5339` |
| next-day chip factor | `review_src/app.py:5406` |
| next-day tech factor | `review_src/app.py:5500` |
| next-day outlook synthesis | `review_src/app.py:5565` |
| next-day outlook persistence | `review_src/app.py:5692` |

## 9. 目前資料流

### 首頁 / 列表

1. 前端 `static/index.html` 呼叫 config/status/watchlist/quotes：
   - `review_src/static/index.html:816`
   - `review_src/static/index.html:825`
   - `review_src/static/index.html:834`
   - `review_src/static/index.html:953`
   - `review_src/static/index.html:954`
2. 後端 `/api/quotes` 或 `/api/quotes/watchlist`：
   - `review_src/app.py:6413`
   - `review_src/app.py:6418`
3. Quote API 使用 `data_readiness_for_items()`：
   - `review_src/app.py:6434`
4. Row 組裝：
   - `_build_row_uncached()`：`review_src/app.py:4237`
   - `build_row()`：`review_src/app.py:4505`
   - `warm_row_cache()`：`review_src/app.py:4521`
5. Row 組裝讀取 DB/cache，並使用 scoring、practical status、支撐壓力、籌碼成本與分價量 summary。

### 詳細頁

1. 前端 `static/detail.html` 呼叫 `/api/stock/{code}/detail`：`review_src/static/detail.html:58`
2. 後端 endpoint：`review_src/app.py:5811`
3. detail endpoint 會組裝：
   - base row / practical status：`review_src/app.py:5844`
   - US quote：`review_src/app.py:5876`
   - futures signal：`review_src/app.py:5887`
   - next-day outlook：`review_src/app.py:5888`
   - price-volume payload：`review_src/app.py:5940`

### 資料源 → DB/cache

- MIS quote 進 `_mis_quote_cache` 並保存 `mis_quote_snapshot`：`review_src/app.py:684`、`review_src/app.py:645`
- TWSE EOD / valuation 寫入 `eod_price`、`history_price`、`valuation`：`review_src/app.py:826`、`review_src/app.py:872`、`review_src/app.py:974`
- FinMind 寫入 `history_price`、`institution_daily`、`margin_daily`、`foreign_shareholding`：`review_src/app.py:1731`
- Fugle 分價量寫入 `price_volume_profile_daily`：`review_src/app.py:1191`
- Yahoo / yfinance fallback 寫入 `history_price`、`valuation`：`review_src/app.py:1891`、`review_src/app.py:1958`、`review_src/app.py:2013`
- TAIFEX data 使用 module cache 並產生 futures signal：`review_src/market/futures.py:25`、`review_src/market/futures.py:149`

## 10. 目前架構風險

### 10.1 app.py 過大且責任混雜

`review_src/app.py` 為 6001 行，混合 API route、資料抓取、DB 操作、分析邏輯、cache、background task、row 組裝與多因子展望。這會提高修改風險與循環依賴風險。

具體例子：

- route：`review_src/app.py:4652`
- MIS adapter-like code：`review_src/app.py:558`
- TWSE adapter-like code：`review_src/app.py:826`
- FinMind adapter-like code：`review_src/app.py:1707`
- analysis/referee：`review_src/app.py:3798`
- background task：`review_src/app.py:4547`
- API response assembly：`review_src/app.py:4237`、`review_src/app.py:5811`

### 10.2 GET route 可能有副作用

依 AGENTS.md 與 WORKFLOW.md，GET 不應觸發 repair / background scheduling。現在可見下列風險：

- `api_stock_detail` GET 中呼叫 `data_readiness_for_items(... enqueue_missing=True)`：`review_src/app.py:5824`
- `/api/quotes` GET 中呼叫 `data_readiness_for_items(items, mode, enqueue_missing=True)`：`review_src/app.py:6434`
- `data_readiness_for_items()` 在 `enqueue_missing=True` 時會啟動背景補資料 thread：`review_src/app.py:6276`、`review_src/app.py:6280`
- `/api/quotes` 另有 `enqueue_data_repair()` 呼叫：`review_src/app.py:6466`

### 10.3 資料品質判斷散落

目前尚未看到集中式 `core/data_quality.py`：

- `core/data_quality.py`: Unverified / Not found in current source

資料品質與信心判斷散落在多處：

- price-volume quality：`review_src/price_volume.py:213`、`review_src/price_volume.py:218`、`review_src/price_volume.py:267`
- app price-volume unavailable/status：`review_src/app.py:1180`、`review_src/app.py:1403`
- futures confidence：`review_src/market/futures.py:156`、`review_src/market/futures.py:225`
- chip cost confidence：`review_src/chip_cost_engine.py:14`、`review_src/chip_cost_engine.py:291`、`review_src/chip_cost_engine.py:328`
- scoring volume quality：`review_src/scoring.py:401`、`review_src/scoring.py:414`
- next-day factor freshness/decay：`review_src/app.py:5276`、`review_src/app.py:5300`

### 10.4 多個主結論來源風險

目前正式主結論由 practical/referee 層產生：

- `classify_practical_status()`：`review_src/app.py:3798`
- `classify_practical_status_cached()`：`review_src/app.py:3945`

但 legacy scoring 仍產生 `eligible_for_watchlist` 與 `signal`：

- `review_src/scoring.py:723`
- `review_src/scoring.py:726`
- `review_src/scoring.py:879`
- `review_src/scoring.py:882`

目前 `app.py` 仍組裝 legacy signal 到 row：

- `signal = score_result.get("signal")`：`review_src/app.py:4316`
- response 中含 `legacy_signal`：`review_src/app.py:4442`
- response 中也含 `signal`：`review_src/app.py:4469`

這不一定是 bug，但重構時必須維持「裁判層唯一主結論」規則，避免 legacy signal 變成頂層主結論。

### 10.5 DB / background / cache 耦合

- SQLite connection 由 `core/db.py:10` 建立，使用 `check_same_thread=False`：`review_src/core/db.py:12`
- WAL 與 busy timeout 設定於 `review_src/core/db.py:22`、`review_src/core/db.py:23`
- 背景 thread 多處同時啟動：`review_src/app.py:4639`、`review_src/app.py:4644`、`review_src/app.py:4647`、`review_src/app.py:4754`、`review_src/app.py:4770`、`review_src/app.py:4787`、`review_src/app.py:4802`
- 狀態寫入 `fetch_status` 使用 DB：`review_src/core/status.py:15`、`review_src/core/status.py:16`

這代表任何 DB 連線或寫入策略重構都需要分階段驗證，不能一次大改。

## 11. 建議目標架構（只建議，不改程式）

建議目標依賴方向：

```text
api -> service -> repository / adapter / analysis -> core
```

下層不得 import 上層。

### 建議拆分方向

- `api/`
  - 只放 HTTP route、request/response shaping。
  - 從 `review_src/app.py:4652` 到 `review_src/app.py:6418` 逐步搬出。
- `service/`
  - 放 use case orchestration，例如 quotes list、detail page、data readiness、update command。
  - 候選來源：`_build_row_uncached()` `review_src/app.py:4237`、`api_stock_detail()` `review_src/app.py:5811`。
- `repository/`
  - 放 DB 查詢與寫入，隔離 `core/db.py`。
  - 優先處理 watchlist、history_price、valuation、institution、margin、price-volume repositories。
- `adapter/`
  - 放外部資料源：TWSE、MIS、FinMind、Fugle、Yahoo、TAIFEX。
  - 候選來源：`review_src/app.py:558`、`review_src/app.py:826`、`review_src/app.py:1003`、`review_src/app.py:1694`、`review_src/app.py:1885`、`review_src/market/futures.py:25`。
- `analysis/`
  - 放純計算，不直接碰 HTTP route。
  - 可逐步容納 practical status、support/resistance、next-day outlook、chip factors、price-volume scoring wrapper。
- `core/`
  - 保留 config、db bootstrap、http helper、status、utils。
  - 建議新增 `core/data_quality.py` 作為資料品質邊界，但本次不建立。
- `task/`
  - 放背景更新、repair queue、schedule orchestration。
  - 候選來源：`review_src/app.py:4547`、`review_src/app.py:4574`、`review_src/app.py:4581`、`review_src/app.py:2181`、`review_src/app.py:6063`。

### 建議分階段

1. 建立 `docs/REFACTOR_PLAN.md`，只規劃，不實作。
2. 第一階段優先抽出 adapter-like 模組，從邊界清楚的 TAIFEX / futures 與 MIS 開始。
3. 第二階段抽出 repository，先處理 read-only 查詢，降低 GET 副作用風險。
4. 第三階段抽出 service，把 row/detail/data-readiness 組裝與 route 分開。
5. 最後才搬 API route，避免 early-stage circular import。

## 12. Unverified / Not found in current source

下列項目目前未在原始碼中找到或無法驗證為已存在：

- `main.py`
- `api/` 分層目錄
- `service/` 分層目錄
- `repository/` 分層目錄
- `adapter/` 分層目錄
- `analysis/` 分層目錄
- `model/` 分層目錄
- `schema/` 分層目錄
- `task/` 分層目錄
- `core/data_quality.py`
- DB migration framework
