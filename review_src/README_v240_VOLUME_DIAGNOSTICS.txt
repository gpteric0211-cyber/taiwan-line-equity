Taiwan 50 Dashboard v2.40

本版修正：
1. App title 更新為 v2.40。
2. UI 完整度檢查加入 volume 單位自檢，會回傳 history_price / eod_price 的 volume 推斷單位與成交金額中位數。
3. 若 volume 單位不一致或無法判斷，debug 會回傳 warning；這代表量比、OBV、流動性、Volume Profile 不應作強判斷，需先補資料或確認單位。
4. 詳細頁新增「阻礙 / 警示條件」區塊，完整展開 score_stock 的 blockers / warnings，並標明主狀態仍以 practical main_status 為準。
5. 主列表新增資料完整度標籤：資料完整 / 部分待補 / 需更新K線。
6. UI completeness 會檢查主列表是否仍有空白、--、NaN、undefined，並檢查 legacy_signal 是否疑似被主畫面誤用。

注意：打包內附 DB 可能沒有完整歷史資料，因此 /api/debug/volume_units 可能顯示 unknown。實際使用時需先更新盤後與歷史 K 線後再檢查。
