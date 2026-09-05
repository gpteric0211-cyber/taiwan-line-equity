# Cost Data Source Audit

- DB: `review_src\data\taiwan50.db`
- Generated at: `2026-06-26 13:29:27`
- Mode: SQLite read-only (`mode=ro`); no DB writes, no external API calls.

## Summary

| 成本類型 | 所需真實資料 | 本機是否存在 | table / column | 覆蓋日期 | 覆蓋股票數 | 是否可計算成本 | 建議名稱 | 可信度 | 下一步建議 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 外資估算成本 | 外資每日淨買賣 + OHLCV/trade_value/VWAP + 外資持股校準 | ready | institution_daily.foreign_net; history_price.amount; foreign_shareholding.ForeignInvestmentShares | 2023-12-28 ~ 2026-06-23 | 54 | 可做估算；仍非券商真實成本 | 外資公開資料估算成本 | medium | 可沿用現有估算，但前端需標示估算與校準來源 |
| 投信估算成本 | 投信每日淨買賣 + OHLCV/trade_value/VWAP + 投信持股校準 | partial | institution_daily.trust_net; history_price.amount; investment_trust_holding_not_found | 2023-12-28 ~ 2026-06-23 | 54 | 只能做近期買超均價估算；不可稱為真實持倉成本 | 投信近期買超估算成本 | low | 若要提高可信度，需要合法投信持股/庫存來源 |
| 融資每股借款 / 融資買進成本 | 融資餘額股數 + 融資金額 + 單位確認 + 價格參考 | partial | margin_daily.margin_balance; financing_amount=not_found | 2025-08-11 ~ 2026-06-23 | 54 | 不可計算融資每股借款或買進成本 | 融資餘額變化觀察 | low | 先補合法融資金額欄位與單位說明，再開成本公式 |
| 主力券商分點估算成本 | 券商分點每日買賣股數與金額，最好全分點或明確 Top-N 覆蓋 | unavailable | broker_branch_table_not_found | -- | 0 | 不可計算 | 券商分點成本暫停 | none | 取得合法穩定分點買賣資料後再實作 |

## Candidate Tables By Data Family

| category | candidate tables |
| --- | --- |
| foreign / trust | daily_chip_momentum, daily_inner_outer_volume, eod_price, foreign_shareholding, fugle_intraday_trades, history_price, institution_daily, intraday_quote_1m, intraday_quote_snapshot, intraday_time_sales_daily, mis_quote_snapshot, price_volume_distribution, price_volume_profile_daily, price_volume_score_daily, taiwan50_close_batch_items, taiwan50_close_volume_profile_points, tdcc_equity_summary, tdcc_holding_distribution |
| TDCC / holding | daily_chip_momentum, daily_inner_outer_volume, foreign_shareholding, price_volume_score_daily, tdcc_equity_summary, tdcc_holding_distribution, twse_daily_valuation |
| margin | daily_chip_momentum, eod_price, history_price, lending_daily, margin_daily |
| broker branch | -- |
| price / VWAP | daily_chip_momentum, daily_inner_outer_volume, eod_price, fugle_intraday_trades, history_price, intraday_quote_1m, intraday_quote_snapshot, intraday_time_sales_daily, mis_quote_snapshot, next_day_outlook_daily, price_volume_distribution, price_volume_profile_daily, price_volume_score_daily, stock_state_history, taiwan50_close_batch_items, taiwan50_close_batch_runs, taiwan50_close_volume_profile_points, twse_daily_valuation |

## Financing Amount Diagnostic

- `financing_amount_column`: `not_found`
- `financing_balance_column`: `margin_daily.margin_balance`
- `financing_balance_unit_guess`: `ambiguous_no_explicit_share_or_lot_unit`
- `amount_unit_guess`: `amount_column_not_found`
- `can_compute_financing_loan_per_share`: `False`
- `can_compute_estimated_financing_purchase_cost`: `False`
- Reason: local DB has financing balance/change, but no explicit financing amount / loan amount column. Ambiguous financing amount units must not be treated as computable cost.

