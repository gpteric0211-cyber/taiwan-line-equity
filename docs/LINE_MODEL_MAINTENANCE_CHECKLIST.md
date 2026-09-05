# LINE model：整合維護窗與驗收清單

## 當前狀態對齊 — 2026-08-31 14:50（Asia/Taipei）

本節取代下方各歷史「最新授權」的時段／完成狀態，**不新增部署授權**。
原始方法與失敗記錄保留，不修改既有plan.json或heartbeat。

- Q1已於8/30完成一次舊版快速確認，總恢復25.419355秒；不是Phase1部署。
  [Q1原始報告](LINE_STACK_QUICK_RESTART_Q1_20260830.md)保留M0 127.055702秒及全部失敗。
- [現行量測計畫](LINE_STACK_RESTART_VARIANCE_PLAN.md)及plan.json已記錄R1/R2/R3暫停、
  automatic_successor_runs=false、phase1_deployment_authorized=false。
  下方凌晨三個ACTIVE時段是歷史，不得據此恢復／新排量測；本轮沒有讀回或更動排程器。
- absence-only與成本样本的指定漏洞已完成後續離線修復，最新完整結果為1031 passed。
  本輪確認351來源、11 protected、116歷史JSONL與該結果一致；不是本輪重新跑測試。
  舊688/731與「8個錯放尚未修」不能再當目前來源狀態；也不能據1031宣稱已部署。
- 14:50:51本機netstat快照只有8000 LISTENING，8010/8020/8021無監聽紀錄。
  未探測health、未查明停止原因，不自行啟動；舊404或舊ready不得當作今天的現場回應。
- Q1記錄之啟動來源與現在有16份原清單內檔案不同，其中13份非測試；
  另12條目前manifest路徑不在舊339清單中，不等於12份都是新增。
  因此直接啟動目前版本不能描述為只部署原三項；須先核定發佈來源／依賴與恢復版本。
- 真實新A20/B30、typed期間能力、vision cold、Load Matrix／5日100筆、共用核心／V2接線、
  真實LINE人工抽驗仍未完成／未核准。本輪沒有執行這些工作或新增局部重啟机制。
- 下一步先由使用者核准部署版本範圍、窗口與恢復預算；不挪用Q1的300秒量測授權，
  不以單次25.42秒替使用者核定中斷預算，部署前後完整回歸要求維持。

逐檔雙SHA、6項Phase C逐ID/ledger、完整suite來源綁定與未完目標見
[當前證據對齊報告](../logs/line_model_shadow/goal_evidence_reconciliation_20260831_1450/REPORT.md)。

---

## 歷史授權：再量三次變異，不部署（2026-08-30 15:27；已被Q1單次計畫取代）

使用者不接受用單次 127 秒直接決定 Phase 1 窗口；改核准不同時段再量 2–3 次。
已排定三次舊版完整重啟量測：8/31 02:30、8/31 06:30、9/1 02:30，台北時間。
每次仍先保存 pending／切回原 legacy／重啟量測／還原 pending、不再次重啟。
每次預算 300 秒，實際間隔至少三小時，失敗與跳過均保存；不新建局部重啟或熱重載。

- [x] 本對話 heartbeat `line` ACTIVE，讀回時段一致；舊 `line-phase-d` PAUSED，避免提前跑後續階段。
- [x] 本輪完整 py_compile 339、pytest tests -q 688 passed／18.23s；protected 11/11、來源 339/339 不變。
- [x] 獨立任務「調查 cloudflared HTTP/2 連線受阻」已建立，只做唯讀診斷，不改網路。
- [ ] R1／R2／R3 原始量測與 warmup／runner load／總恢復變異表：目前 0/3。
- [ ] 真正 PC 冷開機覆蓋：尚未證明；每次記錄實際 uptime，禁止把模型重載當 PC 冷開機。
- [ ] 使用者看完最壞觀測值及不確定性後，另行核准正式部署預算／窗口。

