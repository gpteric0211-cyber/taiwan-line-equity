# LINE 股票分析機器人部署與 API 填寫

## API 填寫位置

所有 LINE 與本機 Qwen 設定只填在專案最外層：

```text
.env.line_bot
```

公開空白範本是：

```text
review_src/.env.line_bot.example
```

根目錄的 `.env.line_bot` 已被 Git 忽略。舊版 `review_src/.env.line_bot` 只保留相容讀取，
新設定請不要再放在舊位置。不得把真實 `Channel Secret`、`Channel Access Token`、
`BOT_MARKET_DATA_TOKEN` 貼到 Markdown、Python、前端、GitHub Issue 或 log。

## 必填欄位

1. `LINE_CHANNEL_SECRET`
   - LINE Developers Console 的 `Basic settings`。
2. `LINE_CHANNEL_ACCESS_TOKEN`
   - LINE Developers Console 的 `Messaging API`。

`LINE_WEBHOOK_PUBLIC_URL` 不再需要手動填寫。根目錄的一鍵啟動器會建立新的 Cloudflare
Quick Tunnel，將公開網址寫入 LINE Messaging API，再由 LINE 官方測試與 `active` 狀態確認。

`BOT_MARKET_DATA_TOKEN` 可以留空；啟動器會在每次啟動時安全產生，並只交給本機的
LINE Gateway 與唯讀股票 API，不會顯示或寫入檔案。

`QWEN_API_KEY=ollama` 不是雲端 API key；Ollama 的本機 OpenAI-compatible API 不需要向
模型供應商申請 key。

## 本機服務

部署使用四個互相隔離的程序：

| 服務 | 預設位址 | 對外公開 |
|---|---|---|
| Qwen3.8-27B Ollama | `127.0.0.1:8020` | 否 |
| 唯讀股票 API | `127.0.0.1:8010` | 否 |
| LINE Webhook Gateway | `127.0.0.1:8021` | 只經 HTTPS 反向代理公開 `/line/webhook` |
| Cloudflare Quick Tunnel | 啟動時動態產生 | 是，只代理 `8021` |

Dashboard `127.0.0.1:8000` 不得直接公開。LINE Gateway 不直接讀寫「市場資料 SQLite」；它先呼叫
唯讀股票 API，通過資料品質閘門後才把精簡 FACTS 交給本機 Qwen。對話記憶則使用另一個獨立、
加密的 SQLite，不能作為行情、技術指標、新聞或裁判結論來源。
啟動器同時設定 `OLLAMA_NO_CLOUD=true`，避免本機股票 FACTS 被轉送至 Ollama Cloud。

## 啟動

雙擊：

```text
啟動LINE股票機器人.cmd
```

啟動器會依序檢查 API 欄位、Ollama 執行檔與已註冊模型，啟動三服務與 Cloudflare Tunnel，
自動更新及驗證 LINE Webhook。設定 `LINE_MODEL_BACKGROUND_PREWARM=true` 時，才經既有
GPU admission controller 排入背景文字暖機；「LINE 官方驗證通過」不代表模型已載入。
應另外核對 `/api/ps` 的模型／context 及 `/healthz` 的暖機、queue 狀態。若 8010、8020、8021
已由上一組完整且健康的服務占用，啟動器會安全沿用，不再把它誤判為 Exit code 3；若只有部分
連接埠占用或健康檢查失敗，仍會拒絕啟動，以免誤關其他程式。第一次預載可能需要數分鐘；
看到「LINE 官方驗證通過」才代表通道驗證完成；模型未常駐時可能仍只能使用 DB fallback。
預設在服務運行期間讓模型常駐 VRAM，避免閒置後的第一則 LINE 訊息重新等候；關閉啟動視窗
後會釋放模型與 VRAM。

