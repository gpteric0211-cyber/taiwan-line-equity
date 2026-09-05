# 全上市／上櫃股票每日資料庫

## 目標與資料邊界

資料庫以目前官方公司名冊中的上市、上櫃普通股為母體。ETF、權證、ETN、興櫃及非四碼商品不會因為出現在交易所行情原始檔就進入普通股母體。

關聯式資料表沒有「預設排序」；所有提供給 Dashboard、LINE Bot 與 AI 的歷史查詢都必須明確使用 `ORDER BY trade_date DESC, code`。資料庫不以填入假值的方式補齊空欄：來源尚未更新、歷史不足或無完整分價量時，品質欄位會回 `source_delayed`、`insufficient_history`、`unavailable` 或其他明確原因。

## 正規化資料表

| 資料表 | 主鍵 | 用途與來源 |
|---|---|---|
| `stock_master` | `code` | 官方 TWSE／TPEx 公司名冊、上市別、啟用狀態與上市日期 |
| `history_price` | `(date, code)` | 官方每日開、高、低、收、成交股數、成交金額 |
| `twse_daily_valuation` | `(data_date, symbol)` | 同日殖利率、本益比、股價淨值比；上市取 TWSE， 上櫃取 TPEx |
| `daily_technical_snapshot` | `(trade_date, code)` | 由同一份正式 `scoring.py` 公式產生的 RSI、MA、EMA、MACD、KD、ATR、布林、OBV 與品質證據 |
| `price_volume_distribution` | `(stock_id, trade_date, price)` | Fugle 同日各價位成交張數、內盤、外盤、未分類量 |
| `fugle_intraday_trades` | provider identity | 可追溯的逐筆成交；方向是依 bid/ask 與 tick rule 推估，不是假稱交易所買賣別 |
| `price_volume_profile_daily` / `price_volume_score_daily` | `(code, date)` | 完整性與官方成交量核對後的支撐／賣壓輪廓與多日分數 |

OHLCV 與估值各保留一份 canonical 原始資料；`daily_technical_snapshot` 只保存衍生值，不複製收盤價或本益比，避免多份價格互相矛盾。

## 每日資料流

```text
TWSE/TPEx 官方公司名冊
        ↓
stock_master（全普通股母體）
        ↓
Fugle 盤後逐筆＋分價量擷取 ─────────────┐
        ↓                               │
TWSE MI_INDEX / TPEx dailyQuotes 精確交易日 OHLCV
        ↓                               │
官方 TWSE / TPEx 同日估值               │
        ↓                               │
shared scoring.py → daily_technical_snapshot
        ↓                               ↓
官方成交量對帳 ← price_volume_distribution
        ↓
validated profile / support-pressure
        ↓
唯讀 Bot API → LINE Bot / AI
```

執行入口：

```powershell
scripts\run_full_market_daily_database_update.bat --stage capture --window-end 17:50
scripts\run_full_market_daily_database_update.bat --stage finalize --window-end 23:59
```

`capture` 只落庫 Fugle 原始逐筆／分價量，不能核對、升級品質或評分；`finalize` 只抓官方來源、
核對同日官方成交量與執行 publication/parity gate，不能再呼叫 Fugle。兩階段使用同一把程序鎖。
批次使用腳本所在位置推導 repo root 與虛擬環境，不硬編碼磁碟、使用者名稱或安裝路徑。非交易日
會經官方台灣交易日曆閘門跳過；盤後抓取的資料日期必須和最近完成交易日相同。

Windows 排程先用無副作用預覽驗證：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_windows_post_close_tasks.ps1 -WhatIf
```

正式註冊為 15:00 Fugle capture 與 18:10／23:40／週一至週六 06:45 official finalize 兩個工作；工作
排程器會保存當下解析出的絕對 action path，專案移動後必須重新註冊。全市場 Fugle 一天約需
兩個 endpoint × 股票數的 API 請求，正式啟用前必須確認 API 方案額度與允許的請求速率。

## 歷史回補與技術快照

官方精確日期回補可續跑，預設保留狀態檔：

```powershell
review_src\.venv\Scripts\python.exe scripts\backfill_full_market_history.py --days 600 --max-dates-per-run 20 --resume
review_src\.venv\Scripts\python.exe scripts\update_market_analytics_snapshots.py --skip-stock-master --backfill
```

第一步只把交易所回覆日期與要求日期一致、且市場母體覆蓋率至少 95% 的日期標成完成。第二步對每檔股票依日期重建技術快照：至少 120 筆有效 OHLCV、最新一筆為官方來源、必要 RSI／MACD 都存在且日期缺口合格時，`decision_ready` 才會是 `1`。

RSI 採 Wilder 定義，初始平均漲跌為前 N 個價差的簡單平均，其後以 `1/N` 遞迴平滑。為了和目前券商常見顯示方式及既有 Dashboard 一致，每個 as-of 日期最多取 120 筆 close；公式版本記在每列 `formula_version`。不同網站若採全歷史平滑、不同還原權值或盤中未收盤價，可能出現合理差異，因此比較時必須同時鎖定交易日、調整政策與公式。

## LINE Bot／AI 查詢

單日完整資料：

```http
GET /api/bot/market-data/{code}/daily?trade_date=YYYY-MM-DD
```

依日期倒序的 OHLCV、技術與估值：

```http
GET /api/bot/market-data/{code}/history?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD&limit=60&offset=0
```

兩個 endpoint 都是 SQLite 唯讀連線與精確日期 join，不會因查詢缺資料而啟動補抓、排程或 DB 寫入。AI 必須遵守 `technical_decision_ready` 與分價量 `decision_ready`；不可把空值自行補成數字，也不可把內外盤推估說成法人或主力買賣超。

## 驗收 SQL

```sql
PRAGMA quick_check;

SELECT market, COUNT(*)
FROM stock_master
WHERE is_active=1 AND security_type='stock'
GROUP BY market;

SELECT date, market, COUNT(*)
FROM history_price
WHERE date=(SELECT MAX(date) FROM history_price)
GROUP BY date, market;

SELECT trade_date, COUNT(*) AS rows,
       SUM(decision_ready) AS decision_ready
FROM daily_technical_snapshot
GROUP BY trade_date
ORDER BY trade_date DESC
LIMIT 5;

SELECT trade_date, COUNT(DISTINCT stock_id) AS captured_stocks
FROM price_volume_distribution
GROUP BY trade_date
ORDER BY trade_date DESC
LIMIT 5;
```

資料完整性的通過條件不能只看總筆數；還要核對母體數、同日來源日期、來源品質、無交易證據、技術指標 input rows 與分價量官方量對帳結果。
