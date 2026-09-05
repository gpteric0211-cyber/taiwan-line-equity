# taiwan_50 Git／SQLite／密鑰完整性鑑識稽核

日期：2026-09-03（Asia/Taipei）  
性質：唯讀鑑識與修復規劃；本次未執行修復  
專案：`taiwan_50`  
證據目錄：`$Desktop\taiwan_50_integrity_audit_20260903-130804`  
Git master backup：`$Desktop\taiwan_50_git_backup_20260903\.git`

## 1. 結論摘要

| 等級 | 結論 |
|---|---|
| 🔴 | Git 不是單一孤兒物件問題。`git fsck` 在已驗證的 forensic `.git` 找到 5 個損壞／缺失 loose objects；其中 `453961feeadaf9b09cad6bf673ff9077973d50e0` 仍由本機 `refs/codex/turn-diffs/.../base` 路徑可達，對應 `tests/test_line_bot_gateway.py`。最終 Git 判定為 **`history_recovery_required`**。 |
| 🔴 | SQLite 損毀是真實且多次發生。21 個實體 `.db` 分成 17 個唯一 SHA-256 內容；正式 DB 不開啟，其餘 16 個唯一樣本均以 sealed copy 的 disposable clone 檢查，結果為 **9 個 confirmed_corrupt、7 個 structurally_ok_on_base_copy**。2026-09-01～09-03 可合理區分 6 次主要損毀事件，另有 2026-08-21 一次。 |
| 🔴 | Windows 事件在 2026-08-21～09-03 期間有 **23 次真正的 storage reset 警告**：`stornvme`／RaidPort1 13 次、`storahci`／RaidPort0 10 次。另有 Kernel-Power 41 一次。這是目前比「llama-server 多開」更直接的根因線索；但 RaidPort 與專案所在 C: 的精確映射仍未證實。 |
| 🔴 | 兩個可能的 writer runtime 分別使用 SQLite **3.45.1** 與 **3.50.4**，兩者均落在 SQLite 官方公布的 WAL-reset bug 受影響範圍。程式又存在多連線、WAL、TRUNCATE checkpoint 與 candidate publish/replace 路徑，因此具實際適用性；缺少同一瞬間雙 connection 寫入／checkpoint 的直接 trace，尚不能宣告它就是唯一根因。 |
| 🔴 | Portable 與 Git 歷史存在候選密鑰欄位。零明文掃描確認 `.env`、`.env.example`、`.env.line_bot.example` 及歷史 blob 中有多組 nonempty candidate secrets；有效性未知，但已出現在 build／history 就應以「可能已散布」處理。 |
| 🟡 | 稽核期間正式 `review_src/data/taiwan50.db` 的 size、mtime 與 SHA-256 發生變化：12,444,160,000 → 12,444,471,296 bytes，最後寫入 2026-09-03 14:01:50 Asia/Taipei。最後程序快照為 0，不等於全程沒有短暫 writer。正式 DB 因未開啟，結構狀態為 **`inconclusive_incoherent_snapshot`**。 |
| 🟡 | 目前工作樹相對 forensic index 為 75 個 tracked、未暫存修改，另有 634 個未追蹤檔；ignored 檔未列入，`.pytest_cache` 因 ACL 無法枚舉。修 Git 時不能只 fresh clone 後覆蓋舊目錄。 |
| 🟡 | `.pytest_cache` 是獨立 ACL 問題：實體目錄、非 reparse point，目前 identity 對 ACL 與 children 均收到 `0x80070005`；早期觀察 owner 為 `CodexSandboxOffline`。這不是 Git／SQLite 損毀證據。 |

本次沒有證據支持「單一根因已確認」。最合理的整體模型是：**儲存裝置 reset／I/O 不穩定是最高優先硬體層假設；受影響 SQLite 版本配合多連線與 checkpoint 是高相關軟體層風險；candidate replace／sidecar 處理和未完整協調的多服務 writer 會放大事故。**

## 2. 稽核邊界與方法

- 未修改 source、tests、scripts、config、`.git`、`.env`、正式 DB 或既有備份。
- 未啟動 app、migration、tests、scheduler、LINE bot、dashboard、uvicorn 或 model server。
- 未用 SQLite 開啟正式 `review_src/data/taiwan50.db`。
- Git repo-aware 診斷只在通過 SHA-256 manifest 的 forensic `.git` 執行；工作樹狀態比對也使用 forensic git-dir，而非來源 `.git`。
- DB integrity PRAGMA 只在 audit 目錄的 disposable clones 執行。
- Secret detector 在本機記憶體處理內容，只輸出 path、field、分類與 opaque group ID；沒有輸出值、片段、精確值長度或可重現 secret hash。
- `handle.exe/handle64.exe` 不可用，因此檔案 handle 覆蓋為 `indeterminate_handle_tool_unavailable`。
- 程序盤點是時間點快照，不是 ETW／Process Monitor 連續追蹤。

## 3. 現場與工作樹狀態

### 3.1 程序與來源穩定性

- 人工停止服務後，對 `python/pythonw/uvicorn/pytest/git/llama-server/ollama/cloudflared` 及專案路徑的多次程序快照最終為 0。
- 最終 recheck 仍為 0，但正式 DB 在 13:35:07～14:01:50 Asia/Taipei 間發生內容與大小變化，證明「最後沒有程序」不能回推「整段稽核都沒有 writer」。
- 來源 `.git` 相對 Phase 0B manifest 有 730 筆差異：726 筆只有 LastWriteTime 變化、2 個 Codex turn-diff refs 被移除、2 個新 turn-diff refs 被加入；沒有既有 object 的 length/SHA-256 內容差異。變化窗口為 13:28:34～13:30:07 Asia/Taipei，符合 Codex turn-diff capture ref 輪替，而不是新的 object corruption 證據。
- forensic `.git` 在 Git 診斷後重新驗證：5,055 files、manifest diff 0。

### 3.2 Git 可見工作樹

使用 forensic git-dir 對目前工作樹執行 porcelain v2 read-only status：

- branch：`master`
- snapshot HEAD：`6d9e34b2875058394004800a605509f049b45ea2`
- staged change：0
- tracked unstaged modified：75
- untracked：634
- unmerged：0
- ignored：未枚舉
- `.pytest_cache`：Permission denied，未計入完整未追蹤／ignored 覆蓋

