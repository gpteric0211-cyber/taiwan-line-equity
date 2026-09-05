# Phase B 生成契約修正與離線稽核

## 最新補充：清單 r2 與邊界稽核（2026-08-30 12:50）

本輪新增獨立 `audit_line_model_validation_boundaries.py`，以 53 個合成輸入檢查真 validator
及 deterministic repair；不是模型生成測試。修正中文季度／空白日期後，40 日期負例與 5 正例符合預期，
但 absence-only 證據夾帶多空主張的 8 個案例仍錯誤放行，CLI 維持 exit 2／needs_rework。
沒有把「工具測試通過」當「產品語意驗收通過」。

新增 12 pipeline＋8 工具 pytest cases，完整 **688 passed／13.35s**；py_compile 通過，
protected hashes 11/11 不變。這輪無部署／模型呼叫／正式設定或 DB 修改。
舊 B30 的 `phase_b_extended_period_replay_20260830.json` 仍是離線舊輸出稽核：30/30 判定不變，
不是新版 30 筆生成或原 14 reject 的新模型精確配對。

八項缺口的驗收條件、完整／部分維護交付邊界及未完成 harness 均見
[清單 r2](LINE_MODEL_MAINTENANCE_CHECKLIST.md)。Typed 期間與自動非數值語意處理仍未實作；
不得讓新版 B30／五筆人工好樣本覆蓋已知反例。

## 前輪補充：日期漏檢修正（2026-08-30 12:23）

Q2 單獨漏過的狀態已從「已知未修」改為「source/tests 已修、未部署」。
正則原有的英文字母前綴豁免會漏過 Q1–Q4、q2，甚至 EPS9元；移除此豁免，
不影響 placeholder 在 text_template 經後端合格 fact 綁定後插入數值／日期。

- 新增 38 cases：36 個四欄位 pipeline 案例＋2 個合格／unavailable 日期引用案例。
- 修正前新 pipeline 測試為 24 failed／12 passed；失敗均為預期 reject 卻錯誤 pass。
- 修正後該檔 51 passed／0.30s；最終完整 tests **668 passed／14.21s**，py_compile 通過。
- Protected analysis hashes 11/11 不變；prompt/schema/packet builder 不變，無新增模型呼叫。
- 歷史 30 筆重新離線驗證：30/30 判定不變，仍 16 pass／14 reject；不是新生成或品質改善證據。
  新 artifact：`logs/line_model_shadow/phase_b_date_boundary_replay_20260830.json`。
- 12:23:16 三服務 ready；cold capability GET=404。沒有重啟、模型 POST 或正式設定變更。
- Typed 缺失期間仍未實作；裸年度／季度仍拒絕，合格日期 placeholder 及定性缺失解釋可用。
  這是修 false negative，不宣稱合法缺失日期的 false-positive／表達能力需求已全部解決。
- absence-only 證據搭配非數值偏多語句的語意漏洞未修；保留為獨立候選發布 blocker。

維護部署集合、停止條件、vision 前提、同題與同封包的差異、五筆人工閱讀及 service gap
統一於 [整合維護清單](LINE_MODEL_MAINTENANCE_CHECKLIST.md)。原始 14 reject 必須使用固定封包
配對；現有 live B30 重新取 DB/新聞，不能冒稱完全同 case。配對 harness 尚未實作。

以下是 08:02 生成契約及後續審查的歷史紀錄；其中「Q2 未修」由本節更新，其他未完成 gate 保留。

更新：2026-08-30 08:02，Asia/Taipei。**原始碼／測試完成，尚未部署，沒有新增模型呼叫。**

這是降低生成錯誤的候選修正，不是品質已提升或 Phase A–E 已通過的宣告。
本輪遵循 maintainable-refactor：先查原始樣本、只改生成／驗證邊界，不動股票公式、referee、DB、
正式 `.env`、GPU 排程或正式回覆內容；沒有重啟服務或執行冷載入。

## 原始證據與拒絕率

完整原始輸出、封包及每題 first pass：
[`final_phase_b_current_source_v2_20260830.json`](../logs/line_model_shadow/final_phase_b_current_source_v2_20260830.json)。
全部嘗試：[JSONL](../logs/line_model_shadow/final_phase_b_current_source_v2_20260830.jsonl)。

