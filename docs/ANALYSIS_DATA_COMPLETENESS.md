# 隔日展望資料完整度與補齊順序

更新基準：2026-09-02（Asia/Taipei）。本文件區分「表或程式已存在」與「正式 DB 已有可用資料」；前者不得冒充後者。

## 結論

目前資料庫仍不足以啟用使用者指定的 Material-event／Normal 兩套正式權重，但資料地基已明顯補齊：官方 OHLCV 600 日、技術向量 600 日、三大法人 720 日、信用交易 900 日、海外市場 400 日、TAIFEX 夜盤 400 日與 TWSE／TPEx 官方估值配對 200 日均已隔離驗證後原子發布。新聞研究 schema、去重與 7 日 hot-content 規則已存在，但正式新聞、sealed artifact、prediction/outcome 仍為 0。正式 referee 在 MaterialityClassificationContract、point-in-time factor snapshot、真實 prediction/outcome 與統計 gate 完成前，必須維持既有穩定邏輯；不得用事後資料回填假預測。

## 完整判斷鏈與硬性 gate

1. 每個因子必須先通過 `event_at/source_published_at/first_seen_at/validation_passed_at/usable_from` 的 point-in-time cutoff；事後回填資料不能冒充當時已知。
2. 先由版本化 MaterialityClassificationContract 決定 `normal` 或 `material_event`，不能讓 Qwen 自由心證切換 regime。目前此契約尚未凍結，因此兩套候選權重不可正式啟用。
3. 六個因子桶分別產生方向、強度、coverage、freshness 與 evidence IDs。資料缺失只能 `neutral/unavailable` 並降低 confidence，不得把缺失權重分配給其他桶。
4. 個股量價桶先計 deterministic 結構分數，再計五家族技術 ensemble；高度相關指標先合併並設 cap，不能讓 KD/KDJ 或多條同源均線重複投票。
5. 法人、融資、融券與借券必須保持單位和語意分離；借券 shares 不得與融券 lots 直接相加。估算成本只能 explanation-only，不能冒充真實總持倉成本。
6. 新聞先做來源權威、事實驗證、事件 fingerprint/revision 去重、materiality 與市場是否已反應，再進事件桶；同一 revision 只能貢獻一次。
7. 最終只有單一 referee 可以產生主結論。Qwen 可說明 evidence 與情境，但不能另建第二個 main status。

## 籌碼桶的可實作判斷內容

- 三大法人必須分開保留外資、投信、自營商的買進、賣出、淨額；判斷時比較當日淨額、占當日成交量／成交額比例、5/20 日累積、連續性、加速度與價格背離，不能只把三者淨額相加。
- 融資判斷使用前日餘額、買進、賣出、現償、當日餘額、增減、官方限額與使用率。融資大增搭配急漲屬擁擠／追價風險；融資下降搭配守價才可能是籌碼沉澱，但都必須與流動性和價位位置共同判斷。
- 融券使用前日餘額、賣出、買進、券償、當日餘額、增減、官方限額與使用率；借券賣出則使用 shares 的前日餘額、賣出、還券、調整與當日餘額。兩者是不同制度、不同單位，只能各自標準化後再形成家族分數。
- TDCC 是週頻集中度確認因子，使用原始級距推導小戶／大戶占比與四週變化；不可把週資料當成每日新訊號，也不可從錯誤摘要反推原始資料。
- `estimated_chip_cost_daily` 只能描述近期增量部位估算；沒有逐筆持倉與分點授權資料時，外資／投信／自營商／主力／融資戶真實成本一律 unavailable、方向權重 0。
- 籌碼家族的精確內部權重與門檻必須以 point-in-time walk-forward 校準並版本化；本次只補資料權威與可計算欄位，不自行發明一組未驗證的數字讓 gate 變綠。

## 已核對資料覆蓋

