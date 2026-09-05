# LINE Bot / AI 股票資料介面

## 現況

本專案提供一個獨立、唯讀的 Bot API。它和 Dashboard 共用同一份 SQLite，
但不載入 Dashboard 的 background task、資料修復或寫入型 POST route。

正式資料生命週期：

1. 開市日 13:35，由 Windows Task 讀取當下 watchlist。
2. 對 Fugle trades 使用 `offset / limit / sort=asc` 分頁到底，逐頁核對日期與股票代碼，並依 `serial` 去重。
3. 同時保存 Fugle 分價量的 `volumeAtBid / volumeAtAsk`。
4. 15:00 官方 TWSE / TPEx 日線完成後，核對同日官方成交量、完整逐筆成交量與分價量總量。
5. 只有通過核對的資料才升級為 `VALIDATED`，並允許產生當日分價量支撐／賣壓。

週末、國定假日與已知颱風休市由官方交易日曆閘門跳過。來源日期不一致、分頁未完成、13:30 收盤證據不足或成交量不一致時，資料維持 `source_delayed / unverified`，不會形成支撐／賣壓結論。

## 資料表

- `fugle_intraday_trades`：同日逐筆成交、bid/ask、累計量與推估方向。
- `fugle_intraday_capture_runs`：逐筆分頁完整性、頁數、正規化／實際保存筆數、最後成交時間與成交量證據。
- `price_volume_distribution`：每個成交價的總張數、內盤張數、外盤張數、未分類張數與品質狀態。
- `daily_inner_outer_volume`：每日內外盤彙總。
- `price_volume_profile_daily` / `price_volume_score_daily`：盤後核對後的 profile 與多日分數。
- `history_price`：TWSE / TPEx 官方 OHLCV，作為同日收盤與成交量核對基準。
- `stock_master`：官方上市／上櫃普通股母體與 market/exchange metadata。
- `daily_technical_snapshot`：依交易日保存共用公式的 RSI、MA、MACD、KD、ATR、布林與品質證據。
- `twse_daily_valuation`：上市／上櫃同日 PE、PB、殖利率（欄位名稱沿用既有 schema）。

不另外複製一份 Bot DB，避免 Dashboard、LINE Bot 與 AI 讀到不同版本。

## 啟動唯讀 API

### 單一市場資料庫設定（2026-08-31）

Web 與 LINE Bot API 統一使用 `core/market_database_config.py` 選擇市場資料庫。
平常只在共用 `review_src/.env` 設定一次（未設定即使用以下預設）：

```text
TAIWAN50_DB_PATH=data/taiwan50.db
```

- 優先順序：明確的 process `TAIWAN50_DB_PATH` → 共用 `review_src/.env` → portable 預設。
- 明確空白的 process 值沿用既有語意：使用 portable 預設，不改讀共用檔案值。
- 相對路徑一律以 `review_src` 為基準，與啟動目錄無關；子程序接收解析後的同一路徑。
- `.env.line_bot` 不可另外指定不同市場庫。舊設定若解析成同一路徑仍相容；不同則在載入／啟動前以
  `market_database_configuration_conflict` 拒絕，不悄悄切換、複製資料或退回另一個庫。
- 若需換庫，先停止相關服務、修改共用設定、確認兩個 launcher 沒有不同的 process override，
  再按既有維護流程重啟兩端。這不是熱切換功能；不能只改正在運行其中一端的環境。
- `LINE_MEMORY_DB_PATH` 仍只保存對話等私有狀態，不是第二份市場來源，不與股票 DB 合併。
- Bot API 保持 SQLite `mode=ro`／`query_only`；不載入 Web DB bootstrap。只有原本明確更新流程寫入市場庫。

資料路徑：原有來源與排程 → 同一市場 SQLite → 共用 canonical service／referee → Web 或唯讀 Bot API → LINE。
Web 自選股盤中與 Taiwan50／LINE 預設盤後模式仍保留；比較兩端數值必須使用相同股票、交易日與分析模式，
不是把盤中價格與前一完整交易日的收盤數字混為一談。本次沒有刪除既有上游 adapter 或改變更新排程。

LINE 私有設定或執行環境另設（不要放入公開檔案）：

```text
BOT_MARKET_DATA_TOKEN=<至少 32 字元的隨機 token>
BOT_ENABLE_UNVERIFIED_TRADES=false
```

`TAIWAN50_DB_PATH` 的相對路徑以 `review_src` 為基準。請勿把 token 放在 URL、程式碼、log 或 Git。

從 repo 根目錄啟動：

