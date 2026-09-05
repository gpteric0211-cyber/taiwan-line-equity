# Fugle Intraday Supplemental Update Report

## 結果摘要

- 是否有 FUGLE_API_KEY：是
- 是否顯示完整 API key：否
- API key 狀態：****MQ==
- 是否 dry-run：否
- 是否 --write：是
- 是否寫 DB：是
- 模式：write
- 執行狀態：ok
- 嚴格完整股票數：3
- 擷取成功股票數（含待官方量核對）：3
- 待官方量核對股票數：0
- 來源延遲股票數：0
- 真正可重試失敗股票數：0
- 日曆閘門：2026-09-03 / TWSE official annual schedules / verified=是
- MIS 本交易時段證據：not_called
- Fugle API 是否獲准：是
- 閘門原因：official calendar passed; provider date and official EOD volume must still reconcile
- HTTP 請求數：22
- 每分鐘請求上限：50
- 主動限速等待次數：0
- 主動限速等待秒數：0.0

- 測試 / 匯入股票：3491, 2317, 2382
- response date 檢查基準：2026-09-03
- 是否執行 prune：是

本階段只處理指定股票的 Fugle intraday 補充資料，不是全市場更新，也沒有修改 GET API、前端或分析公式。
逐筆 side_inferred 是推論值：先用 bid/ask 判斷，沒有可用 bid/ask 才用前一筆成交價 tick rule。它不是交易所原始內外盤欄位。

## 各股票結果

### 3491

- capture outcome：complete
- trades HTTP status：200
- trades response date：2026-09-03
- trades data length：902
- trades normalized rows：902
- trades written rows：902
- trades data_quality：OK
- earliest_trade_time：09:00:15.340178
- latest_trade_time：13:30:00.000000
- trade_count：902
- ordering_note：ordered_by_time_serial
- coverage_note：appears_regular_session_covered
- previous_close：1480.0
- side_counts：`{"ASK": 373, "BID": 529}`
- method_counts：`{"PRICE_TICK_FROM_PREV_TRADE": 1, "PRICE_VS_BID_ASK": 901}`
- volumes HTTP status：200
- volumes response date：2026-09-03
- volumes data length：23
- volumes normalized rows：23
- volumes written rows：23
- volumes data_quality：OK
- bid/ask summary written rows：1
- bid_volume：1079
- ask_volume：683
- neutral_volume：17

### 2317

- capture outcome：complete
- trades HTTP status：200
- trades response date：2026-09-03
- trades data length：5122
- trades normalized rows：5122
- trades written rows：5122
- trades data_quality：OK
- earliest_trade_time：09:00:07.206225
- latest_trade_time：13:30:00.000000
- trade_count：5122
- ordering_note：ordered_by_time_serial
- coverage_note：appears_regular_session_covered
- previous_close：256.0
- side_counts：`{"ASK": 2096, "BID": 3026}`
- method_counts：`{"PRICE_TICK_FROM_PREV_TRADE": 1, "PRICE_VS_BID_ASK": 5121}`
- volumes HTTP status：200
- volumes response date：2026-09-03
- volumes data length：15
- volumes normalized rows：15
- volumes written rows：15
- volumes data_quality：OK
- bid/ask summary written rows：1
- bid_volume：18444
- ask_volume：9336
- neutral_volume：755

### 2382

- capture outcome：complete
- trades HTTP status：200
- trades response date：2026-09-03
- trades data length：2503
- trades normalized rows：2503
- trades written rows：2503
- trades data_quality：OK
- earliest_trade_time：09:00:01.989019
- latest_trade_time：13:30:00.000000
- trade_count：2503
- ordering_note：ordered_by_time_serial
- coverage_note：appears_regular_session_covered
- previous_close：342.0
- side_counts：`{"ASK": 800, "BID": 1703}`
- method_counts：`{"PRICE_TICK_FROM_PREV_TRADE": 1, "PRICE_VS_BID_ASK": 2502}`
- volumes HTTP status：200
- volumes response date：2026-09-03
- volumes data length：21
- volumes normalized rows：21
- volumes written rows：21
- volumes data_quality：OK
- bid/ask summary written rows：1
- bid_volume：6583
- ask_volume：4886
- neutral_volume：123