| 因子或資料 | 正式表狀態 | 2026-09-02 核對結果 | 現在能否計入隔日展望 |
|---|---|---|---|
| 官方 OHLCV | 已補齊目前契約 | `history_price` 1,122,159 筆、600 個市場交易日、2024-03-15～2026-09-01 | 可，仍須逐檔檢查日期、來源與 coverage |
| 技術向量 | 已補齊 | `technical_indicator_vector_daily` 1,121,328 筆、600 日、1,978 檔；每列保存 62 個版本化指標值與品質 metadata | 可；最新正規化 component 另保留相容輸出 |
| 技術摘要/component | 已補齊最新相容面 | `daily_technical_snapshot` 最新 1,978 檔；`technical_indicator_component` 最新日 62×1,978，歷史權威改為 compact vector | 可；歷史 walk-forward 必須讀 vector，不可誤讀僅最新的 legacy snapshot |
| 分價量 | 部分 | 19 個有資料日期；最新日雖涵蓋多數股票，裁判層仍須通過 `coverage_days >= required_days * 0.8` 且 `status=ok` | 多數股票目前不可計分，只能顯示排除原因 |
| 三大法人買／賣／淨額 | 已補齊 | 1,273,935 筆、720 個已驗證交易日、2023-09-13～2026-09-01 | 可；歷史回填的 `usable_from` 是本次驗證後，不會產生 look-ahead |
| 融資／融券／借券 | 已發布 | `credit_balance_daily` 1,555,988 筆、900 日、1,888 檔、2022-12-08～2026-09-01；上市 896,884、上櫃 659,104，官方使用率 1,462,252 筆 | 可；逐日仍須通過 `available_at`／freshness，借券不可與融券相加 |
| 外資／投信估算成本 | 部分 | `estimated_chip_cost_daily` 有資料，但只是近期增量部位移動平均估算 | 僅說明，方向權重維持 0 |
| 真實法人／主力／融資戶總成本 | 無 | 官方彙總流量無法反推出真實持倉成本；券商分點與融資金額原始表無資料 | 不可計算，也不可用 POC 冒充 |
| TDCC 集保分布 | 已補齊目前官方窗口 | 原始級距 33,510 筆、1,977 檔、6 個週資料日；衍生摘要已從原始級距重建 | 可作 concentration 說明；週頻且歷史仍短，正式權重仍需校準 |
| 估值 | 官方歷史已發布 | `twse_daily_valuation` 389,869 筆、200 個完整 TWSE／TPEx 配對交易日、2025-11-05～2026-09-01；legacy `valuation` 保留 28 日相容面 | 可；歷史回填保留實際 `available_at`，缺資料時 neutral/unavailable，不重配權重 |
| 稀釋／公司事件 | 部分 | corporate action 只涵蓋 53 檔；官方公司事件另有資料但尚未完成 materiality contract | verified 事件可做風險揭露；正式方向權重尚不可啟用 |
| 海外市場 | 已補齊候選契約 | 13 個 ticker 各 400 個完整交易日，共 5,200 筆，2025-01-29～2026-09-01；2026-09-02 尚未收盤的 13 根日 K 已排除 | 可做環境因子；來源為 supplemental，不能冒充官方且仍須 point-in-time cutoff |
| 台灣夜盤 | 已補齊候選契約 | 7 個契約、2,582 筆、400 個交易日，2025-01-08～2026-09-02；TX/MTX/SOF/TE/ZEF 各 400 日 | 可做夜盤背景；永遠不能 override referee |
| 新聞研究要點 | schema 有、資料無 | `research_news_item`、`event_cluster`、`event_revision` 皆為 0；已具 key points、content hash、event fingerprint、revision、來源權利與 expiry 欄位 | 不可宣稱已搜尋或已納入 |
| 時段 artifact | schema 有、資料無 | retrieval run、premarket artifact、event delta 皆為 0 | Web／LINE 尚不能依 18:00／21:00／06:00／07:00 選最新 sealed 結果 |
| 預測與實績 | schema 與嚴格寫入流程有、真實資料尚無 | prediction、outcome、statistical manifest 皆為 0；outcome materializer 本次看到 0 筆 sealed prediction，因此寫入 0 | 無法證明候選權重優於穩定基準；統計結果必須維持 `remain_shadow_insufficient_power` |