這是手動常駐啟動器，不是已安裝的 Windows 開機服務。電腦重開後，不能把舊的
`logs/line_bot/runtime_state.json` 當成仍在線的證據；需重新執行既有啟動器，檢查新 PID、
三服務健康、tunnel、官方 webhook 與模型常駐。新增開機自動啟動／監管機制需另外授權。

2026-08-31 16:11–16:12 背景暖機修正已經由既有 launcher 完整重啟部署：原生預載與 READY 生成分開；
`LINE_MODEL_STARTUP_LOAD_TIMEOUT_SECONDS` 為獨立背景載入上限（預設 180 秒，30–240 秒），
READY 最多 20 秒。這些是工程上限，不是硬體 p95 或已核定的服務中斷預算；不延長 LINE
回覆期限。完整失敗紀錄、測試與部署邊界見
[修復原始失敗／離線報告](../logs/line_model_shadow/line_stack_recovery_20260831/REPORT.md)與
[本次部署驗收](../logs/line_model_shadow/line_warmup_deployment_20260831_1606/REPORT.md)。
本次完整恢復觀測為 53.402 秒（含停止到啟動的 30.170 秒操作延遲），預載／chat 均 200；
前後全套各 1040 passed、protected 11/11 不變。這是單次暖快取主機的啟動證據，
不是 PC cold／P95／股票回答品質驗收；Phase 2 與 V2 接管尚未核准。
日誌位於：

```text
logs/line_bot/
```

日誌不得包含 API key、LINE user ID、reply token 或完整市場 API payload。

## 支援的股票問法

可用官方股票簡稱、公司全名或四位數代號，例如：

```text
分析台積電
分析 2330
京元電子近 20 個交易日
2449 2026/08/21
```

股票名稱只接受上市／上櫃 active stock master 的精確名稱或已驗證別名。若文字可能是錯字或
同時對應多檔股票，系統會列出候選並要求用代號確認，不會擅自選一檔。例如「金元電子」會
提示確認「久元（6261）」或「京元電子（2449）」。純日期中的四位年份也不會被誤認成股票代號。
自然簡稱、句首承接語與句尾語氣詞也會先轉成可信候選；例如「星宇呢」或「所以星宇呢」都會
詢問「你是指星宇航空（2646）嗎？」。
單一候選會暫存在同一個使用者／聊天範圍的加密 session，使用者回覆「是」後才切換標的並開始分析；回覆
「不是」則清除候選，不會誤用上一檔股票或自行猜測。

### 連續追問

LINE Gateway 只在 LINE 確認回覆成功後，保存該使用者的問題、實際送出的回答、最近股票、資料日與回答主題。例如：

```text
分析台積電
那本益比呢
為什麼是中性
支撐在哪裡
再詳細一點
還可以問什麼
```

後續問題沒有再次寫股票名稱時，系統只在確認它是追問句型後沿用上一檔；若輸入新的明確股票
名稱或代號就切換標的。未知公司名稱、錯字候選或多標的問題不會套用上一檔資料。

追問會依主題只投影必要 FACTS：估值問題不再重播 RSI／MACD，支撐問題不再重播完整 OHLCV
日報；「再詳細一點」會補充證據關係、訊號分歧與觀察條件。法人買賣、法人近期增量估算成本、
美股產業背景、台灣期貨夜盤及官方重大訊息已有唯讀資料契約；營收、EPS、ROE、券商分點客戶
身分或一般媒體即時新聞若沒有相應可信資料，系統會明確說資料不足，不讓 Qwen 猜測。

### 技術圖表圖片

使用者可直接上傳 PNG、JPEG 或 WebP 的 K 線／技術圖表截圖。LINE Gateway 只從 LINE 官方
Message Content API 下載 LINE 代管圖片，限制單張 8 MiB，並以檔案特徵驗證格式；不接受外部
圖片 URL，也不把圖片寫入磁碟、SQLite 或 log。圖片只在一次請求的有界記憶體內交給本機
`qwen3-vl:8b-instruct`，完成後僅留下有限的非圖片摘要供同一 session 追問。

