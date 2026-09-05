# Taiwan Line Equity 重建：目前審查狀態
本節描述新專案；後方為歷史紀錄，不能當成目前部署的驗證結果。
- 初始遷移 106 個業務表、19,242,910 筆逐筆摘要相同。四庫備份及另一目錄的實際復原檢查通過；金鑰分開保存。
- 持股 API／LINE 共用加密 repository，圖片必須本人確認；合成圖片、390 px 手機操作、股票明細與 LINE 格式驗證通過，沒有發送 LINE 訊息。
- 真實 Qwen3.8-27B 已使用合成確認持股執行分析，最新探針走 model 路徑並通過既有財務事實驗證；仍保留模型拒絕時的資料式回覆。
- 新增官方公司行動停牌來源及 TAIFEX JSON／CSV 格式處理。官方全市場 9/4 更新的必要來源已通過；正式候選發布正在重跑。
- 整合 finalize 允許發布已通過官方 gate 的資料，Fugle 缺漏及 full_analysis_ready=false 明確保留。未變更評分公式、原資料保存期限、V2／V3 權重或原 rollout 授權。
- 最近完整回歸 1,890 passed；之後停牌、外網、SQLite 復原與官方發布範圍均有針對性回歸。最終乾淨副本回歸尚未完成。
- Quick Tunnel 公開頁面 200、未登入持股 401、外網首帳初始化關閉。LINE 正式 endpoint 切換及整合服務最終重啟待完成。
- 舊專案封存已複製 246,600 個可讀檔案；77 個快取／測試暫存目錄被舊 Windows ACL 拒讀。原始專案保留，未宣稱完全刪除或移走。
- 尚待：模型摘要核對、乾淨安裝回歸、正式更新發布、Git 提交及 GitHub 遠端、最終逐需求審查。
操作與證據見 [新專案說明](../README.md)、[遷移紀錄](project/MIGRATION.md)。

---
# Codex Review Packet — 排程與全市場盤後更新複查（2026-09-05）

## 本次複查結果

- 系統唯讀查驗：原有 6 個股市更新 Windows 工作全部 Disabled；未發現正在執行的更新／scheduler Python 或 cmd 程序。
- 有效設定：AUTO_REFRESH_MARKET_DATA_ON_START=False、AUTO_UPDATE_TW50_ON_START=False。
- 專案 review_src/scripts 未找到 place_order、submit_order、buy_order、sell_order、Shioaji 或 Fubon 下單實作。此结論只涵蓋本專案，不能代表其他券商軟體或遠端帳戶。
- 最新已收盤交易日為 2026-09-04；正式 DB 的當日日線、技術、法人、融資融券借券、估值、分價量、內外盤及每日籌碼筆數全部為 0。
- DB 最新行情仍是 2026-09-03（1,942 檔）；9/4 Fugle trades 原始快取為 0。新驗收報告 daily_update_complete=false、safe_to_publish=false、SQLite quick_check=ok。
- 這次是檢查及修正一鍵程式；沒有執行全市場正式 DB 寫入，也沒有再次宣稱首次 9/3 更新等於最新 9/4 已更新。

## 本次修正

- scripts/run_isolated_manual_daily_analysis_update.py：凍結目標日期並在成功發布後以相同日期、相同 DB 驗收；plan-only/help 不建立快照。
- 一鍵更新今日分析資料.bat：移除會驗錯日期的第二次預設日期驗收；非零結果要求查看發布 receipt，避免把「已發布但不完整」誤說成未發布。
- scripts/run_manual_daily_analysis_update.py：補日游標改從最後完整 publication 起算，重試尾端部分寫入日；跨日跳過 capture 明確標記；新增第 8 階段月營收／政策／已設定授權新聞，完整驗收改第 9 階段。
- scripts/verify_daily_analysis_update.py：用股票代碼集合驗證官方 OHLCV 與各衍生表，額外股票不能抵銷缺股；空母體不能通過。新增漏分類代碼、分價量分數表及 TW50 成分代碼核對；舊日期報告不能讓今日 capture/scoring 變成 ready。美股／ETF、夜盤、公告、交易限制及外部事件納入完整度，partial 不等於 ok；已設定新聞來源失敗不可被忽略。
- tests/test_manual_daily_analysis_update.py：新增異碼等筆數、partial 來源、尾端補日、指定日期與 DB 發布後驗收、失敗候選不驗舊 DB 等回歸案例。
- docs/MANUAL_MARKET_UPDATE_GUIDE.md：更新 9 階段、來源範圍與跨日分價量無法保證回補的限制。
- 未改動評分公式、RSI、裁判層、支撐壓力或 API 行為。

