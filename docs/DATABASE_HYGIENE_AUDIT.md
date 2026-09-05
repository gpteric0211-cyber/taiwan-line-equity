# Database Hygiene Audit — 2026-09-02

## 正式資料庫狀態

- 正式檔：`review_src/data/taiwan50.db`
- 大小：12,451,524,608 bytes
- `PRAGMA integrity_check`：`ok`
- `PRAGMA quick_check`：`ok`
- 外鍵違規：0
- 掃描資料表：105
- 已定義業務鍵的重複 findings：0

完整機器可讀報告：`docs/DATABASE_HYGIENE_AUDIT.json`；資料覆蓋與完整 integrity 證據：`docs/ANALYSIS_DATA_COMPLETENESS_AUDIT.json`。

## 已刪除項目

### 完全重複索引

以下 9 個索引的欄位、方向與既有 PK／UNIQUE 索引完全相同；刪除前後各表 row count 不變，業務資料刪除數為 0：

1. `idx_branch_trade_code_date_broker_source`
2. `idx_daily_inner_outer_volume_code_date`
3. `idx_est_chip_cost_code_date_type`
4. `idx_full_market_not_applicable_date`
5. `idx_margin_amount_code_date_source`
6. `idx_mis_quote_snapshot_code_ts`
7. `idx_single_track_retrieval_attempt_run`
8. `idx_single_track_source_snapshot_run`
9. `idx_users_email`

這次沒有對正式 DB 執行一般性 `VACUUM`；索引刪除留下 471 個 freelist pages，避免為了回收少量空間對約 12GB 正式檔做不必要的整檔重寫。

### 從未發布的 staging／候選檔

- `taiwan50.20260902_180010.54580.{db,wal,shm}` 與 `taiwan50.20260902_182202.15256.{db,wal,shm}`：合計 16,675,653,416 bytes。
- `taiwan50.20260902_204728.53936.db`：失敗候選，觀察大小 12,355,149,824 bytes；pipeline 在拒絕發布後自動清除。
- `taiwan50.20260902_214107.42404.db`：因偵測到美股當日未收盤 K 棒而主動中止，刪除 12,355,149,824 bytes。

上述檔案都位於 staging、從未成為 Web／LINE 正式資料庫，刪除不影響業務資料。成功發布後 `review_src/data/.update_staging` 為空。

## 保留而未刪除的資料

- 11 個「左前綴索引候選」不是完全相同索引；較短索引仍可能降低高頻查詢 I/O。未取得 query-plan／負載基準前不視為安全冗餘，因此保留。
- `institution_daily`、`margin_daily`、`lending_daily` 是 active compatibility readers 仍使用的鏡像表，不能只因 canonical 表存在就刪除。
- 法人鏡像 1,273,935 個重疊 rows 已與 canonical 對齊；18 個 legacy-only FinMind rows 保留並標示 supplemental。
- 信用交易鏡像 1,555,988 個 margin 與 1,555,988 個 lending 重疊 rows 均已對齊；legacy-only rows 為 0。
- `review_src/data/taiwan50.previous.db` 保留為乾淨 rollback。
- `review_src/data/.recovery_staging/taiwan50.reindex_candidate.db`（12,701,564,928 bytes）是舊復原候選；雖已被乾淨 active 與 rollback 取代，但刪除動作被安全審查要求精確明示授權，因此本次未刪除。

## 損毀修復說明

本次在正式外部歷史發布前發現 `data_observation_version` 的次要索引 `idx_observation_source_event` B-tree 損毀。主表與 `technical_indicator_vector_daily` 可完整掃描；在隔離 recovery copy 上移除損壞索引 schema entry，以 `VACUUM INTO` 重建資料庫，再建立正確索引並以原子方式發布。修復後正式檔已通過完整 `integrity_check=ok`。

這證明目前 SQLite 邏輯結構已修復，不代表底層儲存裝置根因已排除。Windows 曾出現 Event ID 129 storage reset 與跨檔案損壞，仍應保留硬體／檔案系統風險監控與外部備份。