視覺模型會讀取畫面中清楚可見的股票名稱／代號、週期、K 線結構、均線、量能與 RSI、MACD、
KD 等標籤或數值。所有圖片讀值固定標成 `estimated`；模糊、裁切、信心不足、非技術圖或沒有
可靠可見證據時直接回覆無法可靠辨識，不用曲線位置補猜精確數字。辨識到股票後會再經官方
股票母體解析，並向唯讀 Bot API 取得最近通過品質門檻的官方 OHLCV／技術資料作對照。圖片
週期或日期和官方資料不同時會明確提醒，圖片永遠不能進入 referee、評分或覆寫主結論。

圖片分析成功後可自然追問，例如：

```text
這張圖的 RSI 怎麼看？
剛才 K 線有止跌嗎？
圖上的均線和官方資料為什麼不同？
再詳細一點
```

Bot 會沿用剛才已確認的股票與有限圖片摘要；若圖中標的與前文股票不同，改以圖中可確認標的
重新查證，不把前一檔資料套錯。圖片未清楚顯示股票時，只有在同一 session 已有明確標的才沿用
前文，否則會自然詢問公司名稱或四碼代號。

對話脈絡採「近期完整雙方對話＋較舊滾動摘要＋個股摘要」三層結構。原始 LINE User ID、group ID、
room ID、webhook event ID 與 message ID 都先用每個 Channel 獨立的 HMAC 金鑰轉成不可逆索引；
問題、回答、session 與摘要使用 Fernet 認證加密後才寫入獨立 SQLite。私人對話、群組、聊天室與
不同使用者各自隔離；群組事件缺少 `userId` 時不保存也不重用對話，以免混到其他人。

本地 Qwen 每輪只取得有字數上限的最近 8 組問答、目前個股的歷史摘要與全域滾動摘要。摘要只
壓縮已存在的對話，輸出中若出現來源沒有的代號、日期、價格或百分比會被拒絕。歷史市場內容固定
標成歷史脈絡；本輪唯讀 Bot API 的 canonical FACTS 與唯一 referee 永遠優先，舊記憶不能授權
模型把舊價格說成現價，也不能推翻新結論。設定如下：

```text
LINE_CONVERSATION_TTL_SECONDS=1800
LINE_CONVERSATION_MAX_SESSIONS=1000
LINE_CONVERSATION_MAX_TURNS=8
LINE_CONVERSATION_MAX_USER_CHARS=2400
LINE_MEMORY_STORAGE=sqlite
LINE_MEMORY_DB_PATH=review_src/data/line_conversation_memory.sqlite3
LINE_MEMORY_KEY_FILE=review_src/data/line_conversation_memory.key
LINE_MEMORY_RECENT_EXCHANGES=8
LINE_MEMORY_PROMPT_CHARACTER_BUDGET=6000
LINE_MEMORY_COMPACTION_TRIGGER=3
LINE_MEMORY_COMPACTION_BATCH_SIZE=10
LINE_MEMORY_COMPACTION_TIMEOUT_SECONDS=60
LINE_MEMORY_RAW_RETENTION_SECONDS=86400
LINE_MEMORY_SUMMARY_RETENTION_SECONDS=2592000
LINE_MEMORY_PRIVACY_NOTICE_VERSION=2026-08-28
LINE_MEMORY_LONG_TERM_APPROVED=true
QWEN_MEMORY_MODEL_ID=qwen3-vl:8b-instruct
```

目前版本保留逐字問答 24 小時；加密 session、最後一次實際回覆、滾動摘要與個股摘要保留
30 天。正式告知內容為 `docs/LINE_BOT_PRIVACY_NOTICE.md` 版本 2026-08-28，使用者也可在 LINE
輸入「隱私告知」查看保存期間與刪除方式。若未設定告知版本或長期保存開關，安全上限仍會
自動降回 24 小時。

