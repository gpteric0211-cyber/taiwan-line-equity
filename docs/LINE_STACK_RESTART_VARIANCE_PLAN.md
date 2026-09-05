# LINE 完整重啟量測計畫 — 本次限單次快速確認

更新：2026-08-30 17:39，Asia/Taipei。狀態：Q1 快速確認完成1/1；line與line-phase-d均PAUSED，其餘樣本不自動執行。

Q1於17:31:27.1386138完整重啟，17:31:52.558觀測完整恢復（25.419355秒）；
六檔pending已精確還原且不再重啟，前後完整測試均688 passed、來源339/339、protected11/11。
這是快速確認，不是完整變異或Phase1部署。當次原始結果與完整測試碼見
`docs/LINE_STACK_QUICK_RESTART_Q1_20260830.md`；第3–6節方法保留不變。

沿用本對話 automation `line`，取消原 8/31 02:30、8/31 06:30、9/1 02:30 排程。
只排 Q1 一次；完成、失敗或跳過後暫停同一 heartbeat，不續排 R1／R2／R3。
本次不新增 heartbeat，也不提前承諾未來 OS 操作一定獲准。
Windows 時區為 Taipei Standard Time（UTC+08:00、無夏令時間）。
原始配置核對與雜湊／回歸證據：`logs/line_model_shadow/restart_variance_20260830/planning_evidence.json`。

## 1. 使用者授權與明確禁止

在既有 2026-08-30 14:23 的 127.055702 秒樣本之外，只做 **一次快速確認**。
這不是完整變異證據，也不是 Phase 1 部署。使用者最新確認為開發階段、無真實
使用者；這是使用者提供的環境狀態，不能代替流量量測或成為省略任何准入檢查的理由。

每次沿用已驗證流程：保存當次 pending → 精確恢復凍結舊版六檔 → 完整重啟既有
launcher → 完整恢復／留存證據 → 還原當次 pending，**不再次重啟**。
不可把「恢復 pending」誤解為用 pending 啟動正式服務。

- 每次可接受中斷上限沿用 300 秒；不是保證一定在 300 秒內恢復。
- 不新增局部重啟、熱重載、替代 launcher、系統服務、Windows 排程或復原基礎設施。
- 不部署 cold-load 保護／新 Schema／Q2 修正，不做 Phase 2 或後續階段。
- 不呼叫 Phase A/B/D runner、品質樣本、併發負載或 vision cold 探針。
- 不改股票公式、referee、canonical DB、model/context/KV/parallel 設定或網路設定。
- 原有 startup text warmup 是完整重啟流程的一部分；不得再追加另一個 warmup 呼叫。
- 不重開電腦、不清空 OS/檔案快取、不刻意卸載額外模型來製造「漂亮的冷／暖樣本」。
- 使用 Codex 現有 thread heartbeat 延後執行，不建立未審查的自動重啟程式。

## 2. 指定時段、間隔與停止條件

| 樣本 | 台北時間的預定准入 | 準備／量測窗口 | 時間依據 |
| --- | --- | --- | --- |
| Q1 快速確認 | 2026-08-30 17:24 | 17:24–17:54，最晚 17:49 開始重啟 | M0 14:23:41.9307356 + 至少三小時；最早合法停止時間 17:23:41.9307356 |

這是准入時刻，不保證精確於該分鐘停止服務。以既有 `line` heartbeat 排一次，執行時
重新讀此計畫及 plan.json；**完成、失敗、skip 或 missed_window 均暫停 heartbeat**，
不再自動啟用原 R1／R2／R3、今日串接樣本或額外樣本，等使用者日後明確恢復。