Tracked 修改主要分布：`review_src/` 44、`scripts/` 11、`docs/` 10、`packaging/` 2、`tests/` 2，其餘為根目錄與規則檔。Untracked 主要分布：`review_src/` 184、`tests/` 148、`MitakeGU/` 134、`scripts/` 71、`docs/` 55、`.tmp/` 20。

這表示 fresh clone 只能提供乾淨物件庫，不能代表本地工作成果已保存。

## 4. Git 鑑識

### 4.1 備份完整性

- source、master backup、forensic copy：均為 5,055 files、298 directories、68,154,235 bytes。
- source-before、source-after、master、forensic file manifests 完全一致。
- 四份 file manifest 各 797,778 bytes，SHA-256 均為 `4bdeb2c83ab1540b30afa5b36ae969327b98f39fcdc57ace9f4ea086d89725d6`。
- 無 alternates、無 linked gitdir、無 linked worktree、無 promisor/partial clone、無 lock、無 pack temp。
- 備份與 repo 同在同一實體磁碟，只能防操作失誤，不能防磁碟故障。

### 4.2 fsck 結果

Git 2.55.0.windows.5；`fsck --full` exit 3。確認 5 個損壞／缺失 loose objects：

| OID | cat-file | 本機可達性結論 |
|---|---|---|
| `453961feeadaf9b09cad6bf673ff9077973d50e0` | `-t` 為 blob；`-s` 宣告 108,865 bytes，但 fsck checksum/inflate 失敗 | `rev-list --objects --all --reflog` 命中 `tests/test_line_bot_gateway.py`；`fsck --name-objects` 命中 `refs/codex/turn-diffs/.../base...`。**reachable corrupt object**。 |
| `590f5084f3e8e96099fe020ef2339c943663dad1` | type/size exit 128 | covered local refs/reflogs/index/pseudo refs/log/rev-list 無命中。 |
| `79022d3dad6fae9cc835fc43817139cca37b0bc8` | type/size exit 128 | covered local refs/reflogs/index/pseudo refs/log/rev-list 無命中。 |
| `7e984a5c1283475456e5fcc619a3398908a59fb1` | type/size exit 128 | covered local refs/reflogs/index/pseudo refs/log/rev-list 無命中。 |
| `e0122bbc9a76bc5d56bc5c8a3579ebdab733df0a` | type/size exit 128 | covered local refs/reflogs/index/pseudo refs/log/rev-list 無命中。 |

限制：後四個只能稱為「未被本次覆蓋的本機 refs、reflogs、index、pseudo refs、raw log 與 rev-list 引用」，不能排除已刪除 ref、遠端 ref、其他 clone 或未保留 worktree。repo 中沒有 `.idx` pack 可供交叉恢復。

`log --find-object=<OID>` 對五個 OID 均無輸出；這只代表沒有找到 diff transition，不能推翻 `rev-list` 對第一個 OID 的 reachability 證據。

### 4.3 `tmp_obj_*` 正確分群

複製 forensic 目錄會重設 CreationTime，因此初次把六個檔案分成同一群是無效結果。以下以複製前記錄的來源 CreationTime、連續差距不超過五分鐘分群：

| Group | UTC 時間 | 檔案 |
|---|---|---|
| G1 | 2026-08-30 10:30:29 ～ 10:35:06 | `objects/4d/tmp_obj_d0VHet`、`objects/59/tmp_obj_gQuQrm` |
| G2 | 2026-08-30 14:53:41 | `objects/d6/tmp_obj_8kAjA9` |
| G3 | 2026-09-01 12:39:32 | `objects/09/tmp_obj_5eeH7N` |
| G4 | 2026-09-01 16:26:08 | `objects/f6/tmp_obj_GbzBjq` |
| G5 | 2026-09-02 06:57:24 | `objects/b0/tmp_obj_2c0SJx` |

六個 tmp object 的 SHA-256 均不等於五個損壞 loose object 的檔案 SHA-256。時間聚集只支持「可能同次中斷」，不能證明是 fetch、Codex 或任何特定程式造成。

### 4.4 Git 最終判定

**`history_recovery_required`**

理由：至少一個損壞 blob 仍由 local-only Codex ref 路徑可達；另有 75 個 tracked 修改與 634 個 untracked 檔。直接刪 loose object、直接 `git init`、或以 fresh clone 覆蓋現場，都可能永久丟失本機歷史或工作成果。

## 5. SQLite 鑑識

### 5.1 清冊與去重

- 清冊：38 files，95,973,425,227 bytes。
- `.db`：21 個實體檔。
- unique DB SHA-256：17 組。
- 正式 DB：DB13，未複製、未以 SQLite 開啟。
- 其餘 16 組：77,038,919,680 bytes；全部來源前後 size/mtime/hash 穩定，sealed SHA 與來源一致後才建立 disposable clone。
- sealed evidence：28 files、77,039,116,288 bytes。
- disposable clones：檢查前 16 DB 組；SQLite 開關後零長度 WAL/SHM 可在 disposable tree 被清理或重建，sealed evidence 未直接開啟。

### 5.2 每個唯一樣本結果