詳見 [完整分時量測計畫與執行停止條件](LINE_STACK_RESTART_VARIANCE_PLAN.md)。
Phase 1、typed／語意／兩個 harness、Phase 2+ 均不是這次排程可擴張的範圍。
下方「等待使用者是否接受 127 秒」是上一輪狀態，不應覆蓋本次僅量測的新授權。

---

## 最新授權與量測結果：只量舊版完整重啟（2026-08-30）

本輪不部署 Phase 1，使用者不核准新增局部重啟／熱重載；另准較長的量測窗口。
選定上限 300 秒，實際 14:23:41.9307356–14:25:48.988 全條件恢復，127.055702 秒。
低流量判斷：前一小時 persisted event=0、GPU queue=0；不代表所有外部請求都可見。

- [x] 完整量測前回歸：py_compile 339；pytest tests -q 688 passed／18.40s。
- [x] 舊版來源恢復／雜湊比對 6/6，暫時切回舊版；pending 備份保留。
- [x] 只用既有 launcher 完整重啟一次；沒有新增局部生命週期機制。
- [x] 同時量三服務、公開 tunnel、官方 webhook、text model residency／warmup completion。
- [x] 全部 failed probes、公開 502/530/ReadTimeout 與官方 endpoint 切換 mismatch 保留。
- [x] pending 六檔恢復、不再次重啟；全 339 Python source hashes 與原值相同。
- [x] 完整量測後回歸：py_compile 339；pytest tests -q 688 passed／19.23s；protected 11/11。
- [x] 正確 cold capability 仍 404，確認未冒稱 Phase 1 已部署。
- [ ] 使用者決定 127 秒是否可接受、是否核准另一次正式 Phase 1 部署窗口。
- [ ] 新版 Phase 1 部署與验收：本輪未做；不能把舊版重啟成功當作完成。
- [ ] Vision cold、Phase 2 A20/B30／paired replay／人工品質與後續 gates：未執行。

原始證據、完整測試碼與秒數計算見
[完整 launcher 重啟量測](LINE_STACK_FULL_RESTART_MEASUREMENT_20260830.md)。
127 秒超過使用者提出的 120 秒參考值；僅 1 次樣本，沒有穩定／P95 保證。
在使用者下一次明確核准前，不追加部署、模型樣本或新的重啟工程。

---

以下 13:50 與 r2 內容為歷史。其部署窗口不同於上方已完成的「只量測」窗口。

## 最新授權：僅 Phase 1（2026-08-30 13:50，Asia/Taipei）

使用者已核准由執行者填實際時段、約 15 秒中斷上限的部分部署窗；不是仍缺使用者時段。
本次僅 cold-load 保護＋新 Schema／Q2 日期修正；vision 前提不足明確跳過。
下方 r2 的完整窗口項目保留為歷史／後續要求，**不授權本次執行模型品質、typed／語意、
paired replay 或負載測試**。Phase 1 經使用者簽核前，不進 Phase 2 或後續階段。

- [x] 部署前完整編譯 339 檔；`pytest tests -q`：688 passed／13.62s；protected hashes 11/11。
- [x] 核對現有 launcher：child 結束會帶停整組；公開 tunnel 需重建，模型常駐會中斷。
- [ ] 約 15 秒內正式可用性恢復方案：未就緒，未停服務，不臨時擴大重啟設計。
- [ ] Cold／Schema／日期修正部署：未執行；13:48:11 capability 仍 404。
- [ ] 重啟後三服務 ready、實測 service gap、部署後完整 tests：未執行，不能用部署前快照替代。
- [x] Vision cold 明確跳過：專用 cold lifecycle 與停止／恢復保護未就緒。

完整原文、測試碼與原始請求見 [Phase 1 部署前驗收](LINE_MODEL_PHASE1_DEPLOYMENT_REVIEW.md)。
維護開始／結束及 gap 皆 `not_run`；未開窗，不稱零停機。Phase 1 尚未完成。

---

