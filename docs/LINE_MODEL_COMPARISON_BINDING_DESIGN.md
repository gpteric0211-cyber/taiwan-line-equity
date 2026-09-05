# 4.2 比較／欄位綁定設計（design-v1.2，非實作）

版本：design-v1.2，2026-08-31，Asia/Taipei；取代v1.1的現行文法條款，不覆寫v1／v1.1原始證據。
使用者本輪核准：「只修訂設計，統一理由碼優先序、補有限位置／區間及成本樣本規則；不改validator、不新增正式測試、不部署。」本文件內容待審，不等於實作授權。
**本輪只修改設計與審查文件，沒有更新量化parser、新增pytest測試、凍結正式expected或修改validator。**
本輪交付即停止。後續若核准離線量化，先按v1.2重跑同一corpus，再依實測取捨決定正式測試凍結；production修復仍需紅燈審閱後另行核准。

維護紀錄：v1.1的前綴／別名／有限照應保留；v1.2統一無綁定與未知標籤的reason選擇，新增明訂位置／區間及foreign_estimated樣本比較文法。
公司身分、跨強分隔照應、日期區間、接近容差、D08/D09及成本業務可用性仍不擴權。
v1.1完整原文與本次diff保存於[本輪證據目錄](../logs/line_model_shadow/comparison_binding_design_v1_2_20260831/REPORT.md)；[上輪v1→v1.1 diff](../logs/line_model_shadow/comparison_binding_design_v1_1_20260831/LINE_MODEL_COMPARISON_BINDING_DESIGN.md.diff)僅供歷史核對。

## 1. 決策與範圍

採用**有限文法、逐主張綁定**，不採用「動詞兩側最近placeholder」啟發式。
同block其他無關數字、其他block引用、數值剛好相同，均不得替某項主張提供運算元。
每項比較必須有自己的有序`(left fact ID, operator, right fact ID)`；每個明示欄位標籤必須匹配自己的placeholder或第4.4節唯一且可追溯的明示宣告。後者是有限文法擴充，不是從packet補造引用。

已確證根因直接採用，不重新跑D01/D03/D05證明：

- D01：原A3 inference block抽取後，缺布林中軌F010的比較仍pass；不是只發生於limitation。
- D03：人工將PE標為PB，F033仍是pe_ratio=28.05；不是原模型輸出的PB錯標。
- D05：人工把「高於」改「低於」。2420實際高於2393.5、2389.25、2383.67，三個「低於」均假。

本輪靜態位置（4.1後行號）：

- `review_src/core/line_model_validation.py:828–836`，舊block總數檢查；不是背景引用的舊746–755行。
- 同檔`:855–873`，既有placeholder存在／引用／authority／quality／use_scope與render流程。
- 同檔`:939–945`，既有absence語意守門；後續新增純檢查的候選接入點在此之後、success return之前。
- 同檔`:442–483`，已有非常有限的limitation比較helper；不搬動、不重写、不擴權。

原文`:835–836`：

```python
        if _COMPARATIVE_TEXT.search(template) and len(set(numeric_placeholders)) < 2:
            reasons.append("comparison_without_two_numeric_placeholders")
```

資料流仍是來源→既有DB/repository→service的model_facts→ModelFactPacketV2→模型output→validator→候選renderer。
本設計只消費已存packet/output；不查DB或資料來源、不更動API/cache/background task，不改價格、RSI、MA、MACD計算或referee結論。
本輪歷史packet取代實機讀取；無DB table、endpoint、排程或資料來源變更。

## 2. 歷史階段0實際結果與證據範圍

本節是design-v1設計階段的歷史結果，**不是v1.2本輪重跑結果**。
證據目錄：`logs/line_model_shadow/comparison_binding_design_20260831/`。

| 檢查 | 結果 | 原始證據 |
| --- | --- | --- |
| 凍結來源集合 | 352份，含4.1新test，無新增／遺失／hash漂移 | [before_integrity.json](../logs/line_model_shadow/comparison_binding_design_20260831/before_integrity.json) |
| protected／歷史JSONL／S1-R／private | 11／116／2／2全部相同 | 同上，逐路徑SHA；不顯示secret內容 |
| 全量py_compile | 352成功，含root launchers | [compile.json](../logs/line_model_shadow/comparison_binding_design_20260831/compile.json) |
| 完整pytest | **1200 passed in 21.74s**，exit0 | [完整stdout](../logs/line_model_shadow/comparison_binding_design_20260831/pytest_baseline.txt) |
| 執行時間 | 19:15:59–19:16:21+08:00 | [實際command/exit](../logs/line_model_shadow/comparison_binding_design_20260831/pytest_execution.json) |
| 逐test ID/狀態 | 與4.1後測完全一致；沒有skip/xfail/filter | [pytest_states.json](../logs/line_model_shadow/comparison_binding_design_20260831/pytest_states.json) |
| 測試後完整性 | 各組hash／來源集合全部不變 | [after_tests_integrity.json](../logs/line_model_shadow/comparison_binding_design_20260831/after_tests_integrity.json) |

既有dirty worktree原樣保存於`git_status_before.txt`，不清理。DB隔離沿用來源相符的前輪靜態稽核；最新4.1 test有socket／SQLite tripwire。舊suite內隔離SQLite測試不等於操作正式DB；本輪未連線／hash正式DB。有效pytest設定重新檢查，沒有新conftest/config/PYTEST覆寫。

本輪沒有新模型／LINE呼叫；pytest期間model log新增POST行為空。只有本機CPU/磁碟測試工作，未量測正式回覆延遲，不宣稱零影響。
這次全綠只證明**既有基準不變**，不能證明尚未寫出的4.2規則正確。

## 3. 句型盤點：資料、方法及實際例句

只讀來源：新版A20二十筆、舊A20二十筆、舊B30三十筆；70份相容性fixture＋13份S1-R；9份既有D診斷；16個`test_line_model_*.py`及audit script的AST字串常數。
實際31個輸入檔SHA凍結於[inventory JSON](../logs/line_model_shadow/comparison_binding_design_20260831/sentence_inventory.json)。
挑選含比較／欄位cue的原文，共317個surface occurrence／240個不同字串；其中255來自模型歷史、51來自既有fixture、11來自D診斷。另有47個靜態常數命中。
這是搜尋盤點，不是候選parser執行結果，不是新增測試；AST常數不涵蓋所有動態拼接，也不宣稱覆蓋全部116份歷史檔案。

舊B30第21、23筆model_output為截斷JSON，分別`Unterminated string ... char1811/1831`。
保留完整raw_output/error，不修補；70筆中只有68筆可解析輸出，不把兩筆無效資料算成解析成功。初版盤點工具曾在第21筆停止，補上保存解析失敗的路徑後完成；未修改歷史資料。

下表為原文片段，完整文字、block_type、surface、ID及每個placeholder對應fact均在上述JSON及[逐句原文](../logs/line_model_shadow/comparison_binding_design_20260831/sentence_inventory.txt)。此處「支援」是設計預期，不是已實測轉綠。