| 問題 | 嘗試 | Pass | Reject | 拒絕率 | 首次通過：全批序號／該題第幾次 |
| --- | ---: | ---: | ---: | ---: | --- |
| 基本面＋籌碼＋技術＋新聞 | 10 | 7 | 3 | 30% | 1／1 |
| 技術＋估值＋支撐壓力＋風險 | 10 | 5 | 5 | 50% | 2／1 |
| 籌碼＋夜盤＋美股＋事件 | 10 | 4 | 6 | 60% | 9／3 |

合計 16 pass／14 reject；原始未修復輸出只有 11 pass，另 5 筆經既有 deterministic repair
及第二次嚴格驗證後通過。沒有刪除失敗、重寫原模型輸出或以最佳樣本代表成功率。

14 筆 final reject 的原因可重疊，不可相加當總筆數：

- 6 筆 placeholder 不在同塊 evidence_ids，伴隨 unresolved placeholder。
- 3 筆區塊 schema 不符，實際含超過八個引用 ID。
- 3 筆 limitation-only fact 被放進 inference／scenario。
- 2 筆比較句未提供雙方數值 placeholder。
- 2 筆在 900 output tokens 截斷為不完整 JSON（全批序號 21、23）。
- 4 筆帶未綁定的數字／日期；另有 requested-scope coverage 失敗。

第 3 筆使用 `{{F028}}`、`{{F027}}`，卻複製另一組 scope IDs，漏列這兩個 ID；第 7 筆首塊列十個 ID。
不能透過事後自動加 evidence 或放寬上限，將這些錯誤算為通過。

## 修正與邊界

1. 新增純生成 JSON Schema：focused 三至四塊、comprehensive 四至五塊；每塊最多八個 IDs、
   text_template 最多二百字、條件最多四項。限制形狀，不代替證據驗證。
2. `qwen_chat_detailed` 明確 opt-in：native 使用 `format`，OpenAI-compatible 使用 `response_format`。
   普通正式 `qwen_chat` 不傳 schema，原路徑不變；串流取消仍啟用，格式錯誤不降級為無約束重試。
3. 提示先要求選擇本塊實際事實，不得整列複製 scope 清單；維持雙方比較引用、缺失只限 limitation、
   DB 權威、唯一 referee、不補數字／日期及禁止保證和命令式買賣等規則。
4. Schema 全文納入 prompt 與 token preflight；沒有暗增 output reserve、context 或 profile cap。
5. 修補 `missing_data` 數值漏檢：原先只檢查型別／字數，現在與 research_limitations 同樣拒絕
   未綁定數字／日期。這是加嚴 validator，不是降低拒絕門檻。
6. 觀測新增 schema version、schema SHA-256、generation prompt SHA-256、schema prompt token cost。
   collector 比對實際回報指紋，不符即保存該筆、停止後續收集並非零退出。
   **此指紋只綁定生成 prompt/schema，不宣稱驗證所有已載入 runtime source。**
7. Schema 請求的 HTTP 400 不再一概歸為 context overflow；只有錯誤包含 context 超量訊息時使用
   該分類，其餘為 model_http_400。不記錄原始錯誤本文、不偷偷重試。

官方依據（複核 2026-08-30）：[Ollama Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs)、
[OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)。官方支援格式不代表本機版本已通過
每個 schema keyword／吞吐測試；目前只有 request transport 及後端驗證的自動測試。

## 封包數學與資訊保留

最後離線稽核：[v4 replay](../logs/line_model_shadow/phase_b_schema_offline_replay_v4_20260830.json)。
使用全部 30 筆「歷史已精簡封包」，不是重建今日 DB projection，也沒有呼叫模型。

- 儲存輸出的 pass/reject 判定：30/30 不變。
- Schema＋完整提示估算：10,679–10,952 tokens，全部不超過 comprehensive cap 11,000。
- context=16,384、output reserve=900、safety margin=2,048；context capacity=13,436，profile cap 更嚴。
- 30/30 沒有額外刪除 fact 或 event。39／40 facts 的新聞問題保留 6 events；另一題保留 53 facts。
- 此為相對歷史 compacted packet 的「零額外刪減」，不是原始 DB 每個欄位都完整送入模型。
- 最小 cap 餘裕只有 48 tokens；新資料／新聞仍須重新 preflight，不能推論未來都不必精簡。

