# 資料保存規則

本次未改動原歷史行情保存期限。下表是程式與遷移設定的索引；真正清理行為仍由各 repository／schema 的原邏輯決定。共享資料表若被多個清理流程涵蓋，有效期限依最早適用的清理界線，不能把單一設定誤當成全庫政策。

| 類型 | 原規則／本次規則 | 權威來源 |
| --- | --- | --- |
| 市場基礎資料 | 600 個不同交易日期 | `review_src/core/market_foundation_schema.py` |
| 市場審計 | 730 日 | 同上 |
| Fugle intraday | 各表 300 個不同記錄交易日期 | `review_src/core/fugle_intraday_schema.py` |
| 推估籌碼成本 | 每代號／類型 720 個不同日期 | `review_src/repository/estimated_chip_cost_repository.py` |
| 1 分鐘資料設定 | 30 日；仍依所屬清理器執行 | `review_src/core/config.py` |
| 一般新聞 radar | 30 日 | `review_src/repository/news_radar_repository.py` |
| MOPS 重大訊息原始表 | 730 個不同公告日期 | `review_src/repository/official_event_repository.py` |
| 外部市場新聞事件表 | 730 個日曆日 | `review_src/repository/external_event_repository.py` |
| 官方公司行動 | 原有永久事件表 | `review_src/core/corporate_action_schema.py` |
| 新增重大新聞 metadata | 永久 | `review_src/core/material_news_schema.py` |
| LINE 原始對話／摘要 | 保留原 .env.line_bot 設定及其上限／同意門檻 | `review_src/core/line_memory_config.py` |
| 新增已確認持股 | 使用者刪除前持續保存 | `review_src/repository/portfolio_repository.py` |
| 新增持股草稿 | 1 小時；保存前仍需本人確認 | 同上 |
| LINE 綁定碼／下一張持股圖片意圖 | 10 分鐘、一次性 | 同上 |

重大新聞永久保存標題、日期、來源網址及分類／驗證狀態等 metadata，不複製完整新聞全文。MOPS 官方重大訊息及官方減資／面額變更停牌公告也會先歸檔，再依原規則清理詳細來源表。其他新聞以明確公司行動／重大訊息字詞分類，例如除權、除息、減資、股票分割、合併、下市與停止交易；保存原來源和驗證狀態。官方公司行動仍沿用原結構，沒有以模型猜測除權日期或自動更改持股數量。

清理一般新聞前先歸檔重大事件；即使此次來源沒有新資料也會執行清理，避免一般新聞無限累積。這是修復清理執行條件，並未縮短原期限。

備份與封存是獨立檔案。刪除線上個人紀錄不會逐一改寫既有離線備份；應限制備份存取，依實際備份政策處理舊副本。加密金鑰必須與資料副本分開保管。

可考慮的後續調整是把所有歷史表的保存政策集中成可執行的 registry，並為重要事件加入人工標記入口。本次只建立 `config/retention-policy.json` 作政策索引；任何歷史期限調整都需先由專案擁有者決定。