實際重啟開始距任何上一筆實際重啟開始 **至少 3 小時**；不符合則跳過，不縮短間隔。
最晚必須在窗口結束前 5 分鐘完成准入並開始重啟，否則記錄 missed_window，不窗口外補跑。
Q1 最多一次重啟，失敗也算一次；沒有為湊成功率而追加嘗試的授權。
若恢復失敗、超過 300 秒、來源身份不明或 pending 無法恢復，停止測量並立即報告。
超過預算不代表把服務丟著不管：優先用原已驗證版本恢復，記錄實際總影響；不臨時造新機制。
不得因 App／主機不可用而跨日補做突襲停機；正在恢復中的故障仍優先恢復，不中途放棄。

機器可讀清單維持在 `logs/line_model_shadow/restart_variance_20260830/plan.json`；
本次輸出改用獨立 `logs/line_model_shadow/restart_quick_20260830/Q1/`，不覆蓋 M0 或
原 R1／R2／R3 目錄。停止前先建立不可覆寫的 `restart_intent.json`
（時間、slot、目前 launcher 身分）。若已有 intent，先查現場 PID／job／observer，
復原或補記已有樣本，**絕不重新發起**。無准入則保存原始原因，不冒稱完成重啟。
完成含精確還原 pending、完整編譯／pytest 與 hashes 驗證；收尾不再次重啟。

交付本次 warmup、runner load、總恢復、原始失敗及暖冷狀態，明確標示：
**這是快速確認樣本，不是完整變異證據；正式上線前仍需補測多次不同暖／冷狀態。**
第 7 節保留為未來多次量測的模板，本次不執行其完整變異統計或發布決策。
結案後回到正常開發狀態，不因量測繼續等待；這不構成 Phase 1 部署或 Phase 2+ 授權。

## 3. 每次動作前必做的准入檢查

1. 讀 AGENTS.md、maintainable-refactor 及既有完整量測報告與本計畫；核對尚無新授權改變。
2. 查當前時間／slot，確認不是遲到回放、不是已執行、且間隔至少 3 小時。
3. 全部 339 個 frozen source 與 `phase1_20260830/predeployment_manifest.json` 一致，
   六個 legacy／pending snapshot 與原 `legacy_launch_manifest.json` 雜湊一致；
   protected 11/11，私有設定 hash、model digest、context、driver、CUDA、runner 皆核對。
   若 pending 或 bound source 有新改動，**不要覆寫或回退使用者工作**，停止准入並報告。
4. 確認 model/controller queue=0、active_category=null，無正在執行的維護／模型 job；
   讀取最近 15 分鐘 receipt/exchange 聚合數，不讀出訊息或識別碼；有新事件則跳過。
   缺少可用流量證據也不能聲稱離峰已被證明，記錄為 preflight 不足並跳過。
5. 核對 Windows 市場更新與 Codex 排程是否會競爭；**不停止或修改它們來做出空載樣本**。
   目前另外暫停舊 `line-phase-d` heartbeat，因為它違反使用者最新的階段禁止；不自動恢復。
6. 確認可唯讀查詢程序身分、訪問官方 LINE、開始獨立 observer，以及已獲准執行完整 stop+start。
   未能在停止前確認必要權限／工具就緒，記錄 permission_preflight_failed；不先停服務再等批准。
   不修改 Codex sandbox、全域權限或防火牆來繞過限制。
7. 在 pending 原始碼狀態先完整 py_compile（所有 review_src/scripts/tests Python），
   完整 `pytest tests -q`；0 failure 才继续。每次重新跑，不沿用今天結果。
   目前已核對的專案直譯器為 `review_src/.venv/Scripts/python.exe`（現有 Windows launcher）；
   不能使用 PATH 上未安裝 pytest 的系統 Python，也不能為排程臨時安裝或更新依賴。
   保存每條命令、開始／結束時間、exit code 與完整 stdout/stderr，不只末行通過數。
8. 保存當次 pending 六檔的 byte-exact 備份與 hash；使用 apply_patch 恢復 frozen legacy。
   核對 legacy 六檔 hash 後編譯；觀測器至少取得一組全 healthy baseline。
9. 停止前再次核對 active/queue、LINE 活動、完整 PID/parent/creation time 與指令路徑。
   PID 必須重新發現；不得沿用原樣本的 36500、11460 或其他舊 PID。

