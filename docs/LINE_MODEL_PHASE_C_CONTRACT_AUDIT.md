# LINE Model V2 Phase C Contract Audit

## 當前來源重新驗收 — 2026-09-01 11:23（Asia/Taipei）

**Phase C 在目前 generation-contract source 下通過；這仍是離線合成模型邊界驗證，不代表真實 28B 品質或已部署。**

### 實際資料流與 mock 邊界

三個契約都位於 `tests/test_line_model_v2_contracts.py`，不是三份不同測試檔。`offline_v2_contract` 會封鎖 socket 與 SQLite；唯一替換的是 `line_model_shadow_service.qwen_chat_detailed` 的模型回傳。以下路徑沒有 mock：

```text
execute_line_model_shadow
→ rule_based request planning
→ build_model_fact_packet_v2
→ compact_packet_to_token_budget
→ output schema / token preflight
→ synthetic qwen_chat_detailed completion
→ validate_model_analysis_v2
→ deterministic repair / renderer
→ append-only tmp_path evidence ledger
```

helper 會從實際 `user_prompt` 解出 `MODEL_FACT_PACKET_V2`，並斷言它與 `result["compacted_packet"]` byte-for-structure 相同；沒有手寫另一份 packet 取代 builder。

### 本輪精確測試

```text
test_contract_a_new_packet_pipeline_allows_grounded_missing_data_explanation
test_contract_b_new_packet_pipeline_rejects_unbound_number_with_existing_evidence_id
test_contract_c_new_packet_pipeline_skips_model_when_preflight_rejects
test_numeric_binding_requires_both_typed_placeholder_and_matching_evidence_in_pipeline[raw-number-without-id]
test_numeric_binding_requires_both_typed_placeholder_and_matching_evidence_in_pipeline[raw-number-with-eligible-id]
test_numeric_binding_requires_both_typed_placeholder_and_matching_evidence_in_pipeline[placeholder-without-listed-id]
```

結果：`6 passed in 0.21s`。

- Contract A：raw/final validator 均 `pass`；實際 compacted packet 為 `model-fact-packet-v2`，renderer 產出三段受證據約束文字。
- Contract B：raw/final 均拒絕模型捏造的 `88.88`，reason 包含 `ungrounded_numeric_or_date_claim`，rendered blocks 為空。
- Contract C：4K context 超量時 `candidate_model_called=false`、`finish_reason=preflight_rejected`，reason 精確為 `estimated_prompt_exceeds_effective_budget`。
- 裸數字無 ID：`ungrounded_numeric_or_date_claim`。
- 裸數字有合格 ID、但沒有 typed placeholder：仍為 `ungrounded_numeric_or_date_claim`。
- placeholder 沒列入同一 block 的 `evidence_ids`：`placeholder_without_matching_evidence`，且 unresolved placeholder 不得渲染。
- 六案都由 fixture 驗證沒有 socket/SQLite 呼叫；candidate 不能取代正式回覆。

### 完整回歸與目前 SHA

完整 suite：`1439 passed in 28.25s`。

| 檔案 | SHA256 |
|---|---|
| `review_src/core/line_model_contract.py` | `59611b86bfe2e4f9632c36dca97989877779f16bf2c7777d9356f0dabaf38982` |
| `review_src/core/line_model_output_schema.py` | `842c739fb49a569ccb013b25ea6283bd55bb4ef3edf683d2b668fd3575d2ea21` |
| `review_src/core/line_model_validation.py` | `fcaf9de02eb09f55c4754ddd98fff2f37d7f53469f0ec97e052534ae406d2b60` |
| `review_src/services/line_model_shadow_service.py` | `82612365b37bb8b9919135e45bac98fb170d6a714201a8e81da8c3b791b44c4c` |
| `review_src/services/line_request_planning_service.py` | `341964ccd002f805aeb5ed9f66ab144d28740200029e0bfd0d6401d39043c13b` |
| `review_src/services/model_admission_service.py` | `a92cfa135e02536f8ac924af298c5fba3b678a39d9854dea1479efed3ce4a35a` |
| `tests/test_line_model_shadow_service.py` | `507471feb339697ff582b94a9f53c18cabd884a9e0524019d2fbe455a15d3923` |
| `tests/test_line_model_v2_contracts.py` | `79e98f2e239577c55427ad5071f3d8176ab4e7cb6839377f9ef0e2860be09eea` |

### 能證明／不能證明

能證明：A/B/C 與三個數值綁定案例確實走目前 ModelFactPacketV2、token preflight、validator 及 renderer；不是舊 `line_bot_service.qwen_chat` mock。

不能證明：真實 28B 會穩定遵守契約、A20 拒絕率已下降、LINE reply 延遲、Phase D P95 或 canary 安全。這些必須在新 source 部署後重新收集。

---

以下為先前版本稽核紀錄；SHA、行號與 suite 數字不再代表目前工作樹。

