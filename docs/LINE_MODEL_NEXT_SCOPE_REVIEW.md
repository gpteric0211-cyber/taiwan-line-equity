# LINE 模型下一步範圍與授權審查

日期：2026-08-30。狀態：S1-R已補契約並完成紅燈驗證；完整pytest為8 failed、731 passed，修復未開始。
本次不是runtime／部署授權。第1–5節保留前輪核對紀錄；本次執行結果以最新CODEX_REVIEW_PACKET.md為準。

## 1. 本輪已做／未做

依使用者最新要求，本輪只核對現況、重跑既有離線檢查並提出最小下一步。
没有新增或修改Python測試、production程式、公式、referee、DB、env或launcher。
没有重啟、部署、真實模型／LINE呼叫，也沒有執行新取消／跨thread探針。

本輪既有檢查：

- 全部review_src/scripts/tests的340個Python檔py_compile通過。
- 完整pytest tests -q：712 passed in 50.16s。
- 11/11 protected hashes一致；340個Python來源與上一輪712通過版本完全相同。
- 既有語意CLI：53案例，8個期待不符，exit=2；不是模型輸出品質樣本。

原始證據：logs/line_model_shadow/contract_next_step_review_20260830_2259/。

## 2. 審查建議哪些合理、哪些需調整

前一步「同controller自己的worker不分category立即拒絕」可以作為該子項的離線可合併成果。
條件原本是「同worker且同category」，不是單純從「同worker」擴大；這是文字精確性的修正。
mergeable不等於已merge、已部署、V2可接線或其他安全契約已完成。

四個下一步選項不能視為同樣小且互相完全獨立：

1. 跨thread等待與取消生命週期都可能影響interactive排隊、fallback及延遲；不能說永遠不影響使用者體感。
2. 七條共用核心設計已存在；未完成的是具體實作邊界、憑據／預算等細節及驗收，不應重新從零「開始設計」。
3. Typed absence是缺少功能契約實作；absence-only語意是已知錯誤放行。二者不能混稱一個未修bug。
4. 已做過的舊版重啟快速確認不能重跑來冒充新部署；本輪不新增維護或排程授權。

採取下一步應依實測缺口與目標依賴排序，而不只因為「controller已開了頭」就一直擴張controller。

## 3. 七條契約現況

下列為局部原始碼／既有測試證據，不是全面安全簽核；文件舊階段文字不代表當前全部已實作。

| 契約 | 已有證據 | 尚未完成／證據邊界 |
|---|---|---|
| 一：單一准入、共用核心 | line_model_shadow_service.py:361有已准入函式；631有外層排程入口 | 函式仍含shadow開關、封包、生成、validation與evidence；沒有已核發執行憑據的純共用核心 |
| 二：重入拒絕 | model_admission_service.py:89以Thread identity拒絕自身worker；11項同類／跨類／外部正例重跑通過 | 未追蹤跨thread間接等待、多controller等待循環；不宣稱任意死結已解決 |
| 三：絕對期限與slot後預算 | controller在submit與出列時檢查deadline；既有deadline測試全綠 | shadow已准入函式仍採自己的background timeout，未成為接收interactive絕對期限的共用核心；不能當完整預算重算完成 |
| 四：取消、終態、釋放 | 現有cooperative shadow／maintenance單元測試通過 | 未有完整取消／出列狀態保證；Future未標RUNNING是具體靜態疑點；真正GPU停止／不合作取消未驗收 |
| 五：單次生成、失敗回退 | 已准入shadow函式單次qwen呼叫與deterministic repair、既有fallback機制存在 | 不是新共用核心的端到端生成上限、晚到結果及取消相交驗收 |
| 六：型別、驗證、副作用隔離 | ModelValidationResult、數值／引用驗證與真實validator稽核已存在 | absence-only八個反例仍錯放行；typed absence期間未實作；不能宣稱模型所有語意受證據限制 |
| 七：授權與停點 | 本輪來源hash不變、未新增測試／runtime、未部署 | 下一步測試仍待本提案審查，後續修復與V2必須另外核准 |

關鍵原文位置：

- docs/LINE_MODEL_SHARED_CORE_CONTRACT.md:89–104：取消成功不再執行、終態唯一與釋放責任。
- review_src/services/model_admission_service.py:193–220：出列後設active狀態、deadline檢查及呼叫on_start／callable。
- review_src/services/line_model_shadow_service.py:361–370：已准入函式參數無interactive絕對deadline；約465–477仍為shadow background timeout。
- docs/LINE_MODEL_MAINTENANCE_CHECKLIST.md:165–183：typed期間與absence-only不同待辦。