來源 → canonical DB → services/referee → market API → LINE 的金融資料流不变。
本計畫只安排既有 stack 生命周期與唯讀觀測，不改這條資料流的邏輯。

## 4. 沿用已驗證觀測與完整重啟

觀測器原檔：`logs/line_model_shadow/restart_measurement_20260830/observe_restart.py`。
程式接受 `--root`、`--output`、`--duration`；各次 output 指向自己的新 slot 目錄。
不得沿用原目錄的 `observer_stop.json`，否則觀測器會立即退出。
沿用 local/public probe 完成後 0.5 秒、官方 GET 完成後 5 秒的取樣與原始起訖時間。
timeout 參數不是整次 HTTP 的硬 deadline；保留所有慢探測與失敗，不改算法以美化數字。

獨立 observer 在停止前運作；既有 launcher tree 全停後，立即用已核對舊來源完整啟動，
Start-Process 必須 Hidden；stop 與 start 的已准許執行環境必須相同。
不要改用「偵測既有三服務健康所以 reuse」冒充完整重啟。新 state 必須 reused_local_stack=false。
完整恢復需同時滿足：8010 readyz／8020 version／8021 health、**新 endpoint** 公開 health、
官方 endpoint active/match、文字模型原 digest/context resident、startup warmup 完成且 GPU idle。
記錄 `completed_tasks` 從本次新 process 的初值到完成，不把上次累積值當本次完成證據。
公開第一次 200 之後的再失敗也保留。額外官方空事件 webhook test 可證明實際 POST 路徑，
不可將仍指向舊網址的 GET active=true 當新服務已可用。

恢复後先保存新 PID／來源／模型身分，再還原當次 pending 六檔，**不重啟**。
若在切回 legacy 後、停止服務前取消准入，也必須還原當次 pending；先核對當前檔案
仍等於自己切入的 legacy，避免覆寫這段期間其他人的新修改。遇到不符即保留兩份並報告。
核對所有 frozen source hash、private config、protected 11/11；正確 cold capability
`/internal/line-model-benchmark/cold-load-capability` 應仍為 404。
再完整 py_compile 與 `pytest tests -q`；將測試全原文／結果與能力界線一起交付。

## 5. 暖／冷狀態的可核對定義

每次 preflight 記錄下列原始 metadata，不憑凌晨／白天直接命名為冷／暖：

- OS LastBootUpTime、觀測時間與 `os_uptime_seconds`；是否 Windows Fast Startup／電源循環能確認。
- `os_boot_kind=unknown|observed_os_boot|user_confirmed_power_cycle`，預設 unknown。
- `text_resident_before`、model digest/context/KV/量化、常駐 model 清單與既有模型最近載入時間。
- launcher age、上次完整 restart/warmup 距今秒數、最近模型工作數／時間（可取得才填）。
- GPU 使用率／VRAM、CPU／可用 RAM、既有背景工作與持久化 LINE 活動數。
- 空白／unknown 和 0 不同；無法量 OS page cache 時填 unknown，禁止假定已清空。

工程標籤：`recent_os_boot` 僅代表可核對 uptime <= 30 分鐘；不是已證實關機後冷開機。
`long_uptime` 代表 uptime > 30 分鐘；model_resident/nonresident 另列，不互相替代。
每次完整停掉模型服務後的載入皆屬 `model_cold_after_stack_restart`，仍可能受 OS cache 影響。
此次只有「不同時段、自然存在的主機狀態」授權；沒有主動 PC reboot/cache flush。
若三次都 long_uptime + model resident before，就明說 **未涵蓋真正冷開機**。
若使用者另外安排自然重新開機，可據實註記或另排一次，但本 heartbeat 不新增第四次停止服務。

## 6. 特別量化 text warmup，而不只總時間