| 原文／來源 | 設計處理 |
| --- | --- |
| `收盤價{{F001}}高於布林中軌`，A20新第3筆／D01，後面另有DIF/Signal/RSI | 必拒missing operand；F010僅在別block存在不能補引用 |
| `DIF{{F019}}高於Signal{{F021}}`，同D01 | 獨立比較，兩ID匹配後只驗這一項；不能替前項補位 |
| `收盤價{{F001}}高於短中長期均線{{F022}}、{{F023}}、{{F025}}`，新A1／D04 | 分成三個相同left/operator的比較；數值關係通過，名稱長短的額外語意不推定 |
| 同上「低於」，D05 | 三個比較false，精確contradiction理由；不能只驗第一對 |
| `股價淨值比為{{F033}}`，D03 | 欄位不符；數字與引用合法也必拒 |
| `本益比為{{F033}}，股價淨值比為{{F032}}，殖利率為{{F031}}`，新A8/A15 | 三個獨立scalar標籤；field正確可保留 |
| `市盈率{{F033}}、市帳比{{F032}}及殖利率{{F031}}`，新A9 | 收錄確定同義詞，不因換名誤拒 |
| `收盤{{F102}}高於均線{{F103}}，僅反映價格相對位置。`，S1-R grounded_comparison | close=100/ma20=90，保留pass；不新增domain/trade_date必填條件 |
| `當日成交量{{F006}}低於資料庫均量{{F030}}`，舊A17/B2、C01；B14/C02使用F043 | 支援volume_shares→volume_ma20；不綁定固定F006 |
| `收盤欄位為 {{F007}}。`，shadow test:815 | 欄位後綴是明訂語法，映射close，保留pass |
| `MACD快線{{F019}}高於慢線{{F021}}`，新A13／B14 | 同一局部MACD構造中，快/慢線映射dif/signal；不借跨句MACD前綴 |
| `KD指標K值{{F018}}高於D值{{F017}}`，B29 | 同一局部KD構造，kd.k/kd.d；不把單獨K/D當通用field |
| `布林中軌與上軌分別為{{F010}}與{{F011}}`，新A5 | 明訂平行標籤／placeholder列表，按序配對，不做最近鄰 |
| `收盤價為{{F001}}，位於布林中軌{{F010}}與上軌{{F011}}之間`，新A3 | v1/v1.1未支援；v1.2第4.7節可承接唯一close主詞；裸「之間」遇等於端點不猜inclusive |
| `{{F028}}高於中性水準`，新A2 | 中性水準沒有明確數值fact，不偷偷填50；必拒missing operand |
| `OSC{{F020}}為正值`，D01 | 正負號的一元主張不屬本次雙operand規則，保留為未驗證能力，不當完整語意通過 |
| `若收盤價...`，D08 | deferred，保留既有結果，不用現在數值斷言未來條件為假 |
| `布林中軌與中期均線分別為{{F010}}與{{F025}}`，D09 | 前者可核欄位；後者中期的天數命名明確deferred，不判ma20/ma60對錯 |

### 3.1 v1量化與v1.1路由（歷史，不是v1.2效果）

以下是**v1工具已實測**，不是v1.1預測或正式validator行為：

- 固定317 occurrence／240個不同字串；98 occurrence、76不同字串（31.67%）在「單一block投影」下從現行pass變候選reject。
- 人工分成13個實質缺口、56個數值／欄位子句相符但文法不覆蓋（56/240＝23.33%）、7個接近／混合語意未裁定。56不代表整段定性推論、issuer或D09天數名稱已verified。
- 原完整response層只有D01/D03/D05三個診斷新增拒絕；歷史完整response原本有其它舊guard拒絕。不能用「歷史完整response新增拒絕0」掩蓋上述子句可用性代價。
- 原文、facts與判定：[v1完整量化報告](../logs/line_model_shadow/comparison_binding_contract_20260831/IMPACT_REPORT.md)、[76句人工裁定](../logs/line_model_shadow/comparison_binding_contract_20260831/impact_adjudicated.json)。初版工具的101/78與修正後98/76均保留，不能覆寫成v1.1數字。

下表是**v1.1歷史設計路由**，每個56字串只列一次；ID為上述JSON的`first_occurrence`，不是測試ID或v1.2 PASS預測。
同句可能有多個缺口，這裡按主要處理路徑分組，不把次要問題抹除。

| 路由 | first_occurrence完整清單 | 本次規則／明確殘留 |
| --- | --- | --- |
| alias | 122,138,171,202 | 第5節股息率／股息殖利率／市淨率；仍非整段估值推理背書 |
| neutral_prefix | 216 | 第4.2節估值數據顯示；不放任任意prefix |
| namespace | 71,162,181,250 | 第4.5節相鄰布林／MACD同族延續；一元正值、多空原因仍未驗證 |
| local_subject | 103,113,121,137,200 | 第4.4節只補明示close後的省略LHS；個別短中長期MA命名仍D09，不能算已解決D09 |
| cross_boundary | 75 | 分號前的close不准跨強分隔補到後項，仍為已知收斂限制 |
| range_or_position | 8,76,111,135,148,161,177,180,191,197,224,232,238,247 | 部分namespace／布林帶別名／照應可補；區間／之上／跨句价格仍未支援，161的接近亦不新增容差 |
| issuer | 100,108,116,118,132,142,144,187,196,212,214,231,235,254 | 公司前綴不抹除；部分估值prefix／量能照應可補但不能使issuer已驗證；142/187/212/235另有未限定最低等詞 |
| cost_or_date | 102,112,120,136,146,154,168,178,198,207,225,239,248 | explanation成本／日期／未達門檻等不在新增比較域；154亦有issuer，239亦有OBV。239均量照應子句可個別覆蓋，不代表整段可通過 |

上輪提供[56句路由原文與provenance](../logs/line_model_shadow/comparison_binding_design_v1_1_20260831/coverage_routes.json)，只從已凍結人工裁定抽取，不另抄packet或改expected。

### 3.2 v1.1已測結果與v1.2殘留邊界

相同317／240，v1.1最終r3新增拒絕77 occurrence／63不同字串（26.25%）；其中13實質缺口、42數值部分相符但文法未覆蓋（17.5%）、8未裁定混合語意。
完整來源：[r3量化](../logs/line_model_shadow/comparison_binding_v1_1_impact_20260831/impact_r3.json)、[逐句人工覆核](../logs/line_model_shadow/comparison_binding_v1_1_impact_20260831/MANUAL_REVIEW.md)。不是正式validator拒絕率。
42主要路由為位置／區間14、issuer14、成本／日期13、跨分號1。v1.2只補位置與成本樣本中的有限子句，**不承諾14+13個完整字串皆能放行**：

- 位置組8、76等具有可明示綁定的價格／端點；111、135、177、197、232、238、247等「價格位於中軌上方」仍可能缺本視窗close宣告。不能從較早另一句或packet唯一close推定「價格」。
- 161及新增未裁定206含「接近」，仍unsupported，不定容差；個別短中長期均線名稱仍D09，不將deferred改成verified。
- 成本102、146等的樣本／所需天數子句是目標；112、120、136、198、225、248另有日期範圍，154有issuer／非白名單敘述，239有OBV，不能宣稱整句覆蓋。
- issuer14與跨分號75不改；剩餘8個未裁定不自行升級verified。
- v1.1理由衝突（原第90、199、311行）以本版第6節決策程序唯一裁定；不回寫r3觀測結果。

**v1.2尚未執行候選量化，沒有新的解除數量或拒絕率。**

## 4. 輸入及有限文法（v1.2修訂契約，非Python實作）

### 4.1 Surface／輸出責任

- 新規則僅處理`fact/inference/limitation`的`text_template`；不新增output schema。
- `scenario`的text與所有`conditions`不作本次當前真值判定；保留現有schema/數值/引用/政策檢查。第7節規定不能用這個排除宣稱安全。
- `missing_data/research_limitations`沿用現有非數值限制句規則，4.1／成本樣本grammar不動。
- 每項綁定只可取**同一block evidence_ids**內、packet中唯一的fact。不得跨block、借metadata、按相同數值替換、按fact排列猜ID。
- 成功只代表本檢查沒有新增拒絕，不改寫output、不移除失敗子句；任一已決定拒絕使整份response拒絕且`rendered_blocks=()`，避免只展示剩餘句導致誤導。

### 4.2 Token與分隔