保留的設計迭代：初版直接追加 schema 每題多刪四筆；v2/v3 尚有局部刪減。
v4 去除重複 JSON 範例與提示文字後達成零額外刪減，没有調高 cap／減少 facts 最低門檻。
`phase_b_schema_offline_replay_20260830.json` 與 v2/v3 僅為設計迭代，不是最終封包驗收。

重現命令（output 必須是新檔；工具拒絕覆寫原始或既有證據）：

```text
python scripts/audit_line_model_phase_b_replay.py logs/line_model_shadow/final_phase_b_current_source_v2_20260830.jsonl --output logs/line_model_shadow/phase_b_replay_new.json
```

## 驗證與部署邊界

- py_compile：10 個本輪 Python sources/tests 通過。
- 全套：630 passed in 12.71s；新增 20 collected cases、修改 1 個既有 source-binding case。
- protected analysis hashes：11/11 match。
- 07:55:39、08:02:08 唯讀 health：8010／8020／8021 全部 HTTP 200、ready=true。
  這是兩個當下快照，不是整段維護窗／真實 LINE reply 的連續證明。
- 08:02:08 Ollama PID 36972、text runner PID 30200 建立時間未變；本輪未啟停任何服務。
- API routes／renderer 未改，全套回歸已執行；未啟動額外 dashboard、未新發 LINE 測試訊息。
- 風險：**中**。Schema 編譯成本、27B 實際遵循程度、900-token 截斷及新版 P95 尚未量測。

本輪改變 generation prompt/schema 與一條 validator 規則，舊 Phase A/B 只能作歷史對照。
**部署後必須重收 Phase A 至少二十筆、Phase B 各題全部嘗試及內容，重跑 preemption／Load Matrix。**
本輪 model calls=0；不能用離線 replay 當新模型樣本、交易日或 P95 evidence。

下一步：先確認維護窗後部署本輪與 cold-load 保護，保存連續 health 與真實 LINE telemetry；
驗證 collector 生成指紋，再重收 A/B 及取消讓道測試。未通過前保持 shadow、不開 canary。
五交易日／每 profile 百筆、冷載入、高併發 gate、人工五題比較與上線合規審查仍未完成。

## 後續七項審查 — 2026-08-30 08:17

本節是唯讀查證與 validator 邊界重現；沒有部署、改 runtime/Python、改正式設定或呼叫模型。
原始觀測：`logs/line_model_shadow/review_followup_20260830.json`。

### 1. Cold-load 保護：仍未部署

08:12:36 對正式 8021 的認證 GET `/internal/line-model-benchmark/cold-load-capability`
回 `404`、`detail=Not Found`，不是新版回覆的 `enabled=false`。
現行 LINE 程序仍自 06:25:53 啟動；模型、服務端 cold lifecycle 與新版 schema 並未重新部署。

- 三服務 ready；GPU queue=0、active=null；text 常駐，當下 vision 未常駐（設定啟用不等於常駐）。
- 新磁碟 collector 能阻止自己對舊 server 送 cold POST；這不等於服務端保護已生效。
- 舊 authenticated benchmark cold endpoint 的風險仍在，不能用舊 collector 或直接 cold POST 繞過。
- 本輪 WMI 程序查詢起初被 sandbox 拒絕；經唯讀權限檢查後取得 PID/parent/start-time census，
  只有一個 llama-server PID 30200，parent 為現存 Ollama PID 36972；沒有執行任何終止命令。
- 現行 launcher 監看所有 managed child；任一 child 結束會走 finally 停止其他 managed children。
  因此不可把「只重啟 8021」假定為不影響其他服務的安全捷徑。需要明確維護窗與恢復方案。

### 2. Schema 實機效果：尚未驗證

新版模型呼叫仍為零；14/30 拒絕率不能因單元測試或 token replay 被改寫。
需要新一輪三題各十次，全數保存 raw/final validator 分布、每筆輸出及封包，不能只重跑至第一筆 pass。
可以先跑高拒絕第三題作 smoke check，但那不替代完整三十筆比較。

### 3. missing_data 日期：誤拒風險與漏檢皆已重現

| 輸入文字 | 現行 validator | 解讀 |
| --- | --- | --- |
| 缺少2026年Q2的EPS數據 | reject | 原因為 ungrounded_numeric_or_date_claim；不是判定句子事實錯誤 |
| 缺少Q2的EPS數據 | pass | 單個數字前有英文字母時的正則邊界漏檢，不代表季度已綁定 |
| 缺少該季度EPS數據 | pass | 泛稱缺失說明沒有期間／數值斷言 |
| 缺少EPS 9.9元數據 | reject | 明確裸數值被拒絕 |