## 欄位 mapping

- trades：`fugle_intraday_trades.code / trade_date / trade_time / price / size / volume / bid / ask / serial / source / fetched_at / data_quality / raw_json`
- side inference：`side_inferred / side_label_zh / side_method / side_confidence / side_reason / prev_price / prev_price_source`
- volumes：`price_volume_distribution.stock_id / trade_date / price / volume_lots / volume_at_bid / volume_at_ask / neutral_volume_lots / source / data_quality / fetched_at`
- bid/ask summary：`daily_inner_outer_volume.stock_code / trade_date / bid_volume / ask_volume / neutral_volume / total_volume / source / data_quality / mapping_note`

## side inference 規則

- 優先使用成交價與 bid/ask：`price >= ask` 為 ASK / 外盤，`price <= bid` 為 BID / 內盤，中間價為 MID / 中性。
- bid/ask 缺漏時才用 tick fallback；第一筆使用本地 `history_price` 的前一交易日收盤價，後續使用前一筆成交價。
- 同價時沿用前一個已知 ASK/BID/MID；第一筆等於前收且無前一方向時為 MID / 中性。
- bid >= ask 時標記 INVALID_QUOTE / 報價異常，不用 tick fallback 掩蓋異常報價。
- 沒有前收時第一筆標記 UNKNOWN / 無法判斷，後續仍可用前一筆成交價分類。
- 不宣稱這是交易所原始內外盤，不做盤前集合競價特殊規則。
- trade side note：side_inferred is inferred from bid/ask first, then a tick rule fallback. It is supplemental and must not be treated as an exchange-original side.

## bid/ask mapping 注意事項

- 本階段保留 Fugle `volumeAtBid` / `volumeAtAsk` 原始來源欄位。
- bid/ask volume 不強制命名為內盤或外盤；中文 UI 命名前仍需確認 Fugle 欄位語意。
- mapping note：Fugle stock intraday-volumes documentation defines volumeAtBid as source-calculated inner volume and volumeAtAsk as source-calculated outer volume. The opening auction's first trade may be excluded, and these fields are not institutional net buy/sell.

## 保留規則

- Fugle intraday 補充資料保留最近 300 個交易日。
- `history_price` 官方日線仍維持既有 600 個交易日保留規則。
- 本階段 prune 僅作用於 Fugle 補充資料，不影響官方日線、法人、TDCC 或籌碼資料。

## Prune 結果

```json
{
  "retain_trading_days": 300,
  "cutoff_dates": {
    "fugle_intraday_trades": null,
    "fugle_intraday_capture_runs": null,
    "price_volume_distribution": null,
    "daily_inner_outer_volume": null
  },
  "deleted": {
    "fugle_intraday_trades": 0,
    "fugle_intraday_capture_runs": 0,
    "price_volume_distribution": 0,
    "daily_inner_outer_volume": 0
  }
}
```

## 寫入後表格統計

```json
{
  "fugle_intraday_trades": [
    {
      "code": "3491",
      "latest_date": "2026-09-03",
      "row_count": 902
    },
    {
      "code": "2317",
      "latest_date": "2026-09-03",
      "row_count": 5122
    },
    {
      "code": "2382",
      "latest_date": "2026-09-03",
      "row_count": 2503
    }
  ],
  "price_volume_distribution": [
    {
      "code": "3491",
      "latest_date": "2026-09-03",
      "row_count": 23
    },
    {
      "code": "2317",
      "latest_date": "2026-09-03",
      "row_count": 15
    },
    {
      "code": "2382",
      "latest_date": "2026-09-03",
      "row_count": 21
    }
  ],
  "daily_inner_outer_volume": [
    {
      "code": "3491",
      "latest_date": "2026-09-03",
      "row_count": 1
    },
    {
      "code": "2317",
      "latest_date": "2026-09-03",
      "row_count": 1
    },
    {
      "code": "2382",
      "latest_date": "2026-09-03",
      "row_count": 1
    }
  ]
}
```

## 下一步建議

- 目前建議先維持 2317 / 3491 / 2382 小範圍驗證。
- 若連續數個交易日穩定，再另開任務評估擴大到自選股。
- 不建議直接擴大到全市場 Fugle intraday 抓取。
