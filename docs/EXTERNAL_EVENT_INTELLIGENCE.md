# 外部事件與社群查證資料契約

## 目的與邊界

外部事件只作個股裁判層的下游背景，不產生第二個主結論，所有輸出
`can_override_main_status=false`。系統只描述「潛在正向／負向／混合／方向不足」及條件式價格確認，
不得輸出必漲、必跌或保證價格。

正式 LINE reply-only 關鍵路徑與所有 GET route 不直接抓新聞。官方／授權外部資料仍由
`scripts/update_external_analysis_context.py` 明確抓取並寫入 DB；使用者正式回覆只讀已落地資料，
避免網站延遲、失敗或惡意內容阻塞回覆。

`LINE_MODEL_RESEARCH_ROLLOUT=shadow` 時，正式回覆成功後的候選分析可在 GPU admission **之前**做
bounded metadata-only 搜尋。這條 post-reply shadow lane 只存在記憶體，不抓文章本文、不寫 canonical
table、不修改 referee，也不會把候選回答送給 LINE。來源逾時、429、離線或格式錯誤只會形成明確
research limitation；GPU slot 不會在網路等待期間被占用。

## 已啟用來源

| 類型 | 來源 | 用途 | 品質 |
|---|---|---|---|
| 月營收 | TWSE OpenAPI `t187ap05_L` | 上市公司當月、年增、累計年增 | 官方結構化資料 |
| 月營收 | TPEx OpenAPI `mopsfin_t187ap05_O` | 上櫃公司當月、年增、累計年增 | 官方結構化資料 |
| 重大訊息／法說 | TWSE／TPEx MOPS 每日重大訊息 | 主旨、說明、事件日期 | 官方公司揭露 |
| 政策 | 行政院本院新聞、部會新聞 RSS | 台灣政策與部會發布 | 官方 RSS |
| 政策 | 經濟部新聞 RSS | 產業、能源與經濟政策 | 官方 RSS |
| 美國政策 | White House News RSS | 川普政府與美國政策正式發布 | 美國政府官方 RSS |
| 免費新聞雷達 | GDELT DOC 2.0 | 最近 3 日標題、時間、媒體及原文 URL | 未驗證補充索引 |
| Shadow 備援 | Google News RSS 搜尋結果 | GDELT 無法連線時的標題、來源、索引時間與聚合連結 | 實驗性、未驗證、canary 前須重審 |

可選的 `LICENSED_NEWS_FEEDS_JSON` 與川普社群授權 Feed 預設停用。每個 Feed 必須有 HTTPS URL、
publisher 與 `license_reference`；未提供授權依據時不得抓取。

Truth Social 官方條款禁止未授權自動化存取與系統性擷取，因此程式明確拒絕 `truthsocial.com` 直連。
只能在取得書面授權或合法資料供應商後設定 `TRUMP_SOCIAL_AUTHORIZED_FEED_URL`。

## 時間、可靠度與參考價值

每筆資料保留發布時間、事件日、來源網址、publisher、來源類別、授權類別、內容指紋、抓取時間、
方向、信心與分析版本。正式個股背景必須同時通過：

- `source_quality` 為 `official` 或 `licensed`。
- `quality_status=ok`。
- `reliability_score >= 0.70`。
- `reference_value_score >= 0.45`。
- 有效發布日期且不在未來。

可靠度表示「來源身分與內容來源是否可信」；參考價值表示「這則內容是否真的能映射到該股票」。
官方來源不代表一定影響股價，沒有明確個股／產業關聯的官方新聞仍可能只有低參考價值。

個股事件採新到舊排序、內容指紋去重與同主題最新優先。時間半衰期如下：

- 授權社群：3 日。
- 政策／官方新聞：7 日。
- 官方月營收：45 日。

有效權重為 `可靠度 × 參考價值 × 0.5^(事件年齡/半衰期)`。舊新聞不會因數量多就壓過同主題新消息。

GDELT 使用獨立的 `news_radar_event` table，只保存新聞 metadata，不保存媒體全文。其資料固定為
`source_quality=supplemental`、`verification_status=unverified`、`ready_for_referee=false`，因此不會進入
上述消息加權。來源逾時或回傳 429 時標成 `source_delayed`，不阻斷其他官方資料更新，也不立即重試
造成更多流量。

Post-reply 受控研究與上述排程 DB radar 分離：

