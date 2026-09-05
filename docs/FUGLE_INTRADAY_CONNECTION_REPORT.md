# Fugle Intraday Connection Report

## 結論摘要

- 是否有 FUGLE_API_KEY：是
- 是否有顯示完整 API key：否
- API key 狀態：****Nw==
- default TLS verify 是否成功：是
- certifi TLS verify 是否成功：否
- 是否仍有 CERTIFICATE_VERIFY_FAILED：否
- 是否成功呼叫 Fugle API：是
- 是否遇到 API key / 權限 / rate limit 問題：not_observed
- 是否遇到 TLS / 憑證問題：否
- 是否建議進入下一階段 DB 匯入設計：是
- JSON runtime 是否有保存：是

本階段未寫 DB、未做全市場、未新增排程、未新增正式 parser、未改 GET API、未改前端、未改分析邏輯。

## TLS 診斷

- Python 版本：`3.11.9 (tags/v3.11.9:de54cf5, Apr  2 2024, 10:12:12) [MSC v.1938 64 bit (AMD64)]`
- requests 版本：`2.34.2`
- urllib3 版本：`2.7.0`
- certifi 可 import：是
- certifi.where()：`<python-environment>\site-packages\certifi\cacert.pem`
- OpenSSL version：`OpenSSL 3.0.13 30 Jan 2024`
- OS：`Windows 10 (Windows-10-10.0.26200-SP0)`
- REQUESTS_CA_BUNDLE：否
- SSL_CERT_FILE：否
- API base：`https://api.fugle.tw/marketdata/v1.0`

### default

- URL：`https://api.fugle.tw/marketdata/v1.0/stock/intraday/volumes/2317`
- ok：是
- HTTP status：200
- status class：ok
- error type：``
- error：``

## 各股票結果

### 2317

#### trades

- URL：`https://api.fugle.tw/marketdata/v1.0/stock/intraday/trades/2317`
- HTTP status：200
- status class：ok
- JSON parse：是
- response date：`2026-06-25`
- response symbol：`2317`
- data length：50
- sample fields：`bid, ask, price, size, volume, time, serial`
- response date 是否為今日：是
- error type：``
- error：``

- trades 是否有 date / symbol / data：是
- trades 是否有 time / price / size：是
- trades 是否足以作 time-sales：是
- side 判斷：`side_inference_possible_but_not_approved`

#### volumes

- URL：`https://api.fugle.tw/marketdata/v1.0/stock/intraday/volumes/2317`
- HTTP status：200
- status class：ok
- JSON parse：是
- response date：`2026-06-25`
- response symbol：`2317`
- data length：9
- sample fields：`price, volume, volumeAtBid, volumeAtAsk`
- response date 是否為今日：是
- error type：``
- error：``

- volumes 是否有 date / symbol / data：是
- volumes 是否有 price / volume：是
- volumes 是否足以作分價量表：是
- volumes 是否提供 volumeAtBid / volumeAtAsk：是
- 內外盤狀態：`inner_outer_available_from_volumes`

### 3491

#### trades

- URL：`https://api.fugle.tw/marketdata/v1.0/stock/intraday/trades/3491`
- HTTP status：200
- status class：ok
- JSON parse：是
- response date：`2026-06-25`
- response symbol：`3491`
- data length：50
- sample fields：`bid, ask, price, size, volume, time, serial`
- response date 是否為今日：是
- error type：``
- error：``

- trades 是否有 date / symbol / data：是
- trades 是否有 time / price / size：是
- trades 是否足以作 time-sales：是
- side 判斷：`side_inference_possible_but_not_approved`

#### volumes

- URL：`https://api.fugle.tw/marketdata/v1.0/stock/intraday/volumes/3491`
- HTTP status：200
- status class：ok
- JSON parse：是
- response date：`2026-06-25`
- response symbol：`3491`
- data length：20
- sample fields：`price, volume, volumeAtBid, volumeAtAsk`
- response date 是否為今日：是
- error type：``
- error：``

- volumes 是否有 date / symbol / data：是
- volumes 是否有 price / volume：是
- volumes 是否足以作分價量表：是
- volumes 是否提供 volumeAtBid / volumeAtAsk：是
- 內外盤狀態：`inner_outer_available_from_volumes`