| Group | 代表來源 | 結果 | 重點 |
|---|---|---|---|
| DB01 | `review_src/data/backups/corrupt_20260821_2145/taiwan50.active-original.db` | `confirmed_corrupt` | open/schema 階段即 malformed/corrupt；sealed WAL 為 0 bytes。 |
| DB02 | `review_src/data/yfinance_cache/cookies.db` | `structurally_ok_on_base_copy` | quick/integrity ok、FK 0。 |
| DB03 | `review_src/data/taiwan50.pre-repair-corrupt.20260901-2037.db` | `confirmed_corrupt` | open/schema 階段即 malformed/corrupt；同 SHA 的第二個實體檔在 root `backups/`。 |
| DB04 | `review_src/data/taiwan50.backup.before_valuation_update_20260702_002041.db` | `structurally_ok_on_base_copy` | WAL mode；sealed WAL 0 bytes；quick/integrity ok、FK 0。 |
| DB05 | `.recovery_staging/taiwan50.credit-repair.20260903-102227.raw.db` | `confirmed_corrupt` | quick/integrity errors；錯誤頁包含 42、43、299～506。 |
| DB06 | `backups/taiwan50_before_price_volume_repair_2026-08-25.db` | `structurally_ok_on_base_copy` | WAL mode；sealed WAL 0 bytes；quick/integrity ok、FK 0。 |
| DB07 | `backups/taiwan50.corrupt.20260902-093932.db` | `confirmed_corrupt` | open/schema 階段即 malformed/corrupt；有第二個相同 SHA 實體檔。 |
| DB08 | `backups/taiwan50.recorrupt.20260901-232836.db` | `confirmed_corrupt` | open/schema 階段即 malformed/corrupt；有第二個相同 SHA 實體檔及零長度 WAL。 |
| DB09 | `review_src/data/backups/taiwan50.post-corporate-action.20260903-110538.db` | `structurally_ok_on_base_copy` | delete mode；quick/integrity ok、FK 0。 |
| DB10 | `backups/taiwan50.corrupt.20260902-105106.db` | `confirmed_corrupt` | open/schema 階段即 malformed/corrupt。 |
| DB11 | `review_src/data/taiwan50.previous.db` | `structurally_ok_on_base_copy` | delete mode；quick/integrity ok、FK 0。 |
| DB12 | `review_src/data/backups/taiwan50.corrupt-credit-btree.20260903-104028.db` | `confirmed_corrupt` | open/schema 階段即 malformed/corrupt；與 DB05 同大小但 SHA 不同。 |
| DB13 | `review_src/data/taiwan50.db` | `inconclusive_incoherent_snapshot` | 正式 DB 未開啟；稽核中 size/mtime/hash 改變。 |
| DB14 | `.recovery_staging/taiwan50.reindex_candidate.db` | `confirmed_corrupt` | quick/integrity errors；錯誤頁 223,969～224,068；屬修復候選衍生物，不能另算原始事故。 |
| DB15 | `review_src/data/backups/recovered_20260821_2145.db` | `structurally_ok_on_base_copy` | delete mode；quick/integrity ok、FK 0。 |
| DB16 | `review_src/data/yfinance_cache/tkr-tz.db` | `structurally_ok_on_base_copy` | quick/integrity ok、FK 0。 |
| DB17 | `backups/taiwan50.corrupt.20260901-1025.db` | `confirmed_corrupt` | quick/integrity errors；多個大範圍錯誤頁群。 |

沒有任何樣本可分類為 `structurally_ok_with_wal_copy`：保留下來的 matching WAL 均為 0 bytes。SHM 是可重建 wal-index，不是交易權威內容；現存或缺失 sidecar 都不能反推事故當下狀態。sidecar 存在也不等於異常關閉。

### 5.3 事故數

檔名時間不是事故發生時間，但配合唯一 SHA、修復報告與 integrity 結果，可合理區分：

| 事件標籤 | 對應樣本 | 判讀 |
|---|---|---|
| 2026-08-21 21:45 | DB01 → DB15 | 原始壞檔與 recovered donor/產物；1 次早期事故。 |
| 2026-09-01 10:25 | DB17 | confirmed corrupt。 |
| 2026-09-01 20:37 | DB03 | 兩個實體檔、同一 SHA；只算 1 次。 |
| 2026-09-01 23:28 | DB08 | 兩個實體檔、同一 SHA；只算 1 次。 |
| 2026-09-02 09:39 | DB07 | 兩個實體檔、同一 SHA；只算 1 次。 |
| 2026-09-02 10:51 | DB10 | confirmed corrupt。 |
| 2026-09-03 10:22～10:40 | DB05、DB12、DB14 | credit B-tree 原始壞檔、隔離檔與 reindex candidate，合理視為同一事故鏈，不算 3 次。 |

因此 2026-09-01～09-03 是 **6 次可合理區分的主要事故**，加上 8/21 為 7 次；不是 9 個 unique corrupt SHA 就等於 9 次獨立事故。

### 5.4 日誌與時間線

- 五份 database recovery report 均聲稱 `status=ok`、source preserved、完成後 quick/integrity ok；這證明恢復產物當時通過檢查，不證明其後不會再損毀。
- 2026-09-02 06:45:17 Asia/Taipei，`storahci` Event 129；`post_close_20260902_064501.log` 由檔名顯示同分鐘啟動，mtime 延續到 07:37:35。這是具體時間重疊，但 RaidPort0 是否承載 C: 尚未映射。
- 2026-09-03 06:45:01～06:47:57，latest isolated update：`isolated_update_failed`、`DatabaseError`、exit 11、`published=false`。
- 2026-09-03 09:12:52、09:13:02、10:16:17、10:16:27 Asia/Taipei，RaidPort0 連續 storage reset；接著 10:22 出現 credit repair raw DB、10:40 隔離 corrupt-credit-btree、11:05 post-corporate-action structurally-ok backup。時間相關性強，但仍缺少裝置映射與 block-level trace。
- 全 log tree 的零原始行掃描覆蓋 85,456 textual files、2,642,908,590 bytes；找出 recovery、integrity、malformed、readonly、checkpoint 等分類。大量 `checkpoint` 是 model checkpoint、測試或 source copy 的文字誤中，不能當成 SQLite checkpoint 證據。

### 5.5 Windows 儲存事件

期間：2026-08-21～09-03。

| Provider / Event | 次數 | 時間範圍 | 意義 |
|---|---:|---|---|
| `stornvme` / 129 / RaidPort1 | 13 | 8/23 23:11～8/30 23:53 Asia/Taipei | 裝置 reset 警告；專案磁碟為 NVMe，相關性高，但 port→C: 尚未直接證實。 |
| `storahci` / 129 / RaidPort0 | 10 | 8/22 13:26～9/3 10:16 Asia/Taipei | 裝置 reset 警告；9/2 post-close 與 9/3 credit-B-tree 事故附近均有時間重疊。 |
| Kernel-Power / 41 | 1 | 8/23 01:11 Asia/Taipei | 非正常關機／電源事件。 |
| NTFS / 98 | 49 | 8/21～8/31 | 皆為資訊等級，不能直接當成 NTFS corruption。 |

### 5.6 Runtime 與 WAL-reset bug

| Runtime | Python | SQLite | 判定 |
|---|---|---|---|
| 系統 runtime | 3.11.9 | 3.45.1 | 受影響範圍。 |
| `review_src/.venv` | 3.13.15 | 3.50.4 | 受影響範圍；官方 backport 修正版為 3.50.7。 |

