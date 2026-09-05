Taiwan50 v2.33 自選股優先 + 個股詳細頁修正版

修正內容：
1. 首頁自選股模式改用 /api/quotes/watchlist 專用端點，不會等待台灣50分頁資料。
2. 啟動時預設不自動跑台灣50 FinMind 50檔背景更新，避免自選股排隊等待。
   - 需要啟動自動補台灣50，可在 .env 設定 AUTO_UPDATE_TW50_ON_START=1。
3. 自選股若存在，啟動時優先補自選股資料。
4. 列表整列可點擊，並新增「詳細分析」按鈕。
5. 新增 /stock/{code} 與 /detail/{code} 頁面路由，點擊可開啟個股詳細頁。
6. 詳細頁 API 現在可從 watchlist、eod_price、台灣50成分清單、resolve_stock 多層 fallback 找股票。
   - 即使台灣50該股尚未有 eod_price，也能打開詳細頁並顯示可用資料。
7. 保留 v2.32 FinMind Token 防呆：Token is illegal 時會自動停用 token 並重試免 token。

使用方式：
- 第一次使用先執行 SETUP_ONCE.cmd，之後執行 RUN_DASHBOARD.cmd。
- 進首頁預設顯示自選股。
- 點擊股票整列或「詳細分析」會開新分頁。
- 台灣50資料需要時再切到台灣50分頁或按更新按鈕。