```powershell
review_src\.venv\Scripts\python.exe -m uvicorn bot_app:app --app-dir review_src --host 127.0.0.1 --port 8010 --env-file review_src\.env
```

不要把既有 Dashboard `app:app` 公開到網際網路；它還包含管理與寫入功能。若 LINE webhook 與 Bot API 不在同一台機器，應透過私人網路或只允許必要路徑的反向代理連線。

## 每日資料查詢

```http
GET /api/bot/market-data/2454/daily?analysis_mode=close_batch&include_levels=true&level_limit=100
Authorization: Bearer <BOT_MARKET_DATA_TOKEN>
```

`analysis_mode` 目前預設為 `close_batch`。即使使用者在盤中詢問，也使用最近完整交易日的
官方收盤資料形成條件式規劃，不把即時成交價當成必要門檻。回傳會明確包含
`analysis_mode=close_batch`、`update_mode=close_batch`、`is_realtime=false`、`data_date`、
`timezone=Asia/Taipei` 與 `decision_audit.price_basis=completed_close`。

保留的 `analysis_mode=intraday` 只供未來明確啟用即時分析；它會要求當日有效且足夠新鮮的
即時成交價。來源暫時沒有成交價時不得拿昨收、買價或賣價冒充現價，並回傳
`advisory.reason_code=intraday_price_not_ready`。明確歷史 `trade_date` 只能搭配
`close_batch`，避免把歷史收盤與盤中模式混用。

主要欄位：

- `ohlcv`：同一日期的官方開高低收與成交量；即使分價量尚未到，也可獨立回傳。
- `price_levels[].volume_lots`：該價位總成交張數。
- `price_levels[].inner_lots`：Fugle `volumeAtBid`，來源定義的內盤累計量。
- `price_levels[].outer_lots`：Fugle `volumeAtAsk`，來源定義的外盤累計量。
- `price_levels[].neutral_lots`：未歸類量，可能包含開盤第一筆集中撮合。
- `flow_summary.net_active_lots`：外盤減內盤，只代表主動成交力道。
- `support_pressure`：只在同日完整、單一 Fugle source、盤後快照與官方量全部核對成功時提供。
- `data_quality`：AI 必須先檢查 `decision_ready`，不得忽略狀態。
- `advisory.price_basis`：目前 LINE 預設必須為 `completed_close`；只有明確切換
  `analysis_mode=intraday` 且即時價通過新鮮度檢查時才能是 `intraday`。

`inner_lots / outer_lots` 不是法人買超／賣超，也不能識別主力或投資人身分。若任何價位缺 bid/ask，API 會回 `direction_available=false` 與 `null`，不會把缺值偽造成 0 或「內外盤相當」。