SQLite 官方資料指出 WAL-reset bug 存在於 3.7.0～3.51.2，3.51.3 及後續版本修正，另有 3.44.6、3.50.7 backport。觸發前提是同一 WAL DB 有兩個以上 thread/process connections，且寫入或 checkpoint 在極窄時間窗重疊：

- <https://www.sqlite.org/wal.html#the_wal_reset_bug>
- <https://www.sqlite.org/howtocorrupt.html#race_condition_when_writing_to_a_wal_mode_database>

本 repo 的靜態證據：

- `review_src/core/db.py:29-32`：writer connection、busy timeout 30s、foreign keys ON、synchronous FULL；schema setup 路徑會設定 WAL。
- `review_src/repository/line_conversation_repository.py:41-68`：獨立 connection、WAL、synchronous FULL；`:921/:941/:971/:1002` 有多個 `wal_checkpoint(TRUNCATE)`。
- `scripts/run_single_track_v3_scheduler.py:82`：scheduler writable connection。
- `scripts/run_isolated_post_close_pipeline.py:114-118`：SQLite backup snapshot；`:44` 會 TRUNCATE checkpoint；`:167-182` 有 sidecar cleanup 與 `os.replace(candidate, active)`／rollback replace。
- `scripts/recover_market_database.py:344-346`：recovery destination 暫時使用 journal_mode OFF、synchronous OFF、foreign_keys OFF；`:397/:403` 再切回 WAL/FULL。這不是 live source 的已證實弱化，但若中間檔被誤發布或中斷，風險高。
- 沒找到 writer 路徑使用 `nolock`；主要 runtime 的 synchronous 為 FULL，屬反證。

結論：WAL-reset bug 為 **Medium** 信心的實際適用假設；需要 connection/write/checkpoint 時序 trace 才能升為 confirmed。

### 5.7 根因矩陣

| 假設 | 機制 | 支持證據 | 反證／缺口 | 如何證偽 | 信心 |
|---|---|---|---|---|---|
| storage/controller reset | I/O reset 或不完整持久化使 DB/WAL/object 寫入受損 | 23 次 Event 129；9/2 post-close 與 9/3 credit-B-tree 事故附近具時間重疊；另有 Kernel-Power 41 | RaidPort0/1 尚未映射到 C:；無 SMART、firmware、controller trace | 映射 port→disk/volume；讀 SMART/NVMe health；在健康不同磁碟重跑一段觀察期 | **Medium-High** |
| SQLite WAL-reset bug | 受影響版本在多 connection write/checkpoint race 中造成 corruption | SQLite 3.45.1/3.50.4；WAL；多 connections；TRUNCATE checkpoint；多服務 | 缺同一瞬間雙 writer/checkpoint trace；觸發窗口罕見 | 全 runtime 升級 fixed SQLite 後，以壓力／故障注入在 clone 重現；比對事件是否消失 | **Medium** |
| active DB publish/replace 與舊 handle/sidecar 錯配 | replace active DB 時其他 process 仍持有舊 DB/WAL/SHM，造成世代分裂或錯配 | isolated pipeline 有 sidecar unlink、active/previous `os.replace`；曾同時存在多個 Python 服務 | Windows 通常會阻擋被占用檔 replace；無 handle trace | 所有 writer 共用 OS-level publish lock，ETW/ProcMon 證明 publish 前 handle=0；比較事故率 | **Medium** |
| live DB 被檔案層 copy | DB 與 WAL 在不同瞬間被複製，快照不一致 | repo 有 copy/snapshot/backup 流程；大量備份 |主要 snapshot code 使用 SQLite backup API；現存 WAL 都是 0 bytes | 檢查每個產物 manifest 是否同一 sealed generation；禁止普通 copy live DB 後觀察 | **Low-Medium** |
| DB/WAL 錯配 | 不同 generation 的 DB/WAL 被組合或 sidecar 被提前刪除 | publish path明確清理 sidecars；多份舊 DB 保留零 WAL/SHM | 事故當下 sidecar 未保留；現在缺失不能反推過去 | 未來事故原子封存 DB/WAL/SHM 與 salts/frame metadata | **Medium-Low** |
| 非正常終止／斷電 | commit/checkpoint 中斷 | Kernel-Power 41；storage reset；人工最後發現多個長時間殘留程序 | 人工 stop 發生在事故後；一般 WAL 能承受正常 process crash | UPS/電源與系統事件對時；健康 storage 上做 controlled crash test | **Medium** |
| scheduler/shadow/web/LINE writer 重疊 | 多個未協調 writer 同時更新／checkpoint | Phase 0A 曾見 8 個 Python 服務樹；大量 writer connection；scheduler connection | 沒有 DB handle 證據；shadow/model service不一定寫 DB | 加 connection identity/audit table 或 ETW/ProcMon；每次 transaction記 writer PID/role | **Medium** |
| llama-server 殭屍是直接 DB writer | llama process直接開 SQLite | 五個跨日殘留 process 是啟停管理異常線索 | 無 source/handle 證據顯示 llama-server 開 `taiwan50.db`；低 working set 不是 DB handle 證據 | 連續 handle/ProcMon trace | **Low** |
| `nolock`／OFF／弱 synchronous | 鎖或耐久性被繞過 | recovery destination 使用 OFF/OFF | live writer 路徑無 `nolock`，主要為 FULL；OFF 在中間 destination | 對所有 connection 啟動時記錄 effective PRAGMA；禁止 candidate 未恢復 FULL 即 publish | **Low for live / Medium for candidate** |
| repair/restore 直接替換或截斷 | 修復腳本以 replace/unlink 發布，其他 process未完全停止 | code 中有 unlink/replace；live DB 稽核中曾短暫變動 | 沒有證據把 14:01 寫入歸因特定腳本 | Task Scheduler operational log、process creation audit、publish transaction receipt | **Medium** |
| 同一壞檔被反覆隔離 | 同一內容被多次改名造成「多次事故」假象 | DB03/DB07/DB08 各有相同 SHA 複本 | 仍有 9 個 unique corrupt SHA、6 個 9/1～9/3事故標籤 | 以 SHA 與 error-page family持續去重 | **部分成立；不能解釋全部** |
| antivirus／同步／備份程式干預 | 第三方 process 鎖、刪 sidecar 或攔截 I/O | 一般機制可行 | 本次沒有 AV/sync event/handle 證據 | ProcMon filter DB/WAL/SHM；檢查 AV/同步歷史 | **Unknown-Low** |
| 檢測假陽性 | 檔名或 quick check誤判 | 早期只看 corrupt 檔名確實不足 | 本次 9 unique clones實測 confirmed corrupt | 用第二 SQLite fixed runtime與獨立工具交叉驗證 | **Low** |
| `.pytest_cache` 導致 DB/Git corruption | ACL 錯誤擴散 | cache owner/ACL異常 | 不同 path、不同權限邊界，無資料流連結 | 修復／重建 cache 後觀察；不預期影響 DB/Git | **獨立 ACL 問題，高信心** |

