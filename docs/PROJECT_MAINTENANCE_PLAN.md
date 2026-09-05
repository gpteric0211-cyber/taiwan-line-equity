# 專案瘦身與可維護架構計畫

## 最新執行增量：2026-08-31 09:29 +08:00

本節優先於下方原始盤點狀態。使用者本輪明確要求先審查 26 筆，再开始清理，
不代表核准 C21 契約變更、validator 擴充或部署。
已完成 [26 筆逐案語句／證據審查](LINE_MODEL_26_SAMPLE_MAPPING_REVIEW.md)，再執行 C1/C2 第一批。

### 已執行的小批次

- 精確範圍：僅 `tests/__pycache__` 內 manifest 明列的 **254 個 `.pyc`**。未刪目錄。
- 全部有同名 `.py` 可重建；無 Git tracked cache、symlink/junction/reparse point；逐檔 fsutil hardlink count=1。
- 執行前確認沒有 Python pytest/compile process，Windows task action 中也沒有測試候選；未停用背景工作。
- 先建立 `logs/absence_mapping_review_20260831/test_bytecode_recovery.zip`，
  再逐一解讀 archive entry 比對 SHA；254/254 相符後才刪除原快取。
- 先建立獨占建立的 `cache_deletion_journal.jsonl` intent，再逐檔 `Remove-Item -LiteralPath`；
  沒用遞迴刪除、shell拼接、git clean或刪除未知檔案。既有intent阻止自動重跑。
- 原快取邏輯大小 **4,392,268 bytes**（約4.19 MiB）；還原zip **1,642,026 bytes**。
  僅這兩者淨減 **2,750,242 bytes**（約2.62 MiB）；未量實際配置磁區，不能當成實體磁碟釋放量。
  本輪另新增26筆完整稽核JSON／測試stdout，所以**不宣稱整個repo淨縮小**。
- 清理後完整pytest不重新寫bytecode（僅本次驗證程序設PYTHONDONTWRITEBYTECODE=1，不改設定檔），
  已確認目標目錄剩餘檔數0；之後一般測試仍可自行重建快取。

完整證據及本機一次性清理工具在 `logs/absence_mapping_review_20260831/`：
`cache_manifest.json`（路徑、size、原碼與快取SHA）、`cleanup_admission.json`、
`cache_deletion_journal.jsonl`（每筆時間戳）、`cleanup_result.json`、`clean_test_bytecode.ps1`。
該腳本明示 Windows-only，不是跨平台runtime功能或新的重啟機制。

還原建議：平常直接重跑測試，讓Python按當前版本重建。若稽核需要byte-exact舊快取，
先核對manifest的原碼SHA與zip entries，僅解出個別同名檔到原 `tests/__pycache__`；
目的檔已存在則停止，不覆寫，不把3.11快取当成3.13可執行驗收證據。zip保留，未永久銷毀。

### 刻意保留的項目與理由

| 項目 | 本轮決定 |
|---|---|
| scripts／根目錄／review_src 的 pycache | 保留。root launcher正在執行；scripts cache還有歷史編譯失敗／權限事故引用，未納入本批 |
| .pytest_cache | 保留 lastfailed/nodeids 等除錯狀態，不以清理掩蓋紅燈 |
| finmind_update_service.py 空殼 | 有 skill 04 規劃引用；未做動態入口/打包全證明，不刪production |
| corrupt-20260830-ff | 事故稽核文件明確引用，保留 |
| models/runtime/venv/DB/env | 現役依賴／資料與設定，保留 |
| dist四套發行、安裝下載包、DB備份 | 尚未確認哪套為必要回退／離線安裝來源，不以名稱或日期認定垃圾 |
| A20/B30、restart_intent、legacy/pending、紅燈、歷史報告 | 驗收及恢復證據；測試直接讀取，全部保留 |