欄位依據：[Fugle Intraday Volumes 官方文件](https://developer.fugle.tw/docs/data/http-api/intraday/volumes/)。

## 逐筆成交

逐筆 endpoint 目前預設關閉：

```http
GET /api/bot/market-data/2454/trades?trade_date=2026-08-21&limit=100&offset=0
```

新版 collector 已保存完整性證據；但正式對外啟用前仍建議完成 keyset cursor、rate limit 與 response-size gate。不得為了畫面有資料而把舊的一頁式 capture 稱為完整逐筆。

每筆 `inferred_side` 是以成交價對 bid/ask、再以 tick rule 補判的推估值，必須和 `side_is_estimated=true`、`side_method`、`side_confidence` 一起使用；它不是交易所提供的原始買賣別。

## 歷史 OHLCV、技術指標與估值

```http
GET /api/bot/market-data/2454/history?date_from=2026-01-01&date_to=2026-08-21&limit=60&offset=0
Authorization: Bearer <BOT_MARKET_DATA_TOKEN>
```

資料依 `trade_date DESC` 明確排序，並以 `(code, trade_date)` 精確 join OHLCV、技術快照與估值。`technical_decision_ready=false` 或估值為 `null` 時，LINE Bot 必須照實說明資料不足／來源尚未提供，不能用前一日數字冒充指定日期。

完整全市場資料流、排程與回補方式見 `docs/FULL_MARKET_DATABASE_ARCHITECTURE.md`。

分頁與原始欄位依據：[Fugle Intraday Trades 官方文件](https://developer.fugle.tw/docs/data/http-api/intraday/trades/)。

## 低檔止跌／條件式分批篩選

```http
GET /api/bot/market-data/screen?strategy=bottom&limit=5
Authorization: Bearer <BOT_MARKET_DATA_TOKEN>
```

這個策略不以「RSI 最低」直接當成買點，也不宣稱能知道絕對最低點。它沿用
`daily_technical_snapshot` 內既有 Wilder RSI14，依序檢查：

1. 最近完整交易日、技術快照及唯一裁判層均通過品質門檻。
2. RSI14 位於 30～45 低檔觀察帶且較前一交易日回升；低於 30 或連續三日下滑不視為止跌。
3. 收盤守住中等以上支撐、前 10 日低點，且仍在支撐區或其上方 1% 內。
4. RSI、MACD 柱狀體、價格止穩三項至少兩項改善；裁判層為「警戒」時三項都必須改善。
5. 成交量至少為 20 日均量 50%；下跌且量比達 1.2 時拒絕。
6. 下方風險至少用 0.5 ATR 估算，至首道賣壓區上緣的報酬風險比需達 1.5，風險距離不得超過 6% 或 2 ATR。
7. 最近 20 筆完整交易日若有超過 20% 的價格斷層，視為可能有公司行動或未還原資料，停止產生低檔候選。

合格 payload 會帶 `rsi14`、`low_zone_stage`、`low_zone_summary`、
`confirmation_count`、`reward_risk_ratio`、分批條件與失效條件。
`batch_entry_eligible` 是唯一裁判層下游的條件式執行框架，
`can_override_main_status` 永遠為 `false`。這個 GET 只讀 SQLite，不匯入資料、不排修復工作，也不寫入 DB。

## LINE Bot + AI 調用方式

整體市場或近期題材不需要股票代號：

```http
GET /api/bot/market-data/market-brief?q=最近市場在炒什麼
Authorization: Bearer <BOT_MARKET_DATA_TOKEN>
```

回傳內容包含查詢參考日、近期可靠事件、實際發布日期、來源可靠度與參考價值、時間衰減後的
摘要，以及全球市場收盤背景。若問題帶有明確主題（例如川普、關稅或半導體），
`topic_match_required=true`；只有事件標題、摘要或官方影響標籤實際相符時才會把
`topic_match` 設為 `true`。沒有相符一手內容時，LINE Bot 必須明說無法確認並請使用者提供原文，
不能拿其他事件代答。此 GET 為唯讀，`can_override_main_status=false`。

使用者可直接在 LINE 中詢問，例如：

```text
聯發科今天哪個價位成交最多？支撐和賣壓在哪？外盤有沒有比內盤強？
```

既有 LINE webhook 的安全流程應是：

```text
LINE 使用者文字
→ 驗證 X-Line-Signature
→ 只允許固定股票資料 tool
→ 從文字解析四碼股票代碼／日期
→ 以 analysis_mode=close_batch 呼叫 daily endpoint
→ 先檢查 data_quality
→ AI 只整理 API 已提供的事實
→ LINE reply
```

給 AI 的必要規則：

- 不可讓 AI 執行任意 SQL、檔案或 URL。
- `decision_ready=false` 時，必須說「分價量尚未完成核對」，不得猜支撐／賣壓。
- 不可把外盤減內盤說成法人、外資、投信或主力買超。
- 不可由 AI 自行重算或改寫 RSI、MACD、支撐壓力與裁判層結論。
- 不傳送 DB path、raw JSON、token、LINE user ID、reply token 或 traceback 給模型。

本 repo 尚未寫入 `LINE_CHANNEL_SECRET`、`LINE_CHANNEL_ACCESS_TOKEN` 或任何 LINE 使用者 ID。實際接上既有 LINE Bot 時，需要把 webhook 程式位置與 LINE 頻道設定提供給開發端；憑證只能放環境變數。

## 目前資料狀態

截至 2026-08-23：

- 官方 `stock_master` 為上市 1,089、上櫃 890，共 1,979 檔普通股。
- 最近 130 個已驗證交易日的 TWSE／TPEx 精確日期回補完成；最新完成日為 2026-08-21。
- `daily_technical_snapshot` 已對 1,979 檔重建；最新日停牌、無交易或歷史不足的股票會保留明確 unavailable／insufficient 狀態。
- 2026-08-21 Fugle 分價量已實抓 34 檔並完成逐筆分頁；官方成交量核對後 30 檔 validated，4 檔誤差超過 5% 而拒絕。其餘 1,945 檔不會被假裝成已有分價量。

全市場自動收集首次排程為 2026-08-24 13:35。當日驗收應確認：

- 13:35 capture task `LastTaskResult=0`。
- `fugle_intraday_capture_runs.capture_complete=1`。
- 最後成交時間涵蓋 13:30。
- trades size 合計、分價量總量、官方同日成交量通過規則。
- 分價量 quality 為明示 `VALIDATED`，不是舊的 `OK`。
- Bot API 的 `decision_ready=true` 後才顯示支撐／賣壓。