### 5.8 Claude 建議的參考價值

有參考價值的部分：先停止服務、確認 ports/process、在啟動流程加入 singleton guard，都是正確的操作風險控制；「服務仍在跑時不要做 repair/publish」也應納入正式流程。

需要修正的部分：

- 多 process 開 SQLite 與 WAL sidecar 存在，本身不等於 corruption。
- process crash 通常是 SQLite WAL 能處理的情境，不能直接寫成「容易留下無法回滾的髒 WAL」。
- llama-server 的低 working set 不能證明 zombie，也沒有證據它開啟 `taiwan50.db`。
- 真正更強的證據是 storage Event 129、受影響 SQLite 版本、程式中的 checkpoint/publish 路徑，以及 9 個 unique confirmed-corrupt samples。

因此 Claude 的建議適合作為「服務啟停與 singleton 防護」參考，不適合把 llama-server 多開列為已確認首要根因。

## 6. 密鑰暴露稽核

### 6.1 覆蓋

- 本機設定檔：989 個。
- zip：26 個，皆以記憶體唯讀解析，未解壓到專案。
- Git rev-list objects：1,185；blob：963；成功掃描 962。
- 未覆蓋：已知損壞 blob `453961...`，batch short-read；它的已知 path 是 test file，內容仍未輸出。
- 最終分類：37 nonempty candidate secret、15 empty、153 exact_example_value、6 missing、1 placeholder_pattern、104 environment_reference。
- detector 是 over-approximation；第三方套件 test passwords、protocol schema 欄位、token counter 等不視為正式憑證。

### 6.2 已確認的候選散布面

以下只列 field 與 opaque group，不含值：

| Field / group | 位置 | 判讀 |
|---|---|---|
| `BOT_MARKET_DATA_TOKEN` / G0001 | `review_src/.env.example`、Git history、current/dated portable `.env.example`；dated portable `.env` | baseline 自身不是明確 placeholder，且被打包。 |
| `FINMIND_TOKEN` / G0002 | 同上 | candidate；有效性 unknown。 |
| `FUGLE_API_KEY` / G0003 | 同上 | candidate；有效性 unknown。 |
| `JWT_SECRET_KEY` / G0004 | 同上 | candidate；若被正式使用，輪替會使既有 session/token失效。 |
| `TURNSTILE_SECRET_KEY` / G0005 | 同上 | candidate；有效性 unknown。 |
| `LINE_CHANNEL_SECRET` / G0012 | Git history `review_src/.env.line_bot.example`；兩個 portable builds 的相同 example | candidate；可能影響 webhook signature。 |
| `LINE_CHANNEL_ACCESS_TOKEN` / G0013 | 同上 | candidate；可能允許 LINE Messaging API 操作。 |
| `BOT_MARKET_DATA_TOKEN` / G0014 | 同上 | 與 G0001 不同的候選組。 |
| `QWEN_API_KEY` / G0015 | 同上 | 若只為本機固定 sentinel，外部風險較低；仍不應散布。 |
| `channel_access_token` / G0017；`internal_token` / G0016/G0019；`token` / G0018 | Git history 的 LINE restart measurement docs | candidate；是否為遮罩、fixture 或有效值 unknown。 |

專案根 `.env.line_bot`：exists、3,202 bytes、內容未讀；current tracked-name hit 0、history name hit 0。這只證明該名稱未被目前 tree／已覆蓋歷史追蹤，不證明其值沒有被複製到其他檔案。

### 6.3 Packaging 機制

- `packaging/build_portable_windows.ps1:53-62` 有 `$IncludeEnv` 條件，可讓 `.env` 進入 portable tree。這是 dated build 含 `.env` 的直接可行機制。
- `packaging/build_full_post_close_windows.ps1:118-129` 主 tree copy 明確把 `.env` 放在 `ExcludeFiles`，但另行複製 `.env.example` 與 `.env.line_bot.example`。
- 同一 full build 在 `:305-307` 會在 finalize 前移除 generated `.env`。目前 non-dated full build未發現實際 `.env`，但兩個 example 本身已有 candidate values。
- `dist/TaiwanStock_PostClose_Portable_Windows_20260824/review_src/.env` 實際存在且多個欄位與 baseline exact match，表示歷史 artifact 曾攜帶 env 檔。

### 6.4 Rotation 表

| Priority | Fields | 推定服務 | Owner | 停機／相容風險 | 部署與撤銷驗證 |
|---|---|---|---|---|---|
| P0 | `LINE_CHANNEL_ACCESS_TOKEN`, `LINE_CHANNEL_SECRET` | LINE Messaging API / webhook | LINE channel owner | access token切換需同步部署；channel secret變更會影響簽章驗證 | 先建立／取得新值並部署至唯一 secret store；以合法 webhook與 reply smoke驗證；再撤銷舊值；確認舊值失效。 |
| P0 | `BOT_MARKET_DATA_TOKEN`（G0001/G0014） | Bot ↔ market API | App owner | 兩端不同步會造成 Bot中斷 | 支援短期 dual-token；先 server 接受新舊、更新 client、驗證，再移除舊值。 |
| P0 | `JWT_SECRET_KEY` | Web auth | Auth owner | 直接更換會使 session/JWT失效 | 先 key-id/key-ring過渡或安排全員重新登入；驗證新 token與舊 token撤銷策略。 |
| P0 | `TURNSTILE_SECRET_KEY` | Cloudflare Turnstile | Cloudflare owner | 錯誤切換會阻擋登入／驗證 | 新 secret部署並測試成功後撤銷舊 secret；檢查 server-side verification。 |
| P1 | `FUGLE_API_KEY`, `FINMIND_TOKEN` | 市場資料供應商 | Data owner | 切換失敗會造成行情／批次缺資料 | 新 key先以受控 smoke驗證 quota/權限，再部署、撤銷舊 key並觀察 freshness。 |
| P1 | `QWEN_API_KEY` | 本機模型 API | Model runtime owner | 若只 loopback則外部風險較低；多服務需同步 | 改成 runtime secret或短期 session token；驗證舊值不能再呼叫。 |
| P1 | Git history docs中的 G0016/G0017/G0018/G0019 | LINE/internal measurement artifacts | Repo + service owners | 值類型與有效性 unknown | 先和上述 rotated credential 做 opaque group對照；有效者納入撤銷，無效 fixture則文件化。 |