## 驗證及限制

- 4 個修改 Python 檔（含測試）py_compile 通過。
- 手動更新／盤後／隔離發布／全市場順序及 readiness／分價量 lifecycle 共 58 tests passed。
- BAT --plan-only --date 2026-09-04 exit 0，列出 9 階段、writes_db=false。
- 修改後 verifier 實際讀正式 DB 完成，quick_check=ok，9/4 缺口明確報告；未用舊報告聲稱新日期完整。
- 風險：中（更新協調及驗收條件改變；新外部資料階段已接既有來源服務，但本次沒有實際網路抓取或正式發布驗證）。
- 新報告 docs/MANUAL_EXTERNAL_EVENTS_REPORT.json 會在首次執行新增階段後產生；目前缺少 receipt 正確視為尚未驗證。
- 當日分價量在沒有快取／逐筆來源時，現有 Fugle intraday 不保證跨日回補。9/4 的缺口須取得合法歷史逐筆／分價量來源，不能靠無限重跑 BAT 解決。
- 券商分點成本原始資料、人工驗證公司行動及研究／模型產物不是已完成的每日行情抓取項目；不能把既有估算籌碼成本說成分點資料完整。
- 下一步：保持排程停用，往後於交易日當天盤後手動擷取並保存 Fugle 資料；9/4 需歷史來源才能完整補回。現有安全發布 gate 在分價量缺少時仍會拒絕候選，不能宣稱全市場更新已完成。

---

# 歷史紀錄 — 全市場手動每日更新（2026-09-04）

## 任務與結論

- 任務：停止所有既有每日自動更新，改成可顯示進度的一鍵手動更新；範圍為全部有效上市、上櫃股票，不限台灣 50。
- 更新目標交易日：`2026-09-03`。
- 結果：首次隔離更新已安全發布；正式 DB 的完整 `PRAGMA integrity_check` 為 `ok`。
- 完整度：`safe_to_publish=true`、`daily_update_complete=false`。已取得的官方列及衍生資料彼此一致，但上游來源對 2 檔股票尚未提供可核對的 9/3 分類，因此不得宣稱全市場 100% 齊全。

## 已停用的 Windows 排程

以下 6 個工作均於完成後重新查驗為 `Disabled`：

1. `Taiwan Stock External Analysis Context`
2. `Taiwan Stock Fugle Full-Market Capture`
3. `Taiwan Stock Official EOD Reconciliation`
4. `Taiwan50 Fugle Watchlist Price Volume Update`
5. `Taiwan50 TDCC Equity Update`
6. `Watchlist TDCC Equity Update`

App 啟動旗標亦為：`AUTO_REFRESH_MARKET_DATA_ON_START=False`、`AUTO_UPDATE_TW50_ON_START=False`。
Repo 內沒有 `.github` 目錄，因此沒有另外存在的 GitHub Actions cron；系統層面所有指向此 repo 的排程動作就是上述 6 項，均已停用。

## 一鍵更新資料流

根目錄入口：`一鍵更新今日分析資料.bat`。

1. 補齊先前遺漏的官方交易日。
2. 重播同日已保存的 Fugle 原始 JSON。
3. 擷取尚缺的全市場逐筆成交、分價量與內外盤資料，並每 30 秒顯示進度。
4. 寫入官方收盤 OHLCV、估值、技術指標、三大法人、融資、融券、借券及估算籌碼成本。
5. 更新全市場每日籌碼動能。
6. 產生台灣 50 相容批次，不作為全市場母體。
7. 更新 TDCC 週頻股權分散資料。
8. 逐表驗收來源日期、列數、分價量核對狀態與 SQLite 完整性。

