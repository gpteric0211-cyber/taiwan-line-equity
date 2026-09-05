Taiwan 50 Dashboard v2.43 Practical RR / Chip Dates / Market Proxy

本版修正 v2.42 審查提出的核心問題：

1. 修正 rr_poor 永遠 False
   - classify_practical_status() 現在會用「上方賣壓區下緣」與「停損基準」計算 risk_reward_ratio。
   - RR < 1.2 會加入「風險報酬不足」理由，並參與「偏多但不追價 / 警戒」判斷。
   - 若已突破原賣壓區，不硬算舊 RR，改以量價確認與後續新目標觀察。

2. 修正單一嚴重警告仍被判可觀察
   - 新增 has_severe_warning()。
   - 跌破 MA20、RSI 偏弱、已跌破支撐、跳空高開收黑、無量突破賣壓、突破後收弱等，即使只有一條，也會進入警戒，不再直接可觀察。

3. 籌碼日期回傳
   - build_institution_info() 回傳 note/date/freshness。
   - latest_margin_info() 回傳融資融券日期。
   - API 回傳 institution_date / margin_date，詳細頁會顯示籌碼資料日期。

4. 禁止 action_hint 細分
   - 禁止 + 跌破支撐：提示需等量縮止跌並站回支撐。
   - 禁止 + 停損距離過大：提示需等價格靠近支撐或形成較近防守點。

5. 大盤 proxy 第一階段接入
   - classify_market_environment() 優先使用 0050；若無 0050，使用 2330 作 proxy。
   - 僅作外部提示，不推翻個股主狀態，不作 no_data blocker。

6. scoring.py legacy 註解
   - eligible_for_watchlist / signal / technical_total 明確標示為 legacy。
   - 主畫面正式狀態仍由 app.py classify_practical_status() 決定。

7. 版本標示更新
   - FastAPI title 與手機首頁 title 更新為 v2.43。

驗證：
- app.py / scoring.py / us_relations.py 語法編譯通過。
- /api/debug/us-relations/coverage 可執行。
- /api/debug/ui-completeness?mode=tw50 可執行。
- /api/debug/data-readiness?mode=tw50 可執行。
- /api/debug/volume_units 可執行。
- /api/stock/2412/detail 可回傳。
