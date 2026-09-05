# 策略 Point-in-Time 基線回測

- 回測版本：`strategy-backtest-v1`
- 產生時間：2026-08-26T17:32:49+08:00
- 訊號：T 日完整收盤；成交假設：下一交易日開盤；退出：第 5／10／20 個交易日收盤。
- 本報告只驗證規則，不會自動修改正式裁判、RSI、MACD 或篩選係數。

## 裁決摘要

- 正式權重決定：**維持不變**。現有預排序權重尚未通過穩定的樣本外單調性驗證。
- 樣本外排序結果：5日相關係數 0.938、Q5-Q1 0.32%；10日相關係數 0.642、Q5-Q1 0.24%；20日相關係數 0.988、Q5-Q1 3.07%。
- 完整既有進場規則樣本：5d=97筆、10d=97筆、20d=57筆；已通過每個持有期至少 30 筆的最低研究門檻。
- 更嚴格 RSI／MACD 變體樣本：full_plus_rsi_delta_1_5（5d=86筆、10d=86筆、20d=48筆）；full_plus_macd_two_days（5d=44筆、10d=44筆、20d=19筆）；full_plus_both_stricter（5d=37筆、10d=37筆、20d=16筆）；不得因少數高報酬直接升級規則。
- 全市場面板共有 133 個交易日，已通過最低 60 日覆蓋門檻。
- 結論：本輪完成的是可重跑、無前視偏誤的驗證基線；證據支持繼續蒐集資料，不支持現在改權重。

## 資料覆蓋

| 面板 | 觀測值 | 交易日 | 股票數 | 日期範圍 | 每日股票中位數 |
|---|---:|---:|---:|---|---:|
| established_universe | 99,026 | 227 | 857 | 2025-03-07～2026-08-25 | 426.0 |
| broad_market | 76,523 | 133 | 847 | 2026-01-21～2026-08-12 | 559.0 |
| sparse | 141 | 80 | 2 | 2024-11-04～2025-03-06 | 2.0 |

## 預排序分數單調性

Q1 為最低分、Q5 為最高分。相關係數愈接近 1，代表分數與後續報酬排序愈一致。

| 面板 | 樣本 | 持有期 | Q1～Q5 平均報酬% | 相關係數 | Q5-Q1% |
|---|---|---:|---|---:|---:|
| established_universe | full | 5 | 0.48/0.13/0.11/0.03/-0.13 | -0.935 | -0.61 |
| established_universe | full | 10 | 1.32/0.64/0.48/0.30/0.13 | -0.938 | -1.19 |
| established_universe | full | 20 | 3.07/1.87/1.66/1.18/0.77 | -0.959 | -2.30 |
| established_universe | test | 5 | 0.67/0.41/0.41/0.22/0.09 | -0.971 | -0.58 |
| established_universe | test | 10 | 2.06/1.43/1.21/0.99/0.85 | -0.954 | -1.21 |
| established_universe | test | 20 | 4.32/3.36/3.18/2.79/2.14 | -0.974 | -2.18 |
| broad_market | full | 5 | 0.91/0.63/0.40/0.27/0.20 | -0.972 | -0.71 |
| broad_market | full | 10 | 2.58/1.75/1.25/1.16/0.89 | -0.942 | -1.69 |
| broad_market | full | 20 | 4.78/3.60/2.81/2.85/2.42 | -0.926 | -2.35 |
| broad_market | test | 5 | -1.12/-1.17/-1.02/-0.85/-0.80 | 0.938 | 0.32 |
| broad_market | test | 10 | -1.66/-1.95/-1.82/-1.61/-1.42 | 0.642 | 0.24 |
| broad_market | test | 20 | -6.88/-5.73/-5.42/-4.36/-3.81 | 0.988 | 3.07 |

## 低檔止跌條件比較（每檔訊號間隔至少 20 個交易日）

