# C21 契約對齊提案（待核准，不是部署計畫）

日期：2026-08-31。文件風險低；測試契約變更仍需明確核准。本輪未改 production 或測試碼。

## 1. 現況不是「只差兩個測試就能上線」

目前完整回歸 2 failed／941 passed。兩個舊 C21 測試要求在證據不足時接受 repair 後的文字，
但已核准的新 absence guard 必須拒絕。不能放寬 validator 換取綠燈。

更重要的是：當前 validator 重播既有 A20／B30，50/50 都拒絕；26 筆歷史 pass 變 reject。
本提案只解決舊測試與新安全契約的矛盾，不宣稱解決 26 筆歷史相容性、模型品質或任何部署 gate。

## 2. 最小候選變更（尚未執行）

只修改 `tests/test_line_model_shadow_service.py` 的兩個函式：

1. `test_deterministic_repair_only_removes_limitation_placeholders`：保留原 packet/output、
   repair_codes、刪除 placeholder 的斷言；最後從「必須 pass」改為精確確認
   `passed=False`、`reason_codes=("unverifiable_limitation_claim",)`、`rendered_blocks=()`。
   原 F001 沒有 domain/field，不能因模型寫了「支撐」就推定它是支撐資料。
2. `test_deterministic_repair_generalizes_cited_event_id_and_required_day_threshold`：
   保留原 packet/output、移除原始 E001／60、repair_codes 的斷言；最後同樣精確拒絕 U 且不 render。
   required_days=60 是門檻，不證明實際資料天數不足；repair 一般化文字沒有新增缺失證據。

**不需要再複製兩份新正例。** 既有
`tests/test_line_model_absence_only_compatibility_contract.py:365` 已透過 AST 讀取上述原始 fixture，
並生成 F1／F2 原始負例、兩個 synthetic 正例和 PE 相容性控制，共五例。
本輪重跑結果為 `5 passed, 129 deselected`；完整 packet/output、raw/repair 與精確理由已保留。

- F1 正例只新增 `domain=support_resistance, field=availability`。
- F2 正例只將 research_limitations 改為「事件尚未驗證」，已有 E001 unverified 作依據。
- 原始 fixture 仍作負例，不能用正例覆蓋歷史，也不回填 DB 或 historical packet。

## 3. 新發現的指紋相依性：不能漏列第二個測試檔

`legacy_fixture()`（相容性測試第135行附近）把整個舊測試檔的 SHA-256、函式行號及
packet/output 的 literal_input_sha256 寫入 provenance。
`frozen_cases()` 又把 provenance、輸入與 expected 一起做 case hash。

因此，僅更新兩個最後斷言，也會讓 **全部五個 C21 case** 的 source-file provenance 改變，
其中未修改的 PE 控制案同樣受影響。若只改舊測試檔，固定案例 manifest 會正確報漂移。
這是來源版本綁定，不是應該刪掉的測試，也不能自行重生全部 70 個 golden hashes。

須在同一授權白名單明列：

- `tests/test_line_model_shadow_service.py`：僅上述兩函式最後 validator 斷言及原因註解。
- `tests/test_line_model_absence_only_compatibility_contract.py`：僅五個 C21 常數 hash，
  而且必須先輸出完整 before/after case 差異，證明差異只在 `provenance.legacy.file_sha256`
  及因行數變更連帶影響的 `provenance.legacy.line`。
- 審查文件及新的不可覆寫證據目錄。

必須保持：五案 packet/output、expected_raw、expected_after_repair、repair codes、全部 expected rendered、
literal_input_sha256 不變；其他 65 個 case hash、原 S1-R 13 個案例與歷史 JSONL 不變。
hash 不在測試執行時自動更新。若出現其他差異，停止並報告，不擴大白名單。

## 4. 驗收

1. 凍結現有兩個測試檔、validator、原語料、70 case manifest 與 11 protected hashes。
2. 執行修改前全量 py_compile（含根目錄 launcher）與 pytest tests -q，保留兩個原始失敗。
3. 對上述兩個 validator 結果精確斷言 U 與空 rendered；不能只斷言 not passed。
4. 重跑全部 70 相容性、S1-R、Phase C、日期／數字邊界與完整 suite；新測試數預期 0，
   修改行為預期只對齊兩條既有測試，不能用 deselect／skip／xfail 讓完整 suite 變綠。
5. 附五個 C21 完整 JSON、舊測試前後完整函式、五個指紋唯一合法差異及全部 stdout/exit。
6. production／DB／env／launcher／歷史 packet 零修改；11 protected 相同；無模型／LINE 呼叫、重啟、部署。

即使全套轉綠，也只通過「測試契約對齊」。Phase B 的 50 筆歷史重播仍須另處理，
Phase A／D 的新版本實機證據與 Phase E canary gate 不因此成立。

## 5. 後續品質設計應分三類，不直接加寬 regex

本輪直接檢查原 packet，例子保存在
`logs/line_model_shadow/goal_revalidation_20260831_0910/findings.txt` 與 `current_replay.json`：

- **可定位的 backend metadata，但自然語言映射缺失**：A16 有部分 section 的 token_budget 省略資訊，
  且六個 event 均 unverified。不能因 parser 不識別就宣稱這些 metadata 不存在；
  但也不能把「部分內容省略」改成「整章都沒提供」，或把本包事件外推成所有新聞。
- **來源 metadata 不足**：A11 的相對估值缺失僅出現在 evidence_summary 的文字 value；
  該 fact 是 missing／limitation。必須向上游確認明確欄位與資料品質契約，不能由模型文字反推真偽。
- **真正沒有足夠方向證據**：只有 missing/limitation 的均線偏多等主張繼續拒絕。
  不把 quality 提升為 ok，不刪不合格尾句來製造整則 pass。

後續提案應先列出每種可用 typed metadata、主張作用域、配對正負例與影響數量，再審查文法或上游投影。
本提案不授權這些 production 修改，也不直接啟動模型重跑 30 筆。
