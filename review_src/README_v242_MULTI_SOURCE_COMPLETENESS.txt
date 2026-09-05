Taiwan 50 Dashboard v2.42 Multi-Source Data Completeness

本版重點不是防空白，而是改成多來源補資料與真實資料完整性 Gate：

1. 一鍵補齊改為多來源流程
   - TWSE OpenAPI：盤後價格、PE/PB/殖利率
   - FinMind：歷史K線、法人、融資融券
   - Yahoo Finance/yfinance：FinMind/TWSE 缺資料時補台股歷史K線與 PE/PB/EPS/殖利率
   - Corporate actions：除權息資料

2. 新增 API
   POST /api/update/ensure-complete
   會背景執行 TWSE / FinMind / Yahoo 多來源完整補齊。

3. 台灣50頁 Gate
   /api/quotes?mode=tw50 會先檢查 /api/debug/data-readiness。
   ready=false 時不顯示半成品列表。
   ready=true 才顯示正式 RSI 排序與支撐/賣壓分析。

4. Data Readiness 更嚴格
   現在不只檢查筆數，也檢查：
   - 最新價格日期
   - 最新歷史K線日期
   - 法人資料日期
   - 融資資料日期
   - 估值 / EPS 是否已由 TWSE 或 Yahoo 補齊
   - volume 單位是否確認為 shares

5. PE / EPS 不亂猜
   - TWSE 有 PE/PB/殖利率就用 TWSE。
   - Yahoo 有 EPS / trailingPE / priceToBook 就補齊。
   - 如果所有公開來源都沒有，ready=false，正式列表不顯示，避免假分析。

6. 資料來源限制
   系統會嘗試所有內建公開來源，但不能憑空創造資料。
   若 FinMind token 無效或公開來源暫停，data-readiness 會列出缺口。
   這是避免用錯資料，不是用空值假裝完整。
