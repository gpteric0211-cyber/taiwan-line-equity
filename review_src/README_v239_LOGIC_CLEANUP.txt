Taiwan 50 Dashboard v2.39 Logic Cleanup and UI Fallback Edition

本版修正 v2.38 程式審查後確認需要處理的問題：

1. App title 版本號更新為 v2.39，避免 /docs 顯示舊版 v2.35。
2. build_status_change 改用風險語意 risk_rank，不再單純用 status_level 數字比較。
   - 可觀察 → 偏多但不追價：顯示「技術轉強但位置已高」
   - 偏多但不追價 → 可觀察：顯示「位置改善」
   - 警戒 → 偏多但不追價：顯示「技術轉強但仍不宜追價」
   - prev 為 None / 0 / 資料不足時，不再誤判惡化，改為新納入追蹤。
3. 除權息提示從 days_before=1 改為 days_before=3；除息後仍維持 5 個交易日 MA20 / 前低降權。
4. 刪除 app.py 內舊版 US_RELATION_MAP / DEFAULT_US_RELATIONS / legacy unused 函式。
   只使用 us_relations.py 的逐檔美股關聯表。
5. UI 欄位 fallback 再強化：
   - price 不再輸出 --，改為「需更新價格」
   - display_text 會把 -- 視為無效值並套用 fallback
   - RSI 顯示缺值時會顯示「RSI5 需更新 / RSI10 需更新 / RSI14 需更新」
6. 新增 /api/debug/ui-completeness
   用來掃描主列表輸出是否仍有空白、--、NaN、undefined 等無效欄位。
   本版內建資料庫檢查結果：tw50 checked=50, issue_count=0。
7. 保留 /api/debug/us-relations/coverage
   可檢查目前台灣50成分與 us_relations.py 對應表 coverage。
8. lending_daily 仍為第二階段預留表；本版不宣稱已啟用借券分析。
9. classify_market_environment 目前仍為 no_data 空殼；大盤 no_data 不進 blocker，也不在主列表顯示。

建議測試：
- /api/debug/ui-completeness?mode=tw50
- /api/debug/us-relations/coverage
- /api/quotes?mode=tw50
- /api/stock/2412/detail