1. 比對用NFKC；移除非換行空白，保留raw字串及token對應原始offset。不得改寫模型正文。
2. placeholder只接受既有`{{F[0-9]{3,}}}`。缺括號、E-ID、裸數字仍交既有guard拒絕。
3. 強分隔為`。；;！？!?\n`及`但是/然而/但/而`；弱分隔為`，,`及`並且/同時/且/並`，長token先辨識。強分隔清空所有照應／namespace狀態；弱分隔只允許第4.4–4.5節的有限延續，不泛用承接主詞。既有limitation helper「逗號承接量能解釋」能力仍不修改。
4. `、/與/及/和`只在明訂列表文法內連接，不能泛用作跨主張代詞／主詞承接。`或`不視為AND列表；沒有清楚命題的OR比較拒絕structure-unverifiable。
5. 每個分隔段開頭，可消費一次`截至{{date-ID}}`，再消費至多一個中性前綴：`技術面顯示/技術面/估值指標顯示/估值方面/估值指標/估值面顯示/估值數據顯示/配合`；v1.2僅另加corpus原文`籌碼面資料顯示/籌碼與機構面資料顯示/機構籌碼方面`（最長先取）。date與前綴可各自省略；date仍須通過既有date check。新date／中性前綴重設照應視窗。前綴後立即遇弱分隔可消費該一個分隔，再解析主張；不把前綴當operand，也不因此建立成本namespace。不得用任意`.*?`吞掉未知欄位、公司名稱、否定或語氣。
6. `MACD指標中/MACD指標/MACD的/MACD`及`KD指標/KD`是局部namespace prefix；原同構造作用保留，額外延續只依第4.5節。prefix本身不是證據。
7. 固定字詞匹配最長者優先：`不低於`不能拆成`低於`；`股價淨值比`不能拆成`股價`；`布林通道中軌`不能拆成無context的`中軌`。
8. 不在未知詞片段中截取已知suffix救回解析；如`並非高於`不得刪成`高於`、`可能高於`不降格成無修飾比較。引號`「」『』“”\"'`包住的文字不當成無引述主張，重設照應；若其中有適用的明確比較則記structure-unverifiable，不藉引號豁免驗證。這是未支援引述的保守邊界，不是他人主張歸屬能力。

### 4.3 文法及每項operand歸屬

記號：P=placeholder，L=第5節已知／未知／待裁定label token，O=第6節operator。ListSep與NameSep如下明訂。
每個比較命中須完整覆蓋自己的label/placeholder/operator/list部分；剩下的新operator或label+placeholder也須再次掃描，不能第一個合法match就結束。

```text
NamedOperand := L [欄位|數值] [為|是] P
Operand      := NamedOperand | P
ListSep      := 、 | 與 | 及 | 和
NameSep      := 與 | 及 | 和
MAList       := 均線 [分別為|為] P (ListSep P)+
             | 短中長期均線 [分別為|為] P (ListSep P){2}
Scalar       := NamedOperand | MAList
Pair         := Operand O Operand
RightList    := NamedOperand (ListSep NamedOperand)+ | MAList
Comparison   := Pair | Operand O RightList
Parallel     := L (NameSep L)+ 分別為 P (NameSep P)+
```

列表括號是結構記號；實作時每個connector token須完整消費，不接受連續／缺項connector。
`MAList`可作獨立scalar列表，故D07「短中長期均線分別為...」不必捏造比較operator才能核欄位。
RightList也允许單一NamedOperand後接ListSep＋MAList（新A8的布林中軌＋三MA），不允許任意交錯、跨分隔或遞迴嵌套。

- `Comparison`每個RHS產生一項相同LHS/O的比較，原順序保留；列表最多7個RHS、全部ID聯集仍受原8-ID上限。
- `短中長期均線`在此僅宣稱三個列出的MA皆位於同側，必須恰好3個不同ID／不同MA field；只查MA family與3個數學關係，**不據位置推出ma10/ma20/ma60名稱契約**。其短／中／長個別命名的適當性仍是D09延伸議題。
- `Parallel`必須label與placeholder等長，左到右zip；兩個label只配一個P（例如`布林中軌與中期均線均為{{F010}}`）不因數值相同合併；可決定的部分拒絕missing/ambiguous。
- 單一LHS可供**同一RightList**重用；另一個弱分隔後的新比較僅在第4.4節明訂條件下照應，不能按最近placeholder抓數字。多個明確Pair並列，各自獨立。
- `收盤價{{F001}}高於布林中軌`不是跳過的「regex不匹配」：若沒有本視窗中先前明示的布林中軌宣告，產生binding-missing。D01正是沒有這項宣告的情形，不能用packet中有F010替代。
- `{{F001}}高於{{F010}}`可依fact field family核對數值，不憑未出現的中文label推定名稱；`未知名詞{{F001}}`不能截掉未知名詞，退化成bare P。
- 支援的Operator每次出現都要被完整構造消費，否則拒絕structure-unverifiable。不能用「沒有成功解析」当成「沒有錯」。
- 類似`DIF...高於Signal...、OSC...為正值`：list item的P之後若緊接`為正/為負/為正值/為負值`，該item是新的一元主張，不加入前項RHS。若沒有此後綴而完整符合NamedOperand，才可作RHS列表項；無法唯一切分則拒絕ambiguous。一元主張第7節另列，不能把OSC當前項RHS數量。

以上是確定文法，不是以字距找最近placeholder。結構不唯一或未知語法不得任選一種解析。

### 4.4 有限照應：已明示宣告、唯一解析、逐對驗算

目的只補「先寫出值，緊接同句比較時省略重複寫值」；不解讀任意中文代詞，不新增輸出schema。
定義一個**照應視窗**：同block內，強分隔、date／中性前綴、future/deferred、未知或失敗構造、普通散文、引述、否定／未支援修飾之前，连续以弱分隔串接的完整Scalar/Comparison/Parallel；v1.2的Position/Range/Cost構造只在第4.7–4.8節規定成功時延續。不能越過任何重設點找較早值。
`、/與/及/和`仍是構造內列表符，不啟動另一個照應句；弱分隔前後都需完整解析。

| 狀態 | 寫入與清除（必須遵守，不以最近值代替） |
| --- | --- |
| 宣告表 | 只登記本視窗先前完整且已通過欄位／資格／數值檢查之`NamedOperand`的實際P；含完整Pair/列表/Parallel中的明示項。鍵為完整canonical field，值為fact ID集合及原始P span。不得預載全packet或僅evidence_ids，也不登記bare-P、推導照應、D09或其它deferred項。 |
| 比較主詞 | 緊鄰前一完整單元若為單一NamedOperand，主詞為該P；若為成功Comparison，主詞為它唯一的LHS。多項Scalar/Parallel、bare-P scalar及一元「為正」句不建立主詞。即使表仍有close，也不能跳過剛列出的多項數值或一元句來借它。 |
| 重設 | 視窗重設時宣告表、主詞與namespace全部清空；block結束亦清空。不跨強分隔、普通散文、條件或引述。遇失敗構造保留其拒絕理由且重設，不能靠後項補成成功。 |

新增文法只在既有完整構造比對後使用，不能把原先有P的錯標改當照應：

```text
DeclaredRef          := SingularKnownLabel [欄位|數值]
ResolvedOperand      := Operand | DeclaredRef
ReferencedComparison := ResolvedOperand O ResolvedOperand
                     | ResolvedOperand O RightList
ContinuedComparison  := O ResolvedOperand | O RightList
```