## 4. 本輪重新核對到的語意缺口

既有scripts/audit_line_model_validation_boundaries.py以合成packet呼叫真實validator和deterministic repair。
它不是模型呼叫，也不是從DB／packet builder一路到LINE的端到端測試。

| 家族 | 數量 | raw／repair後實際結果 | 判定 |
|---|---:|---|---|
| 未綁定日期／季度 | 40 | 全部拒絕 | 此有限負例集合符合預期；不代表typed正向期間已支援 |
| absence-only混入當前多空主張 | 8 | 全部放行 | 8/8錯誤接受，仍為release blocker |
| 誠實缺失／雙邊引用比較正例 | 5 | 全部放行 | 五個正例符合預期；不代表任意推理正確 |

實際反例文字：

> 支撐壓力區間資料不可用；均線結構偏多。

唯一引用F101是quality=unavailable、use_scope=[limitation]的支撐壓力缺失fact，沒有均線偏多證據。
實際raw_pass=true、after_repair_pass=true、raw_reasons=[pass]、repair_codes=[]。
另一句「支撐壓力資料不足，但價格趨勢已轉弱。」同樣在四個欄位錯誤放行。

四個欄位：text_template、conditions、missing_data、research_limitations。

為何712通過仍有此漏洞：tests/test_line_model_validation_boundary_audit.py:54–63驗證稽核工具
是否如實回報needs_rework，不是要求expectation_mismatch_count必須為零。
這個工具測試本身沒有錯；但不能用其通過替代獨立的release contract。

## 5. 其他controller候選為何不在本輪直接修

### Future.cancel生命週期疑點

controller使用標準Future，出列後沒有set_running_or_notify_cancel，便執行on_start與callable。
本機Python 3.13.15的Future.cancel在RUNNING／FINISHED時回False，否則可設CANCELLED；
set_result／set_exception遇到CANCELLED會拋InvalidStateError。