輸入 `清除對話`、`清除記憶`、`重新開始` 或 `忘記上一檔` 會清除目前聊天範圍；輸入
`刪除我的資料`、`刪除我的記憶`、`忘記我` 會刪除同一使用者在私人、群組及 room 的全部記憶。
LINE `unsend` 會抑制被撤回訊息並停用引用該訊息的摘要，`unfollow` 會刪除該使用者全部記憶。

追問辨識不再只比對單一固定句型。像「你是怎麼分析的」「這是如何得出的」「可以說明一下
剛才的看法嗎」都會沿用上一檔股票；「那華航呢」「換成 2610 看看」若包含可確認的新股票，
則會切換標的。若句子看似公司名稱但無法由官方股票母體確認，仍會要求代號，不會把上一檔
資料誤套到未知公司。

詢問「你的評估邏輯是什麼」「你評估時主要看什麼」「這個結論考量了什麼」時，也會承接上一
檔股票並切換到簡短判斷說明。對外只說明趨勢位置、動能、量價配合與風險條件等高層次面向，
最多引用一項當前個股證據，不公開完整規則、公式、具體門檻、計算係數或內部權重；回答末尾
會自然詢問使用者想再看 RSI、量價、支撐或其他單一面向。

### 一般投資對話

沒有指定個股時，LINE Bot 也可回答 RSI、MACD、量價、基本面、財報、新聞查證、分批條件與
風險管理等一般投資教育問題。這類問題由 Qwen 以 2～5 句自然繁體中文回答；模型不可聲稱掌握
未提供的今日行情、即時價格或最新新聞，也不可給保證漲跌、立即下單或個人化部位配置。模型
不可用時會回傳簡短的安全說明，而不是把整句誤當成股票名稱。

股票特定回答與一般投資回答都不得對外顯示 `FACTS`、裁判層、規則樹、內部權重、資料庫、
API、模型或供應商實作。對外只用「目前判斷」及趨勢、動能、量價、籌碼、基本面、消息等
高層次理由說明；股票特定解讀仍須在同一則訊息附上短版投資風險聲明。

## 專業判斷輸出契約

LINE 回覆先判斷使用者真正問的是價格、RSI、支撐、買賣決策、法人籌碼或美股背景，只把該主題
必要的 FACTS 交給 Qwen。除非使用者要求完整分析，否則不固定列出開高低收、RSI、MACD、估值
與成交量，也不重複上一輪已說過的數字。

「能不能買／續抱嗎／要不要減碼」使用以下流程：

1. 從唯讀資料庫取得同日官方 OHLCV、已物化技術資料及有效支撐／賣壓。
2. 由 Dashboard／LINE 共用的唯一 referee 產生主狀態；Qwen 不重算也不能覆寫。
3. 下游條件式顧問層依價格位於支撐或賣壓的位置，區分不追價、支撐止穩後小比例分批、等待
   重新站回支撐、續抱觀察或接近壓力分批調節等情境。
4. 最近法人買賣與外資／投信「估算成本」只有在日期及信心通過品質閘門時才作背景；估算成本
   不得稱為真實持倉成本。券商分點主力成本沒有合法可靠來源時不顯示。
5. 最近美股收盤依官方台股產業代碼選擇相對應的美國指數／產業 ETF；台灣期貨夜盤依金融、
   電子、半導體或大盤合約組合判讀。兩者只調整觀察信心，不能單獨翻轉個股裁判結論。
6. 近 7 日官方重大訊息優先呈現需注意事件，但不以標題直接判定利多或利空。
7. 回答本輪問題、給失效條件，並在缺少持有狀態或投資週期時只追問一個必要問題。

主結論只接受共用 referee core 的版本化輸出，且必須同時具備可信官方日線、可用技術資料、
有效支撐及賣壓區。任何欄位或版本不符合契約時一律降級為「資料不足」。Qwen 只能改寫後端
提供的定性 evidence 與已核准的條件式情境；若新增數字、改寫主結論、把內外盤稱作法人動向、
在無基準下斷言估值高低、給保證、目標價、一次重押或自行增加部位比例，系統會捨棄模型輸出，
改用 deterministic 安全回覆。系統維持唯讀，不提供下單功能。