本次沒有登入供應商、測試有效性、撤銷或輪替任何憑證。

## 7. `.pytest_cache` 權限

- exists：true
- type：physical directory
- reparse point：false
- 最終 `Get-Acl`：UnauthorizedAccessException / `0x80070005`
- 最終 children enumeration：UnauthorizedAccessException / `0x80070005`
- 早期較高權限觀察 owner：`DESKTOP-O1SSRTM\CodexSandboxOffline`
- 早期 ACL 僅見 OWNER RIGHTS、SYSTEM、Administrators FullControl；一般 `User` 無法讀取。
- handle：未覆蓋，因 handle tool 不可用。

判定：**獨立 ACL/account identity 問題**。不支持它造成 Git object 或 SQLite page corruption。

## 8. 建議修復順序

以下是修復草案，不代表本次已執行。

### 8.1 先處理儲存層與可恢復性

1. 把 `.git` master backup、forensic evidence、目前工作樹、最新可用 DB 備份複製到不同實體磁碟，逐檔 SHA-256 驗證。現有 Desktop backup 在同一顆磁碟，不能防裝置故障。
2. 映射 `\Device\RaidPort0`、`RaidPort1` 到實際 controller/disk/volume；取得 NVMe SMART、media/data integrity errors、unsafe shutdowns、temperature、controller reset、firmware 與 driver 版本。
3. 在儲存 reset 根因處理前，不把新一輪大量 DB repair/write 當成永久修復。可先用唯讀或單一 writer 維持服務。
4. 啟用 Task Scheduler Operational log 或等價 process-creation telemetry，補足 14:01 transient writer 的身份證據。

驗證：至少跨越原本會觸發 post-close 的時段，Event 129 不再出現；在不同健康磁碟上的控制組也不再產生新 corrupt SHA。

### 8.2 SQLite 修復

1. 從 DB09（post-corporate-action）與 DB11（previous）等 structurally-ok 候選中選 donor；先比對業務日期、table row counts、權威資料版本與必要 schema，不能只因 integrity ok 就直接取代正式 DB。
2. 所有 writer runtime 升級到含 WAL-reset 修正的 SQLite：優先统一到 >=3.51.3 的穩定版本，或對 3.50 線至少使用 3.50.7。確認 Python runtime 實際載入的 `sqlite3.sqlite_version`，不是只看套件版本。
3. 把所有 DB write/publish 統一到單一 writer coordinator；Web/LINE/dashboard read path 使用 read-only connection，background tasks經 queue進入 writer。
4. 啟動器加入 OS-level singleton mutex/file lock、PID identity/creation-time驗證、port ownership檢查；PID lock 必須能辨識 stale PID與 PID reuse，不能只有「檔案存在」。
5. `run_isolated_post_close_pipeline` 的 publish lock 必須被所有可能 writer共同遵守。publish 前確認沒有 DB handles；不能在仍有 connection 時 unlink DB-WAL/SHM 或 `os.replace(active)`。
6. live snapshot 一律由受控單一 connection 使用 SQLite backup API建立到新檔；不要普通檔案 copy live DB。DB/WAL/SHM 若用於鑑識，需同一時點原子封存並保留 generation metadata。
7. recovery destination 不再長時間使用 journal_mode OFF/synchronous OFF；若為效能保留，只限尚未發布的隔離 candidate，且中斷時不得 publish，發布前必須切回耐久模式並完整驗證。
8. 新 candidate 依序跑 quick_check、完整 integrity_check、foreign_key_check、業務表 digest/row-count/date coverage，再以可回滾、單一 writer的 maintenance window發布。

驗證：

- fixed SQLite runtime版本一致。
- 多程序壓力測試只能在 disposable DB進行，涵蓋 writer + TRUNCATE checkpoint + restart。
- 正式發布前後完整 integrity check為 ok、FK 0、資料日期與權威表 digest符合預期。
- 連續觀察至少數個 post-close週期，沒有新 `corrupt/recorrupt` unique SHA、Event 129 或 unpublished DatabaseError。

### 8.3 Git 修復

1. 先把 75 個 tracked修改、634 個 untracked檔及必要 ignored artifacts做獨立工作樹 manifest／備份；不得把 fresh clone直接覆蓋現場。
2. 在健康的不同磁碟建立可信 remote的新 clone，先 `git fsck --full --no-dangling --no-progress`。
3. 盤點 local-only branch/tag/stash/reflog/worktree/Codex turn-diff refs；把「應保留」與「可捨棄 transient ref」分開由人工決策。
4. 從可信 clone、其他 clone或舊 backup尋找完全相同 OID `453961...`。只有取得可重新計算為相同 OID的完整 blob，才能視為該 reachable hole已補齊。
5. 後四個 loose objects在完成 local-only表面盤點前不要直接刪。最安全方向是以健康 clone為新 repo，經驗證後移植本機 refs與工作樹成果，而非在損壞 repo內原地手術。
6. 將工作樹變更以 patch/bundle/逐檔 manifest方式導入健康 repo，逐批檢查；最後完整 fsck、branch/ref/reflog與工作樹差異驗收。

驗證：完整 fsck exit 0；所有要保留 refs均可遍歷；原 75/634 工作成果均有明確去向；remote/本機 HEAD與人工選定歷史一致。

### 8.4 密鑰修復