1. `DeclaredRef`只可出現在比較operand slot。label必須映射到**單一完整field**，且宣告表恰有一個合格fact ID。沒有宣告→`claim_operand_binding_missing`；同field兩個不同ID→`claim_operand_binding_ambiguous`，值相同也不選第一／最後一筆。同ID的合法重複提及只合併span，不形成第二個fact；packet重複ID仍拒絕。
2. `均線/RSI`family label、D09標籤、`它/其/該值/前者/後者`不作DeclaredRef。對可切成operand slot的未知名詞，**無P且無合法DeclaredRef先判missing；有P可綁定而label未知才判unknown**，唯一優先序見第6節。不得先搜packet猜名稱／公司。forward reference、其他block、僅列evidence_id或metadata均不成立。
3. `ContinuedComparison`必須緊接弱分隔並以完整O開始；LHS只能是緊鄰前一單元留下的唯一比較主詞。主詞不存在→missing；存在多種結構解析→ambiguous。前一Pair的RHS、最後一個P、同包唯一close，都不是替代選擇。
4. 原始明示P及其來源位置必須仍在同一block正文与evidence_ids內；每個照應展開成自己的有序pair並保存`binding_origin_span`／本次使用span／ID／field。這些僅為離線審查資料，不改原文、rendered或repair。不從另一個列表的數量湊足引用。
5. 每對仍執行第6節全部qualification、field、unit、period/as_of及Decimal真值檢查；照應不是放行。原有`numeric_placeholders >= 2`等舊guard完全不放寬，舊path先拒絕的案例仍保持原reason。
6. 新宣告只有在該完整單元成功後加入表；比較內可直接用明示P，但不得把同一未完成單元的P當「先前宣告」來補它自己的缺值。有限狀態最多保存block原8個ID，不開新token／ID上限。

可核對的設計例（這裡P名稱為示意，不是新fixture或實测）：`收盤價{{F001}}高於布林中軌{{F010}}，高於均線{{F025}}`的後項LHS只可為F001；
`布林中軌{{F010}}，收盤價{{F001}}高於布林中軌`的右項只可回指前項F010。
改成`布林中軌{{F010}}。收盤價{{F001}}高於布林中軌`，或將首項放別block，就沒有可用宣告，必拒missing。
若F010只存在packet或引用清單，與上述前項明示完全不同；D01仍必拒。

### 4.5 Namespace只跨相鄰同族單元延續

v1同一構造內的namespace規則保留；v1.1僅增加以下確定延續：

- 完整明示MACD prefix或`macd.*`完整標籤、布林完整標籤、KD prefix建立對應namespace，**該單元所有被標記的operand均須屬同一族**。例如close對布林中軌的比較含兩族，不替下一句建立布林namespace。
- 只越過弱分隔到緊鄰同族的Scalar/Comparison/Parallel；`MACD...DIF...高於Signal...，柱狀體P為正值`可核柱狀體label，但「為正值」真值仍第7節未驗證，且該一元句不再把namespace／主詞往後傳。
- `布林中軌P，上軌P`可核上軌；若中間插入成交量、普通散文、條件、另一namespace或強分隔即清空。不從同包有bollinger欄位、或較早一個布林字詞猜namespace。
- 未知／多族／解析不完整時清空，不保留陳舊namespace。未帶namespace的簡稱不任意映射；有P且綁定完整才unknown-label，缺P且無合法DeclaredRef先missing，統一依第6節。

### 4.6 公司名稱不是中性前綴：本輪不抹除issuer主張

在本corpus所用packet，`request`僅有depth/scopes/analysis_cutoff/locale，fact亦無可核對issuer name/code；
來源建構原文見`review_src/core/line_model_contract.py:463–480`、`:581–600`（本輪只讀，未改）。
歷史外層question雖提及台積電，並不是validator已接收且可驗證的canonical issuer欄位。
因此`台積電收盤價P`、`2330...`等**不得透過刪前綴、硬編公司名、相信使用者問題或fixture路徑直接放行**。
在適用數值slot有P且綁定完整、但無法核對issuer時，維持label-unverifiable；缺P且不能合法照應先missing（若舊數字guard先拒則保留舊reason）。不因調整理由優先序就解除issuer拒絕。
此是已知可用性限制／未完成的實體綁定需求，不能宣稱已排除錯股歸屬；要真正解決需另行界定可信issuer輸入契約，不在本次加入packet欄位或修改service。

### 4.7 有限位置與區間（v1.2新增，非距離／趨勢規則）

只在第4.1節surface、舊path通過後處理。X/A/B均須為原Operand或合法DeclaredRef；不用最近P，不從packet補主詞／端點。
僅支援下列完整構造，`位於`與後綴連同列表必須全部消費；不是在任意散文中找到「上方」就推論：

```text
PositionTail := 上方 | 之上 | 下方 | 之下
PositionRHS  := ResolvedOperand | RightList | GroupMA
             | NamedOperand ListSep GroupMA
GroupMA      := 各期均線（P (ListSep P)+）
Position     := X 位於 PositionRHS PositionTail
ContinuedPosition := 位於 PositionRHS PositionTail
Range        := X 位於 A 與 B 之間 [（含兩端）|（不含兩端）]
ContinuedRange := 位於 A 與 B 之間 [（含兩端）|（不含兩端）]
```

括號經NFKC配對識別，ASCII/fullwidth等價；缺括號、缺端點、連續connector均不修復。Range連接詞本版只收`與`；`及/或`、半開區間、`介於/在/落在`等替代結構仍unsupported，不能默認AND或猜邊界。

1. Position的`上方/之上`展開為X>A，`下方/之下`為X<A，相等必為false；列表每一項獨立驗，不以部分true通過整列。沿用RightList的首NamedOperand＋一個MAList；另只在PositionRHS允許首NamedOperand＋一個GroupMA，不擴充一般Comparison的RightList，禁止任意交錯／巢狀列表。GroupMA只核每個MA field及數值，不推定期限名稱；2–7項、必須不同MA field/ID；整個Position全部RHS合計至多7項，聯集仍受8-ID上限。
2. Position/Range只新增**價格域**：close/open/high/low、完整MA family、Bollinger（均TWD）。不把TWD的MACD當價格，也不把PE/PB、成交量或日期當可用區間；不合域用not-comparable。一般O比較原有volume/MACD/KD支援不變。
3. Range先逐slot核X/A/B的引用、標籤、資格及單位/period/as_of/currency等；A、B保留原文順序。本有限文法要求A<=B；若A>B，拒`comparison_structure_unverifiable`（端點順序不在支援語法），不排序、不交換ID，也不宣稱原財務數值為假。
4. **端點含括規則**：明寫`含兩端`→A<=X<=B；明寫`不含兩端`→A<X<B。只寫`之間`且A<X<B可驗為true；X<A或X>B為false；X=A或X=B則拒`comparison_structure_unverifiable`，因inclusive未聲明。A=B時，只有明寫含兩端且X=A可true；明寫不含兩端必false；裸之間等於端點仍unverifiable。沒有epsilon、四捨五入、價格tick容差。
5. 每個Range記錄原始X/A/B、含括mode與span，以及左右兩個有序pair；任一false用`comparison_direction_contradicts_evidence`。三slot各自綁定，不以P總數代替；同ID明示重用須每個角色field皆合格，packet重複ID仍拒。
6. Continued形式只可緊接弱分隔且前項有唯一主詞，與第4.4節相同；成功Position/Range留下X為主詞，不能留下最後一個端點。其所有明示且合格NamedOperand才可登記宣告；混合族不延續namespace。任何false/unsupported/deferred不留下主詞／宣告／namespace。
7. Range內A為完整布林label時，B的簡稱中軌/上軌/下軌可使用同構造Bollinger context；不能越過強分隔或從packet推定。比較完是否延續namespace仍依全部operand同族規則（close加Bollinger不是同族）。
8. `價格`不新增為close全域別名；未明示P／合法DeclaredRef的「價格位於中軌上方」仍missing。「收盤價」也不能越過句號借較早F001。`接近/大致位於/略高於/不在區間內`等不被截短成上述文法，仍structure。
9. 個別短/中/長期MA的D09處理與未來D08不變；不得因Position有數值就裁定那些名稱。集體MAList/GroupMA能核数值關係，但不給期限命名背書；定性「位於均線系統上方/位於中性偏強區間」沒有明確數值構造，沿用舊path且記未驗，不能當已verified。