以下為 r2 歷史清單；其「未獲維護授權」不是目前狀態。

更新：2026-08-30 12:50，Asia/Taipei。版本：r2。狀態：**needs_rework；未獲維護授權，未部署**。

本文件將一次窗口內的部署、測試、退出條件集中管理。尚未提供維護開始／結束時段，
這輪沒有重啟、卸載／載入模型、改正式設定或發 LINE 訊息。

## 1. 這輪已完成與尚未完成

| 項目 | 可核對的狀態 |
| --- | --- |
| Q2 與中文日期漏檢 | Q2、第二季、二〇二六 年、八 月已修；完整回歸 688 passed，尚未部署 |
| Cold-load 保護 | 前輪完成原始碼；最近一次 12:23 capability GET 仍 404，不能稱部署完成 |
| 新生成 Schema | 原始碼已有，尚未完成新版實機驗證 |
| Typed 缺失期間 | 未實作；本輪沒有放行模型自行填寫的年度／季度 |
| absence-only 支撐多空語句 | 獨立稽核重現 8 個錯誤放行；exit 2，漏洞未修 |
| Vision cold-load | 現有探針先暖載再推理，沒有完整冷態證據；完整窗口前須補專用探針 |
| 新版 B30／精確配對 replay | 尚未執行；現有 live runner 只保證同題，不保證同封包 |
| Phase D／canary | 五交易日、百筆/profile、完整 Load Matrix 等門檻仍未通過 |

日期根因在 `review_src/core/line_model_validation.py` 的 `(?<![A-Za-z])`：它會略過
緊接英文字母的單一數字，使 `Q2`／`EPS9元` 漏檢。這是驗證器規則的 false negative，
不是 GPU、環境、權限或模型大小問題。前輪移除該例外，本輪補中文季度與空白單位，
覆蓋 text_template、conditions、missing_data、research_limitations 四個面向。沒有放寬來源要求。

- 裸 `缺少2026年Q2的EPS數據` 和 `缺少Q2的EPS數據` 均拒絕。
- 中文季度及數字與單位間的空白不再繞過檢查：`第二季`、`二〇二六 年`、`八 月` 均拒絕。
- `目前缺少可核對的基本面資料，只能說明判讀限制。` 仍可通過並由模型解釋。
- 合格 canonical date fact 的 placeholder 仍可由後端插入日期；unavailable fact 不可。
- 這只修「省略年份可繞過驗證」，**不等於已修好合法缺失期間的精確表達能力**。
  後者需 typed 缺失期間，區分使用者要求的期間與 DB 已確認缺失的期間；不得以 user text
  代替 DB absence evidence，也不得為降低拒絕率直接白名單所有日期。

## 2. 窗口前必備

預設要求是完整驗收窗，不自動改成部分交付。以下共同前置，加上第 2A 節的離線日期／語意／typed
期間工程、第 4 節 vision cold 及第 5 節精確 replay 的離線前置，全部就緒後才安排完整窗口。
實機量測與五筆人工審閱在窗口內或其後完成，不要求尚未生成的新實機樣本在窗口前就通過。

若使用者另行同意「僅安全部署的部分窗口」，才可縮限；窗口開始前必須書面列出排除項目：

| 以目前原始碼可安排的工作 | 目前不能承諾取得的驗收 |
| --- | --- |
| Cold 保護、新 Schema、日期修正部署及健康／gap 記錄 | Vision 冷啟動成功證據：專用探針未完成 |
| 一次 text cold lifecycle 的成功或失敗完整紀錄 | 原 14 reject／16 pass 的精確模型配對：harness 未完成 |
| 新 A20／B30、逐題拒絕率與原因分類 | Typed 缺失期間功能：未實作 |
| 新樣本具備條件時的五筆人工審閱 | 自動語意安全通過：已知 8 個反例未修 |