## 美股收盤背景更新

完整每日資料更新會一併嘗試保存最近可用的 S&P 500、Nasdaq、SOX、台積電 ADR，以及美國
金融、工業、原物料、能源、醫療、消費、公用事業與科技類股背景。分析時依資料庫內的官方
台股產業代碼挑選相對應組合，不把費半權重套在所有股票上。
也可在專案根目錄手動執行：

```text
review_src/.venv/Scripts/python.exe scripts/update_global_market_snapshot.py
```

此為明確更新工作，寫入 `global_market_daily_snapshot`；Dashboard 與 Bot 的 GET 查詢仍是唯讀。
若日期過期、少於兩個有效標的或來源品質不合格，LINE 會排除該背景，不以舊資料推論今日行情。

## 法人成本、夜盤與消息來源

### 外資／投信近期增量估算成本

- 法人身分與每日買進、賣出、買賣超來自 TWSE／TPEx 官方三大法人日報。
- 寫入前逐檔檢查 `買進股數 - 賣出股數 = 買賣超股數`；任一來源批次對不上就拒絕更新。
- 每日價格基礎取官方日線的 `成交金額 / 成交股數`，作為全市場成交均價代理。
- 近 240 個交易日中，淨買超以當日成交均價加入增量部位；淨賣超按既有移動平均成本扣除。
- 結果只能稱為「近期增量估算成本」。官方公開資料沒有法人逐筆成交價、期初庫存與完整轉倉，
  因此不能宣稱是外資或投信全部真實持倉成本，也不使用 FIFO 假裝有逐筆庫存。

券商分點資料不能拿來辨識外資或投信。證交所的券商分點商品說明明載資料同時包含券商自營部
與一般受託客戶，同一分點內可能混有本國自然人、外資、投信及其他法人。即使依券商名稱判斷
「外資券商」，也無法證明該筆交易的實際投資人身分。未來若取得合法授權的逐價買賣金額與股數，
只能另建「分點流量／成交密集區」分析，仍不得改稱外資或投信成本。

### 台灣期貨夜盤

- 使用 TAIFEX 官方 OpenAPI 的期貨每日行情，只取盤後交易、有效成交量及最具流動性的近月合約。
- 依股票產業組合 TX／MTX、電子 TE／ZEF、金融 TF／ZFF、半導體 SOF；日期過期即排除。
- 夜盤 `can_override_main_status` 永遠為 `false`，只提高或降低隔日進場條件的嚴格度。

### 時事與官方重大訊息

- 上市公司使用 TWSE OpenAPI 的 MOPS 每日重大訊息；上櫃公司使用 TPEx OpenAPI 對應資料。
- 系統保留主旨及官方說明，辨識法說會；近 7 日才納入，不能只看標題判定利多或利空。
- 官方月營收來自 TWSE／TPEx OpenAPI；政策與時事來自行政院、經濟部及 White House 官方 RSS。
- 消息先按發布時間排序、內容與主題去重，再依來源可靠度、個股參考價值及事件半衰期降權；
  舊新聞不會因數量較多壓過同主題的新消息。
- 一般媒體與川普社群 Feed 預設停用，只有提供 HTTPS、publisher 與授權依據後才可接入。
  Truth Social 不作未授權自動擷取。
- CMoney 只作社群待查證線索。後端目前遭網站拒絕一般請求且沒有 API／書面授權，因此不繞過
  限制；授權前只接受人工提供的當日逐則內容，再向官方來源交叉驗證。完整契約見
  `docs/EXTERNAL_EVENT_INTELLIGENCE.md`。
- 免費 GDELT 雷達只保存最近新聞的標題、時間、媒體與原文網址，並固定標成待查證；不保存媒體
  全文、不直接判多空、不進 referee。429 或逾時會標成 `source_delayed`，其他官方更新仍繼續。