由原始碼可推導的風險是：cancel回True不保證工作未執行，後續寫結果可能使worker出錯退出。
**本輪沒有新探針，尚未動態重現，不能稱已有紅燈實證或正式服務事故。**
在review_src的Python來源搜尋「.cancel(」未找到直接呼叫；不等於外部呼叫者或間接呼叫永遠不可能發生。
它值得獨立測「排隊取消先贏／開始執行先贏」，但不能拿它取代cooperative cancel_event、逾時停止或GPU釋放驗收。

### 跨thread間接等待

controller目前可見category、sequence、deadline與active工作狀態，沒有完整的父工作／等待依賴圖。
只看worker忙碌就拒絕其他thread，會誤傷上一輪已確認合法的external_cross。
不能靠thread名稱、category或全域busy旗標猜測依賴，也不本輪新增context傳播、task graph或攔截Thread.join。

## 6. 選定下一步：S1-R，absence-only阻擋式回歸紅燈

S1-R是測試子步代號，**不是部署Phase 1**。

選它而非直接展開選項1：八個錯誤已有可重跑證據，直接違反使用者「依DB證據分析、不亂推理」目標。
不再重新尋找相同漏洞，而是把現有CLI的失敗要求接成可阻擋的pytest契約。
這一步仍只是安全驗證基礎建設，不會改善LINE回答，也不是語意漏洞已修復。

### 本次修改白名單

1. 僅新增tests/test_line_model_absence_only_release_contract.py。
2. 更新docs/CODEX_REVIEW_PACKET.md交付證據。
3. 使用新的logs/line_model_shadow/獨立子目錄保存完整樣本與命令輸出。
4. 依本次審查補充本文件第6–7節，明訂內容指紋與精確拒絕原因；不重寫七條共用核心基準。

既有audit腳本、validator、repair、packet builder、prompt/schema、controller與現有測試均不修改。
若不改既有程式無法完成測試，停止並列出原因，不自行擴張白名單。

### 測試契約

- 直接import scripts.audit_line_model_validation_boundaries.build_cases（或該module再呼叫build_cases）；不得手抄另一份packet／文字。
- 同來源import只避免兩份來源分歧，不能防原來源改動。因此固定13個case ID各自的SHA-256黃金指紋。
- 指紋涵蓋整個case物件（case_id、family、surface、expected_pass、完整packet與output），不是只hash文字或來源檔名。
- 指紋序列化固定為UTF-8、json.dumps(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)。
  JSON object key順序與檔案換行不影響內容指紋；字串、數值、array順序、quality／use_scope等內容更動都必須失敗。
- 黃金指紋由本次修改前的build_cases計算，逐案與前輪53案原始證據比對相同，再固定到新測試；測試時不得重新產生expected值。
- 指紋有意更版必須另附case內容差異與審查；不可自動更新、選擇性略過或為了全綠換樣本。惡意同時修改測試與指紋仍需code review防範。
- 四個欄位乘上偏多／轉弱兩種主張，共8個負例；raw及repair後都必須拒絕。
- 這13個凍結案例的負例，raw／repair後都必須passed is False，reason_codes精確等於
  ("absence_only_evidence_cannot_support_claim",)，且rendered_blocks為空；不得只檢查False或任意exception。
- 此精確碼是本次測試新增的待實作契約，現有production尚未實作，不能聲稱已存在；本次不修改production來回傳此碼。
  語意為：沒有合格證據支持的當前主張藏在缺失段落／欄位，只有absence證據不能使其成立。
  missing_fact_used_outside_limitation只限制block type，不能當作此語意碼的替代。
- 不接受invalid_json、invalid_block_schema、其他通用拒絕、拼錯reason、pass與拒絕碼混用或額外無關理由。
  精確單碼是針對這組單一缺陷的凍結案例，不要求未來所有混合錯誤輸出都只能一個reason。
- 另保留5個既有正向案例，raw及repair後都必須通過，防止把模型全面禁語當修復。
- 正例reason_codes精確為("pass",)，並保留非空rendered_blocks；比較正例須仍有已解析的雙邊數值。
- 使用真實validate_model_analysis_v2與deterministic_limitation_placeholder_repair；不mock其判定。
- 未知／刪失／重複case ID、少於預定8+5案例必須使測試失敗；不得動態挑選當下會過的案例。
- 每案保存完整packet、output、repair後output、兩次pass/reasons、rendered_blocks。
- 真正assert負例不得通過；不能只assert稽核報needs_rework，不能xfail、skip或放寬expectation。
- 同一新測試檔補測測試自身的防繞過規則：相同ID下文字／packet變動、ID少／多／重複，與「只因schema拒絕／錯碼／pass加拒絕碼」均不可假綠。
  這些helper自測可使用人造verdict，但不得mock或替換13個真實validator／repair契約的判定；自測數量與8+5主案例分開報告。
- 預期當前版本8個負例紅、5個正例綠；實測為準，任何不同结果保留原文並分析，不刻意製造紅燈。
- 完整py_compile、pytest tests -q及11項protected hashes；全套因新契約出現已知紅燈必須誠實報告。

### 能與不能證明

能證明：給定既有合成packet，validator／repair對8個當前無證據主張及5個正例是否符合契約。
不能證明：真實模型拒絕率、DB到packet builder的資料流、typed absence期間、任意自然語言的語意正確性、
完整分析品質、實機P95、併發、LINE體驗或部署成功。
後續修復必須補packet builder路徑與混合有效／缺失證據案例，不能只靠本組手寫packet就宣稱整體完成。

### 明確停點

只交付紅燈及完整證據後停止。不得自行修validator、加入詞語黑名單、將所有limitation變固定拒絕，
或為了全綠刪掉有合格證據的模型分析。修復策略需再提出「逐主張證據綁定／受控缺失段落」等設計及影響範圍。
不做typed schema、跨thread追蹤、Future取消修復、V2接線、共用核心、部署／重啟、模型／LINE、GPU負載測試。

## 7. 授權狀態與可直接使用的核准文字

最新授權：使用者同意S1-R範圍與方向，要求先補內容來源綁定與精確reason code，再開始寫測試。
本文件先補規則，之後只按上述白名單執行；交付紅燈仍是硬停點，未授權修復。

前輪提案的核准文字（歷史紀錄）：

> 核准S1-R：僅新增absence-only阻擋式回歸測試，重用既有8個負例與5個正例，使用真實validator及repair。
> 完整保存紅燈、全部測試結果及11項protected hashes；不改任何production、現有測試、schema、prompt或controller。
> 紅燈交付後停止，先讓我審查；不自行修復、不部署、不接V2、不呼叫模型或LINE。

本文件不構成後續修復授權；本次紅燈交付後停止，等待使用者審查。