「可安排」不是保證測試成功；失敗或停止也必須交付紀錄。部分窗口不構成 Phase A–E 全部完成，
五交易日、百筆/profile、全 Load Matrix、真實 LINE 延遲與 canary 更不能在一次窗口中補稱完成。

- [ ] 使用者確認開始、結束時刻（Asia/Taipei）、可接受的中斷範圍與恢復期限。
- [x] 本輪新增 12 個日期 pipeline cases＋8 個稽核工具 tests；完整 688 passed／13.35s。
- [x] 正確標示前輪 38 cases＝36 pipeline＋2 direct validator，不是 38 次真實模型呼叫。
- [x] 股票公式／資料品質 protected hashes 11/11 不變。
- [ ] 凍結最終版本後再次核對來源雜湊；若這輪後又有任何程式變更，重跑完整 `tests`，
      不以舊 630 或 70 targeted 作部署依據。保留 tests command／結果／版本於同一 manifest。
- [ ] 凍結本次 reviewed diff、既有 dirty tree、設定基準與可恢復副本；不得用 git reset 清掉使用者變更。
      秘密設定不進公開報告／git；source-on-disk hash 不冒充已載入 process 的證明。
- [ ] 列清可部署集合：cold lifecycle／API／collector safeguards、新 Schema／adapter／prompt、
      日期 validator 與對應測試。不改公式、referee、DB、context profile、GPU driver 或 canary。
- [ ] 啟動獨立於被重啟 stack 的健康監測，先確認 LINE ingress/reply telemetry 實際可寫且已遮蔽識別資料。
      記錄空流量、漏測、timeout；沒有真實流量不能稱真實 LINE 零影響。
- [ ] 完成精確封包 replay harness 的審查（若本窗要宣稱原 14 個 reject case 的對照結果）。
      該 harness 目前未實作；不得在正式 GPU 外另跑繞過 admission 的離線模型 runner。
- [ ] 決定 vision cold 是否納入；只有第 4 節前提全部達成才可列入。

完整回歸命令（由 repo root，使用專案 Python；其他平台換成相應 Python 執行檔）：

```powershell
$env:PYTHONPATH='review_src'
.\review_src\.venv\Scripts\python.exe -m py_compile review_src/core/line_model_validation.py tests/test_line_model_v2_contracts.py
.\review_src\.venv\Scripts\python.exe -m pytest tests -q
```

## 2A. 日期、typed 期間與語意的可執行關卡

### 已有命令：邊界稽核不是 pytest 總數

```powershell
.\review_src\.venv\Scripts\python.exe scripts/audit_line_model_validation_boundaries.py
if ($LASTEXITCODE -ne 0) { throw 'Boundary gate failed; do not approve candidate release.' }
```

加 `--output logs/line_model_shadow/<unique-run>.json` 可保存全部合成輸入、原始判定、
deterministic repair 後判定、輸出與 source hash；既有檔案不可覆寫。這是獨立 CLI，
尚未宣稱已接入部署自動化；執行維護的人必須跑它並保留退出碼，不得被 pytest pass 取代。

- [x] 四個欄位均檢查：text_template、conditions、missing_data、research_limitations。
- [x] 日期矩陣包括 Q2、q2、Ｑ２、Q 2、2026年Q2、ISO 日期、民國一一五年、第二季、
      二〇二六 年、八 月：共 40 案例，原始與 repair 後都必須 reject。
- [x] 5 個正向控制仍通過：四個欄位的誠實缺失說明、雙邊精確引用的價格比較。
- [ ] 8 個 absence-only 語意負例均必須 reject；任一 raw 或 repair 後錯誤 pass，CLI exit 2。
      現在是 8/8 錯誤放行，不能勾通過。這不是實機模型的拒絕率。
- [ ] 每次新增期間表達或修復規則，都增加負例與合理正例；有限案例通過不代表任意日期／語意已獲證明。

### Typed 缺失期間：以下是待實作驗收，不是現有功能

- [ ] 後端從 canonical metadata 產生 absence record，包含穩定 ID、標的、field、期間、
      品質／availability_reason、as_of；模型只能引用 ID，不能自行提供期間值。
