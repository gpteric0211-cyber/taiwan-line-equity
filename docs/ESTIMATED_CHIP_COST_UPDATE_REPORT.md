# Estimated Chip Cost Update Report

- Generated at: 2026-07-01 15:52:14
- DB: `review_src\data\taiwan50.db`
- Codes: 3491, 2317, 6757, 2330, 2454
- Requested days: 720
- writes_db: true
- generated_rows: 8622
- write_count: 8622
- prune_deleted: 0
- max_retained_trade_dates_per_code_type: 602

## Cost Type Counts

| cost_type | generated | valid_cost_rows |
| --- | ---: | ---: |
| foreign_estimated | 1437 | 1303 |
| trust_estimated | 1437 | 1053 |
| margin_incremental_estimated | 1437 | 0 |
| margin_reliable_cost | 1437 | 0 |
| main_force_reference_zone | 1437 | 0 |
| main_force_branch_cost | 1437 | 0 |

## Status Counts

| key | count |
| --- | ---: |
| foreign_estimated:estimated | 1303 |
| foreign_estimated:unavailable | 134 |
| main_force_branch_cost:missing_required_source | 1437 |
| main_force_reference_zone:unavailable | 1437 |
| margin_incremental_estimated:unavailable | 1437 |
| margin_reliable_cost:missing_required_source | 1437 |
| trust_estimated:estimated | 1053 |
| trust_estimated:insufficient_data | 250 |
| trust_estimated:unavailable | 134 |

## Margin Unit Gate

- margin_balance_unit: `unit_unknown` unless a legal importer provides verified units.
- unit_unknown_count: 1437
- margin_incremental_estimated_valid_count: 0

## Safety Confirmations

- Moving-average inventory method is used; FIFO is not claimed.
- `margin_balance` is not used as reliable financing cost.
- Missing financing amount rows produce `missing_required_source` for `margin_reliable_cost`.
- Broker branch main-force cost requires branch shares and amounts; missing data produces `missing_required_source`.
- Main-force reference zone uses existing price-volume / POC rows only; it is `proxy_only` when usable and `unavailable` when no valid existing row is available.
- No volume residual method is used.
- No external API, Goodinfo scraping, captcha bypass, or Cloudflare bypass is used.

## Sample Rows

### 2317

| date | type | status | cost | confidence | debug |
| --- | --- | --- | ---: | --- | --- |
| 2025-06-16 | foreign_estimated | unavailable | -- | unavailable | missing_institution_net |
| 2025-06-17 | foreign_estimated | unavailable | -- | unavailable | missing_institution_net |
| 2025-06-18 | foreign_estimated | unavailable | -- | unavailable | missing_institution_net |
| 2025-06-19 | foreign_estimated | unavailable | -- | unavailable | missing_institution_net |
| 2025-06-20 | foreign_estimated | unavailable | -- | unavailable | missing_institution_net |
| 2025-06-23 | foreign_estimated | unavailable | -- | unavailable | missing_institution_net |

### 2330

| date | type | status | cost | confidence | debug |
| --- | --- | --- | ---: | --- | --- |
| 2023-12-28 | foreign_estimated | estimated | 591.6285 | medium | moving_average_inventory_estimate |
| 2023-12-29 | foreign_estimated | estimated | 591.6286 | medium | moving_average_inventory_estimate |
| 2024-01-02 | foreign_estimated | estimated | 591.6284 | medium | moving_average_inventory_estimate |
| 2024-01-03 | foreign_estimated | estimated | 591.6284 | medium | moving_average_inventory_estimate |
| 2024-01-04 | foreign_estimated | estimated | 591.6284 | medium | moving_average_inventory_estimate |
| 2024-01-05 | foreign_estimated | estimated | 591.6284 | medium | moving_average_inventory_estimate |

### 2454

| date | type | status | cost | confidence | debug |
| --- | --- | --- | ---: | --- | --- |
| 2025-11-06 | foreign_estimated | estimated | 1290.0 | medium | moving_average_inventory_estimate |
| 2025-11-07 | foreign_estimated | estimated | 1290.0 | medium | moving_average_inventory_estimate |
| 2025-11-10 | foreign_estimated | estimated | 1290.0 | medium | moving_average_inventory_estimate |
| 2025-11-11 | foreign_estimated | estimated | 1290.0 | medium | moving_average_inventory_estimate |
| 2025-11-12 | foreign_estimated | estimated | 1290.0 | medium | moving_average_inventory_estimate |
| 2025-11-13 | foreign_estimated | estimated | 1290.0 | medium | moving_average_inventory_estimate |

### 3491

| date | type | status | cost | confidence | debug |
| --- | --- | --- | ---: | --- | --- |
| 2025-08-11 | foreign_estimated | estimated | 339.8054 | medium | moving_average_inventory_estimate |
| 2025-08-12 | foreign_estimated | estimated | 339.9061 | medium | moving_average_inventory_estimate |
| 2025-08-13 | foreign_estimated | estimated | 339.9061 | medium | moving_average_inventory_estimate |
| 2025-08-14 | foreign_estimated | estimated | 340.0122 | medium | moving_average_inventory_estimate |
| 2025-08-15 | foreign_estimated | estimated | 340.0447 | medium | moving_average_inventory_estimate |
| 2025-08-18 | foreign_estimated | estimated | 340.0465 | medium | moving_average_inventory_estimate |

### 6757

| date | type | status | cost | confidence | debug |
| --- | --- | --- | ---: | --- | --- |
| 2025-08-11 | foreign_estimated | estimated | 84.9927 | medium | moving_average_inventory_estimate |
| 2025-08-12 | foreign_estimated | estimated | 84.9927 | medium | moving_average_inventory_estimate |
| 2025-08-13 | foreign_estimated | estimated | 84.9927 | medium | moving_average_inventory_estimate |
| 2025-08-14 | foreign_estimated | estimated | 84.9906 | medium | moving_average_inventory_estimate |
| 2025-08-15 | foreign_estimated | estimated | 84.9906 | medium | moving_average_inventory_estimate |
| 2025-08-18 | foreign_estimated | estimated | 84.9859 | medium | moving_average_inventory_estimate |

