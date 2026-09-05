# 手動市場資料更新指南

目前開發環境的 Windows 自動更新工作已停用。每日資料改由人工執行，避免排程在開發或 DB 維護期間與其他 writer 競爭。

## 全市場每日分析資料一鍵更新（建議入口）

請在專案根目錄雙擊：

```text
一鍵更新今日分析資料.bat
```

範圍是 `stock_master` 中全部有效上市與上櫃股票，不是只有台灣 50。流程依序處理：

1. 補齊今日以前遺漏的官方交易日，避免技術指標中間缺日。
2. 若同日曾中斷，先重播已保存的 Fugle 原始 JSON，避免重複呼叫 API。
3. 擷取當日 Fugle 尚缺的逐筆成交、分價量與內外盤補充資料。
4. 更新官方 OHLCV 收盤、PE／PB／殖利率、三大法人、融資、融券、借券、估算籌碼成本與技術指標。
5. 更新全市場 `daily_chip_momentum`。
6. 產生台灣 50 相容用收盤批次；這只是額外輸出，不是全市場更新的母體。
7. 更新 TDCC 週頻資料；來源沒有新週資料時保留並明確顯示最新官方週日期。
8. 更新官方月營收、政策事件與已設定的授權新聞來源，接手先前外部資料排程的遺漏項目。
9. 逐表及逐股票代碼核對母體、資料日期、官方來源、分價量核對狀態與 SQLite 完整性；另外驗收美股／產業 ETF、夜盤、公告與交易限制的來源結果。

正式 DB 不會被直接逐步修改。工具先建立一致性快照，在隔離候選 DB 完成全部步驟；安全發布 gate 與完整性檢查通過後才原子替換正式 DB。更新期間若正式 DB 被其他 writer 修改，發布會中止，不會覆蓋較新的正式資料。若官方來源延遲但所有實際取得的資料都已逐表落地並通過安全發布 gate，候選可原子發布，但 `daily_update_complete` 仍會是 `false`、BAT 會回傳非 0，不能誤稱全市場齊全。

進度與結果：

```text
docs\MANUAL_DAILY_ANALYSIS_UPDATE_REPORT.json
docs\MANUAL_DAILY_ANALYSIS_UPDATE_VERIFICATION.json
docs\MANUAL_EXTERNAL_EVENTS_REPORT.json
logs\manual_daily_update\isolated_update_latest.json
```

只檢查執行計畫、不抓網路、不建立快照、不寫 DB：

```powershell
一鍵更新今日分析資料.bat --plan-only
```

`safe_to_publish=true` 只代表候選 DB 完整、實際官方列與衍生表一致且可安全替換正式 DB。`daily_update_complete=true` 才代表當日全市場資料契約完整通過。`price_volume_scoring_ready` 是另一個多日歷史門檻；今日分價量完整不代表已累積足夠天數供裁判層計分，工具會分開報告，不能混稱。

指定日期可用 `一鍵更新今日分析資料.bat --date YYYY-MM-DD`。更新與發布後驗收會使用同一個凍結日期及同一個 DB，即使跨午夜也不會驗錯日期。補日從最後一個通過全市場分類契約的批次之後開始，因此部分寫入的尾端交易日仍會重試。

當日逐筆成交／分價量應在當天盤後執行並保存。若已跨日且沒有原始快取或 DB 逐筆資料，現有 Fugle intraday 流程無法保證補回歷史分價量；反覆執行 BAT 不會憑空補齊，需另外取得合法歷史逐筆／分價量來源。遇到這種缺口，完整度維持 false，安全發布 gate 也可能拒絕候選。不要把來源缺資料誤判為停牌、無交易或零值。

月營收與 TDCC 保留來源的月／週日期；美股、夜盤依各自市場時間呈現，不應改寫成台股當日日期。尚未設定的授權新聞 feed 明確列為 disabled；已設定但抓取失敗會列入不完整。券商分點原始買賣資料及人工驗證公司行動不在自動抓取範圍，既有估算籌碼成本不等於券商分點成本。

## 僅更新官方 OHLCV（舊的縮小範圍入口）

下列舊入口只處理官方日線，不包含使用者要求的完整每日分析資料；除非只想修 OHLCV，否則請使用上方的新一鍵入口。