- [ ] 同標的／field／期間／as_of 完全相符的 absence record 才能由後端顯示「該期間缺資料」。
- [ ] 只在使用者問題出現的日期只能作 requested period，不得自動變成「DB 已確認該期缺資料」。
- [ ] 測試不存在的 ID、錯標的、錯 field、錯季、錯 as_of、重複／歧義 ID、以 unavailable 冒充數值，
      全部拒絕；正確 absence 期間可顯示，但不能授權 EPS／價格等財務數值。
- [ ] 同步版本化 packet、generation schema、prompt、validator／renderer；重跑 token preflight
      及完整回歸。沒有上述測試與實作的命令／結果前，不得標 typed 功能完成。

### Absence-only 語意處理：禁止只靠關鍵字黑名單

- [ ] 重跑上述 CLI 的 bullish／bearish 反例，涵蓋四個欄位，檢查 raw 與 deterministic repair。
- [ ] 證明只引用 absence fact 的限制段落，不能混入「均線結構偏多／價格趨勢轉弱」等當前主張。
- [ ] 加入混合有效＋absence 證據、跨句偷渡、否定句、條件句案例；對每個當前主張核對實際合格證據，
      不能只確認 evidence_id 存在，也不能把整段改名 limitation 就通過。
- [ ] 採可驗證的逐主張綁定或受控缺失段落安全降級；保留有合格證據的模型推論、情境及反證分析。
      不允許為了過測試禁止所有模型解釋，也不宣稱詞語過濾能證明完整自然語言語意。
- [ ] 負例拒絕、正例可解釋、人工五筆審閱皆通過；任一未通過維持 candidate 不對外。

## 3. 同一窗口的順序與停止條件

1. 記錄時間、服務 health、GPU/model residency、版本、process parent/start time 與待執行工作；
   先停止新增 benchmark 工作，按核准方式排空，不把 queue=0 誤當沒有外部使用者。
2. 部署凍結的整包來源並按核准方式重啟 stack。現有 launcher 在任何 managed child 結束後
   會於 finally 停止其餘 managed process；不能假設只重啟 LINE worker 就不影響整個 stack。
   維持完整服務設定，不再以「只留模型服務」收集正式性能證據。
3. 驗證所有服務 readiness、正確的官方 webhook、新 process identity、啟動時來源 manifest、
   cold capability contract 與候選回報的 prompt/schema 指紋。後兩者不代表全部 runtime source 已驗證。
4. Cold 保護驗收分兩種證據：
   - 拒絕路徑：在部署服務拒絕缺少授權／確認的請求時，同時確認卸載／生成／queue 工作沒有發生；
     HTTP status 單獨不算證據。不得故意製造 OOM／孤兒 process 來測保護。
   - 正向路徑：經批准才執行一次 text unload→preload→resident→READY 的完整維護 lifecycle，
     保存 started/finished journal、單一 exclusive admission、load timing、residency、無重試與
     recovery_required 結果。HTTP 200 或 capability 存在均不能代替此項。
5. 若第 4 節通過，才跑獨立 vision cold case；否則標 `not_run: vision_cold_protocol_not_ready`。
6. 恢復標準 text/vision 配置後重收新版 A20、B30、互動搶佔／fallback 量測；精確配對與人工閱讀
   依第 5 節執行。維護流量仍不是正常 live Load Matrix 或真實 LINE 使用者回覆樣本。
7. 停止探針，恢復原安全設定、關閉 cold flag，確認健康、webhook、residency、無異常 runner、
   candidate replacement 仍禁用；保存所有中斷與失敗，不刪掉失敗樣本。

時間准入：

```text
remaining_window > configured_load_timeout + unload_ready_transport_allowance + recovery_reserve
```