所有寫入先在 SQLite 一致性快照的隔離候選 DB 執行。候選通過安全發布 gate 與完整性檢查後才原子替換正式 DB；正式 DB 在更新期間若有其他 writer 變動，發布會中止。

## 首次更新驗收

- 有效上市／上櫃股票：1,978 檔。
- 9/3 官方 OHLCV：1,942 檔。
- 9/3 官方無交易分類：34 檔。
- 尚未被官方 OHLCV 或官方無交易分類涵蓋：2 檔，`1563 巧新`、`6949 沛爾生醫-創`。
- 技術向量／技術快照：各 1,942 檔。
- 經官方量能核對的分價量、分價量輪廓、分價量分數、內外盤、每日籌碼動能：各 1,942 檔。
- 三大法人：1,850 檔。
- 融資／融券／借券餘額：1,874 檔。
- 官方估值：1,967 檔。
- 估算籌碼成本：1,942 檔。
- 台灣 50 相容收盤批次：50/50，0 errors。
- TDCC：最新官方週日期 `2026-08-28`，1,977 檔；相對交易日 6 天，通過 14 天新鮮度門檻。
- Fugle 快取重播：1,437,545 筆逐筆成交，成功涵蓋 1,942 檔。
- 正式 DB：13,238,919,168 bytes；完整 `integrity_check=ok`。
- 發布前舊正式 DB 保留為 `review_src/data/taiwan50.previous.db`。

來源缺口沒有被推定為停牌或無交易，也沒有填入假數字。這是 `daily_update_complete=false` 的主要原因；9/2 亦有相同的點時母體缺口，因此 previous-day continuity gate 未通過。分價量裁判層的 30 日／80% 歷史覆蓋門檻仍未累積完成，與「今日分價量已寫入」分開呈現。

## 建立或修改的檔案

- `一鍵更新今日分析資料.bat`：使用相對路徑選擇 Python、呼叫隔離更新、發布後再次驗收，並顯示報告位置與 exit code。
- `scripts/run_manual_daily_analysis_update.py`：協調 8 階段全市場更新與進度報告。
- `scripts/run_isolated_manual_daily_analysis_update.py`：在隔離候選 DB 執行，沿用一致性快照、發布鎖、正式 DB 指紋檢查與原子發布。
- `scripts/verify_daily_analysis_update.py`：唯讀逐表驗收，分離 `safe_to_publish` 與 `daily_update_complete`。
- `tests/test_manual_daily_analysis_update.py`：驗證全市場母體、fail-closed、來源部分完成可安全發布、retryable capture、plan-only 與點時母體契約。
- `docs/MANUAL_MARKET_UPDATE_GUIDE.md`：說明手動入口、全市場範圍、資料項目、報告及 gate 語意。
- `docs/MANUAL_DAILY_ANALYSIS_UPDATE_REPORT.json`：首次更新分階段結果。
- `docs/MANUAL_DAILY_ANALYSIS_UPDATE_VERIFICATION.json`：首次更新逐表完整度結果。
- `logs/manual_daily_update/isolated_update_latest.json`：隔離候選發布 receipt。
- `docs/CODEX_REVIEW_PACKET.md`：本次最新審查摘要。

## 驗證與風險

- Python `py_compile`：通過。
- 手動更新、盤後更新、全市場順序／readiness、分價量 capture／service 測試：73 passed。
- BAT `--plan-only`：通過，不建立快照、不抓網路、不寫 DB。
- 首次正式執行：候選 pipeline exit 0、`published=true`、正式 DB 完整 integrity check 通過。
- 風險等級：中。原因是官方來源尚缺 2 檔分類且多日分價量評分覆蓋尚未達標；不是 DB 損壞或已取得資料落地失敗。

## 下一步

保持所有排程停用。稍後官方來源補齊時再次雙擊 `一鍵更新今日分析資料.bat`；流程會利用已保存快取，只補仍缺的來源並重新驗收。在 `daily_update_complete=true` 前，不得把今日資料宣稱為全市場完全齊全，也不得讓不合格分價量進入裁判層評分。