每次保存 observation JSONL、operator QPC/wall-clock events、來源 hash、runtime PID、
Ollama/model/driver/CUDA/VRAM/context/KV/parallel/hardware、完整 pytest、protected hashes、
原始失敗原因、phase-independent readonly 日誌摘錄。所有 endpoint/identifier/secret 去識別化。

必須分開回報：

1. `restart_to_services_ready_seconds`：以 operator stop_requested 的 monotonic/QPC 為零点，
   到各端點已觀察到 bad 後首次 good 的完成時間；沿用 M0 起算點。另列最後 good／首次 bad
   及恢復前最後 bad／首次 good 的探測起訖，提供實際不可用區間的取樣上下界。
   若端點沒有觀察到 bad，不將它寫成零中斷；標為 sampling_not_observed 並核對 PID 更新。
2. `restart_to_public_first_ready_seconds` 及任何其後失敗區間。
3. `restart_to_official_confirmation_seconds`，新 endpoint 身分綁定。
4. `restart_to_text_resident_seconds`。
5. `restart_to_warmup_complete_seconds`。
6. `warmup_admission_first_observed_at`、`warmup_completion_first_observed_at` 與差值，
   明訂為觀測值，附前後 probe 行號／取樣間隙；不是假裝知道精確 scheduler start。
7. `runner_load_seconds`：Ollama 原始 `llama-server started in ... seconds`；若同一載入報兩個
   數字保留兩者及語意，不挑較快那個。另列 `/api/chat` 原始 duration，不把它當純 generation。
8. `total_recovery_seconds`：全部恢復條件同時成立，沿用原樣本算法。
9. 全部 probe pass/fail/總數和原因；任何 timeout/OOM/orphan/無恢復樣本，保留 censored/failed 狀態。

## 7. 最終變異報告，不提前核准正式部署

表格列原 M0 與 R1/R2/R3，每列附原始 artifact/行號、時間、暖冷 metadata、warmup、
runner load、total recovery、成功／失敗／跳過、比較資格與排除原因。
新增 3 筆與含原 M0 的兩組統計分開：完成數、失敗數、min、max、max-min、median，
最多 3–4 筆，**不計或宣稱有代表性的 P95**。版本／環境不同分組，失敗列不能消失。
若失敗造成總恢復未知，寫 lower bound／未恢復，不能只用成功樣本的 max 當風險上限。

最後只提供 `最壞觀測值 + 緩衝` 的候選比較表；緩衝須涵蓋取樣誤差、觀測變異、tunnel
逾時／尚未涵蓋狀態的不確定性，不預設單一百分比就是有保證。
**正式窗口與中斷預算由使用者看完樣本後另行決定**；量測完成不觸發 Phase 1 自動部署。

## 8. HTTP/2 小任務與排程可靠性

另建任務「調查 cloudflared HTTP/2 連線受阻」：唯讀 DNS/TCP/TLS/ALPN、防火牆有效設定、
路由／上游／precheck 誤判診斷。不得在本輪改網路配置或為取得樣本臨時強制 HTTP/2。
該任務只寫其獨立工作目錄，不與本 repo 的 review packet 爭寫。