### 2382

#### trades

- URL：`https://api.fugle.tw/marketdata/v1.0/stock/intraday/trades/2382`
- HTTP status：200
- status class：ok
- JSON parse：是
- response date：`2026-06-25`
- response symbol：`2382`
- data length：50
- sample fields：`bid, ask, price, size, volume, time, serial`
- response date 是否為今日：是
- error type：``
- error：``

- trades 是否有 date / symbol / data：是
- trades 是否有 time / price / size：是
- trades 是否足以作 time-sales：是
- side 判斷：`side_inference_possible_but_not_approved`

#### volumes

- URL：`https://api.fugle.tw/marketdata/v1.0/stock/intraday/volumes/2382`
- HTTP status：200
- status class：ok
- JSON parse：是
- response date：`2026-06-25`
- response symbol：`2382`
- data length：21
- sample fields：`price, volume, volumeAtBid, volumeAtAsk`
- response date 是否為今日：是
- error type：``
- error：``

- volumes 是否有 date / symbol / data：是
- volumes 是否有 price / volume：是
- volumes 是否足以作分價量表：是
- volumes 是否提供 volumeAtBid / volumeAtAsk：是
- 內外盤狀態：`inner_outer_available_from_volumes`

## 欄位 mapping 初稿

time-sales:

- trade_date / date
- code / symbol
- time
- price
- size
- volume
- bid
- ask
- serial
- source = FUGLE
- fetched_at
- data_quality

price-volume:

- trade_date / date
- code / symbol
- price
- volume
- volumeAtBid
- volumeAtAsk
- source = FUGLE
- fetched_at
- data_quality

inner-outer:

- trade_date / date
- code / symbol
- buy_volume_lots 或 volumeAtAsk
- sell_volume_lots 或 volumeAtBid
- total_volume_lots 或 volume
- neutral_volume_lots
- source = FUGLE
- fetched_at
- data_quality

注意：Fugle 官方文件已定義 volumeAtBid 為內盤累計量、volumeAtAsk 為外盤累計量；兩者仍不是法人／主力買賣超，且可能排除開盤第一筆集中撮合。

## 後續正式 scraper 的日期、執行時間與保留規則

1. 本階段不寫 DB，因此不會產生正式資料保留問題。
2. 若後續進入正式 Fugle scraper，所有正式資料表必須記錄交易日期。
3. 交易日期欄位優先使用 `trade_date`；若既有表使用 `date`，必須維持相容並在文件中說明。
4. Fugle intraday trades / volumes 可能只保留當日盤中資料，隔日可能無法取得前一交易日完整明細。
5. 正式 Fugle scraper 必須在當天盤後執行，例如 15:00 後。
6. 正式流程不得假設可以隔日補抓前一日 intraday trades / volumes。
7. 若當日盤後抓取失敗，應標記 `SOURCE_DELAYED` / `FAILED` / `PARTIAL`，並保留錯誤報告。
8. Fugle 補充資料正式寫入 DB 後，只保留最近 300 個交易日。
9. 300 交易日 prune 只限 Fugle 補充資料表，不得刪 `history_price`、官方日線、法人、TDCC、籌碼資料。
10. 官方日線 `history_price` 仍維持既有 600 交易日保留規則。
11. prune 判斷必須以 `trade_date` / `date` 為準，不得以 `fetched_at` 取代交易日期。

## 風險與下一步

- API key 權限可能不足時，先確認 response 訊息，不要猜測付費方案。
- 若遇到 429，需另設 rate limit 與重試策略。
- 若 TLS 失敗，先修 Python / certifi / CA bundle，不進 DB 匯入。
- Fugle intraday 可能只保留當日盤中 / 當日盤後資料；正式 scraper 必須當天盤後執行。
- 對外呈現時固定保留 Fugle 官方 inner/outer 語意，並明示不是投資人身分或法人買賣超。
- 本階段未寫 DB、未做排程、未做全市場。

若 default/certifi TLS 成功且 2317 / 3491 / 2382 的 trades / volumes 都成功，下一步才可另開 Fugle 補充資料 DB schema / parser / 300 交易日保留設計。

若 TLS 仍失敗，應先修 Windows Python CA / certifi / requests 憑證鏈，不要進 DB 匯入。