每個不可中途安全取消的 case 開始前都要滿足該式；reserve 必須先定義，未知就不開始。
現有 text maintenance preload read timeout 預設 1,260 秒，collector 另留 90 秒；
這不是整個窗口的完成上限，也不是 LINE reply deadline。加上恢復與其他驗證，
不能以過去 6–10 秒的 service gap 承諾本次可在十秒內完成或全程可服務。

以下立即停止新增測試、保存已發生結果，再依已核准恢復方案處理：
來源指紋不符、公式雜湊不同、health 不健康、cold timeout、residency 不符合、異常 runner、
未預期卸載／offload、超出窗口預算、任何候選對外發送。停止 client 等待不等於 server/GPU 已停止；
不得因此盲目重試或按 process 名稱批次刪除。

**一次維護窗不代表保證只重啟一次。** Cold flag 是 process 環境設定；目前沒有已驗證的
熱更新／自動失效機制。若開啟與還原各需 restart，兩次 gap 都在同一窗口記錄與核准範圍內。

## 4. Vision 前置條件：目前實際缺什麼

已確認的呼叫順序：`_start_vision_probe()` 先要求 text resident，接著
`_ensure_vision_model_resident()` 用 maintenance qwen_chat（120 秒）暖載 vision，
最後才送 image 推理。新增的長 timeout text preload **沒有套到這條 vision 暖載路徑**。

- v6 的 409 `model_not_resident` 在 text 前置檢查發生，不能叫做 vision 冷推理已執行而失敗。
- 12:23 的唯讀 `/api/ps` 只有 text 模型，不證明 vision artifact 缺失或設定無效。
- 目前缺少能保存「vision 初始非 resident→冷載入→第一次 image 推理」各階段的獨立驗收契約，
  以及該路徑足夠的 timeout／停止恢復保護。現有 warm probe 通過不補足這兩項。

同窗納入 vision cold 前必須完成：

- [ ] 核對已安裝的 configured vision model/digest 與 image 支援，不在窗口臨時下載／換模型。
- [ ] 核對 text 必須 resident、vision 初始確實 non-resident；若 vision 已 resident，
      只能在核准的 model-specific 卸載程序下轉 cold，不可誤卸載 text。
- [ ] 在同一 shared admission 內隔離冷載入，明確 timeout、恢復／停止條件、GPU reserve、無 offload。
- [ ] 保存首次 image call 的 load metrics、輸入 fixture hash、有效解析輸出、前後 residency，
      並驗證 text 仍可服務；不能先暖載再把後面的 image request 標 cold。
- [ ] 先跑這條新增探針的單元／失敗路徑與完整回歸，再凍結來源；本輪沒有實作或執行它。

能事先完成可安排同窗；未完成就不開完整窗。只有使用者另核准部分窗才能排除 vision cold，
且整體 cold/Load Matrix gate 繼續未通過。

## 5. B30 與人工語意驗收：同題不等於同 case

舊基準是 `final_phase_b_current_source_v2_20260830.jsonl`：30 次，16 pass／14 reject。
新報告至少分成以下兩條證據，禁止混為一組百分比：

1. **Current B30**：三題各十次，使用新版 pipeline／當時真實 DB facts；保存全部嘗試，
   raw／repair後 pass、reject、失敗／取消、原因分布、每題分母與全文。另以同等固定案例、
   fake reply adapter 比較 stable payload bytes；這不證明真實 LINE transport 零變化。
2. **Paired regression**：由舊案例的固定 question＋packet hash 定義 case，而非新 attempt index。
   對舊 14 reject 全部建立配對，並納入舊 16 pass 檢查退步；固定 model digest／參數／seed（若支援），
   記錄新舊 prompt/schema/validator 版本。不可執行或資料不可比就標 unpaired，不能改稱通過。
   單次配對不能消除模型隨機性；任何重跑都留在同份報告。現有 runner 會重新取 DB／新聞，
   不能直接拿它的第 N 筆冒充舊第 N 筆的完全同案例。

Paired harness 的完成條件（目前全部未完成）：