### 4.8 成本樣本對（v1.2新增，與research_limitations分離）

本節只讓第4.1節block的**明示樣本／需求數值及其關係**可被4.2核對；不改`_research_foreign_cost_sample_insufficient`、不改research_limitations、absence語意、repair或成本計算。後端的required_days是比較值，不由本規則發明60日或80%門檻。

初始namespace白名單只有`canonical_costs.foreign_estimated`，由corpus及現有field來源確認；不以`canonical_costs.*`通配任何新成本類別。其他類別先列未支援，不能以名稱相似或相同count混用。

| 成本label角色 | 完整明示詞彙 | 同成本context內局部詞彙 | 精確field |
| --- | --- | --- | --- |
| sample | 外資估算樣本天數、外資成本估算樣本天數、外資近期增量成本估算樣本天數 | 樣本天數、目前樣本天數 | canonical_costs.foreign_estimated.sample_days |
| required | 外資估算所需天數、外資成本估算所需天數、外資近期增量成本估算所需天數 | 所需天數、所需的天數、門檻天數 | canonical_costs.foreign_estimated.required_days |

```text
CountSuffix := 天數 | 天 | 日 | 個交易日
CostOperand := CostLabel [為|是] P [CountSuffix]
             | 所需[的] P CountSuffix
CostScalar  := CostOperand
CostComparison := CostOperand O CostOperand
CostContinued  := O CostOperand
CostThreshold  := CostOperand 未達 CostOperand
CostContinuedThreshold := 未達 CostOperand
```

CostLabel僅指上表完整／局部詞彙；未知文字在可辨識成本skeleton中保留為不透明slot，不截短為已知詞。上述成本構造先於一般Scalar/Comparison做完整形狀辨識，不能先截成bare P救回未知成本label；`所需[的] P CountSuffix`的suffix不可省略，其role固定required。O沿用第6節，不把「未達」新增為全域股票operator。

1. 完整sample/required label可建立foreign_estimated局部context；同一完整CostComparison中一端明示成本類別，可限定另一端的局部label。另一端若明寫未支援成本名稱，有P則unknown-label，不能把它當局部詞硬套foreign；已知外資label配到其他namespace的fact則field-mismatch。只有前述label檢查均通過的比較（含bare-P配對），才將跨成本namespace／不在白名單的配對判not-comparable。三種情況依第6節順序互斥，不任選reason或任選成本類別。單獨「籌碼面」／packet含成本facts／之前別句有外資都不建立context。
2. 只有連續、成功、同類CostScalar/CostComparison才跨弱分隔延續成本context；generic價格/量能單元、日期、prefix、散文、失敗、D08/D09、強分隔均清空。CostContinued沿用唯一前項主詞；若前項是required便以required為LHS，不擅自交換成sample。
3. 初版CostOperand只接受明示P（CostContinued僅省LHS）；不增加成本裸label的DeclaredRef或「它／此門檻」代詞，一般ReferencedComparison也不得繞過此限制。正確例：`外資估算樣本天數為{{F057}}，低於所需天數{{F056}}`；不能把缺F056改成從packet取required_days。
4. CostComparison／Threshold必須是相同namespace下的一個sample與一個required；一般O正反順序皆可，按文字原樣驗算，Threshold另依第7點固定角色。一般O的sample對sample、跨foreign/其他成本、日數對價格，在沒有更早的missing／unknown／field-mismatch時為not-comparable。packet中這兩個精確field各須唯一，不先篩掉壞quality／重複候選再湊一對；多筆同field用ambiguous，即使引用的ID之一碰巧可用。CostScalar只核自己角色，不能單靠它證明樣本不足。
5. 兩field必須有`domain=institutional_context`、`unit=count`；沿用canonical_db與numeric_claim准入，quality=ok或既有可標示estimated。CostScalar對自身field亦須滿足本點與第6點的型別、period、日期／cutoff資格，但不要求尚未參與比較的另一field出現；一旦形成比較，兩端與唯一性檢查不可省略。`foreign_estimated`名稱不等於所有fact都是estimated；若quality=estimated仍須renderer標示推估。**這不放寬既有research成本helper只接受quality=ok的契約**；其88個測試與所有既有負例須原樣保留。
6. 成本對明確要求period=daily、兩端trade_date/as_of均有效且一致、不晚於packet合法analysis_cutoff；不擴散成其他舊fixture的普遍metadata必填規則。實體與currency若提供，依第6節一致性檢查；不從question補code。count須finite Decimal整數（bool/NaN/Infinity/小數count不可用）；不自行加required固定值或成本可用性分界。數值域異常仍須上游quality負責，不能因本比較真就宣稱成本已可用。
7. `未達`仅指本成本對sample<required，若LHS不是sample或RHS不是required為field mismatch，不倒轉operand。一般O則只驗原方向，如required>sample；0<60、17<45、18<60為數學例，不是寫死常數；sample=required時「低於／未達」必false、「等於／不低於」依O可true。
8. 「樣本不足／充足、可精確估算、持倉動向、統計顯著性、外資偏多」不因上述比較成功取得新豁免；非數值限制句沿用舊語意規則。後項新比較獨立驗，成本前項不能替沒有引用的方向判斷提供證據。
9. `estimate_start_date/end_date/as_of_trade_date`的日期scalar／區間、中文數字、OBV、`官方連續資料僅P個交易日`等非白名單句型本輪不擴充。不得把日期丟進Decimal或默認日期跨度等於交易樣本數；舊date／裸數字guard結果不變。

## 5. 欄位標籤映射 v1.2

映射核對`field`全字串、unit，以及存在時的domain。不按F編號、不按數值、不用`endswith(pe_ratio)`接受任意namespace。
初始詞彙只來自固定corpus／fixture及明確同義詞；「可辨識」不代表自動授權新的比較對象。

| 中文／明確token | canonical field | domain（若提供）／unit |
| --- | --- | --- |
| 本益比、市盈率、PE | pe_ratio | valuation／ratio |
| 股價淨值比、市帳比、市淨率、PB | pb_ratio | valuation／ratio |
| 殖利率、股利殖利率、股息率、股息殖利率 | dividend_yield_pct | valuation／percent |
| 收盤價、收盤 | close | official_ohlcv／TWD |
| 開盤價、開盤、當日開盤 | open | official_ohlcv／TWD |
| 最高價、當日最高價、當日最高 | high | official_ohlcv／TWD |
| 最低價、當日最低價、當日最低 | low | official_ohlcv／TWD |
| 當日成交量、成交量 | volume_shares | official_ohlcv或trading_state／shares |
| 資料庫均量、均量 | volume_ma20 | technical／shares |
| 布林中軌、布林通道中軌、布林帶中軌 | bollinger.middle | technical／TWD |
| 布林上軌、布林通道上軌、布林帶上軌 | bollinger.upper | technical／TWD |
| 布林下軌、布林通道下軌、布林帶下軌 | bollinger.lower | technical／TWD |
| 平均真實波幅 | atr14 | technical／TWD |
| DIF；MACD局部prefix下的快線 | macd.dif | technical／TWD |
| Signal、訊號線；MACD局部prefix下的慢線 | macd.signal | technical／TWD |
| OSC、Oscillator；MACD局部prefix下的柱狀體 | macd.oscillator | technical／TWD |
| KD局部prefix下的K值、D值 | kd.k、kd.d（一對一） | technical／index |
| 相對強弱指標、RSI | {rsi.rsi5,rsi.rsi10,rsi.rsi14}（僅family） | technical／index |
| 均線 | {moving_averages.ma5,ma10,ma20,ma60}，各項均需完整moving_averages.前綴 | technical／TWD |