Codex 現有同對話 heartbeat 是排程回來執行的機制，不保證電腦睡眠、App 關閉或權限
不足時能準時執行。請保持主機與 App 可用；因時間錯過則記 skipped/missed，不補做突襲停機。
這是核對過的官方文件限制，不是宣稱已驗證無人值守 OS stop/start 一定成功：
[官方 scheduled tasks](https://learn.chatgpt.com/docs/automations?surface=app)，複核 2026-08-30。

16:10排程時三小時間隔尚未滿；此為歷史狀態。Q1後於17:31合法執行，
目前新增快速樣本 **1/1**；仍沒有完整變異範圍，line已暫停，不安排後續自動量測。

## 9. 原排程交付的歷史驗證與限制（15:27，非新 R1 准入）

專案 Python 全 339 檔 `py_compile` exit 0；完整 `pytest tests -q` 原文：

```text
688 passed in 18.23s
```

15:25:50 核對來源 339/339、protected 11/11、legacy 六檔及 pending 六檔快照均一致，
私有設定 hash 未變。15:26:53 的 LINE ready=true、GPU active=null／queue=0；
15:25:51 正確 cold capability 為 `404 {"detail":"Not Found"}`，本輪未部署。
這些是點狀唯讀觀測與 pending 原始碼回歸，不能證明未來重啟秒數、冷開機、模型品質、
併發或無使用者影響。這次沒有執行 service-gap 量測，不將 not_run 寫成零秒。

Python/tests 修改 0。完整既有 launcher 測試碼見 `tests/test_start_line_bot_stack.py`，
亦已完整收錄於 `docs/LINE_STACK_FULL_RESTART_MEASUREMENT_20260830.md` 第 7 節；
該測試檔 hash 為 `72d8edd3f96ab135977f4c8d638091599d761d8ea58c78d691615214c1785e18`，
仍與 frozen manifest 一致，不冒稱新增三次 live 量測的測試已執行。
既有 M0 只保留為相同 legacy 方法的歷史比較，不能升格為新部署／新 Schema 證據。

保留的排程／診斷失敗及修正見 `logs/line_model_shadow/restart_variance_20260830/planning_diagnostics.md`。
未修改系統 Python、tzdata、網路、服務或模型。舊 `line-phase-d` 已暫停；
重新讀回確認僅 status 和工具更新時間有變，不恢復到先前會提前執行 Phase D 的狀態。

## 10. 今日改期的歷史證據與未完成項目（16:10，不是Q1結果）

本次依最新授權收斂為一次快速確認。第 3 節九項准入、第 4–6 節觀測／恢復／暖冷／
warmup 定義不變；Q1 真正停止前必須全部重新執行，不能使用改期時的點狀核對代替。
同日三次串接的中間草稿已由本次授權取代，保存於 superseded_chain_plan.json，未曾啟用
該三次新排程或停止服務；不要因歷史草稿存在而繼續執行。
原 plan 原文與 SHA-256、當前來源／健康／流量觀測、排程讀回及完整回歸輸出保存於
`logs/line_model_shadow/restart_variance_20260830/reschedule_same_day/`。
原文副本 `previous_plan.json` 可能僅換行重編碼，原檔 byte hash 另存於 precheck.json；
不冒稱副本與原檔 byte-exact。

16:02:16.4790247：M0 原文 `2026-08-30T14:23:41.9307356+08:00`，
最早准入 `2026-08-30T17:23:41.9307356+08:00`，尚差 4885.4517109 秒。
16:03:15：來源 339/339、protected 11/11、legacy/pending 快照各 6/6、私有設定一致。
16:03:50：三服務 HTTP 200、market/LINE ready=true、模型 queue=0、active_category=null；
正確 cold capability 仍 HTTP 404。本輪沒有部署，不將 404 改稱通過。
16:04:43：最近 900 秒 receipt=0、exchange=0、有效 compaction lease=0，僅代表持久化資料。
16:04:41：OS 上次開機為 8/29 15:38:07.5，`long_uptime`、`os_boot_kind=unknown`；
文字模型 resident、context=16384、driver=610.88；OS cache unknown，未驗證 power cycle。
GPU 瞬時使用率 10% 不等於完全空載，仍须於窗口核對競爭工作與背景資源。
市場排程實際讀回為 Enabled/Ready，並非 Disabled；未修改它們，准入時須重查是否競爭。

16:10當時結論：**not_admitted_spacing_not_satisfied**。當時尚未啟動 observer、未替換六檔、未停止
任何服務、未量得新的恢復秒數；沒有 stop/start 權限已確保的承諾，須在真正停止前確認。
本次排程與來源驗證不能證明模型品質、並發、vision cold 或 Phase 1 升級完成。
Q1 結案後直接交付單次結果並暫停 heartbeat，不再要求逐次核准、不繼續自動量測。
正式部署窗口與中斷預算未核定，完整多次變異證據仍待未來補測。