- GDELT DOC API 為第一來源；官方參數說明：
  <https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/>。
- 目前主機連 GDELT 會 TCP connect timeout，因此 shadow 可改用 Google News RSS metadata-only 備援；
  Google 對新聞搜尋結果的官方說明：
  <https://support.google.com/news/publisher-center/answer/10598160?hl=zh-Hant>。
- Google 並未把 RSS 搜尋端點列為正式 public API，所以目前只允許 shadow。其使用條款、穩定性與
  attribution 規則必須在 canary 前重新複核；未通過不得公開引用。
- 正向 cache TTL 預設 600 秒、負向／逾時 cache TTL 預設 120 秒、最多 256 entries；這些是工程
  預設值，不是來源或 LINE 官方規範。
- 每 query 最多 4 筆、每 request 最多 2 queries，查詢只由 DB 股票代號／名稱及 allowlisted 公共
  主題組成，不把任意使用者文字或 LINE ID 送給搜尋來源。
- `raw_article_bodies_fetched=0`、`raw_body_retention_seconds=0`、
  `canonical_table_writes=0` 是強制觀測欄位。
- 使用者明確要求 `current_news` 且受控事件存在時，封包與 token compaction 至少保留一筆
  `news_radar_context`；模型必須用 `used_event_ids` 精確引用，URL 只由後端 packet renderer 輸出。

## 營收方向

月營收只以官方結構化數字判斷：

- 年增至少 10% 且累計年增至少 5%：正向。
- 年增至多 -10% 且累計年增至多 -5%：負向。
- 月增、年增與累計年增互相矛盾：混合。
- 未達門檻：中性。
- 必要數字缺漏：方向不足。

這只代表營收動能，不等同獲利、自由現金流或股價必然同向。

## CMoney 股市爆料同學會

CMoney 個股頁格式已確認為 `https://www.cmoney.tw/forum/stock/{code}`。它只能作「社群待查證線索」，
不能當事實來源或行情主來源：

- 只處理當日、帶完整時間與原文 URL 的逐則貼文。
- 相同說法被多位會員重複，不算獨立證據。
- 目標價、必漲／必跌、買賣口號只標成 opinion，不能驗證成事實。
- 營收、法說、政策、訂單等事實主張，必須與 MOPS、TWSE／TPEx、政府或公司官方發布逐條比對。
- 數字與官方資料不符標成 `contradicted`；找不到一手來源標成 `unverified`。
- 今日股價反應使用本系統的 TWSE MIS 官方盤中報價，不使用 CMoney 報價當裁判資料。
- 現價、昨收、開高低與漲幅只能說明市場已反應多少，不能證明該貼文造成漲跌。

CMoney 網頁目前對一般後端請求回 403，且其會員條款限制未授權翻載。系統不使用登入 Cookie、
不繞過限制、不建立隱藏爬蟲。自動收集須先取得 CMoney 官方 API、書面授權或合法供應商 Feed；
授權前可由人工提供貼文 URL／內容給 `analysis/community_claim_verification.py` 做逐條查證。

LINE 免費人工送驗格式：

```text
查證 2317：營收年增 54%，而且取得新訂單。股價一定漲。
```

系統最多拆成五條主張，逐條對照已落地的官方月營收、重大訊息與法說資料。只有網址、沒有貼文
內文時會要求使用者補上文字，不會嘗試繞過 CMoney 的存取限制。沒有平台 API 時，原始發布時間
無法獨立驗證，LINE 會明示目前只按送驗時間處理，避免把舊文誤稱為今日消息。

## LINE 顯示

「新聞／消息面」回覆會顯示：

- 最新事件日期及時間衰減後的消息傾向。
- 每則事件 publisher、潛在方向、可靠度、參考價值與標題。
- 官方重大訊息／法說會主旨及官方說明摘要。
- 盤中現價、昨收、今日開／高／低、漲幅與所在日內區間位置；不新鮮時明確拒用。
- 現有 referee 主結論，以及消息不能覆寫主結論的提醒。
- GDELT 標題另列為「待查證新聞線索」，不與已驗證事件混合計分。

## 排程與設定

手動執行：

```powershell
& scripts\run_external_analysis_context_update.bat
```

排程安裝器預設每天 06:15、09:00、11:30、14:00、18:00 更新。搬移專案或換機後必須重新安裝。
授權 Feed 設定只放 `.env`，不得提交真實合約編號、權杖或私有 URL。