## 固定保留規則

| 資料族群 | 保留期限 | 注意事項 |
|---|---:|---|
| 官方 OHLCV、每日技術摘要、技術 component | 最近 600 個交易日 | 依交易日 prune，不以抓取時間代替 |
| Fugle intraday／time-sales／補充分價量 | 最近 300 個交易日 | 與官方 OHLCV 分開 prune |
| 三大法人活動 | 最近 720 個交易日 | 歷史回填的 `fetched_at` 不能冒充當時已知時間 |
| 融資／融券／借券 | 最近 900 個交易日 | 保留官方單位；融資／融券 lots、借券 shares |
| 海外市場日資料 | 最近 400 個完整市場交易日／ticker | supplemental；美股未收盤的當日日 K 不得寫成 completed close |
| TAIFEX 夜盤 | 最近 400 個交易日（依各契約實際可得日） | 官方來源；不得覆蓋主裁判結論 |
| TWSE／TPEx 官方估值 | 最近 200 個完整配對交易日 | 同一日期兩市場都達資料量門檻才算成功；保留真實 `available_at` |
| 估算籌碼成本 | 最近 720 個交易日／code／cost_type | 永遠標示 estimated；不是總持倉真實成本 |
| 內外盤補充資料 | 最近 600 個交易日 | 無合法授權來源時必須 unavailable |
| 新聞 hot content（標題、短摘、key points） | 最長 7 個日曆日且不得超過來源權利 | 到期清空 hot content；保留 URL、hash、event identity 等最小稽核 metadata |
| 原始新聞全文 | 0 秒 | 不寫 DB、不寫一般 log |
| TDCC 原始週級距與摘要 | 至少足以支援 walk-forward 的版本化週資料 | 不得用錯誤衍生摘要覆蓋原始級距 |
| 預測、outcome、release manifest | 不隨 7 日新聞 hot-content 一起刪除 | 保存不可否認的 evidence ID/hash，不保存新聞全文 |

## 補齊順序與 gate

1. 先在隔離候選 DB 回填 600 日技術資料，完整 integrity check 後才原子發布。
2. 修正 TDCC 小戶級距的衍生錯誤，從既有原始級距重建所有週摘要；不重新抓網路、不改原始列。
3. 已完成信用交易 900 日隔離回填與正式發布；四個官方來源同日驗證餘額公式、lots/shares、限額與使用率。
4. 補 official exact-session calendar materialization，讓 18:00／21:00／06:00／06:45＋07:00 deadline 與 15 分鐘 sentinel 能合法啟動。假日清單加猜測的平日不可冒充 sealed exact-session revision。
5. 啟動新聞 retrieval、7 日 key-point retention、內容 hash／事件 fingerprint／revision 去重與 cumulative sealed artifacts。
6. 以已發布的海外 400 日、夜盤 400 日與官方估值 200 日物化 point-in-time factor snapshot，分開技術、籌碼、海外／夜盤、估值風險、事件與 coverage，不把缺失權重轉給別桶。
7. 由真正 sealed canonical artifact 同時記錄 stable/candidate 三個 target 的 prediction，次一交易日再以官方價格產生 outcome；累積 walk-forward 樣本並通過統計 gate，之後才允許候選權重脫離 Shadow。

## 不可用免費資料假造的欄位

- 外資、投信、自營商、融資戶的真實總持倉成本。
- 未取得券商分點逐筆買賣股數與金額時的主力／券商分點成本。
- 缺融資額度／上限時的官方融資使用率。
- 把借券與融券直接相加後稱為空方總部位。
- 用 POC、成交均價或法人近期增量成本冒充真實持倉成本。
- 將模型判斷、社群貼文或新聞標題直接升格成 verified material event。
