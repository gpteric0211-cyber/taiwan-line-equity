台灣50 v2.31 個股詳細頁更新

新增功能：
1. 列表頁點擊個股代號/名稱，會在新分頁開啟 /static/detail.html?code=股票代號。
2. 個股詳細頁包含：
   - 技術數據：RSI、MACD、KD、MA20/MA60、ATR、成交量、量比、OBV、前高前低等。
   - 每個數據都有中文解釋與可能風險提示，不顯示內部分數。
   - 估值：PE、PB、殖利率、EPS 估算值。
   - 籌碼成本：外資、投信、自營商短期估算成本、法人/POC 共振區。
   - 支撐壓力：沿用 v2.30 的狀態型支撐壓力與區間顯示。
   - 相關美股與產業 ETF：用 yfinance 抓 Yahoo Finance 價格。
3. 台股對應美股配對邏輯：個股 + 產業 ETF。
   - 2330：TSM、NVDA、AMD、ASML、AVGO、SMH、SOXX。
   - 2449：TSM、NVDA、AMD、MRVL、AVGO、SMH、SOXX。
   - 2317、2382、2454 也有預設關聯清單。
   - 其他股票使用半導體/科技通用清單。
4. requirements.txt 已加入 yfinance。

注意：
- yfinance 不需要 API key，但價格可能是延遲或最近收盤，適合作為海外風險情緒參考。
- 若第一次啟動缺 yfinance，請執行 SETUP_ONCE.cmd 重新安裝 requirements.txt。
- 成交量仍維持 shares（股）作為 DB 與計算單位，畫面需要顯示張時再除以 1000。