### 正式更新上市 + 上櫃全市場 OHLCV

請雙擊：

```text
scripts/RUN_ALL_MARKET_OFFICIAL_UPDATE.bat
```

這會執行官方資料正式更新並寫入資料庫。它不是 dry run，也不會只更新單一股票。

此入口只使用官方全市場資料，不啟用 Yahoo 全市場更新，也不啟用 PChome。

## 先測試但不寫 DB

```powershell
Set-Location <repo-root>
scripts\manual_update_market_foundation.bat --official-only --dry-run
```

## 正式更新全市場

```powershell
Set-Location <repo-root>
scripts\manual_update_market_foundation.bat --official-only
```

## 只更新 2330

```powershell
Set-Location <repo-root>
scripts\manual_update_market_foundation.bat --official-only --codes 2330
```

## 如何判斷只是測試

看到以下內容代表沒有寫入 DB：

```text
dry_run = true
writes_db = false
status = DRY_RUN
official_written = 0
```

## 如何判斷真的寫入

看到以下內容代表正式寫入：

```text
dry_run = false
writes_db = true
official_written > 0
verify_market_foundation_update.py 顯示 PASS
```

也可以手動驗證：

```powershell
python scripts\verify_market_foundation_update.py --min-count 1000
```

## 不要把 JSON 輸出貼回 CMD

更新工具會輸出 JSON 結果，這些不是命令，不要逐行貼回 CMD 執行。

例如以下都不是命令：

```text
"dry_run": true
"writes_db": false
"official_rows": 1962
```

## RequestsDependencyWarning

如果畫面出現 `requests` / `urllib3` / `chardet` 相關 warning，但 exit code 是 0，代表不影響本次更新。Python 套件版本整理可以另開任務處理。

## 為什麼顯示更新成功，但分價量表 / 即時明細可能沒有更新？

`scripts\RUN_ALL_MARKET_OFFICIAL_UPDATE.bat` 更新的是官方日線 OHLCV。

這個正式更新範圍包含上市與上櫃全市場官方日線資料，也就是每日開高低收與成交量。它不等於更新 Yahoo 分價量表，也不等於更新 Yahoo time-sales 即時明細，更不等於更新內外盤量。

分價量表、即時明細與內外盤量屬於補充資料。這些資料之後需要另外設計自選股或指定股票更新流程，不建議預設做全市場抓取，也不應在 official-only 更新中被標示為完成。

一鍵更新成功後，工具會顯示中文完整度檢查，明確列出：

- 官方日線是否完成
- 分價量表是否已建立、同步或仍未啟用
- 即時明細 / Time Sales 是否已建立、同步或仍未啟用
- 內外盤量是否已建立、同步或仍未啟用
- Yahoo / PChome 是否有啟用
- 本次「完整」實際代表的資料範圍
## Fugle intraday 補充資料更新

Fugle intraday 補充資料用於指定股票的小範圍盤中補資料，不是全市場官方日線更新。

它目前涵蓋：

- 逐筆成交 / time-sales
- 分價量
- `volumeAtBid` / `volumeAtAsk` 的 bid/ask volume summary

正式官方全市場日線仍使用：

```text
scripts\RUN_ALL_MARKET_OFFICIAL_UPDATE.bat
```

Fugle intraday 指定股票更新使用：

```text
scripts\RUN_FUGLE_CODES_INTRADAY_UPDATE.bat 2317,3491,2382
```

預設驗證股票為 `2317,3491,2382`。若未輸入股票代號，bat 也只會跑這三檔。

注意：

- Fugle intraday 補充資料不更新 `history_price`。
- Fugle intraday 補充資料不代表全市場資料完整。
- Fugle intraday 補充資料建議當天盤後執行，例如 15:00 後。
- 正式自動流程在 15:00 只做 Fugle capture；捕捉結果必須等 18:10 後的官方 finalize 核對，
  在此之前不得提供給裁判層評分。
- 官方 finalize 只讀既有 Fugle capture，不會為了補缺口再呼叫 Fugle；23:40 是配合目前最晚
  官方信用額度檔的保底時點，週一至週六 06:45 也只補官方來源，因此週五延遲資料不必等到週一。
