Taiwan 50 Dashboard v2.44 Margin / Market Divergence / External Hint Fix

本版修正 v2.43 後續審查提出的實戰邏輯缺口：

1. 融資資料正式進入主狀態判斷
   - latest_margin_info() 的 margin_delta 不再只是回傳給前端。
   - 若融資增加且股價低於 MA20，會加入「融資增加但股價偏弱」警示。
   - 這會觸發 severe warning，不再被誤判為可觀察。

2. 保守觀察 badge 修正
   - apply_external_adjustment() 原本使用 badge or 'warning'，若原 badge='positive' 會導致保守觀察仍顯示正面顏色。
   - 現在保守觀察會直接指定 warning badge。

3. 大盤 proxy 加入簡易分化判斷
   - classify_market_environment() 現在同時看 0050 與 2330 proxy。
   - 若 0050 與 2330 方向明顯分歧，會顯示「指數分化｜0050 ...、2330 ...」。
   - 注意：這仍是 proxy，不等同完整加權/櫃買指數模型。

4. 法人連續性補強
   - build_institution_info() 從只看最新一筆，改為讀最近 5 筆。
   - 可顯示投信連買N日、投信連賣N日、外資連買N日、外資連賣N日。

5. RR ratio 初始化安全化
   - rr_ratio / rr_note 在 classify_practical_status() 開頭初始化為 None。
   - 不再使用 locals() 判斷。

6. 大盤偏弱 + 個股警戒 的提示補強
   - state=weak 時，若個股已警戒，會給「警戒留意市場走弱」。
   - bearish 時仍是「警戒加重」。

7. Action Hint 加入外部環境文字
   - 可觀察 + 保守觀察：提示外部環境偏弱，降低追價意願。
   - 警戒 + 外部走弱：提示提高風控。
   - 偏多但外部逆風：提示即使個股偏多也不宜追價。

8. 詳細頁 RR 顯示改用 practical 的 RR
   - score_signal.risk_reward_ratio 改用 practical risk_reward_ratio。
   - legacy_risk_reward_ratio 另外保留供 debug。

9. 版本更新
   - FastAPI title 與手機首頁 title 更新為 v2.44。

