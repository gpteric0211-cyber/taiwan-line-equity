# Local Values Export: 2317 鴻海

- Run time: 2026-06-19T01:06:32
- Stock code: 2317
- Data trade date: 2026-06-18
- Price source: FinMind
- DB path: `<repo-root>\review_src\data\taiwan50.db`

## Values

| Field | Value | Local source / formula |
| --- | ---: | --- |
| open | 270.5 | history_price.open |
| high | 271.5 | history_price.high |
| low | 268.5 | history_price.low |
| close | 268.5 | history_price.close |
| volume | 68,996,866 | history_price.volume (shares) |
| change | -3.5 | local close - previous close |
| change_percent | -1.29% | local change / previous close |
| MA5 | 267.5 | scoring.calculate_indicators ma5 |
| MA10 | 269.05 | scoring.calculate_indicators ma10 |
| MA20 | 273.68 | scoring.calculate_indicators ma20 |
| MA60 | 238.26 | scoring.calculate_indicators ma60 |
| RSI5 | 46.66 | scoring.calculate_indicators rsi5 |
| RSI10 | 50.13 | scoring.calculate_indicators rsi10 |
| RSI14 | 52.55 | scoring.calculate_indicators rsi14 |
| KD_K | 44.45 | scoring.calculate_indicators k |
| KD_D | 35.92 | scoring.calculate_indicators d |
| MACD_DIF | 5.3805 | scoring.calculate_indicators dif |
| MACD_DEA | 8.4848 | scoring.calculate_indicators macd_signal |
| MACD_histogram | -3.1043 | scoring.calculate_indicators osc |
| BIAS | N/A | not implemented in current system |
| Bollinger_upper | 305.06 | scoring.calculate_indicators boll_upper |
| Bollinger_mid | 273.68 | scoring.calculate_indicators boll_mid |
| Bollinger_lower | 242.29 | scoring.calculate_indicators boll_lower |
| DMI_plus_DI | N/A | not implemented in current system |
| DMI_minus_DI | N/A | not implemented in current system |
| ADX | N/A | not implemented in current system |

## Source Metadata

| Metadata | Value |
| --- | --- |
| source_type: watchlist_realtime | Would use MIS only if live price is numeric, positive, and within 10% of previous close. |
| source_type: taiwan50_batch | Uses local close-batch/history data and must not use MIS intraday. |
| source_type: detail_realtime | Uses watchlist realtime boundary only when the stock is in watchlist. |
| source_type: detail_batch | Uses close-batch/history boundary for non-watchlist detail. |
| price_source | FinMind |
| technical_source | history_price |
| data_quality | ok |
| data_trade_date | 2026-06-18 |
| fetched_at | 2026-06-19T01:06:32 |
| actual_ohlcv_source | FinMind |
| using_twse_official_close | False |
| using_finmind_fallback | True |
| using_mis_intraday | False |
| taiwan50_batch_uses_mis | False |
| watchlist_realtime_mis_sanity | unknown - no live MIS fetch in this export script |
| previous_close | 272 |
| previous_close_source | history_price.close previous row |
| latest_close | 268.5 |
| latest_vs_previous_close_pct | -1.29% |

## Missing Fields

BIAS, DMI_plus_DI, DMI_minus_DI, ADX

## Fallback Fields

close, high, low, open, volume

## Usage In Current System

| Field group | Enters main status | Enters support/resistance | Enters next-day outlook |
| --- | --- | --- | --- |
| OHLCV history | Yes, through `score_stock_cached()` and `classify_practical_status_cached()` inputs | Yes, through support/resistance helpers | Yes, through technical factor inputs |
| MA / RSI / KD / MACD | Yes, via `scoring.calculate_indicators()` and practical status inputs | Indirectly, price/technical context only | Yes, technical filter uses local indicators |
| Bollinger | Yes, inside `scoring.calculate_indicators()` scoring internals | No direct display path found in this export | Possible via scoring-derived targets only |
| BIAS / DMI / ADX | No, currently N/A | No | No |

## Notes

- This script reads only local SQLite/project functions.
- It does not fetch or scrape Goodinfo, Yahoo, or WantGoo.
- It does not write to the database or JSON cache.