- 不應假設隔日仍可補抓前一交易日完整 intraday。
- Fugle intraday 補充資料保留最近 300 個交易日。
- 官方 `history_price` 日線仍保留最近 600 個交易日。
- `FUGLE_API_KEY` 放在環境變數或 `review_src\.env`，不要貼到文件、log 或 commit。
- Fugle 官方文件定義 `volumeAtBid` 為內盤累計量、`volumeAtAsk` 為外盤累計量；開盤第一筆集中撮合可能不計入兩者，因此其和可小於總量。這些欄位仍不是法人或主力買賣超。
- 逐筆成交 side 推論已在 Fugle trades 寫入前完成，並保存於 `fugle_intraday_trades.side_inferred` 等欄位。
- 逐筆 side 推論順序固定：先用成交價對 bid/ask；若 bid/ask 缺漏才用前收 / 前一筆成交價 tick fallback。
- 逐筆 side 推論是補充推論欄位，不是交易所原始內外盤，不得在一般 UI 宣稱為官方內外盤。
- 既有 Fugle trades 可用以下命令只回填 side 欄位；此命令不呼叫 Fugle API，也不改價量原始欄位。
- backfill 不加 `--write` 時只做 dry-run / report，不修改 DB；要正式更新 DB 必須加 `--write`。
- 不指定 `--date` 時，backfill 會掃所有日期中 `side_inferred` 或 `side_method` 仍為 NULL / 空白的 rows；指定 `--date YYYY-MM-DD` 時只處理該交易日。
- `UNKNOWN` 是明確無法判斷；NULL 代表欄位尚未補齊。verify 必須分開統計 NULL 與 UNKNOWN。

```text
python scripts\update_fugle_intraday_supplemental.py --date 2026-06-25 --backfill-side-inferred --output docs\FUGLE_INTRADAY_UPDATE_REPORT.md
python scripts\update_fugle_intraday_supplemental.py --date 2026-06-25 --backfill-side-inferred --write --output docs\FUGLE_INTRADAY_UPDATE_REPORT.md
```

## TWSE / TPEx official date alignment

All-market official OHLCV updates must not mix different official trading dates.

Before writing `history_price`, the updater checks the official `data_date` from:

- TWSE listed daily OHLCV: `TWSE_OFFICIAL`
- TPEx OTC daily close quotes: `TPEX_OFFICIAL`

If TWSE and TPEx official dates are not aligned, the updater stops with:

```text
SOURCE_DELAYED_RETRYABLE
writes_db=false
official_written=0
```

This is expected when one official exchange has published a newer trading day and the other has not. Do not fill TWSE listed OHLCV with Yahoo, PChome, Fugle, FinMind, or any other weaker source. Re-run the official update after the delayed official source advances.

`verify_market_foundation_update.py` reports both:

- `global_latest_date`: latest date in `history_price`, including supplemental or non-official rows.
- `official_latest_date`: latest date among official TWSE / TPEx OHLCV rows.

Use `official_latest_date` to judge official all-market OHLCV completeness.

## TWSE / TPEx valuation update

OHLCV updates and valuation updates are separate data flows.

`scripts\RUN_ALL_MARKET_OFFICIAL_UPDATE.bat` updates official daily OHLCV in
`history_price`. It does not prove that PE, PB, or dividend yield are current.

Use these read-only dry runs first:

```powershell
python scripts\update_twse_daily_valuation.py --dry-run
python scripts\update_tpex_daily_valuation.py --latest --dry-run
```

If the dry runs look correct and you want to formally update valuation data,
run the explicit write commands:

```powershell
python scripts\update_twse_daily_valuation.py
python scripts\update_tpex_daily_valuation.py --latest
```

These scripts write `twse_daily_valuation` only when dry-run is not used. GET
routes and detail pages must never trigger valuation updates.

If a TWSE or TPEx valuation update fails because the official endpoint is
delayed, temporarily unavailable, or changed format, keep the OHLCV update
result intact and treat valuation as `stale`, `source_delayed`, or
`cannot_verify` in the detail payload. Do not silently fall back to Yahoo or
recompute PE/PB/dividend yield from current price.