| 條件 | 面板 | 樣本 | 持有期 | 次數 | 平均報酬% | 勝率% | 超額報酬% | MAE% | MFE% | P5報酬% | 診斷回撤% |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| rsi_30_45_only | established_universe | full | 5 | 2,962 | -0.02 | 46.73 | 0.43 | -4.19 | 4.79 | -9.72 | -66.10 |
| rsi_30_45_only | established_universe | full | 10 | 2,881 | 0.69 | 48.42 | 0.95 | -5.77 | 7.06 | -11.70 | -86.72 |
| rsi_30_45_only | established_universe | full | 20 | 2,870 | 0.26 | 47.80 | 1.67 | -9.37 | 10.82 | -26.46 | -96.05 |
| rsi_30_45_only | established_universe | test | 5 | 1,138 | 0.30 | 46.84 | 0.69 | -3.50 | 4.54 | -7.70 | -34.24 |
| rsi_30_45_only | established_universe | test | 10 | 1,063 | 1.02 | 46.47 | 1.53 | -5.00 | 7.20 | -11.17 | -35.54 |
| rsi_30_45_only | established_universe | test | 20 | 1,062 | 2.60 | 48.87 | 2.54 | -6.63 | 11.45 | -12.76 | -51.13 |
| rsi_30_45_only | broad_market | full | 5 | 2,232 | 0.89 | 50.22 | 1.11 | -5.98 | 6.72 | -12.54 | -68.50 |
| rsi_30_45_only | broad_market | full | 10 | 2,220 | 1.21 | 49.46 | 1.43 | -8.18 | 10.59 | -18.01 | -85.74 |
| rsi_30_45_only | broad_market | full | 20 | 2,076 | 2.54 | 48.55 | 2.57 | -11.74 | 15.50 | -19.59 | -90.08 |
| rsi_30_45_only | broad_market | test | 5 | 1,120 | -0.25 | 46.70 | 0.87 | -7.56 | 6.89 | -16.10 | -68.50 |
| rsi_30_45_only | broad_market | test | 10 | 1,108 | -1.74 | 40.88 | 1.19 | -10.82 | 9.94 | -21.14 | -85.74 |
| rsi_30_45_only | broad_market | test | 20 | 964 | -2.29 | 38.07 | 2.12 | -16.12 | 12.42 | -24.34 | -90.08 |
| rsi_turn_up | established_universe | full | 5 | 2,182 | -0.35 | 44.27 | 0.25 | -4.43 | 4.63 | -11.52 | -54.96 |
| rsi_turn_up | established_universe | full | 10 | 2,164 | 0.47 | 47.64 | 0.87 | -6.07 | 6.83 | -12.67 | -80.53 |
| rsi_turn_up | established_universe | full | 20 | 2,154 | 0.26 | 48.47 | 1.43 | -9.95 | 10.51 | -26.39 | -95.68 |
| rsi_turn_up | established_universe | test | 5 | 786 | 0.26 | 45.04 | 0.73 | -3.48 | 4.41 | -7.38 | -39.24 |
| rsi_turn_up | established_universe | test | 10 | 770 | 1.35 | 47.40 | 1.62 | -4.85 | 7.22 | -9.90 | -47.39 |
| rsi_turn_up | established_universe | test | 20 | 770 | 2.39 | 48.57 | 2.23 | -6.55 | 11.18 | -12.43 | -59.14 |
| rsi_turn_up | broad_market | full | 5 | 1,625 | 0.37 | 46.46 | 0.68 | -5.71 | 6.72 | -13.32 | -69.81 |
| rsi_turn_up | broad_market | full | 10 | 1,615 | 1.04 | 47.80 | 1.55 | -8.68 | 10.35 | -19.37 | -83.34 |
| rsi_turn_up | broad_market | full | 20 | 1,449 | 0.65 | 44.72 | 2.02 | -12.35 | 13.88 | -22.32 | -89.65 |
| rsi_turn_up | broad_market | test | 5 | 871 | -0.47 | 43.63 | 0.94 | -7.08 | 7.30 | -17.08 | -69.81 |
| rsi_turn_up | broad_market | test | 10 | 862 | -1.44 | 40.02 | 1.90 | -11.44 | 10.10 | -23.30 | -83.34 |
| rsi_turn_up | broad_market | test | 20 | 698 | -3.86 | 34.67 | 2.96 | -17.04 | 11.12 | -25.80 | -89.65 |
| confirmation_2_of_3 | established_universe | full | 5 | 2,064 | -0.35 | 44.04 | 0.20 | -4.46 | 4.62 | -11.46 | -67.33 |
| confirmation_2_of_3 | established_universe | full | 10 | 2,048 | 0.49 | 47.56 | 0.86 | -6.14 | 6.80 | -12.65 | -82.74 |
| confirmation_2_of_3 | established_universe | full | 20 | 2,040 | 0.33 | 49.02 | 1.29 | -9.96 | 10.45 | -25.25 | -95.78 |
| confirmation_2_of_3 | established_universe | test | 5 | 740 | 0.26 | 45.95 | 0.65 | -3.49 | 4.39 | -7.41 | -46.19 |
| confirmation_2_of_3 | established_universe | test | 10 | 728 | 1.31 | 47.53 | 1.55 | -4.90 | 7.19 | -10.54 | -58.01 |
| confirmation_2_of_3 | established_universe | test | 20 | 728 | 2.05 | 48.63 | 1.92 | -6.58 | 10.90 | -12.54 | -72.80 |
| confirmation_2_of_3 | broad_market | full | 5 | 1,508 | 0.06 | 45.16 | 0.60 | -5.77 | 6.66 | -14.14 | -68.35 |
| confirmation_2_of_3 | broad_market | full | 10 | 1,499 | 1.07 | 48.37 | 1.49 | -8.82 | 10.22 | -19.08 | -79.34 |
| confirmation_2_of_3 | broad_market | full | 20 | 1,344 | 0.51 | 44.57 | 1.92 | -12.43 | 13.71 | -22.31 | -83.36 |
| confirmation_2_of_3 | broad_market | test | 5 | 830 | -1.13 | 40.48 | 0.84 | -7.18 | 7.13 | -17.56 | -68.35 |
| confirmation_2_of_3 | broad_market | test | 10 | 822 | -1.35 | 40.51 | 1.70 | -11.55 | 9.81 | -22.90 | -79.34 |
| confirmation_2_of_3 | broad_market | test | 20 | 669 | -3.94 | 34.83 | 2.84 | -17.04 | 11.10 | -25.99 | -83.36 |
| full_existing_rule | established_universe | full | 5 | 190 | -0.78 | 42.63 | -0.06 | -5.03 | 4.14 | -10.22 | -64.32 |
| full_existing_rule | established_universe | full | 10 | 190 | 0.97 | 57.37 | 0.90 | -6.41 | 6.70 | -13.63 | -56.95 |
| full_existing_rule | established_universe | full | 20 | 190 | 1.06 | 53.16 | 0.42 | -9.80 | 10.65 | -21.66 | -91.07 |
| full_existing_rule | established_universe | test | 5 | 61 | 0.48 | 49.18 | 0.87 | -3.32 | 4.37 | -6.78 | -21.44 |
| full_existing_rule | established_universe | test | 10 | 61 | 0.65 | 47.54 | 1.51 | -4.88 | 6.66 | -9.43 | -37.86 |
| full_existing_rule | established_universe | test | 20 | 61 | -0.52 | 42.62 | 0.06 | -7.52 | 9.36 | -13.45 | -62.17 |
| full_existing_rule | broad_market | full | 5 | 144 | -0.08 | 45.14 | 0.12 | -6.29 | 7.03 | -16.34 | -72.13 |
| full_existing_rule | broad_market | full | 10 | 144 | 0.90 | 49.31 | 0.67 | -9.58 | 10.31 | -17.54 | -78.21 |
| full_existing_rule | broad_market | full | 20 | 104 | 0.66 | 48.08 | 2.34 | -14.05 | 13.85 | -23.15 | -76.54 |
| full_existing_rule | broad_market | test | 5 | 97 | -0.40 | 48.45 | 0.16 | -7.47 | 7.91 | -18.02 | -69.77 |
| full_existing_rule | broad_market | test | 10 | 97 | 1.30 | 51.55 | 1.27 | -11.11 | 11.35 | -20.99 | -78.21 |
| full_existing_rule | broad_market | test | 20 | 57 | -1.49 | 40.35 | 4.84 | -19.16 | 14.41 | -27.40 | -76.54 |
| full_plus_rsi_delta_1_5 | established_universe | full | 5 | 151 | -1.02 | 39.07 | 0.02 | -5.23 | 4.09 | -10.08 | -56.37 |
| full_plus_rsi_delta_1_5 | established_universe | full | 10 | 151 | 0.83 | 57.62 | 0.90 | -6.69 | 6.54 | -14.74 | -55.45 |
| full_plus_rsi_delta_1_5 | established_universe | full | 20 | 151 | 1.15 | 52.32 | 0.35 | -9.74 | 10.28 | -18.34 | -88.05 |
| full_plus_rsi_delta_1_5 | established_universe | test | 5 | 48 | 0.97 | 50.00 | 1.35 | -3.22 | 4.78 | -6.70 | -14.92 |
| full_plus_rsi_delta_1_5 | established_universe | test | 10 | 48 | 0.77 | 47.92 | 1.54 | -4.80 | 6.99 | -8.95 | -33.17 |
| full_plus_rsi_delta_1_5 | established_universe | test | 20 | 48 | -1.26 | 39.58 | -0.61 | -7.51 | 8.70 | -13.03 | -46.70 |
| full_plus_rsi_delta_1_5 | broad_market | full | 5 | 128 | 0.08 | 47.66 | 0.17 | -6.15 | 7.70 | -17.35 | -68.38 |
| full_plus_rsi_delta_1_5 | broad_market | full | 10 | 128 | 1.67 | 50.78 | 1.05 | -9.31 | 11.13 | -18.01 | -70.25 |
| full_plus_rsi_delta_1_5 | broad_market | full | 20 | 90 | 0.75 | 45.56 | 2.91 | -14.10 | 14.25 | -24.96 | -74.82 |
| full_plus_rsi_delta_1_5 | broad_market | test | 5 | 86 | -0.20 | 52.33 | 0.13 | -7.22 | 8.84 | -18.04 | -66.42 |
| full_plus_rsi_delta_1_5 | broad_market | test | 10 | 86 | 2.03 | 52.33 | 1.38 | -10.89 | 12.42 | -21.17 | -70.25 |
| full_plus_rsi_delta_1_5 | broad_market | test | 20 | 48 | -1.47 | 39.58 | 5.39 | -19.70 | 14.97 | -27.52 | -74.82 |
| full_plus_macd_two_days | established_universe | full | 5 | 106 | -1.33 | 39.62 | -0.44 | -4.84 | 3.90 | -11.00 | -48.10 |
| full_plus_macd_two_days | established_universe | full | 10 | 106 | 1.77 | 61.32 | 1.00 | -6.16 | 6.73 | -12.13 | -43.01 |
| full_plus_macd_two_days | established_universe | full | 20 | 106 | 3.83 | 63.21 | 1.08 | -8.54 | 11.99 | -18.54 | -75.70 |
| full_plus_macd_two_days | established_universe | test | 5 | 27 | 0.49 | 55.56 | 0.82 | -2.99 | 3.85 | -5.02 | -13.59 |
| full_plus_macd_two_days | established_universe | test | 10 | 27 | 0.02 | 44.44 | 0.27 | -4.42 | 5.81 | -7.47 | -33.12 |
| full_plus_macd_two_days | established_universe | test | 20 | 27 | -2.23 | 44.44 | -2.02 | -7.00 | 7.39 | -14.10 | -53.02 |
| full_plus_macd_two_days | broad_market | full | 5 | 63 | -0.20 | 50.79 | 0.38 | -6.14 | 7.09 | -19.34 | -64.32 |
| full_plus_macd_two_days | broad_market | full | 10 | 63 | 2.65 | 53.97 | 2.27 | -8.37 | 10.87 | -14.06 | -39.77 |
| full_plus_macd_two_days | broad_market | full | 20 | 38 | 1.19 | 55.26 | 2.56 | -13.33 | 13.72 | -15.35 | -34.35 |
| full_plus_macd_two_days | broad_market | test | 5 | 44 | -0.00 | 59.09 | 1.19 | -7.06 | 8.67 | -20.63 | -55.15 |
| full_plus_macd_two_days | broad_market | test | 10 | 44 | 4.07 | 59.09 | 3.92 | -9.31 | 12.97 | -16.79 | -39.77 |
| full_plus_macd_two_days | broad_market | test | 20 | 19 | 0.23 | 47.37 | 7.89 | -19.19 | 16.65 | -19.17 | -34.35 |
| full_plus_both_stricter | established_universe | full | 5 | 87 | -2.12 | 33.33 | -0.79 | -5.14 | 3.51 | -11.25 | -55.09 |
| full_plus_both_stricter | established_universe | full | 10 | 87 | 1.35 | 58.62 | 0.80 | -6.58 | 6.25 | -13.45 | -48.60 |
| full_plus_both_stricter | established_universe | full | 20 | 87 | 3.68 | 62.07 | 0.92 | -8.81 | 11.50 | -17.89 | -67.91 |
| full_plus_both_stricter | established_universe | test | 5 | 23 | 0.62 | 56.52 | 1.03 | -3.10 | 4.06 | -5.23 | -12.36 |
| full_plus_both_stricter | established_universe | test | 10 | 23 | 0.39 | 47.83 | 0.68 | -4.40 | 6.29 | -7.59 | -27.85 |
| full_plus_both_stricter | established_universe | test | 20 | 23 | -2.12 | 43.48 | -1.98 | -7.21 | 7.95 | -14.34 | -44.79 |
| full_plus_both_stricter | broad_market | full | 5 | 52 | 0.22 | 53.85 | 0.98 | -6.13 | 7.69 | -20.09 | -57.75 |
| full_plus_both_stricter | broad_market | full | 10 | 52 | 4.17 | 57.69 | 3.06 | -7.96 | 11.78 | -9.15 | -19.82 |
| full_plus_both_stricter | broad_market | full | 20 | 31 | 2.78 | 58.06 | 3.51 | -12.78 | 15.30 | -14.29 | -27.09 |
| full_plus_both_stricter | broad_market | test | 5 | 37 | 0.82 | 64.86 | 1.93 | -6.89 | 9.63 | -20.85 | -46.08 |
| full_plus_both_stricter | broad_market | test | 10 | 37 | 5.77 | 62.16 | 4.24 | -8.62 | 14.24 | -10.88 | -19.82 |
| full_plus_both_stricter | broad_market | test | 20 | 16 | 2.29 | 56.25 | 7.43 | -18.08 | 19.15 | -22.14 | -27.09 |