- 使用者可輸入 `查證 2317：<今天貼文內文>`；LINE 最多拆成五條主張，逐條顯示官方對照與
  盤中價格反應。只有 CMoney 網址而沒有內文時，系統會要求補貼文字。
- 使用者也可不指定股票，直接問「最近市場在炒什麼」「川普這個消息影響哪些類股」。
  Gateway 會讀取 `GET /api/bot/market-data/market-brief` 的近期可靠事件；若查不到相符的一手
  來源，會要求貼消息文字、截圖或連結，不拿無關舊聞補答案。

### 連續對話與自然追問

- 對話在獨立加密 SQLite 中保存最近雙方問答、已確認股票、問題焦點、是否持有、短線／波段／
  長期偏好；原始 LINE 識別碼不落庫。逐字問答 24 小時到期，有限狀態與摘要 30 天到期，服務
  重啟後仍可承接。
- 較舊問答由本地 Qwen 壓縮；SQLite 跨程序 lease 防止兩個 worker 同時壓縮同一使用者，LINE
  webhook event receipt 防止重送造成重複回答或重複寫入，事件時間戳防止舊事件覆蓋新 session。
- 固定規則先處理股票解析、資料品質與裁判結論；只有無法由明確規則判斷的自然追問，才讓
  本機模型在 `active_stock_follow_up`、`general_investment`、`market_brief`、`clarify` 四種
  意圖中分類。模型只負責路由，不能產生金融事實或把未知公司硬接到上一檔。
- 「如果營收變差呢」「這樣還撐得住嗎」「我沒持有、偏波段」會承接上一檔與使用者情境；
  「最近市場在炒什麼」則切到整體市場，但仍保留上一檔，之後可再問「那對台積電呢」。
- 無法確定時會問一個有用途的澄清問題，例如要接上一檔、看整體市場、產業題材或風險面向，
  不會只重複要求四碼代號。

### 自動更新時間

- `Taiwan Stock Fugle Full-Market Capture`：工作日 15:00 啟動，只更新官方股票母體後捕捉
  Fugle 當日逐筆與分價量；最晚重試至 17:50。這一階段不跑官方盤後數值、不做成交量核對、
  不執行評分，資料固定保持「等待官方驗證」。
- `Taiwan Stock Official EOD Reconciliation`：工作日 18:10 啟動，只抓 TWSE／TPEx 官方資料並
  核對先前 Fugle 成交量，不會再呼叫 Fugle。來源延遲時只重試官方階段至 23:59；23:40
  另有保底 trigger，週一至週六 06:45 也只做官方補查，確保週五延遲資料可在週六早上補齊。
- 時間依官方產製時程保守設定：完整收盤行情約 17:30、估值與不含鉅額的法人資料約 18:00、
  融資融券約 21:00，而目前同批核對所需的信用額度檔約 23:30。來源日期未到當日就維持
  `source_delayed`，不得沿用舊日數字冒充今日。
- 兩個排程共用專案內程序鎖，避免同時寫入；均啟用 `StartWhenAvailable`。搬移資料夾或換機後
  必須重新執行排程安裝器。隔日無法可靠重建前一日 Fugle 逐筆，因此 06:45 不回抓 Fugle。
- `Taiwan Stock External Analysis Context`：每天 06:15、09:00、11:30、14:00、18:00，更新已完成的
  美股收盤、台灣期貨夜盤、官方重大訊息、月營收及外部官方事件。
- 所有 Windows 排程都從腳本所在位置解析專案根目錄；原始碼與設定不含固定使用者或磁碟路徑。

## LINE Developers Console 設定

1. 建立 LINE Official Account，並在 LINE Official Account Manager 啟用 Messaging API。
   目前不能直接在 LINE Developers Console 建立新的 Messaging API channel。
