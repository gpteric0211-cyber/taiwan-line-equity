# 遷移與驗證紀錄

狀態：進行中。此文件不代表所有交付條件已完成。

## 已有證據

- 原行情庫以 SQLite online backup 建立新副本。
- 首次資料完整比對：106 個原業務表、19,242,910 筆資料，欄位與逐筆內容摘要全部一致。
- 三個新架構資料庫的完整 integrity_check 與外鍵檢查通過。
- 原 LINE 記憶庫及加密金鑰已另行遷移；沒有輸出金鑰或 token。
- Qwen3.8-27B 文字服務可用；Qwen3-VL 8B 的合成持股辨識通過。
- 手機 390 px 操作驗證：登入、同意保存、1 張轉 1,000 股、草稿確認、重新載入持久化、新聞、綁定指令與既有頁面路徑通過。
- 首輪完整離線回歸：1,875 passed；目前最近完整回歸為 1,890 passed，新增停牌／復原／外網修正另有針對性測試。最終乾淨副本回歸尚待完成。

## 已修復問題

- 股票 registry materializer 忽略傳入 transaction，誤讀全域資料庫；已讓 repository 使用呼叫端 connection。
- SQLite backup 後未關閉 handle，Windows 無法 rename；已明確關閉連線後發布。
- 原 JWT 預設共享字串；改用每個部署獨立、持久化的隨機金鑰。
- URI 組合未妥善處理特殊字元；使用 Path.as_uri。
- 一般新聞清理依賴此次有新資料；改為每次非 dry-run 更新均執行。
- 圖片首次載入耗盡短回覆期限；新增預載及明確視覺 context 配置。
- 新啟動／排程的子程序逾時回收、逐階段 checkpoint 與過期草稿清理。

## 私有證據位置

| 檔案 | 內容 |
| --- | --- |
| var/migration/source-manifest.json | 初始 source 複製清單與摘要 |
| var/migration/historical-data-comparison.json | 106 表逐筆比對 |
| var/migration/doctor-full.json | 資料庫完整性 |
| var/migration/legacy-archive-copy.log | 舊專案封存複製結果：246,600 檔案成功；77 個舊快取／測試暫存目錄 ACL 拒讀 |
| var/qa/model-warmup.json | 模型預載結果 |
| var/qa/vision-report.json | 合成圖片辨識結果 |
| var/qa/browser-report.json | 手機流程結果 |
| var/test-results/release-check.xml | 1,890 項完整離線回歸 |
| var/qa/restore-rehearsal/verified-restore.json | 四庫於新位置復原、SHA／結構／外鍵／筆數一致 |
| var/qa/corporate-halts-report.json | 官方減資與面額變更停牌資料實測 |
| var/services/public-endpoint.json | 目前臨時 HTTPS 及權限檢查結果 |

歷史比對描述的是遷移初始快照；後續正式資料更新可正常新增／修正行情，不能要求更新後資料仍永遠與舊副本相同。

## 未完成的交付條件

- 最終乾淨副本完整回歸；股票 detail API 與 LINE 官方 payload 驗證已通過，最終部署後再確認。
- 新聞更新與整合啟動已通過；官方全市場資料完整性已通過，正式發布正在重新執行。9/4 Fugle 歷史逐筆缺漏仍未解決，不得宣稱完整價量分析就緒。
- 封存所有可讀原始檔案並核對；目前已知舊 pytest 快取讀取被拒。
- 四庫備份／新位置復原演練已通過；跨位置乾淨安裝及模型檔摘要核對待完成。
- 新 Git 工作樹、秘密資料檢查與 GitHub 交付。
- 對外 HTTPS／LINE webhook 的實際連線設定。
- 更新最終 Review Packet，逐項審查原需求後才標記完成。