## 市場狀態切分（既有完整規則）

僅列完整既有規則；樣本仍少，不能據此調參。

| 市場狀態 | 持有期 | 次數 | 平均報酬% | 勝率% | 超額報酬% | MAE% |
|---|---:|---:|---:|---:|---:|---:|
| bull | 5 | 4 | -3.07 | 25.00 | -2.91 | -5.20 |
| bull | 10 | 4 | -0.88 | 25.00 | -0.39 | -5.63 |
| bull | 20 | 4 | -2.22 | 75.00 | -1.68 | -7.25 |
| sideways | 5 | 54 | -0.82 | 38.89 | -0.71 | -4.16 |
| sideways | 10 | 54 | -0.00 | 48.15 | -0.16 | -5.35 |
| sideways | 20 | 54 | -4.20 | 31.48 | -1.32 | -11.19 |
| bear | 5 | 132 | -0.70 | 44.70 | 0.29 | -5.39 |
| bear | 10 | 132 | 1.43 | 62.12 | 1.38 | -6.87 |
| bear | 20 | 132 | 3.32 | 61.36 | 1.20 | -9.30 |

## 已知限制

- 歷史股票池由目前 stock master 回建，仍有存活者偏誤。
- 公司規模與官方注意／處置的 point-in-time 快照從最新完整交易日才開始，因此歷史回測只套用成交金額與公司行動安全閘門。
- 全市場 technical-ready 覆蓋期間很短；未具足夠未來交易日的持有期維持 unavailable，不補假值。
- 未模擬交易成本、滑價、稅負、下單金額、漲跌停成交限制或投資組合資金約束。
- 診斷最大回撤使用重疊的訊號日批次複利，不可解讀為可實現的投資組合回撤。
- 結果只作研究證據，不會自動修改正式裁判或預排序係數。

診斷回撤是把同一訊號日的報酬等權平均後依日期複利；因持有期重疊，不等同可實現的投資組合最大回撤。

僅供量化資料整理與研究，不構成投資建議；未計入交易成本、稅負、滑價與實際成交限制。