補充規則：

- `欄位/數值`只是上述LABEL的後綴文法，如`收盤欄位為`，不是寬鬆label通配。
- 簡稱`上軌/中軌/下軌`僅限同一Parallel/RightList已有明確布林prefix、第4.7節Range內已明示的Bollinger端點context，或第4.5節相鄰同族namespace延續；不得跨強分隔或從同包field推定。`快線/慢線/柱狀體`同理。
- 新同義詞只作上述完整映射：不把`收益率`猜成殖利率、不把`市淨率`當PE、不把未限定的`最高/最低`猜為當日OHLC，也不從`本益比`的值反推未知label。負值／零不改其field身分。
- `RSI14/MA20/二十日均線`等含裸阿拉伯數字／中文日數標籤仍受既有數字guard；本輪不為技術名稱開數字豁免。若被舊guard拒絕，保留原reason，留待另一範圍。
- `短期均線/中期均線/長期均線/月線/季線`不寫進確定天數映射，列D09預留歧義詞。它們不是一般unknown被自動判錯，也不是已驗證field標籤；依第7節defer。
- 其他明示label+P在P可綁定後：**拒絕`claim_field_label_unverifiable`**；無P且無合法DeclaredRef的operand先missing，詳第6節。不按value/順序反推label。未知slot是經第4節結構切分後留下的完整名詞片段，不做中文斷詞猜測。
- 普通非數值散文、單獨「資料不足」不是label主張，仍走既有規則；不能因不在詞典就整段封口。其方向性推論是否有足夠證據屬既有／另案語意問題。

### 舊fixture的稀疏metadata（不可忽略）

S1-R `grounded_comparison`的F102/F103有field/unit/period/as_of/authority/quality/use_scope，但没有domain與trade_date；C21-PE舊fixture也少日期。若順帶要求所有key必填，會誤傷已核准正例。
因此v1.2仍**不新增普遍metadata必填規則或重算quality**：ID唯一、field及unit必須存在並正確；domain若存在必須匹配，缺domain不透過猜值補造。新成本對的限定資格只依第4.8節，不套到S1-R/C21。
比較的period/as_of使用原fixture已有內容：要求兩端有相同非空period/as_of；trade_date、currency等若任一端提供，兩端需一致，兩端皆缺則不藉本次補造或證明該維度。明示一端缺失／衝突拒絕not-comparable。
這個兼容不依檔名、測試ID或執行環境放行，對同形packet一致。它只證明field/unit/相對數學，不補足全球metadata完整性；嚴格schema／跨時間品質完善另行處理。

## 6. Decimal比較及精確理由碼

### 支援operator全集

| 原文operator | 判定 |
| --- | --- |
| 高於、大於、超過、多於 | left > right |
| 低於、小於、不及、少於 | left < right |
| 等於 | left == right |
| 不低於、不小於、大於等於 | left >= right |
| 不高於、不大於、小於等於 | left <= right |

不支援`約等於/接近/顯著高於/偏高/優於/弱於/增加/減少/突破/跌破/上穿/下穿`的當前真值；後四個含時間轉換不能當單點大小。本輪不定義容差、增減基期、金融門檻或趨勢公式。
明確帶operand與這些但非白名單的比較詞，在適用surface拒絕structure-unverifiable；纯非數值推論保持既有path，不宣稱已核。

### Operand eligibility與比較域

1. 同一block內引用、packet唯一fact；沿用canonical_db、numeric_claim、ok/estimated的既有准入。重複ID不採dict最後覆蓋。
2. 用`Decimal(str(value))`，先拒絕bool/None/container；拒絕解析失敗、NaN、sNaN、±Infinity。不使用float、epsilon、round或render後四捨五入的值。零、負數本身不是錯誤；不另加業務門檻。
3. 先核欄位／單位／比較域，再驗真值；不同unit/currency不換算。PE和PB同是ratio也不當作相同量度。
4. 初始可比較域：價格close/open/high/low ↔ 價格或MA或Bollinger；MA/Bollinger彼此；volume_shares ↔ volume_ma20；MACD DIF ↔ Signal；KD K ↔ D。v1.2另加第4.8節限定的同namespace sample_days ↔ required_days；即使bare-P比較也必須滿足該節成本對資格。其他配對一律not-comparable，不因都是TWD就允許close↔DIF。位置／區間僅價格域。
5. bare-P比較也受同一比較域限制；本輪不設同field但不同公司／不同期間的比較，跨股票或時間比較需要另案實體／基期契約。現有single-stock packet若fact有明示不同code/entity即拒絕；不從文字猜公司。
6. estimated使用既有renderer的`推估`標示，不因此升級為官方值。若缺label/quality/有效數值，先拒絕而非計算。
7. 每對單獨驗算；False必拒。方向反轉但數值保持不變必須紅→綠；相等邊界對strict/non-strict operator分開設fixture。重複比較不得掩蓋同句另一個False。

### 新reason精確字串（設計，不聲稱已實作）

| 原因 | reason_code |
| --- | --- |
| 可辨識slot缺P且無合法先前宣告（即使slot是未知名詞）、沒引用同block | claim_operand_binding_missing |
| 重複ID、多義運算元或多重解析 | claim_operand_binding_ambiguous |
| 所有必要slot可綁定，但明示label未知／缺必要namespace，非第7節預留歧義 | claim_field_label_unverifiable |
| label已知但fact.field/domain/unit不匹配 | claim_field_label_mismatch |
| scalar數值不可解析或非finite | claim_operand_value_invalid |
| 比較兩端不具可比性（值/基礎/單位/實體/比較域） | comparison_operands_not_comparable |
| 可比的兩值與所稱operator矛盾 | comparison_direction_contradicts_evidence |
| 比較結構／operator不支援或剩餘operator未被消費 | comparison_structure_unverifiable |

### 唯一理由決策程序（v1.2；取代v1.1互相衝突的missing／unknown描述）

0. **先完成舊schema/政策/數字/引用/absence檢查，若舊path拒絕就原reason原樣返回，不多添本輪code。**本文件新增構造不能救回舊path已拒的句子。
1. 依第4.1／7節先辨識surface與明訂deferred，不借deferred建立照應。對適用文字切完整claim skeleton；支持的O/Position/Range/Cost構造可保留未知名詞為不透明operand slot，不做斷詞猜測。
2. 若是明確但**未支援的構造/operator**、剩餘operator未消費或根本無法唯一切claim，不捏造slot再報missing：不唯一用ambiguous，無支援形狀用structure。`接近`即使端點未寫P仍structure；`位於中性偏強區間`這種原明列定性排除則不借本規則核真值。
3. 對可辨識claim，先蒐集全部必要slot，再按下表順序執行，取**單claim第一個失敗階段的碼**。每階段先檢查所有slot，不能因左slot先出現unknown就跳過右slot的missing。missing是「沒有明示P且不能合法照應」或未有同block引用，不是「label不在字典」；未知名詞無P不會因同包碰巧有適合數字而跳過missing。
4. label有P但未在映射中，才回unknown；已知label配錯field回mismatch，不改成bare-P重新解析。namespace缺失而P明示可用屬unknown，不從fact補上下文。成本的未知類別label／已知label錯配field／無更早label錯誤的跨namespace配對分別依第4.8.1節，不能混用同一reason。
5. scalar值無效用`claim_operand_value_invalid`；比較／range／成本對任一值或比較metadata不合用`comparison_operands_not_comparable`；已知label自己的domain/unit錯用mismatch先於數值。Range的A>B與裸之間等於端點是**值依賴的文法檢查**，必須在值與metadata可比後才判structure，不與前面的純形狀檢查混成一個優先階段。
6. 列表／Range是一個完整claim：其全部slot通過後才依序驗各pair真值，任何false產生同一contradiction碼；不是每個pair跳過整體slot檢查。獨立claim按block／原始起點累積碼，**第一次出現保留，重複去除**；不做全response字母或severity排序。單claim有missing不再追加該claim的unknown，但其他獨立claim的unknown仍需保留。