2. 在 LINE Developers Console 取得 `Channel Secret` 與 `Channel Access Token`。
3. 第一次完成 Channel 設定後開啟 `Use webhook`；之後每次啟動會自動設定並驗證新網址。
4. 若啟動器顯示 Webhook 尚未啟用，回到 Messaging API 頁開啟 `Use webhook` 後再執行一次。
5. 若完全由本程式回覆，關閉 LINE Official Account 的 Auto-reply，避免重複訊息。

`127.0.0.1` 不能直接填入 LINE Developers Console；必須先設定自己的公開 HTTPS
網域、反向代理或受控 Tunnel。只公開 `/line/webhook`，不得公開 8000、8010 或 8020。
HTTPS 憑證必須由一般瀏覽器信任的憑證機構簽發，不能使用自簽憑證。

## 金融資料安全

- Qwen 不直接連 SQLite。
- Qwen 不執行 SQL、檔案、網址或下單工具。
- 官方 OHLCV 不可信時不交給模型。
- `technical.decision_ready=false` 時不交給模型 RSI／MACD 數字。
- `data_quality.decision_ready=false` 時不交給模型分價量與支撐賣壓。
- 模型回答出現 FACTS 與問題中不存在的新數字時，系統改用固定格式的安全回覆。
- 內外盤不得稱為法人、外資、投信或主力買賣超。
- 系統永遠不提供下單功能。

## 驗證順序

1. `GET http://127.0.0.1:8020/api/version`
2. `GET http://127.0.0.1:8010/healthz`
3. `GET http://127.0.0.1:8021/healthz`
4. 使用正確簽章 POST 空的 `events=[]` 到 `/line/webhook`。
5. 在 LINE 傳送 `2454 今日資料`。
6. 核對回覆日期、官方 OHLCV、RSI、MACD 與 Bot API payload 完全一致。
7. 執行 `python scripts/smoke_test_line_conversation.py`；驗證個股 → 假設追問 → 持倉／週期 →
   風險 → 市場題材 → 回到原個股的 7 回合承接，以及真實 Qwen 模糊追問分類。
8. 確認 `/healthz` 的 `conversation_context_storage=encrypted_sqlite`、
   `conversation_context_restart_persistence=true`、`conversation_context_integrity=ok`。
9. 測試「刪除我的資料」、LINE 撤回及服務重啟後追問；刪除後不得再出現舊股票或舊回答。

## 模型版本

- 模型：`Qwen3.8-27B-Q4_K_M.gguf`
- 權重來源：`Qwen/Qwen3.8-27B`
- GGUF：`ggml-org/Qwen3.8-27B-GGUF`
- GGUF SHA-256：`31629f53165ab6a7dad8c9847dcfd1fdf55829dac1e6e748f4a68581b0033d34`
- 執行引擎：Ollama for Windows `0.32.15`
- 執行檔：`runtime/ollama/ollama.exe`
- 安全性：Ollama Inc. Authenticode 簽章有效；不需關閉防毒或設定排除
- Context：預設 16,384 tokens
- Parallel：預設 1，讓單一 LINE 請求可使用完整 16K context
- Thinking：LINE 一般問答預設關閉，以降低 reply-token 延遲
- 啟用視覺時先預熱視覺模型、最後預熱文字模型；若硬體無法同時常駐兩個大型模型，會優先讓
  日常文字對話保持熱機，收到圖片時再載入視覺模型。

模型與 Ollama 不會由本專案自動更新。更新前必須先用固定股票問題集做數字忠實度、缺資料、
假日與模糊名稱回歸測試。

## 官方參考

- LINE Messaging API 建立流程：https://developers.line.biz/en/docs/messaging-api/getting-started/
- LINE Bot 與 Webhook 設定：https://developers.line.biz/en/docs/messaging-api/building-bot/
- Ollama Windows：https://docs.ollama.com/windows
- Ollama GGUF 匯入：https://docs.ollama.com/import
- Ollama OpenAI compatibility：https://docs.ollama.com/api/openai-compatibility