## Broker Branch Diagnostic

```text
{'broker_table_exists': False, 'broker_tables': [], 'broker_history_days': 0, 'broker_codes_count': 0, 'has_branch_buy_sell_shares': False, 'has_branch_buy_sell_amount': False, 'has_all_branches_or_top_only': 'unavailable_no_broker_branch_table', 'can_compute_branch_vwap_cost': False, 'can_compute_main_force_estimated_cost': False, 'limitations': ['No local broker-branch table with branch buy/sell shares and amounts was found.']}
```

## Sample Code Matrix

| code | status | history | institution | margin | foreign holding | TDCC | trust holding | broker data | VWAP | financing_amount | can_compute_financing_loan_per_share |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 3491 | ok | 211 | 209 | 209 | 213 | 16 | False | False | 1369.61114820996 | not_found | False |
| 2317 | ok | 250 | 148 | 148 | 1147 | 16 | False | False | 259.89747254689433 | not_found | False |
| 6757 | ok | 211 | 208 | 207 | 213 | 16 | False | False | 65.56859314859032 | not_found | False |
| 2330 | ok | 600 | 599 | 150 | 1077 | 16 | False | False | 2400.8282660003756 | not_found | False |
| 2454 | ok | 151 | 141 | 141 | 1138 | 16 | False | False | 4324.226945140265 | not_found | False |

## Sample Details

### 3491

```text
{'code': '3491', 'history_price': {'row_count': 211, 'min_date': '2025-08-11', 'max_date': '2026-06-25'}, 'institution_daily': {'row_count': 209, 'min_date': '2025-08-11', 'max_date': '2026-06-23'}, 'margin_daily': {'row_count': 209, 'mi...
```

### 2317

```text
{'code': '2317', 'history_price': {'row_count': 250, 'min_date': '2025-06-16', 'max_date': '2026-06-25'}, 'institution_daily': {'row_count': 148, 'min_date': '2025-11-06', 'max_date': '2026-06-18'}, 'margin_daily': {'row_count': 148, 'mi...
```

### 6757

```text
{'code': '6757', 'history_price': {'row_count': 211, 'min_date': '2025-08-11', 'max_date': '2026-06-25'}, 'institution_daily': {'row_count': 208, 'min_date': '2025-08-11', 'max_date': '2026-06-19'}, 'margin_daily': {'row_count': 207, 'mi...
```

### 2330

```text
{'code': '2330', 'history_price': {'row_count': 600, 'min_date': '2023-12-28', 'max_date': '2026-06-25'}, 'institution_daily': {'row_count': 599, 'min_date': '2023-12-28', 'max_date': '2026-06-23'}, 'margin_daily': {'row_count': 150, 'mi...
```

### 2454

```text
{'code': '2454', 'history_price': {'row_count': 151, 'min_date': '2025-11-06', 'max_date': '2026-06-25'}, 'institution_daily': {'row_count': 141, 'min_date': '2025-11-06', 'max_date': '2026-06-09'}, 'margin_daily': {'row_count': 141, 'mi...
```

## Missing / NULL Blocking Fields

- `financing_amount`: not found in `margin_daily` or related local margin tables.
- `investment_trust_holding`: not found. TDCC rows are shareholding level distribution, not investor-category holdings.
- `broker_branch_buy_sell_amount`: not found.
- `broker_branch_buy_sell_shares`: not found.

## Full Table Scan