| 可辨識claim的執行順位 | 首個失敗結果（完整字串以上表為準） |
| --- | --- |
| 1. 所有slot的明示P／合法照應與同block引用 | missing |
| 2. 所有ID／宣告綁定唯一性，含成本同field重複 | ambiguous |
| 3. 可在不讀數值下判斷的形狀限制，例如列表長度／括號 | structure；無法切出唯一skeleton者已由本決策程序第2步先拒絕，不進此表 |
| 4. 所有明示label／所需namespace是否已知 | unknown label |
| 5. 所有已知label的field/domain/unit；成本未達的sample→required角色 | field mismatch |
| 6. 數值型別／finite／成本整數；比較域及時間／單位／實體metadata | scalar用value-invalid；比較用not-comparable |
| 7. Range值依賴的文法：A>B或裸之間等於任一端點 | structure，不排序／不猜inclusive |
| 8. 每個pair的operator真值 | direction contradiction |

下表是人工設計例，假設舊path已通過；不是新pytest或模型輸出。P皆須同block引用，例中F號只是角色示意：

| 明確情況 | 唯一預期碼／結果 |
| --- | --- |
| `{{F028}}高於中性水準`，無該基準P／DeclaredRef | missing；不先unknown、不補50 |
| `{{F028}}高於未知基準{{F010}}`，兩P唯一可引用 | unknown；不是missing，不能按F010值反推基準 |
| `神奇比率{{F033}}`，P存在且可引用 | unknown；不改成無標籤scalar |
| `股價淨值比{{F033}}`且field=pe_ratio | mismatch |
| 同一Pair左方未知label但有P、右slot沒P／宣告 | missing；下一獨立unknown scalar仍可另加unknown |
| `收盤價{{F001}}接近上軌` | structure；不支持容差，不能因缺P就改missing |
| 支持Pair缺RHS，同block另一完整Pair為false | 依claim順序missing後contradiction，不能合併成一碼 |
| Range的A>B，同時A的已知label配錯field | mismatch先拒；尚未具備核順序資格，不搶先structure |
| Range端點值為NaN，另一值看似可判區間倒序 | not-comparable；不對NaN排序 |
| 成本另一端是明寫的未知成本名稱＋P | unknown；若明寫外資但fact是別類則mismatch；無label的跨namespace配對才在資格階段not-comparable |

這避免C21/S1-R已正確拒絕案例被換reason；三個D核心因舊path原先pass，必須得到精確新碼，而非借`unverifiable_limitation_claim`掩蓋。
內部審查artifact可保留多個pair、span及field原值，但不在LINE文字曝露內部code或field。沒有新增公開schema或第二個裁判結論。

## 7. 排除範圍與不可假冒的安全性

### D08：未來條件情境

`scenario`及conditions維持舊validator結果，4.2紀錄`deferred_future_condition`，不能拿現值反證假設。
在非scenario的分隔段，若段首精確為`若/如果/假如/倘若/只要`，到下一強／弱分隔前的條件段defer並清空第4.4節所有狀態；不得用條件段的宣告／主詞支持後續當前主張。
逗號後沒有這些prefix、且明確帶自身主詞的當前主張仍須獨立驗證，不讓一個「若」豁免整個fact block。省略主詞的後件沒有可用主詞時仍missing，不能偷偷承接條件。
混合未來／當前、單點「跌破」、條件後件等尚未有完備時間grammar，保留未裁定紀錄；v1.2不解決D08的時間歧義。將block_type換scenario不是已通過安全審核，而是移出此次真值證明；Phase2不能把deferred樣本列為已完整verified。

### D09：名稱歧義

對預留短／中／長期均線詞，只defer該不確定label/claim，不擴散到同block的PE/PB或其他明確Pair。
D09「中期均線F025」不因此判錯，也不偷偷定ma20；診斷觀察結果保留，非正例安全背書。
D05集體三MA比較的真假**不依賴**天數名稱裁定，因此仍須驗三對。group名稱的短中長對應不在此次證明內。

### 其他明列限制（不當成已核能力）

- 不做含糊`接近`、未明示的區間端點含括猜測、超出第4.4節的省略主詞／跨句代詞／引述他人話語完整邏輯；適用surface裡可識別為明確比較但不在grammar的構造保守structure-unverifiable，不能視為成功。
- `位於A與B之間/上方/之上/下方/之下`只在第4.7節完整構造與綁定成立時支援，其他形狀仍拒絕unverifiable而不是判它一定為假。文法支援不代表已實作或已量測零誤拒。
- 上項的觸發限定在有label/placeholder運算元的數值區間／相對位置構造；`位於中性偏強區間`沒有兩個數值端點，是定性分類，不把它硬套成區間比較或追加D01的數學理由碼。也不因未新增拒絕就宣稱該定性判斷已verified。
- `為正/負/正值/負值`及定性多空原因鏈不屬此次雙operand真值，沿用既有規則且不計完整語意verified。不得新造0 fact或偷偷開裸數字例外。
- 欄位列表語句仍有其他未支援寫法；不能把「尚未寫測試」說成「已證明不誤傷」。階段2必須附支援／新增拒絕／deferred的逐例表，未知回歸停止，不改舊expected湊綠。
- explanation block裡只新增第4.8節限定的成本sample_days/required_days文法；成本日期／OBV／其他成本namespace仍不擴充。既有research_limitations成本helper不變不等於4.2已懂所有成本敘述。仍拒的合法數值子句要如實列出，不用擴大limitation豁免繞過。

**本輪不承諾任意自然語言全懂。**這是一個可測試的小語言；超出小語言的明確比較拒絕或明列deferred，不靜默當作可信。若使用者不接受上述可用性縮限，先擴充文法設計，不能直接改prompt壓掉句子來掩飾。

## 8. 相容性判定與下一輪案例藍圖（尚未建立測試檔）

設計保護：原1200的PASS/FAIL與原reason/repair/output不變；包括4.1的160、C21完整134、S1-R 27、成本88、V2 66、shadow29。
此為下輪hard gate，**不是本輪已用候選實作證明**。現有S1-R正例、C21-PE、volume比較及estimated正例按本文文法可保留；各例field/unit和值來自已存fixture，不補造metadata。

| 家族 | 已有核心＋至少五個新增控制的設計（待凍結） |
| --- | --- |
| D01 | 原missing-F010；補回同block F010的正例；F010僅在別block負例；F010只有evidence_id而沒有P負例；加入無關RSI/MACD仍拒；兩個完整Pair正例；第二個Pair缺右邊負例 |
| D03 | 原PE→PB錯標；正確PE/PB/yield三標籤；互換F032/F033即使值相同仍拒；F號重編但field不變正例；未知明示label負例；市盈率/市帳比同義正例；Parallel長度不合負例 |
| D05 | 原三MA全部false；D04三MA全部true；只一條MA違反也拒；高於與等於邊界；不低於相等正例；不高於大於負例；合法負MACD值與0、NaN/bool/Infinity不可比控制 |
| 不誤傷控制 | S1-R grounded_comparison原packet；C21-PE原fixture；C01/C02 volume與estimated；4.1合法估值缺失與不合法尾句；D08/D09觀察結果不變、明確deferred |
| 其他阻斷邊界 | 同ID重複／同值不同field；shares vs lots；currency/period/as_of明示不合；結構殘留第二operator；非scenario不准用另一句「若」豁免當前錯誤 |

### v1.1新增邊界控制（本輪僅設計，與原家族一併凍結，不能取代它們）