1. 先依 rotation 表建立新 credential、部署與驗證，再撤銷舊 credential；不要先做 Git history rewrite而延誤撤銷。
2. 將 `review_src/.env.example`、`.env.line_bot.example` 的 candidate values改為明確 placeholder或 environment reference；example檔不得含可用預設 secret。
3. 移除或永久關閉 generic portable build 的 `$IncludeEnv` 能力；至少預設 false、需顯式安全核准、只允許生成空白 runtime template。
4. build manifest fail closed：任何 `.env`、`.env.*`（只允許人工核准的 sanitized example）、PEM/private key、known secret field的 non-placeholder value都阻止封裝。
5. 對既有 portable zip、release、分享資料夾、雲端備份、CI artifacts、其他 clone做相同零明文掃描；本機沒命中不能排除外部散布。
6. credential撤銷完成後，才評估協調式 Git history rewrite。rewrite會改 commit IDs，必須同步所有 remotes/clones/tags/releases並防舊歷史被重新 push。

驗證：post-build detector只允許 empty/placeholder/environment_reference；新 credential有效、舊 credential失效；Git current/history及外部 artifacts無有效舊值。

### 8.5 `.pytest_cache` 修復

`.pytest_cache` 不含權威資料時，最簡單的修復是先保留需要的測試證據，再由正確登入 identity刪除該 cache並讓 pytest重建。若政策要求保留，可把 owner/ACL重設為目前開發 identity並移除只允許 sandbox owner的 ACL。完成後驗證 `Get-Acl`、children enumeration與一次隔離 pytest cache建立；不需要改 source或 DB。

## 9. 不可逆／高影響動作

- 撤銷 provider credential、LINE token／secret、JWT signing key。
- Git history rewrite、force push、刪 local-only refs/reflogs、刪 loose objects、替換來源 `.git`。
- 以 donor替換正式 DB、recover/reindex/vacuum、刪 WAL/SHM、在 live DB上 checkpoint。
- 刪 portable artifacts或既有 backups。
- 刪／takeown／ACL reset `.pytest_cache`。
- storage firmware更新、driver rollback、磁碟更換。

執行這些動作前應另有可回復備份、owner核准、maintenance window與明確 rollback。

## 10. 證據索引

Final evidence index：`$Desktop\taiwan_50_integrity_audit_20260903-130804\manifests\evidence_index_final.csv`

- 17,420 bytes
- SHA-256 `646b9df91a3b17de5b4fd0cc6948828b5ceb4b0e480ac5e5f1cbadf91a80c26c`
- indexed logs/manifests：113 files、4,737,774 bytes
- forensic `.git`：5,055 files、68,154,235 bytes，由 `git_forensic_working.csv` 逐檔覆蓋
- sealed SQLite：28 files、77,039,116,288 bytes，由 `sqlite_sealed_clone_manifest.json` 覆蓋

主要證據：

| Evidence | Bytes | SHA-256 |
|---|---:|---|
| `logs/phase0c_01_fsck_full_no_dangling.stderr.txt` | 4,844 | `3588c1b67408dae90334e86fea1e1d2d640222b003ec1a4af4a4af8b70b97f5b` |
| `logs/phase0c_02_fsck_full_name_objects.stdout.txt` | 91,913 | `d59275bd0e8b8e2e5a6da4d245a1c86915b6d8589962860d34fc74ad35f13a28` |
| `logs/phase0c_08b_tmp_grouping_corrected.json` | 2,900 | `df3a492a299ba2e77d9a4d97a5ee00ee5868d2dd4826d1d30276b7ac2d88e237` |
| `manifests/sqlite_inventory.csv` | 8,941 | `dcb815561da9006b43a447394368c06a08bafcb36167450f06d7f93b1e4e34b5` |
| `manifests/sqlite_sealed_clone_manifest.json` | 12,614 | `3a068a13577c1cbe7c8ee1e824995ca1d9a2428dd518799719b003c51e57712f` |
| `logs/phase1_08_sqlite_integrity_results.json` | 16,554 | `d1387640eaa2d4eeb25b792c037fa98d9f7f6d4f3f0d0dcc6c14488660b33f83` |
| `logs/phase1_07_windows_events.json` | 55,152 | `56fce89e1676b8e9519b82c31e8a5f40f3a877561c6fdd9875374e5c45ee1087` |
| `logs/phase1_06b_recovery_summary_retry.json` | 18,752 | `8835e009fb73aef68afca789c1c62f8d8dd6d330b14474245af124f459c2b00b` |
| `logs/phase2_01c_zero_plaintext_secret_audit_final.json` | 102,396 | `6d342b2ac4fafc821b81628e57f0c43c6309ba0b32412feddd9096cb83dc39a6` |
| `logs/phase2_02_forensic_git_worktree_status.stdout.txt` | 40,912 | `31dbb17b32776e6f8ae52139bfff9941535b77f95c7cf04a16a8458b40924699` |
| `logs/phase2_04_source_git_drift_detail.json` | 132,731 | `87c85785a44e53d0a1c2c3a6bdb32d1423ba1994e469ef26be29953f0a05ca2a` |

## 11. 已驗證／推論／未知

### 已驗證

- 5 個 Git loose objects損壞；其中 1 個仍由本機 covered ref路徑可達。
- 9 個 unique offline SQLite samples confirmed corrupt；7 個 unique samples完整 integrity ok。
- 23 次 storage reset warning及其時間。
- SQLite runtime版本落入官方 WAL-reset bug受影響範圍。
- portable/history有 nonempty candidate secret欄位，且零明文 detector沒有輸出值。
- `.pytest_cache` ACL access denied。
- 工作樹 75 tracked unstaged修改、634 untracked；ignored未覆蓋。

### 合理推論

- 9/1～9/3 可合理區分 6 次主要 DB事故。
- storage reset、受影響 SQLite版本、多 connection/checkpoint/publish共同構成最可信的多因素事故模型。
- Claude提出的 singleton/PID/port guard值得採用，但 llama-server不是已證實 DB writer。

### 未知

- RaidPort0/1 到 C:／ADATA NVMe的精確映射。
- 每次 corruption真正發生的秒級時間與 writer PID。
- 事故當下 WAL/SHM/journal是否存在、是否同 generation。
- 正式 DB目前結構是否完整；本次刻意未開啟。
- portable/history candidate secrets是否仍有效、是否已被外部取得。
- 已刪 refs、遠端 clone/fork、release、CI artifact、雲端備份與回收筒的暴露範圍。
- `.pytest_cache` 是否有任何 open handle。