- [ ] 讀取舊 JSONL，建立 30 個具唯一 case ID 的 manifest；包含舊 question、完整 compacted packet、
      packet SHA-256、原結果、原輸出 hash、14 reject／16 pass 分組，保留重複封包的不同嘗試。
- [ ] 以同一正式 shared admission 提交固定案例；不得另開本機獨立 GPU semaphore，
      不重新讀 DB／新聞、不再次壓縮改寫封包，不允許任意外部 request 注入 canonical facts。
- [ ] 比對實際送進模型的 packet hash；不同就 mismatch/abort，不算 paired。
- [ ] 預算不足、來源／模型指紋不符、preempted、timeout、程序重啟均保存 attempted/noncompletion，
      不重試到成功後只留成功；未知舊 model/seed/參數必須標比較限制。
- [ ] 測試來源竄改、重複 case ID、缺 case、hash 不符、非 resident、取消、輸出截斷、半途崩潰的紀錄。
- [ ] 產生逐 case 新舊結果／原因／完整輸出對照，驗證 30/30 都有終態，再跑完整回歸與來源凍結。

人工至少閱讀五筆通過比較／推論，預先選擇分層規則，優先覆蓋舊 7／19／29 類型：

固定抽樣規則：三題依序配額 2／2／1，依 attempt index 選最早符合條件的 validator-pass 比較／推論。
「符合」只按區塊類型與雙邊證據是否存在選取，不先按好看與否挑選；選到後再逐句作品質判定。
精確配對可用時另檢查舊 7／19／29 衍生內容，不用相同的新序號冒充 lineage。
不足配額就標 insufficient_samples；人工結果、審閱者與時間記在同頁，不以自動 pass 代替人工簽核。

- 每個比較的雙邊數值、單位、期間、日期及同塊 evidence 都相符。
- 八 ID 限制下，未砍掉足以改變結論的關鍵反證；不要把逐筆 OHLC 列舉當有深度分析。
- 缺失／過期證據只支援限制；不得從 unavailable 支撐「均線偏多」等非數值主張。
- 仍尊重單一 referee、品質標示、不保證獲利與既有短版聲明。
- 同頁列該題全部嘗試數、通過數、拒絕率、所選 attempt/case/hash、完整文字、雙邊證據及審閱結果。
  若找不到五筆合格樣本，如實 fail，不補挑最好結果或刪除失敗。

已知反例 `支撐壓力區間資料不可用；均線結構偏多。` 只引用 absence-only evidence，
目前仍可能 pass；本輪未修復。不以五個好樣本覆蓋這個反例，也不稱自動 validator 已保證全部語意。
在有可驗證的語意處理／安全降級方案前，維持 candidate 不對外、不得 canary。

## 6. 窗口結束交付表（目前全部待執行）

- [ ] Deployed cold safeguard：拒絕路徑「零 model side effects」＋一次完整 text lifecycle 證據。
- [ ] Current B30：全部三十筆、逐題拒絕率、raw/repair 與錯誤分類；context 400 不得發生。
- [ ] 原 14 reject／16 pass 配對表：同封包才能稱精確 case 對照；未配對清楚列出。
- [ ] 五筆以上人工比較／推論審閱與全部分母，包含 absence-only 已知反例處置狀態。
- [ ] Vision cold：至少一筆完整成功，或明確 `not_run`／`fail`；不得替換成暖態證據。
- [ ] 每服務 gap：最後成功→首次失敗→最後失敗→恢復成功時間、取樣間隔、timeout、
      gap 上下界與監測缺口；本機健康與官方 webhook／真實 reply 指標分開。
- [ ] 恢復記錄：最終設定、已關 cold gate、健康、模型／runner 狀態、所有來源綁定、未發候選回覆。

歷史沒有監測的窗口影響仍是 unknown；12:23 只是前輪點狀唯讀 health，本輪未重測，不補寫零停機紀錄。
Phase D 五個交易日／每 profile 百筆與 canary 不是一次維護窗內能完成的事項。