## 當前來源驗收對齊 — 2026-08-31 14:50（Asia/Taipei）

**指定Phase C離線契約可採認；不是新版真實模型品質或部署通過。**
下方2026-08-30的3 passed／舊SHA僅為歷史；最新依據改用成本修復後完整suite及當前來源比對。
本輪沒有重跑pytest，不把13:42的執行時間改稱14:50的新測試。

- 原始完整stdout：[pytest_after_fix.txt](../logs/line_model_shadow/cost_sample_contract_fix_20260831/pytest_after_fix.txt)：
  `1031 passed in 23.88s`，exit0。JUnit逐項核對1031、failure0/error0/skipped0。
- 本輪重新比對：來源351/351、protected11/11、歷史JSONL116/116、S1-R來源2/2相同。
- 完整原始测试碼：[test_line_model_v2_contracts.py](../tests/test_line_model_v2_contracts.py)；
  [byte-exact文字快照](../logs/line_model_shadow/goal_evidence_reconciliation_20260831_1450/phase_c_complete_source.py.txt)。
  A/B/C是同一檔內三個函式，不虛構三份測試檔案。
- [證據JSON](../logs/line_model_shadow/goal_evidence_reconciliation_20260831_1450/evidence.json)：
  六個test ID、最新suite執行metadata、六筆當次合成模型測試ledger全文及SHA。
- [本輪範圍／未完成／下一步](../logs/line_model_shadow/goal_evidence_reconciliation_20260831_1450/REPORT.md)。

### 測試與mock邊界原文

同檔第62行 `offline_v2_contract` 禁止socket/SQLite連線並停用研究；
第131行helper只替換 `line_model_shadow_service.qwen_chat_detailed`，
實際解析傳入prompt中的packet，讓回傳證據ID依真正builder結果綁定：

```python
fragment = user_prompt.split("MODEL_FACT_PACKET_V2：", 1)[1]
packet, end = json.JSONDecoder().raw_decode(fragment)
assert fragment[end:].strip() == line_model_shadow_service.MODEL_ANALYSIS_FINAL_GUARD
output = output_factory(packet)
```

builder、preflight、validator、renderer、暫存evidence寫入不mock。
三筆額外數字案例不是新增測試，本來就在這份1031-item執行中。

| 函式起始行／契約 | 已核對結果 |
|---|---|
| 336／A：誠實缺失＋合格價格＋有界推論 | raw/final pass；三段rendered原文逐一assert |
| 357／B：引用缺失ID仍捏造88.88 | raw/final reject；ungrounded_numeric_or_date_claim；rendered空 |
| 396／C：preflight拒絕 | candidate_model_called=false；estimated_prompt_exceeds_effective_budget |
| 380／裸數字無ID | reject；ungrounded_numeric_or_date_claim |
| 380／裸數字有合格ID但無placeholder | reject；ungrounded_numeric_or_date_claim |
| 380／placeholder未列在同塊evidence_ids | reject；placeholder_without_matching_evidence |

A的完整rendered斷言：

```python
assert result["rendered_blocks"] == [
    "目前缺少可核對的基本面資料，只能說明判讀限制。",
    "收盤價為2420 元。",
    "單一收盤價格只能描述當次價格，不能證明獲利是否改善。",
]
```

B與數字負例共用的拒絕檢查原文：

```python
assert result["raw_validator_result"] == "reject"
assert reason in result["raw_validator_reason_codes"]
assert result["validator_result"] == "reject"
assert reason in result["validator_reason_codes"]
assert result["rendered_blocks"] == []
```

這些B／數字測試檢查理由包含指定碼，不是精確單碼斷言；
不要把S1-R精確單碼規則移植成這些測試已做的宣稱。
C驗的是context/token preflight，**不是interactive queue deadline**。

### 當前SHA（不是舊文中的validator版本）

| 檔案 | SHA256 |
|---|---|
| `review_src/core/line_model_contract.py` | `9bbc8d56cd18151a3985a28679817f757259297f5b41a56be500e3aeccd61b2f` |
| `review_src/core/line_model_output_schema.py` | `f5bede34ae61e93fca0cfd3d7031e3ccef97e9f3bb1174c5222e73789566c68d` |
| `review_src/core/line_model_validation.py` | `2467ef4c06a14aba780ce065c5651e22973866011871572a15b4921bd9088243` |
| `review_src/services/line_model_shadow_service.py` | `23d326a7ed66d5a9e33ba26b1638e19b82ee30423243070763171099c4d9c96a` |
| `review_src/services/line_request_planning_service.py` | `341964ccd002f805aeb5ed9f66ab144d28740200029e0bfd0d6401d39043c13b` |
| `review_src/services/model_admission_service.py` | `a92cfa135e02536f8ac924af298c5fba3b678a39d9854dea1479efed3ce4a35a` |
| `tests/test_line_model_shadow_service.py` | `d1217138539e5f24e8f637f579fe6f6174c49663ba01f3679235f9e23703994d` |
| `tests/test_line_model_v2_contracts.py` | `a3d6eeaaa8eb759bac663de16e96f9a98daa15f3485749a74b127eb6261f3450` |
| `tests/test_line_request_planning_service.py` | `9bdd554dfa1cfb76c28a51ee2f3c1842a892a22720e4078b914f256bc0e9ae2e` |
| `tests/test_model_admission_service.py` | `58839f47bdb73c6ef4f99f525498db5b7ede9d1baf51924c58f88610c804e8a3` |