## 12. 本次變更與驗證

專案內只建立本報告並覆寫 `docs/CODEX_REVIEW_PACKET.md`；沒有修改 Python、分析邏輯、DB、`.git`、tests、scripts、config或密鑰。

- 風險：低（文件寫入）。
- tests：未執行；本任務明確禁止啟動 app/tests，且沒有 Python source變更。
- py_compile：不適用。
- 遺留風險：儲存 reset、正式 DB狀態不明、Git reachable object缺失、候選密鑰散布。
- 下一步：依 8.1～8.4 順序另行執行修復；本稽核不自動進入修復。

## 13. Git 修復執行補遺（2026-09-03）

本節記錄後續獲授權執行的 Git-only 修復；不改寫前述唯讀鑑識在當時的結論。本次仍未處理 SQLite、storage driver／firmware 或 credential rotation。

### 13.1 原方案校正

- `git remote -v` 已再次確認為空，但「沒有 remote」本身不能證明物件可安全捨棄，仍需檢查保留 refs 與 master reachability。
- `453961feeadaf9b09cad6bf673ff9077973d50e0` 並非只被一個 Codex ref 引用；現場重新檢查時共有 7 個 `refs/codex/turn-diffs/...` refs 可達該 blob，全部映射到 `tests/test_line_bot_gateway.py`。
- 5 個損壞物件在 `master` 的 hit count 均為 0；其餘 4 個在 `--all --reflog` 的 hit count 也均為 0。`453961...` 只存在於 Codex turn-diff namespace，不在 branch、tag、remote 或 master 歷史。
- 因 master 可完整封裝及驗證，不需要截斷 master 歷史，也不需要以較舊 commit 建立 orphan 新歷史。正確作法是只將 `refs/heads/master` 封裝為 bundle，讓 Codex transient refs 與 unreachable corrupt loose objects不進入新 repo。
- 沒有直接刪 loose object、刪 Codex ref、覆寫來源 `.git` 或覆蓋原工作目錄。

### 13.2 工作樹封存

修復根目錄：`$Desktop/taiwan_50_git_repair_20260903-162045`

- 封存基準：`master` / `6d9e34b2875058394004800a605509f049b45ea2`。
- 實際狀態：75 個 tracked modified、635 個 untracked。相較原報告的 634，多出的 1 個是本稽核建立的 `docs/GIT_REPO_INTEGRITY_AUDIT_2026-09-03.md`。
- 710 個檔案、78,283,305 bytes 均複製到 `worktree_snapshot/`；來源前後穩定失敗 0、copy mismatch 0。
- `evidence/worktree_manifest.csv` SHA-256：`7302344a142342e4bc3b332da48641c214f50516ad2c306685361d4c653c1d70`。
- 另建立 75 份逐檔 `--binary --full-index` patch；空 patch 0。`evidence/tracked_patch_manifest.csv` SHA-256：`da33e551407af256f02bb4f56b4c2a94857c5b763a1080f1b29b81896fa24222`。

### 13.3 Master 與忠實 rescue clone 驗證

- `master.bundle` 僅包含 `refs/heads/master`，SHA-256：`16ba9be38bb5c733057d3ac4724b71467e0d044d1438bcb2e20cd6d5ce561d67`。
- bundle verify exit 0；source/new master commit count 均為 23；HEAD 完全一致。
- `clean_repo/` 先由 bundle 建立，再套用 75 份 patch及複製 635 個 untracked files。
- patch 套用後有 73 個 byte hash差異，逐一證實全部只屬 LF/CRLF checkout 正規化；以 sealed snapshot 還原原始 bytes後，710/710 內容 SHA-256 全部一致。
- `clean_repo` 最終狀態為 75 tracked modified、635 untracked、content mismatch 0、remote 0、`git fsck --full --no-dangling` exit 0；唯一 ref 為健康的 `refs/heads/master`。

### 13.4 整理後的 tidy repo

為避免「Git 已修好但工作樹仍有 710 個 dirty entries」，另由同一 bundle 建立 `tidy_repo/`：

- 分支：`codex/worktree-rescue-20260903`。
- Rescue commit：`55eba38018255f11e14920c573cf154ea8f3af54`，parent 為原 master HEAD。
- 納入 554 個可維護路徑：原 75 個 tracked修改，加上 479 個 source/docs/tests/scripts/config 類 untracked files。
- 156 個 local-only files不放入 Git：`MitakeGU/` 135、`.tmp/` 20、`backups/` 1。它們仍完整保存在 `worktree_snapshot/untracked/` 及 manifest，不是刪除。
- Staged path與預期 path完全相同；DB、dist、portable、runtime、model、log、cache、實際 `.env` 與 local credentials均未納入 commit。
- `.env.example` 的高風險 credential fields均為空；`.env.line_bot.example` 的 credential fields為空或明確 local placeholder。先前暫存掃描的命中是 newline regex與 `getattr/os.getenv` expression誤判，已用 line-bounded規則重驗。
- 專案 Python 3.13.15 對 tidy repo 執行 `compileall -q -f`：exit 0。未 import module、未開啟 DB、未啟動 app。
- Commit作者使用一次性、不冒用使用者身份的 `Codex Rescue <codex-rescue@local.invalid>`；未寫入全域或 repo identity設定。
- Rescue commit後 `git status` entries 0、remote 0、兩個保留 branch refs均可 `git log`、`git fsck --full --no-dangling` exit 0。

### 13.5 最終處置

- 原始 `taiwan_50` 目錄、原始損壞 `.git`、既有 master backup與鑑識證據均保留，未刪除、未改名、未覆蓋。
- `clean_repo/` 是逐 byte忠實的救援副本，供比對與回復。
- `tidy_repo/` 是建議後續使用的整潔 Git 工作樹；rescue commit只代表完整保存目前可維護成果，不代表所有功能已通過回歸測試或可直接發佈。
- 完整 evidence位於修復根目錄的 `evidence/`。在人工切換工作目錄前，應先檢視 `tidy_repo` 的 commit diff與必要回歸測試；切換不屬於本次執行範圍。