| table | rows | date column | date range | code column | code count | columns |
| --- | --- | --- | --- | --- | --- | --- |
| auth_sessions | 0 | -- | -- ~ -- | -- | -- | id, user_id, token_hash, user_agent, ip, expires_at, revoked_at, created_at |
| corporate_actions | 72 | date | 2025-06-03 ~ 2026-06-24 | code | 51 | code, date, action_type, cash_dividend, stock_dividend, source, is_confirmed, updated_at |
| daily_chip_momentum | 54 | date | 2026-06-18 ~ 2026-06-18 | stock_id | 54 | date, stock_id, close, previous_close, volume, volume_ratio_5d, volume_ratio_20d, foreign_net, trust_net, dealer_net, inst_total_net, margin_balance ... |
| daily_data_source_audit | 18 | run_date | -- ~ -- | code | 0 | id, started_at, finished_at, run_date, mode, code, source, status, message, rows_read, rows_written, payload_json |
| daily_inner_outer_volume | 1954 | trade_date | 2026-06-25 ~ 2026-06-25 | stock_code | 1954 | stock_code, trade_date, inner_volume, outer_volume, total_volume, total_volume_check, inner_ratio, outer_ratio, inner_outer_diff, close_price, prev_close_price, price_change ... |
| email_verifications | 0 | -- | -- ~ -- | code_hash | 0 | id, email, code_hash, purpose, expires_at, used_at, created_at |
| eod_price | 14145 | date | 2026-06-05 ~ 2026-06-25 | code | 1091 | date, code, name, open, high, low, close, volume, amount, change_value, transactions, source ... |
| fetch_status | 198 | updated_at | 1780889592.6802497 ~ 1782451763.6812782 | -- | -- | key, status, message, updated_at |
| foreign_shareholding | 55652 | date | 2021-10-18 ~ 2026-06-25 | code | 54 | date, code, ForeignInvestmentShares, source, updated_at |
| fugle_intraday_trades | 84156 | trade_date | 2026-06-25 ~ 2026-06-25 | code | 1950 | code, trade_date, trade_time, price, size, volume, bid, ask, serial, source, fetched_at, data_quality ... |
| history_price | 24607 | date | 2023-12-28 ~ 2026-06-25 | code | 1974 | date, code, open, high, low, close, volume, source, updated_at, volume_unit, amount, source_quality ... |
| institution_daily | 8815 | date | 2023-12-28 ~ 2026-06-23 | code | 54 | date, code, foreign_net, trust_net, dealer_net, source, updated_at, source_quality, fetched_at |
| intraday_quote_1m | 0 | trade_date | -- ~ -- | stock_id | 0 | trade_date, minute_ts, stock_id, open, high, low, close, volume, turnover, vwap, source, data_status ... |
| intraday_quote_snapshot | 0 | updated_at | -- ~ -- | stock_id | 0 | stock_id, quote_time, price, open, high, low, close, volume, turnover, vwap, change, change_pct ... |
| intraday_time_sales_daily | 0 | trade_date | -- ~ -- | code | 0 | code, trade_date, trade_time, price, volume_lots, side, source, source_quality, fetched_at, raw_json |
| lending_daily | 0 | date | -- ~ -- | code | 0 | date, code, lending_delta, lending_balance, source, updated_at |
| login_attempts | 0 | -- | -- ~ -- | -- | -- | id, ip, email, success, reason, attempted_at |
| margin_daily | 7783 | date | 2025-08-11 ~ 2026-06-23 | code | 54 | date, code, margin_delta, margin_balance, short_delta, short_balance, source, updated_at, source_quality, fetched_at |
| mis_quote_snapshot | 3122 | snapshot_date | 2026-06-10 ~ 2026-06-26 | code | 7 | snapshot_ts, snapshot_date, code, price, change_pct, cumulative_volume, volume_delta_since_last_poll, trade_time, fetched_at, quote_source, is_estimated_tick_volume |
| next_day_outlook_daily | 11 | calc_date | 2026-06-09 ~ 2026-06-11 | code | 5 | calc_date, code, generated_at, model_version, probability_up, opening_probability, sustainability_probability, label, opening_label, sustainability_label, confidence, consistency ... |
| price_volume_distribution | 34680 | trade_date | 2026-06-25 ~ 2026-06-25 | stock_id | 1954 | id, stock_id, trade_date, price, volume_lots, volume_shares, total_volume_lots, snapshot_time, created_at, updated_at, buy_volume_lots, sell_volume_lots ... |
| price_volume_profile_daily | 8452 | date | 2025-08-20 ~ 2026-06-25 | code | 54 | date, code, source_level, source_name, source_hash, trade_scope, volume_unit, total_volume_shares, eod_volume_shares, volume_diff_pct, price_level_count, min_price ... |
| price_volume_score_daily | 512 | date | 2026-06-09 ~ 2026-06-25 | code | 54 | date, code, system_version, close, source_name, source_level, source_hash, coverage_days, required_days, quality, quality_reason, status ... |
| stock_industry_profile | 1974 | updated_at | 1781922758.890585 ~ 1781922758.890585 | code | 1974 | code, name, market, industry, industry_code, source, quality, updated_at |
| stock_state_history | 298 | calc_date | 2026-06-05 ~ 2026-06-11 | code | 50 | code, calc_date, calc_ts, main_status, status_level, display_signal, close, support_text, resistance_text, reasons_json |
| stock_theme_profile | 8910 | updated_at | 1782138772.6559417 ~ 1782138772.6559417 | code | 1974 | code, tag_type, tag_name, source, source_key, quality, updated_at |
| taiwan50_close_batch_items | 0 | data_date | -- ~ -- | symbol | 0 | data_date, symbol, name, rank_no, close_price, reference_price, support_zone, pressure_zone, poc_price, poc_volume, source_status, data_quality ... |
| taiwan50_close_batch_runs | 0 | data_date | -- ~ -- | -- | -- | data_date, updated_at, timezone, update_mode, is_realtime, item_count, error_count, source_status, reason, created_at |
| taiwan50_close_volume_profile_points | 0 | data_date | -- ~ -- | symbol | 0 | data_date, symbol, price, volume, source, updated_at |
| tdcc_equity_summary | 54 | date | 2026-06-18 ~ 2026-06-18 | code | 54 | date, code, total_holders, total_shares, small_10_share_pct, big_400_share_pct, big_1000_share_pct, small_10_change_4w, big_400_change_4w, big_1000_change_4w, holder_count_change_4w, holder_count_change_4w_pct ... |
| tdcc_holding_distribution | 810 | date | 2026-06-18 ~ 2026-06-18 | code | 54 | date, code, level, holders, shares, percent, source, updated_at |
| twse_daily_valuation | 1966 | data_date | 2026-06-12 ~ 2026-06-22 | symbol | 1966 | data_date, symbol, name, close_price, dividend_yield, dividend_year, pe_ratio, pb_ratio, financial_year_quarter, source, source_status, updated_at ... |
| user_watchlist | 0 | updated_at | -- ~ -- | stock_code | 0 | id, user_id, stock_code, stock_name, sort_order, added_at, updated_at |
| users | 0 | updated_at | -- ~ -- | -- | -- | id, email, hashed_password, is_verified, is_active, created_at, updated_at, last_login_at |
| valuation | 14011 | date | 2026-06-05 ~ 2026-06-25 | code | 1080 | date, code, dividend_yield, pe, pb, source, updated_at, eps, eps_source |
| watchlist | 3 | updated_at | 1782096030.624074 ~ 1782441951.745767 | code | 3 | code, name, sort_order, updated_at |

## Conclusion

- 外資估算成本: `ready` for public-data estimation when `foreign_shareholding` and trade value are present, but it remains an estimate.
- 投信估算成本: `partial`; `trust_net` exists, but `investment_trust_holding_not_found` blocks high-confidence holding-cost calibration.
- 融資每股借款 / 融資買進成本: `partial/unavailable for cost`; `margin_balance` exists, but `financing_amount` is absent, so no loan-per-share or purchase-cost calculation should be implemented yet.
- 主力券商分點估算成本: `unavailable`; no local broker-branch buy/sell amount and shares table was found.