清理前後各完整py_compile：349專案來源＋2 audit helper，0錯；
完整pytest：前 `2 failed, 941 passed in 29.63s`，後 `2 failed, 941 passed in 35.38s`。
失敗仍是原C21兩案，沒有新增／修改測試、skip或xfail。
349來源不變、11/11 protected不變、私有設定hash不變。詳見 before/after_cleanup_state.json 與完整stdout。
注意：上述是舊凍結清單，最終盤點發現漏列根目錄Fugle runner；已另補全量350份專案Python編譯，0錯，
見 `compile_full_inventory.txt`。不冒稱額外runner有清理前hash，也不重寫先前輸出。
未重啟、部署、呼叫模型或發送LINE；測試與壓縮有CPU／I/O負載，沒有連續telemetry能宣稱零影響。

### 維護閱讀順序與下一批界線

1. 看 `CODEX_REVIEW_PACKET.md` 的最新段落確認完成/未完成，不把下方歷史報告當現況。
2. 共用市場資料入口看 `LINE_BOT_MARKET_DATA_API.md`：兩端同一resolver，對話庫仍獨立。
3. 本輪語意風險看 `LINE_MODEL_26_SAMPLE_MAPPING_REVIEW.md`；C21另待核准，不能以清理轉綠。
4. 架構責任看本文件第5節；現況程式維持既有api/services/repository/adapter/analysis/core，不搬函式。
5. 容量最大下一批是dist／installer，但要先指定保留的已驗證回退包；不能宣稱那48 GiB全部可刪。

本次清理風險低（可重建且保留byte-exact archive）；模型語意風險仍中／未完成。
原始盤點保留於下方作時間點證據，舊測試數與「未刪檔」描述僅對應當時，不覆蓋本節。

---

## 原始盤點（2026-08-31 01:36；歷史保留）

盤點日期：2026-08-31，Asia/Taipei。狀態：盤點／規劃已完成；未刪檔、未搬移程式、未部署。
前置：先取得獨立的validator修復授權與離線驗收，不把「完成後整理」當作跳過既有核准關卡。
本文件是現況索引與後續批次計畫，不是宣稱所有架構問題已解決。

## 1. 重要結論

主要容量在打包產物、模型與資料，不在Python原始碼。
原始碼維護成本則集中於大型入口／service、文件版本混雜與產物、證據生命週期沒有分清。
「瘦身」分成兩個獨立工作：磁碟空間清理，以及保留行為的程式整理；不能用刪分析邏輯換行數。

本機沒有git remote。GitHub connector可用，但名稱搜尋沒有建立本機與遠端的可靠對應；
未選用搜尋到的相似專案，未新增remote、未commit/push、未刪任何GitHub檔案。
如需同步GitHub，必須提供或確認實際owner/repository，且另檢查目前未提交內容。
盤點時git status --porcelain=v1有72筆已修改、279筆未追蹤狀態項目；未追蹤不代表可以刪除。

## 2. 容量原始數字與限制

取樣完成：2026-08-31T01:36:52.1591260+08:00。
方法：rg --files --hidden --no-ignore -g '!.git/**'列路徑，讀取檔案Length後依頂層分組；
沒有讀DB內容或模型權重，沒有執行app、模型、打包或更新排程。
共204,047個檔案、119,165,691,934 bytes，約110.98 GiB；不含.git。
這是逐檔**邏輯大小**且為非原子取樣，不是實際配置磁區或確定可釋放空間；
未證明同大小檔案內容相同，未完成hardlink、壓縮、執行中引用與備份保留需求核對。

| 頂層 | 檔案數 | bytes | GiB | 本輪處置 |
|---|---:|---:|---:|---|
| dist | 118818 | 51791659639 | 48.23 | 舊產物候選，先確認保留／還原用途 |
| models | 15 | 50228541264 | 46.78 | 保留；不能把已註冊模型或原始權重當快取 |
| runtime | 1049 | 5805531766 | 5.41 | 保留執行環境；安裝下載檔另列候選 |
| review_src | 10668 | 5345154898 | 4.98 | 包含DB與venv，不是4.98 GiB原始碼 |
| logs | 72848 | 4717806902 | 4.39 | 包含DB快照及驗收證據，不可整目錄刪除 |
| backups | 1 | 1263927296 | 1.18 | 恢復用途未撤銷，保留 |
| tests | 332 | 5117037 | <0.01 | 保留測試原碼；pyc另列 |
| docs | 65 | 4617512 | <0.01 | 歷史／現況分流，不靠刪證據減容量 |
| scripts | 214 | 2862228 | <0.01 | 逐一核對CLI／排程／打包呼叫 |

