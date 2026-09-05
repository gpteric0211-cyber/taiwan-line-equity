v2.38 核心邏輯與欄位防空白修正版

修正重點：
1. scoring.py 移除 no_data 進 blockers 的舊邏輯；缺大盤/缺族群不再讓股票變不可觀察。
2. 舊 signal 降級為 legacy_signal，前端主畫面只使用 practical main_status / display_signal。
3. yfinance_quote 失敗時也回傳完整欄位：price/change/change_pct/date/currency/error/source，避免前端空白或 NaN。
4. 前端遇到美股 ok:false 顯示「資料暫無」，不再空白。
5. interpret_special_us_asset 補 USO / BDRY / BOAT 特殊解讀。
6. /api/debug/us-relations/coverage 已可檢查台灣50對應表覆蓋率。
7. 詳細頁技術計算失敗時逐項提示，不讓整區空白。
8. 主列表與詳細頁支撐/賣壓皆有可讀 fallback，不顯示空字串。
9. 外部/狀態變化只有有意義才顯示，隱藏大盤待確認、新納入追蹤、資料不足暫停比較等雜訊。

檢查結果：
- app.py / scoring.py / us_relations.py 語法編譯通過。
- index.html / detail.html JS 語法檢查通過。
- us_relations coverage：目前台灣50 missing=[]。
