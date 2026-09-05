Taiwan 50 Dashboard v2.41 — Data Readiness Gate

本版新增「真實資料完整性」檢查，不再只檢查欄位是否空白。

主要修正：
1. 新增 /api/debug/data-readiness?mode=tw50
   檢查台灣50每檔是否具備：
   - 最新價格
   - 至少120筆歷史K線
   - RSI5 / RSI10 / RSI14
   - MA20 / MA60 / ATR14
   - 可由K線估算支撐/賣壓
   - 20日法人與融資資料
   - 美股關聯表 coverage

2. 台灣50頁資料未完整時，不顯示半成品分析列表。
   會顯示未完成檔數、缺資料原因、補台灣50 120日按鈕。

3. 新增 force=1 debug 模式：
   /api/quotes?mode=tw50&force=1
   僅供檢查半成品輸出，一般畫面不預設使用。

4. PE / EPS 資料邏輯維持嚴格：
   官方來源無可靠值時隱藏，不亂猜，不當成資料完整性錯誤。

5. 美股報價不是台股資料完整性的必要條件。
   yfinance 抓不到時顯示「資料暫無」，不阻擋台股主分析。

建議使用流程：
1. 啟動 RUN_DASHBOARD.cmd
2. 先按「補台灣50 120日」
3. 等背景狀態完成
4. 打開 /api/debug/data-readiness?mode=tw50 確認 ready=true
5. ready=true 後台灣50才會顯示 RSI 排序列表