其他根目錄檔案與隱藏目錄已含於總數，表中只列主要分類。
cache名稱篩選命中39,608檔、818,430,320 bytes（780.52 MiB），
其中大多位於dist與venv，**不是整組獲准刪除清單**。
根目錄__pycache__、.pytest_cache及scripts/tests快取合計6,524,490 bytes（6.22 MiB），
是較小但可先審核的低風險批次。編譯快取刪除後可能使下一次啟動較慢，不能宣稱零影響。

## 3. 清理候選與禁止誤刪

### 3.1 可先審核的可重建檔

1. 根目錄、scripts、tests的__pycache__/*.pyc與.pytest_cache。
   執行前須確認沒有正在跑的測試，逐檔確認對應.py仍在、不含手寫檔，並輸出確切清单。
2. 已完成測試run內的compile_cache：只能選.pyc，不含stdout、JSON、原碼快照、manifest。
   驗收證據依舊保留；若某回合把編譯產物作必要恢復來源，該回合不清。
3. runtime/downloads中可重新取得的安裝包，先確認部署不依賴離線副本：

| 檔案 | bytes |
|---|---:|
| runtime/downloads/OllamaSetup.exe | 1564819104 |
| runtime/downloads/cudart-llama-bin-win-cuda-13.3-x64.zip | 390970417 |
| runtime/downloads/llama-b10516-bin-win-cuda-13.3-x64.zip | 146794747 |

合計2,102,584,268 bytes（1.96 GiB）是待確認候選，不是已回收空間。
不刪runtime/ollama、runtime中的現役執行檔／DLL，不刪venv。

### 3.2 最大空間候選：舊發行產物，需確認用途

dist中目前有四個發行目錄：

- Taiwan50Dashboard_Portable_Windows
- Taiwan50Dashboard_Portable_Windows_EmbeddedTest
- TaiwanStock_PostClose_Portable_Windows
- TaiwanStock_PostClose_Portable_Windows_20260824

兩個PostClose產物各含一個18,973,870,432 bytes模型blob；本機models也有同大小GGUF與Ollama blob。
只有名稱／大小證據，未對四份大型檔案算完整SHA-256，也未做NTFS file-ID查核；不宣稱四份完全重複。
models兩個檔案的PowerShell LinkType/Target為null，不足以保證沒有其他共用配置關係。
若某套dist還是唯一已驗證回退包，必須保留；候選清除須先留下至少一套確認可恢復的版本。
不因日期較舊便刪除、不為了空間卸載本地27B/28B或vision模型。

### 3.3 明確保留

- review_src/data/taiwan50.db及其WAL/SHM；私有.env、LINE設定、對話DB與金鑰。
- backups/taiwan50_before_price_volume_repair_2026-08-25.db。
- logs/market_foundation/taiwan50_pre_scoped_reconcile_20260827.db。
- A20/B30原始JSONL、S1-R／相容性失敗原文、protected/source manifests、restart_intent與legacy/pending快照。
- review_src/services/line_model_benchmark_service.py.corrupt-20260830-ff：不是正常import來源，
  但docs/LINE_MODEL_OPERATIONAL_SAFETY_AUDIT.md:249–250明訂供0xFF事故稽核，保留或未來連索引封存。
- 既有紅燈測試：不能藉瘦身移除59個失敗、改xfail或刪fixture，使套件看起來全綠。

特別注意：tests/test_line_model_absence_only_compatibility_contract.py:37–40直接讀取兩份歷史JSONL：

```python
"logs/line_model_shadow/final_phase_a_current_source_v2_20260830.jsonl"
"logs/line_model_shadow/final_phase_b_current_source_v2_20260830.jsonl"
```

因此logs不能整體刪除、搬家或新增一條規則就當成不必要檔案。
未來若把固定測試語料移到fixtures，須獨立做bytes/hash保持的遷移、改所有讀取者與引用，不能混進validator修復。

## 4. 程式碼候選的真實用途核對

| 檔案／組別 | 已查證用途 | 判定 |
|---|---|---|
| review_src/services/finmind_update_service.py | 只有搬移說明及MOVED_FUNCTIONS=()；在review_src/scripts/tests/packaging靜態引用搜尋未命中 | 待確認可移除的空殼候選，不宣稱完成動態引用／打包驗證 |
| review_src/services/stock_detail_service.py | app.py:226及api/watchlist.py:18直接import | 正在使用，不可按「partial extraction」刪除 |
| run_fugle_all_from_xlsx_progress.py | scripts/run_post_close_daily_pipeline.py:382、packaging/build_full_post_close_windows.ps1:136、tests/test_fugle_batch_runner.py:13引用 | 正在使用，不是根目錄雜檔 |
| 根目錄與review_src/start_dashboard.py | 不同bat/cmd入口各自呼叫；根目錄版本也被打包器使用 | 不是已證明重複；統一入口需另測參數、cwd、port及portable模式 |
| review_src/README_v231至v244 | 14份、合計19,256 bytes，Git已追蹤 | 可做歷史索引以減少視覺雜亂，省不了實質空間；本輪不搬移 |

空殼原文（finmind_update_service.py:3–8）：

```python
# Refactor R6 note:
# `upsert_finmind_stock_data()` and `update_finmind_codes()` remain in app.py for now.
# They still coordinate update-slot locks, status messages, quota sleeps, DB writes, and
# cache pruning. Move them in a later narrower phase with explicit callback wiring.

MOVED_FUNCTIONS: tuple[str, ...] = ()
```

不能只以grep沒有import就認定死碼：還要核對importlib、CLI、Windows排程、BAT/CMD/PS1、
打包複製規則、FastAPI router註冊及外部操作者入口。本輪尚未完成上述完整死碼證明，故程式刪除數=0。

## 5. 現有架構與目標責任

保留目前目錄名稱services/，不另造service/或把整個review_src搬家。
repo仍是一個共用分析核心、兩種使用者入口與獨立Bot資料介面，不拆成新微服務。

```text
網頁入口 app.py → API/use-case組裝 ┐
                                ├→ repository / adapter / analysis → core
LINE入口 line_bot_app.py → LINE service → Bot API(bot_app.py) ┘
                                                ↑
                               canonical DB + quality + 唯一referee
```

模型、圖片與對話記憶屬LINE service的受控依賴；本次不更動它們的接線。
新V2接線是否接管回覆仍須原有獨立驗收，不能藉架構整理開啟。
review_src/core/config.py:16、31、35–36顯示預設資料在data/，可用TAIWAN50_DB_PATH覆寫；
不能因根目錄也有一個taiwan50.db就自行猜哪份可刪，須核對實際設定與使用者程序。

| 現有位置 | 目標責任／可維護規則 |
|---|---|
| app.py、bot_app.py、line_bot_app.py | 應用組装入口；逐步只保留startup/lifespan/router/static mount |
| api/ | HTTP解析、授權與response shaping；不直接做重分析或資料修復 |
| services/ | use case協調；Web、LINE共享同一canonical事實與裁判結果，不複製公式 |
| repository/ | DB存取；明確分開read-only讀取與更新寫入 |
| adapter/ | 外部資料及模型／LINE傳輸，不決定股票主結論 |
| analysis/ | 純分析；原有公式及11個protected檔不因整理改變 |
| core/ | 設定、資料品質、契約及共用infra，不反向import上層 |
| scripts/、維護工具/ | CLI／平台包裝入口；Windows特定邏輯標明平台，不偽裝跨平台 |
| tests/ | 既有檔名先維持；另以索引区分unit、integration、契約、實機驗收 |
| docs/ | 現況索引、設計、驗收、歷史報告分清；來源行號與驗證日期並列 |
| logs/、backups/、dist/、models/、runtime/ | 各自訂生命週期，不能用同一刪除TTL處理 |

本輪實測檔案行數（包括空白行）：

| 檔案 | 行數 | 後續最小拆分方向（非本輪實作） |
|---|---:|---|
| review_src/app.py | 6028 | 先移出單一row/detail use case，再搬对应route；不讓api import app形成循環 |
| review_src/services/line_bot_service.py | 4201 | 按路由、內容組裝、交付後處理逐步明確責任；取消／共用核心／V2接線另案 |
| review_src/services/bot_market_data_service.py | 2505 | 從有純輸入輸出的查詢解析／payload組裝開始，金融判斷仍共享 |

這是優先級建議，不是承諾「檔案變短即安全」。每次只移一組函式，先保留行為與import相容入口。
舊ARCHITECTURE.md雖保留歷史基線，仍有「未找到api/repository/data_quality」文字；不能當作2026-08-31現況。
現有api、adapter、analysis、repository、services與core/data_quality.py都存在。
本輪没有重新做全部GET SQL副作用、循環依賴或死碼動態審查，不宣稱這些問題已解決。

## 6. 順序與逐批驗收

| 批次 | 前提／內容 | 驗收與停止條件 |
|---|---|---|
| R0 修復前置 | 使用者確認validator白名單、C16／C21取捨；完成獨立離線修復 | 候選replay＋完整compile/pytest＋11 hashes；未核准不先改production |
| C1 清理清單 | 精確路徑、類型、bytes、可恢復方式；核對活躍程序、符號／硬連結、排程／打包依賴 | dry-run只列不刪；掃描錯誤／檔案已變動即排除，不默默跳過 |
| C2 可重建快取 | 只執行C1確認的第一批cache清單，避開執行中的服務與測試 | 原碼／fixture／DB/config hashes不變；回報實刪檔數及實際容量差，無假稱零影響 |
| C3 舊發行與安裝包 | 操作者確認哪套dist保留、哪份僅為可重建產物；確認恢復包與取得來源 | 先移至可恢復隔離位置並保留manifest；同磁碟隔離不算已釋放空間；永久刪除另確認 |
| C4 死碼小批 | 每檔完成靜態＋動態入口／打包／排程證據，先從空殼候選 | 修改前後全量compile/pytest；有任何新失敗就停止，不能刪測試補救 |
| C5 架構小步 | 前述驗收穩定後，一次一組helper/service/route責任搬移 | API／LINE序列化契約比對、protected11/11；無新循環依賴，app仍能啟動 |

任何刪除前都要解析為專案內的確切絕對路徑，拒絕指向workspace根、模型、DB、證據或symlink外部目標。
實作設定與命令以專案相對路徑、pathlib、參數注入為基礎，不硬編碼使用者、磁碟代號或機器port。
不使用git clean -fdx、git reset --hard、整個logs/dist的萬用刪除，也不因untracked就丟棄工作。
Git已有多批未提交變更，清理前後必須能區分本輪與使用者／前輪變更；本輪不代為commit或推送。

## 7. 本輪驗收與尚未完成

本輪只新增本文件、在ARCHITECTURE.md加現況入口及更新CODEX_REVIEW_PACKET.md；不修改Python／測試／env。
Protected hashes重新核對11/11一致。未重跑pytest：没有程式碼變更；
最近完整測試仍是absence_only_compat_20260831_010509/final_pytest_stdout.txt的59 failed、827 passed，
是已知待修契約，不冒稱本輪回歸全綠，也不把舊測試結果當作尚未發生的刪除驗收。

盤點命令兩次錯誤已排除：不存在packaging/build_portable.py（實際是ps1/sh）；
PowerShell對ReadLines的.Count曾列成每行1，已改Measure-Object重新計數，上表採更正後實測。
容量掃描的204,047檔與bytes來自完成的同一掃描，不使用那次錯誤行數輸出。

尚未完成：validator修復授權、实际刪除、完整dead-code證明、執行中／外部排程引用查核、dist用途確認、
模型重複內容／磁區查核、GitHub repo身份確認、架構函式搬移及其驗收。
本輪刪除0檔、回收0 bytes、服務重啟0次；未量服務telemetry，不宣稱測得零服務延遲。
maintainable-refactor使磁碟清理、程式整理、金融行為變更與部署維持不同批次，保留原始證據。