### 效力與重跑規則

這次以完整suite在新validator版本執行過的證據替換舊版驗收依據，沒有豁免validator改版後重跑。
新結果的來源之後未變，本輪只改文件，故不重跑同一完整suite；部署前仍按當次規則重新全跑。
固定合成QwenChatResult只驗傳輸邊界後的邏輯，不證明真正28B生成、最新DB資料、
任意自然語句、P95、GPU取消、LINE送達、共用核心／V2接線或發布。
沒有因這份文件更新而新增runtime／模型／LINE／部署授權。

---

## 歷史紀錄 — 2026-08-30（下列SHA與耗時不再代表當前版本）

驗證時間：2026-08-30（Asia/Taipei）

## 結論

Contract A/B/C 已直接接到新的 ModelFactPacketV2 pipeline。三個測試只替換
`services.line_model_shadow_service.qwen_chat_detailed` 的模型回傳，沒有 mock packet builder、
token preflight、validator、renderer 或 evidence artifact 寫入。因此測試仍會真正經過：

```text
execute_line_model_shadow
-> build_model_fact_packet_v2 / compact_model_fact_packet_v2
-> token_preflight
-> fixed qwen_chat_detailed completion
-> validate_model_analysis_v2
-> deterministic renderer
-> append-only evidence artifact
```

完整且具權威性的測試原文：`tests/test_line_model_v2_contracts.py`

Source SHA-256：

- `tests/test_line_model_v2_contracts.py`：
  `fb46194f79c40e2122985672b8e3370485a14295e284715f1ebd6a3fe01b861b`
- `review_src/core/line_model_contract.py`：
  `ecf8f6716f8f34ce7838e48eaa84c554340211828831f8f945d54fff2b328574`
- `review_src/core/line_model_validation.py`：
  `98e2fc245d688029d42429dba7a5b0b278ca8d117df4f5af729bf59a73469516`
- `review_src/services/line_model_shadow_service.py`：
  `a6207da5d8ce55cc14b85e361e37fe6f821b88dc453c74091dd8605979321c49`

## Contract A

測試：`test_contract_a_new_packet_pipeline_allows_grounded_missing_data_explanation`

- 輸入的 valuation quality 為 `unavailable`，availability reason 為 `not_published_yet`。
- 模型只能產生 limitation block，不得補造數值。
- 引用缺資料 evidence 後，validator 必須 pass。
- 明確斷言 compacted packet contract 為 `model-fact-packet-v2`。

## Contract B

測試：`test_contract_b_new_packet_pipeline_rejects_unbound_number_with_existing_evidence_id`

- 模型刻意輸出「每股盈餘是 88.88 元」。
- block 雖引用存在的 evidence ID `F003`，但該 evidence 是 unavailable limitation，不能授權數字。
- validator 必須回傳 `reject`。
- reason 必須包含 `ungrounded_numeric_or_date_claim`。
- `rendered_blocks` 必須為空，88.88 不得到達 renderer 輸出。

這就是 Phase C 額外要求的「evidence ID 存在但數字未完成 typed binding 仍必須拒絕」測試。

## Contract C

測試：`test_contract_c_new_packet_pipeline_skips_model_when_preflight_rejects`

- 將 context 設為 4,096，並建立刻意超量問題。
- 若 qwen callable 被執行，測試立即失敗。
- 結果必須是 `candidate_model_called=false`、`finish_reason=preflight_rejected`。
- reason 必須精確為 `estimated_prompt_exceeds_effective_budget`。

## 本次獨立驗證命令與結果

```text
.\review_src\.venv\Scripts\python.exe -m pytest -q \
  tests\test_line_model_v2_contracts.py::test_contract_a_new_packet_pipeline_allows_grounded_missing_data_explanation \
  tests\test_line_model_v2_contracts.py::test_contract_b_new_packet_pipeline_rejects_unbound_number_with_existing_evidence_id \
  tests\test_line_model_v2_contracts.py::test_contract_c_new_packet_pipeline_skips_model_when_preflight_rejects
```

結果：`3 passed in 0.11s`

## 驗收判定

Phase C：**pass**。

這個判定只證明新 packet/validator contract 的行為；不替代 Phase D 五交易日效能證據，
也不授權 canary 或放寬任何 DB grounding 規則。