金融數值與期間不同，但期間錯誤仍會造成誤導；不能用「長得像日期」就全面放行。
現行 missing_data 沒有 typed period/evidence 欄位，即使合理的日期文字也無法表達已驗證的綁定。
因此這個邊界尚未解決。後續應以後端已確認的缺失欄位／期間參照渲染日期，區分使用者詢問期間
與 DB 證實缺失的期間；不可把使用者提到某季度當成 DB 確實缺少該季度的證據。
本輪沒有擅自改規格放行裸日期，也沒有把 Q2 漏檢固化為應通過的測試。

### 4. Prompt 精簡的實際對照

- 「最多八個 evidence IDs」仍在 FINAL_OUTPUT_GUARD 自然語言及 schema maxItems=8。
- placeholder 必須列在同塊 evidence_ids：system prompt 與 final guard 都保留。
- limitation-only 只能放 limitation：system prompt 與 final guard 都保留。
- 雙方數值比較、DB 權威、referee 不可更動：system prompt 保留，validator 也未放寬。
- 刪除的是重複 JSON 範例及重複敘述；三至四／四至五塊、二百字等形狀數量改由 schema 表達，
  不再假稱每一條都還有原來雙重自然語言提示。

這些是原文檢查，不是模型遵循率證據。實機需分別比較超量 IDs、未綁定 placeholder、
missing-data 越界、比較雙方缺失及 truncation，不能只比較總拒絕率。

### 5. 八引用上限：數值防線有效，語意完整性仍有限

逐讀舊輸出：第 7、19 筆的超量區塊是完整 OHLC/量＋估值列舉，不是已證明不可拆分的九證據推論。
第 29 筆首塊包含股價與三組均線比較、布林通道及 ATR；不能只刪 ID 保留原比較句。
第 29 筆 limitation 又把缺資料與偏多／量能判斷混在一起，需要特別人工審核。

兩個明確標記為 synthetic 的 validator 探針（不是新模型樣本）：

1. 將第 29 筆首塊從九個 ID 刪去 F008、保留 `{{F008}}`：即使降到八個，仍 reject
   `placeholder_without_matching_evidence`／`unresolved_placeholder`。
2. 只引用 unavailable 的 F081，寫「支撐壓力區間資料不可用；均線結構偏多。」並標為 limitation：
   現行 validator **pass**。這證明它無法保證每個非數值語意都有佐證，不能拿 pass 宣稱分析品質安全。

新版人工對照必須逐題記錄：刪掉的 claim/ID/欄位、比較雙方是否保留、重要反證是否消失、
scope 覆蓋、條件情境是否還在，以及拆塊是否擠掉推論內容。沒有人工結果前不放行品質 gate。

### 6. 冷啟動拆成獨立 gates，不能混稱

- **文字模型冷載入 lifecycle：failed。** v6 是此項 120,888 ms、HTTP 500。
- **Vision cold-start／冷態共存：unverified，尚無可簽核成功證據。** v6 的 vision probe 在開始前
  因文字模型未常駐而 409；不能據此說「vision cold-load 本身已測且失敗」。
- **Warm vision contention：保留 v5 歷史證據。** 不替代冷態或新版 source 的驗收。
- 六個 orphan 建立於 02:09–03:26，早於 v6，不能說由 v6 留下；是否造成 timeout 也未證實。

### 7. Phase D：仍未增加可採認交易日／新版樣本

目前 Phase D 檔案為 2026-08-30 的週日診斷；新生成版本零實機樣本，不能累積新版 release P95。
設定指向的 LINE reply telemetry 檔不存在，缺真實 ingress→reply 證據。
既有高併發 candidate 失敗仍保留，不能用 stable fallback 成功替代候選成功。

本輪相關回歸：70 passed in 0.50s；未新增或改 Python/tests。前輪完整 630 passed 是前輪結果，
本輪沒有冒稱又跑過全套。六個產品／collector source hashes 與 08:02 manifest 全部相同。
本輪風險低（唯讀診斷／文件），產品仍未達 release gate；下一步仍需維護窗、上述日期／語意邊界處理，
以及新的實機 A/B、取消讓道、冷態與 Load Matrix，不能核准 canary。