| 規則 | 必要正反控制與人工契約 |
| --- | --- |
| 前綴 | `估值面顯示/估值數據顯示`＋正確PE為正例；近似未知前綴、公司名稱、否定、引用句不能被刪掉放行。相同prefix＋PE標PB仍mismatch。 |
| 別名 | 股息率／股息殖利率配yield、市淨率配PB、布林帶中軌配middle為正例；同值不同field、改成收益率／錯軌／PE互換仍拒。F號重編不能改判。 |
| 明示宣告 | 前一完整scalar列中軌P，後一Pair的中軌缺重複P可唯一回指；其他block、forward、只有evidence_id、跨分號／句號、普通散文間隔均missing。 |
| 唯一性 | 同field两不同ID（即使值相同）回指ambiguous；合法重複提及同ID不多造歧義；packet重複ID維持拒絕。family label／代詞不能靠唯一數值猜對應。 |
| 主詞承接 | 明示close P後弱分隔＋`高於`可借該主詞；前一是多項Parallel、一元OSC正值、deferred、或已清空時不得借更早close。把承接後比較改成False，仍須contradiction。 |
| Namespace | 相鄰MACD／Bollinger正例；中間插入不同族、日期、散文、強分隔後的簡稱unknown。close↔中軌混合Pair不替後一上軌建立namespace。 |
| 狀態污染 | future／D09不種下可用宣告；新date／不同period/as_of/currency／不可比unit仍拒；失敗Pair不得藉後項宣告補回。第二operator未消費仍structure。 |
| 原缺口防倒退 | 原D01/D03/D05 expected不變；D01只補packet或引用清單仍missing；同一小句前方真正明示F010才是另一個可用正例，必須與原例分開凍結。 |

### v1.2必要控制藍圖（文件例，不是正式案例凍結）

| 家族 | 必須覆蓋且不能用候選parser自產expected |
| --- | --- |
| reason順位 | 中性水準無P→missing；未知label有P→unknown；同Pair缺一邊且另一邊unknown只missing；第二獨立claim unknown仍累積；舊path拒絕不添新碼；未支援接近無P仍structure |
| Position | 上方/之上嚴格>、下方/之下嚴格<；相等false；全部RHS逐對核；GroupMA一條false即拒；同值不同field不替換；D09個別名稱不被裁定 |
| Range | 內部true、外部false、裸之間兩端相等unverifiable；含兩端／不含兩端各自等值邊界；A>B不排序；A=B三種mode；缺任一slot／僅packet引用／別block不足；NaN/bool/Infinity/單位日期不合；第二operator殘留 |
| 狀態 | close scalar後ContinuedRange/Position只借close；成功range留下X而非B；false／deferred／強分隔清空；混族不延續namespace；「價格」不自動close；接近／否定／不完整括號不截短 |
| 成本數值 | 原18<60、重編F號17<45、0<60；等於時低於false而不低於true；required>sample反向Pair；sample與required錯標；未達僅sample→required；不把低於改成樣本業務可用 |
| 成本資格 | 同namespace精確field；缺P、不在同block、同field重複、跨成本類別、count對shares/價格、不同period/as_of/trade_date/currency/entity、截止日未來、非整數count、bool/NaN/Infinity均拒；成本quality=estimated只依numeric renderer，原research品質負例不改 |
| 成本context | 完整外資label可限定同Pair另一局部label；相鄰CostScalar→省LHS可用；generic量能、日期、散文、強分隔後不可借舊成本context；籌碼prefix不產生證據；未知date/OBV仍不新增豁免 |
| 原例守恆 | D01/D03/D05原預期不變；原1200狀態、C21/S1-R/成本88及4.1的reason/repair/render不變；D08/D09明列deferred，不作完整語意pass |

#### 若另獲核准：先重跑v1.2影響量化，再凍結正式契約

1. 獨立工具與tests分開；保留v1的98/76及v1.1 r3的77/63原始結果／失敗／工具，不覆寫。新工具須綁v1.2文件SHA、相同31來源SHA、317 occurrence／240字串與原始投影方法。
2. 仍用現行validator作baseline，分開記原完整response及單block投影；列出全部v1.1→v1.2變化、當前pass→候選reject原文、每一pair／range mode與宣告來源span、未支援/deferred、不唯一解析和工具失敗。不可透過刪減corpus降低分母；reason-only變化與pass/reject變化分開統計。
3. 人工逐句覆核變化是否只解決本文列出的表達缺口；13個實質缺口不得被放行，8個未裁定不偷改成verified。保留未解決的42句子集與原因，逐句列出公司／日期／接近／強分隔次要阻礙，不能只列成功例。沿用v1.1的57+3工具控制，新增本版必要控制；既有expected若因本版改變，須保存原值、對應條款及人工理由，不能覆寫原檔。
4. 量化報告先回報。若仍有高collateral（例如超過240的兩位數百分比）或實質漏擋，明確停下讓使用者决定接受有限覆蓋或另外擴充，不自動視為「比v1好就可凍結」。不要求另一輪重述D01/D03/D05根因；需決策的是新文法的可用性取捨。
5. 工具的候選結果**不得生成正式expected**。正式案例從凍結來源衍生、人工逐條對照本文寫死期望；新舊文法之間的預期改動須有具名case與文字理由，不讓parser替自己出考題。

量化取捨確認後的正式階段2順序（這輪沒有做）：

1. 僅新增一個獨立test檔；直接讀原D/fixture來源並凍結**完整input/provenance/expected**，不抄成容易漂移的第二份案例來源。
2. 文法每種支援構造至少一對正負例；unknown/deferred與舊拒絕理由另外列。CASESET_SHA包括內容、期望、来源SHA；少／多／重複ID、同ID換內容都失敗。
3. raw及deterministic repair後皆斷言精確reason、rendered、input不變；新綁定修復不得靠repair猜ID／調換PE/PB／反轉方向詞。
4. 真實跑既有validator取得紅燈；不得為湊數改輸入或expected，不寫候選parser再用自己的parser產expected。
5. 完整suite保存逐ID狀態。新紅燈照實計入failed，不xfail；現有1200全保持，保留所有失敗原文。
6. 原D01預期`claim_operand_binding_missing`；D03預期`claim_field_label_mismatch`；D05預期`comparison_direction_contradicts_evidence`；現validator仍pass是預期要展示的unsafe acceptance。這些是**已列明的設計契約，仍未有本輪正式紅燈驗證或production實作**。
7. 完成紅燈交付即停止，production修改仍須再核准。不一次把本文件當production授權。

未來production白名單提案仍只在`review_src/core/line_model_validation.py`增加純helper＋success前guard；不搬檔，不改既有guard或repair。不修改tests既有casehash來掩蓋相容性落差。若需其他檔案／大範圍語言grammar則停止重新界定。

## 9. 不擴大範圍與驗收停點

不新增claims schema／model call／prompt策略／packet builder／共用核心／跨thread追蹤／Future取消修復；不動referee、數值公式、資料品質來源、DB、env、launcher、GPU排程、LINE傳輸；不重啟、不部署、不V2接管、不開始Phase3+。

本輪變更僅本設計文件、review index及`logs/line_model_shadow/comparison_binding_design_v1_2_20260831/`文件證據（原文快照、diff、hash／條款與連結查核、報告）；不新增Python工具，不改既有v1/v1.1量化工具、其結果或任何tests。
完成後再次核對來源/歷史fixture/設定SHA与文件連結；文件變更不重跑一遍相同suite冒充新能力驗收。
本輪風險低（文件與離線證據），未來parser/語意guard風險中，須以紅綠控制及相容性gate驗證。

**目前完成v1.2文件修訂，尚未執行v1.2影響量化。D01/D03/D05尚未修復；沒有新增4.2正式測試／紅燈，Phase2不通過。**
