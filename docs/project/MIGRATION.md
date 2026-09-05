# 遷移與驗證紀錄
驗證日期：2026-09-05。新專案已在本機執行；GitHub 遠端交付尚未完成。通過測試不代表可以保證零缺陷。
## 需求與結果
| 原需求 | 已實作與驗證 | 邊界／仍需處理 |
| --- | --- | --- |
| 重新建立好維護的新專案 | 新 equity 入口、程序與排程協調、獨立帳號／加密持股 repository、鎖定依賴、操作與架構文件 | 原分析相容層仍保留部分大型模組；後續分階段拆分需保護公式與既有契約 |
| 可攜、相對路徑 | 由模組位置解析根目錄；含空白的新 Git 副本重新安裝及測試成功 | .venv、OS 排程與 GPU runtime 需在目標環境重建；跨作業系統 CI 尚未執行 |
| 原歷史資料與保存規則 | 初始 106 個業務表、19,242,910 筆欄位及逐筆內容摘要相同；保存期限沿用原規則 | 後續每日更新按原規則正常新增及清理；初始摘要不是更新後永久不變的要求 |
| 每日官方行情 | 2026-09-04 候選於 22:57:44 通過完整性檢查並正式發布；1,978 檔母體完整分類（1,944 檔 OHLCV、34 檔官方無交易） | Fugle 該日歷史逐筆缺漏；full_analysis_ready=false、價量評分門檻仍關閉 |
| Qwen 分析持股 | Qwen3.8-27B 實際走 model 回覆路徑、讀取合成確認持股，約 5.45 秒，財務事實檢查通過 | 此耗時為本次探針；模型拒絕、逾時或資料不足仍使用既有資料式回覆 |
| 圖片辨識與持久保存 | Qwen3-VL 8B 合成兩檔持股辨識通過；股／張換算、草稿確認及重新登入持久化通過 | 保存前必須本人確認；不保證所有券商版面辨識正確；原圖不落盤 |
| 手機與 LINE | 390 px 手機流程通過；HTTPS 權限隔離及 LINE 官方空事件測試成功；精確分析回覆 payload 驗證 200，沒有發送測試訊息 | 正式首個帳號需由使用者在本機自行設定密碼；Quick Tunnel 重啟會換網址 |
| 重大新聞永久、一般滾動 | 公司行動及重大公告 metadata 永久保存；其他來源沿用各自原保存期限；清理不依賴此次必須抓到新資料 | 未驗證新聞保留狀態；不保存未授權全文，不自動套用拆股或除權調整持股 |
| 備份可復原 | 四庫在新目錄實際復原，SHA-256、完整性、外鍵及逐表筆數相符 | 加密／簽章金鑰分開備份，不能由資料庫重新生成找回 |
| 原專案移入新位置 | 正式程式、歷史資料、設定、模型與 runtime 已遷移；246,600 個可讀舊檔案另行封存 | 77 個舊快取／測試暫存目錄被 Windows 拒讀；原始 Desktop 專案保留，未宣稱全部刪除或移走 |
| GitHub 與升級流程 | 新 Git 工作樹與提交、原始碼 ZIP／Git bundle 交付流程、Windows／Ubuntu CI 設定 | GitHub 連線目前可見儲存庫為空，尚無遠端 URL，未 push 或執行遠端 CI |
## 已完成的主要修正
- registry materializer 改用呼叫端 transaction，避免誤讀全域資料庫。
- SQLite online backup 明確關閉 handle 後才 rename；復原工具先檢查摘要、完整性與外鍵，不覆寫既有目的。
- JWT 改用每個部署獨立、持久化的隨機金鑰；帳號與個人持股資料庫分離。
- 路徑以 pathlib 處理，SQLite URI 使用 Path.as_uri；Git 固定文字 LF，避免跨位置 checkout 破壞既有證據摘要。
- 新聞清理每次非 dry-run 更新均執行，重大事件先存入永久 metadata 表。
- 公司行動停牌使用官方明確停止／恢復日期；恢復日不算停牌，不用推測日期補分類或生成假行情。
- 官方核心資料可在 gate 通過後獨立發布，逐筆補充資料仍保持未就緒，不變更評分公式、V2／V3 權重或 rollout 授權。
- Windows 入口以 Job Object 回收所屬子程序；已實際只停止父程序驗證全部所屬子程序結束、其他 QA 服務不受影響。
- 整合服務先等待 HTTP 健康檢查，再開 Tunnel 與排程，避免大型資料庫啟動期間過早註冊 LINE endpoint。
## 執行與驗證結果
- 不同且含空白的 Git 副本：依 uv.lock 重新安裝、建立空資料庫、compileall 及 1,913 項離線回歸通過。
- 加入最後啟動順序修正後，正式工作樹完整離線回歸：**1,915 passed，1 個相依套件 deprecation warning**。
- 模型及 Ollama runtime：995 檔、53,175,285,709 bytes 的 SHA-256 與舊檔相符；Ollama blob 名稱摘要亦相符。
- 發布後正式資料庫外鍵違規 0；官方候選完整 integrity_check 通過，舊正式庫保留為 taiwan50.previous.db。
- 最新 `/`、自選股、台灣 50、2330 明細 API 均 200；自選 3 檔及台灣 50 的 50 檔顯示日期均為 2026-09-04。
- LINE 簽章空事件 200、錯誤簽章 401；持股文字及真實模型生成分析的精確 LINE payload 均經官方 validate/reply API 回傳 200。
- Windows「Taiwan Line Equity」排程實際啟動成功，最後核對 Enabled=true、State=Running。先前曾出現停用狀態，原因未證實；目前狀態以最後查核為準，不代表已測試真實登出／重新開機。
## 私有證據位置
這些檔案可能包含本機路徑或執行資訊，保留於本機，不加入原始碼交付包。
| 檔案 | 內容 |
| --- | --- |
| var/migration/source-manifest.json | 初始 source 複製清單 |
| var/migration/historical-data-comparison.json | 106 表逐筆比對 |
| var/migration/doctor-full.json | 初始資料庫完整性 |
| var/migration/archive-exceptions.json | 77 個舊暫存目錄讀取例外，原始來源未刪除 |
| var/migration/model-runtime-digests.json | 995 檔 SHA-256 比對 |
| var/test-results/portable-clean.xml | 新位置 1,913 項離線回歸 |
| var/test-results/final.xml | 最新 1,915 項離線回歸 |
| var/qa/restore-rehearsal/verified-restore.json | 四庫實際復原 |
| var/qa/vision-report.json | 合成持股圖片辨識 |
| var/qa/browser-report.json | 手機流程 |
| var/qa/model-analysis-report.json | 實際模型與精確 LINE 分析 payload 驗證 |
| var/qa/line-api-report.json | 明細 API、簽章與持股 payload 驗證 |
| var/qa/final-api-surfaces.json | 發布後頁面與行情 API |
| var/qa/final-database-report.json | 發布 receipt、資料日期及品質限制 |
| var/qa/windows-job-lifecycle.json | 停止父程序後的子程序回收 |
| var/qa/final-service-report.json | 最後服務／HTTPS／LINE 查核 |
| var/services/public-endpoint.json | 最後一次 Quick Tunnel 啟動結果；不是持續健康監控 |
| var/deliverables/manifest.json | 原始碼與 Git bundle 的版本、摘要及內容檢查 |
## 下一步
先由使用者在本機建立正式帳號並綁定 LINE。提供已授權的 GitHub 儲存庫 URL 後可上傳原始碼並查核 CI。長期手機使用應配置固定 HTTPS 網址；9/4 歷史逐筆資料需要可授權取得的來源才能補回。原 Desktop 來源先保留，待確認舊讀取例外只含可拋棄快取後再處理清理；本次未修改原資料保留期限。
