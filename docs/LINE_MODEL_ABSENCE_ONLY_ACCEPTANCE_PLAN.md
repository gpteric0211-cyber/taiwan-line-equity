# Absence-only v2：範圍、缺口處置與離線驗收計畫

日期：2026-08-31。版本：acceptance-v1（離線測試落檔／實測補記）。狀態：validator候選修復已實作；G6/G7尚未通過，不得部署。
設計來源：[LINE_MODEL_ABSENCE_ONLY_REPAIR_DESIGN.md](LINE_MODEL_ABSENCE_ONLY_REPAIR_DESIGN.md)。
本文件的設計預期不等於修復成果。使用者本輪只核准新增離線相容性測試及證據，未核准production、部署或V2接線。
第1–7節為契約，第8節保留原文件階段紀錄，第9節記錄本輪實測及更正。

## 1. 本次決定與下一個停點

下一步建議是「S1-R相容性契約離線補強」，先驗證預期與現況，再另行核准validator修復。
不跳至controller、共用核心、typed absence期間、模型重訓、網搜、圖片或部署。
來源→DB→service/referee→單股packet→validator→renderer邊界不变，主結論仍由referee產生。

本次補齊的設計決策：

- 保留原13案與全部內容指紋，不以新案例取代原紅燈。
- current_assessment允許映射仍為空；有雙邊數值證據的比較重述走bound_comparison，不混為均線方向判斷。
- A17/B2的「未顯著放大」採設計4.4a的狹義比較重述政策；B14的趨勢影響尾句拒絕unknown。
  這是待核准的語言政策，不是模型實測或一般統計顯著性定義。
- 缺metadata的舊fixture保留為負例，另做明確標記的合成正例；不從模型文字補DB身份／品質。
- historical pass不等於正確；片段合法不等於整則應pass。全量replay與派生正例分開驗收。
- 舊replay工具沒有本次全部驗收能力，不能拿其exit=0或historical pass數替代新guard結果。

當使用者核准「下一個測試子步」時，白名單僅新增tests/test_line_model_absence_only_compatibility_contract.py，
以及更新本文件／review packet與新增不可覆寫的離線證據目錄。
不修改production、原S1-R測試、audit build_cases或既有fixture。完成紅綠現況報告後停下，不自行修validator。
若單一測試檔無法乾淨完成，先提出新增檔案與理由，不讓範圍自行擴張。

未來另行核准「修復子步」時，候選production白名單只有review_src/core/line_model_validation.py內validator/helper；
repair策略、packet builder、模型prompt/schema、renderer、service接線均不在白名單。
tests/test_line_model_shadow_service.py僅可能涉及第5節兩個測試的目的拆分，須在修復授權中明列。
核准文件不等於核准這些候選diff。

## 2. 固定語料、原文與量化口徑

### 2.1 必驗語料與來源指紋

以下路徑均相對專案根目錄。JSONL行號等於attempt_index；block序號從1開始。
讀取指定line/attempt時須同時核對兩者，不能只依陣列位置猜案例。

| 語料 | 檔案 | 筆數／可解析model_output | 歷史final pass/reject | SHA-256 |
|---|---|---|---|---|
| A20 | logs/line_model_shadow/final_phase_a_current_source_v2_20260830.jsonl | 20/20 | 10/10 | ab86c7e371a506f9d0f46084da04ac6f36f0c40d1c6bfd53136e5bedfe563125 |
| B30 | logs/line_model_shadow/final_phase_b_current_source_v2_20260830.jsonl | 30/28 | 16/14 | 95aac3d541d16948e01252c1fe524d4eb7c0eae94a53a7a2e1cdce8f99a9379b |

B21/B23的invalid_json仍在50筆分母內；不可解析不等於沒有樣本。
A20 raw=8 pass/12 reject，B30 raw=11 pass/19 reject；repair後final與raw必須分開列。
本次只重讀原始資料，沒有新模型呼叫，也沒有用尚不存在的新guard得出新拒絕率。

原S1-R測試tests/test_line_model_absence_only_release_contract.py的SHA-256：
b4b41ef6fbbb5210814f913b8a1fb2dd144a95e17199f9ec14ad013757522b78。
唯一case來源scripts/audit_line_model_validation_boundaries.py的SHA-256：
774ffdb59fc0b30721c76aeb07c761460a75440fd34e588f422bfb4a000bbde0。
當前validator SHA-256：25f909d8a202bd8dfa512507eb4d9a05052a996081b85f8af5953b10843d9f32。
未來與這些指紋不同必須說明差異，不能重新生成golden hash使測試變綠。

### 2.2 歷史2＋1片段：不只標籤

A17 block3原文，evidence_ids=[F081,F044,F006,F030]：

> 支撐壓力區間因缺乏合格正規事實而未納入分析，單日逐筆分價量未通過品質門檻，不納入主動成交力道評估。當日成交量{{F006}}低於資料庫均量{{F030}}，量能未顯著放大，需留意後續量價配合情況。

B2 block4原文，evidence_ids=[F006,F030,F044,F081]：

> 支撐壓力區間資料不可用，無法提供具體支撐與壓力位。此外，單日逐筆分價量未通過品質門檻，不納入主動成交力道分析。當日成交量{{F006}}低於資料庫均量{{F030}}，量能未顯著放大，需留意後續量能變化對趨勢的影響。

B14 block4原文，evidence_ids=[F030,F043,F044,F081]：

> 支撐壓力區間資料不可用，無法提供具體支撐與壓力位。單日逐筆分價量未通過品質門檻，不納入主動成交力道分析。當日成交量{{F043}}低於資料庫均量{{F030}}，量能縮減可能影響趨勢持續性判斷。

三包的相關證據均為：

| ID | domain.field | value | quality/use_scope | 作用 |
|---|---|---|---|---|
| F006 | official_ohlcv.volume_shares | 字串15025832 | ok；explanation,numeric_claim | 當日量 |
| F043 | trading_state.volume_shares | 數值15025832 | ok；explanation,numeric_claim | 同一packet的當日量另一來源欄位 |
| F030 | technical.volume_ma20 | 字串22143102 | ok；explanation,numeric_claim | 既有均量，不是昨日量 |
| F081 | support_resistance.availability | null | unavailable；limitation | 只能說限制 |
| F044 | evidence_summary.limits[0] | 單日逐筆分價量未通過品質門檻，不納入主動成交力道；支撐區間另由多日官方 OHLCV 產生 | missing；limitation | 不是量能／多空的數值證據 |

三個數值fact皆canonical_db、unit=shares、period=daily、trade_date/as_of=2026-08-28。
15025832<22143102成立；不從這個關係重算放量分數或推出趨勢將轉弱。
F044只可提供受限用途的資料限制資訊，不因其value有文字就升級為eligible_assessment。
其詳細品質門檻敘述若有限文法無法完整驗證，須獨立記unknown；不能靠合法量能片段放行整段。

| 案例 | 片段級設計預期 | 完整output驗收規則 |
|---|---|---|
| A17/3 | 量能比較＋受控未放大重述可成立 | A17/5另有僅missing summary支持的「均線結構偏多」，整則不得因第3段合法而pass |
| B2/4 | 同上；尾端觀察要求不是影響已發生 | raw原missing_fact_used_outside_limitation必須保留；repair後的B2/5條件式技術判斷仍須另驗，不能預設整則pass |
| B14/4 | V<M可成立；「縮減可能影響趨勢」尾句unknown拒絕 | B14/5還有absence支撐偏多的問題；整則拒絕，不刪第4/5段換取pass |

B2歷史repair_codes=[reclassified_limitation_evidence_block]；A17/B14=[]。
這3個片段不能寫成「3個原本完全合法的回答遭誤殺」。
其中2個是有限比較重述的相容性要求，1個是明確保留拒絕的歧義延伸。

### 2.3 current_assessment量化的限制

前輪人工篩查在主要50筆中找到16個「受保護通道的當前／邊界主張block」。
8個屬歷史final pass，8個屬歷史reject；不是16筆獨立回答。
歷史pass的8個為A2/4、A17/3、A17/5、B2/4、B5/4、B14/4、B14/5、B20/3。
其中5個有unsupported assessment，另3個就是上述2＋1片段；不可直接用8/50算誤拒率。

沒有找到「受保護通道中有合格canonical assessment，卻只因空映射會被誤拒」的確證案例。
這裡的0是已檢語料的確證數，不是production相容率100%，也不是尚未實作的guard實測結果。
例如evidence_summary.main_reasons[0]、main_status、technical_observation文字雖含偏多／可觀察，
實際quality=missing、use_scope=[limitation]，不符合題述「有合格assessment」前提。
global_market_context.stance、taifex_night_context.stance等有合格fact，但所見eligible-only block在本guard外。
外部市場背景不能替個股均線背書；A18/2的此類混用是已知但不在本步修復的全域grounding缺口。

補充掃描的16份歷史檔共370筆、366可解析，曾發現9個類似量能尾句（6個未放大、3個縮減）。
它們包含本節A20/B30、重試與不同來源版本，不能相加成420筆，不能稱獨立樣本或P95證據。
本次硬驗收固定主要50筆＋本文件派生案例；補充語料只作擴充排查，不冒充已完成新guard全量replay。

## 3. 配對、原因碼及拒絕粒度

唯一source of truth是設計第4節；此節補充驗收口徑，不另寫競爭規則。
比較要判斷欄位、metadata、數值關係及完整尾句，不只檢查evidence ID存在或len(ids)>=2。
新guard的理由縮寫：A=absence_only_evidence_cannot_support_claim；U=unverifiable_limitation_claim。
縮寫只用於本文件表格；實際JSON與測試必須使用完整字串精確匹配。

| 輸入情況 | 理由契約 |
|---|---|
| 原S1-R的8個有效schema方向負例 | raw、repair後均精確[A]，不得多出schema或numeric錯誤 |
| 已辨識方向句只有absence／無關close能引用 | A，不因有任意ok fact就改成pass |
| 可能有合格assessment但缺批准映射；比較基準／尾句不明 | U，不能硬說全是absence |
| 同一output獨立有A類與U類錯誤 | 完整保留兩碼，依設計遍歷順序去重；不是擇一遮蔽 |
| 舊schema/數字/date/policy/event/scope檢查已拒絕 | 原因碼原樣保留，不追加語意碼冒充新guard生效 |
| repair只是改標limitation | 必須再經同一新guard，不得藉改標洗白主張 |
| 正例 | passed=True，reason_codes=[pass]，完整rendered值與限制句都保留 |

任何新guard拒絕必須passed=False、rendered_blocks=[]；不可局部刪句再pass。
parser／helper不能改input、補citation、變更quality、刪多餘尾句或吞exception。
若保留內部claim診斷，至少含surface、block_index、原文span、claim_type、引用IDs、可用能力、拒絕原因，
只保存离線artifact，不為此改API response或記錄LINE個資。

## 4. 下一個測試子步的完整契約矩陣

以下均為未寫的測試要求，不是已執行測試碼。
原13案由既有release test直接執行，import既有build_cases＋內容hash；不在新檔複製13案。
新增派生案例必須深複製來源，記source file hash、attempt/block、原文、修改清單及派生內容hash。
不能把派生fixture稱為歷史原樣輸出；不能改原歷史JSONL。

四surface指text_template、conditions、missing_data、research_limitations。
除指定raw先被舊規則拒絕外，每案都驗raw／repair後及input不變；不mock validator或repair。
每列的變體都要有獨立case ID；總數以凍結的case manifest精確核對，不以「至少幾案」代替內容核對。

派生fixture組裝規則：以原honest_limitation:text_template深複製為容器（focused/scopes=[]、三個block），
只向其中加入所選historical numeric facts，再替換第一段為指定比較片段並引用F101及兩個數值ID。
其他兩段、top-level空陣列保持誠實限制；不把historical整包的coverage/minimum-block要求帶進片段測試。
F101是既有synthetic限制fact，不是假裝歷史packet原有的fact；這個派生packet只供離線契約。
其完整field=support_resistance.status已可辨識主題，與F1完全沒有domain/field的情形不同，不能一律誤拒。
所有C04–C20受保護主張都必須明確有F101觸發或位於頂層限制surface；只有C16的guard外對照例外。

| ID族 | 輸入／變體（逐個展開） | 新版本預期 |
|---|---|---|
| S1R | 原8負例＋5正例＋14個防繞過helper | 原13案內容hash不變；8案[A]、5案pass；helper仍全過 |
| C01 | A17比較子句、B2比較子句各自抽出；保留原F006/F030，另用既有F101誠實限制觸發guard | 兩個派生正例pass，雙值與未放大／觀察要求全文保留 |
| C02 | B14比較子句分成「僅V<M」及「V<M＋原趨勢尾句」；F043/F030及F101 | 前者pass；後者[U]，不得只剝尾句變pass |
| C03 | 深複製grounded_comparison；同block加F101、改limitation、加誠實缺失句；僅派生F102補domain=official_ohlcv、F103補domain=technical，兩者trade_date=2026-08-28 | guard內正例pass；原guard外正例另照常重跑；新增metadata是明示的合成fixture，不回填歷史或原13案 |
| C04 | 單一absence、兩筆absence、absence＋無關close三種證據配置；各配偏多／轉弱 | 六案[A]，不得以有任意ok證據豁免 |
| C05 | F101 quality=unavailable/use_scope含explanation；quality=ok/use_scope只limitation；quality=missing/value寫偏多 | 三案方向主張均[A]；value文字不提升能力 |
| C06 | 「資料不足，無法判斷是否偏多。」放四surface | 四案pass；不是偏多字串黑名單 |
| C07 | 「無法判斷支撐壓力，但均線已轉弱。」放四surface | 四案[A]，前半否定不能保護後半 |
| C08 | 「資料不足，並非不能判斷偏多。」放四surface | 四案[U]，未知雙否定不冒充安全句 |
| C09 | 「若資料補齊，再評估支撐壓力。」／「若資料補齊，股價就會上漲。」 | 前者pass；後者[A]；若舊policy先拒絕則保留實測原碼並記為非新guard覆蓋 |
| C10 | C01中V=M、V>M、V<0、M=0、非有限值五種 | 舊規則已拒絕維持原碼；否則[U]。只有V<M且合法範圍可用未放大重述 |
| C11 | C01把M改TWD、另一trade_date、缺as_of、缺trade_date、field改close五種 | 舊規則已拒絕維持原碼；否則[U]，不可只看同時有兩個數字 |
| C12 | C01少一側引用、placeholder未知ID、同ID重複fact且不同value三種 | 不能pass；前兩案精確保留舊ID/placeholder碼；若重複ID舊guard未擋，新guard以[U]拒絕歧義綁定 |
| C13 | 比較與未放大重述間改為分號、句號、換行、轉折、另一surface、另一block六種 | [U]；跨位置不可借用antecedent |
| C14 | 「量能未顯著放大」改「量能顯著縮減」或「量能較昨日縮減」 | 兩案[U]；均量不是昨日量，未放大不是統計顯著縮減 |
| C15 | 合法比較後加「但均線結構偏多。」；反向把壞句放前；把壞句放conditions | 三案[A]，合法前後綴都不能吞掉不當主張 |
| C16 | 有合格technical.trend=偏多/explanation＋F101；同block作current_assessment | [U]，明示空映射的刻意限制；外部eligible-only版本維持舊路徑，不當成語意證明 |
| C17 | 比較兩值quality改estimated；再各別改stale/source_delayed | estimated正例pass且兩端render都保留_render_fact的「推估 」前綴；後兩負例保留ineligible_numeric_placeholder/unresolved_placeholder舊碼，不當新語意碼功勞 |
| C18 | 頂層放方向句而另block有足夠數值；頂層放未知代碼或合法代碼接偏多尾句 | 不借用block授權；已辨識方向[A]，未知代碼[U] |
| C19 | F101不存在/無domain/field，卻說特定支撐缺失；空packet僅說「資料不足」 | 特定主題[U]，空packet一般能力限制pass；不從模型文字認定欄位身份 |
| C20 | response同時有S1-R方向錯誤、未知尾句，互換block次序並重複錯誤 | A/U都保留、依首次出現去重；rendered必為空 |
| C21 | 原Contract A/B/C、PE＋缺基準正例、兩個repair fixture目的拆分 | 依第5節及設計6.2逐項核對，不能用新mock取代真實packet路徑 |
| C22 | 原48組合＋另4數字/date負例、兩個bound-date案例、audit CLI53案 | 48=44負＋4正；日期原碼不變，S1-R新增語意碼不能掩蓋舊錯誤 |

新增測試前先凍結逐案完整packet/output/expected-reasons；舊規則優先列不能只斷言not passed。
需先記下修改前的精確理由，之後精確比對；若unknown先發生於日期/schema，不能列為語意修復成功。
若新測試證實本文件預期與來源不符，保留真實輸出並修改提案供審，不改資料製造紅燈。
缺少case／重複ID／同ID內容更換必須使測試失敗；新派生hash不可在測試執行中自動更新。

## 5. 舊fixture與語意缺口的具體處置

### F1：缺domain/field的placeholder repair

位置：tests/test_line_model_shadow_service.py，test_deterministic_repair_only_removes_limitation_placeholders。
原F001只有canonical_db/unavailable/null/limitation，沒有domain/field；原repair後聲稱支撐不可用。

- 原input／repair output保留，原先repair_codes順序仍為removed_absence_placeholder、removed_event_placeholder。
- 原測試的「repair刪除placeholder且不補數字」目的仍須通過；原metadata下的最終validator預期改為[U]。
- 另建synthetic正例，只在深複製F001增加domain=support_resistance、field=availability，其餘不變。
  repair結果不變，現在有支撐主題來源才可pass；同時保留刪掉metadata後拒絕的配對負例。
- 這不是更換歷史來源；修改舊test最後的pass斷言是明確的行為契約變更，需在修復diff列出，不得偷偷改。

### F2：required_days不能證明coverage不足

位置：同檔test_deterministic_repair_generalizes_cited_event_id_and_required_day_threshold。
原fact只有institutional_context.required_days=60，research_limitations卻說「官方資料未達60日」。
repair改成「官方資料未達所需交易日門檻」並未增加實際coverage證據。

- 保留原input及去除E001／60等repair輸出斷言；原output在新validator最終應[U]。
- 本步不新增coverage schema、不用request scope coverage冒充交易日coverage，兩者不是一回事。
- 派生正例只把research_limitations改為「事件尚未驗證」，其餘packet/output不動；已有E001 unverified支撐。
  此案只證明原repair行為與事件metadata限制可共存，不證明「coverage不足」主張已支援。
- 若要支援肯定的交易日coverage不足主張，另列現有來源欄位與品質映射審查，不以新捏造數值塞進fixture。

### F3：尚未解決、不可標成已修

| 缺口 | 本步狀態／不得宣稱的內容 |
|---|---|
| S1-R八個方向主張仍通過 | production未改；目前仍未修，不可merge為可發布修復 |
| eligible-only block引用無關ok fact作方向／因果判斷 | 不在本guard範圍；A18/2等仍需未來全域claim-binding評估 |
| event-only block有unverified卻缺title，仍說供應鏈會議 | 舊事件grounding缺口未修；本步僅驗事件驗證狀態，不新增新聞查證 |
| technical.trend等合格assessment與absence混在同block | 空映射會拒絕為U；量化只證明所查歷史未見此合格實例，合成C16要把限制寫死 |
| evidence_summary方向文字被投影為missing/limitation | 不從value推翻quality；如要修投影需另核對上游，不在本次白名單 |
| packet不含stock_code，無法逐fact證明同標的 | 沿用上游單股packet前提；不聲稱具備跨股混包偵測能力 |
| 有限語法可能誤拒未收錄的誠實表述 | 逐案replay分類及原文交審，不能標成一般NLP完備 |

## 6. 歷史replay不能沿用舊統計口徑

scripts/audit_line_model_phase_b_replay.py的audit_attempts確實會呼叫現行validator重判raw/stored output，
但counts[result["validator_result"]]累加的是歷史結果；且main()無條件return 0。
它还跑重新壓縮的prompt preflight；本次語意replay不要把重壓縮packet當validator原輸入。
因此既有工具是參考，不是本次50筆修復gate；本輪不改該腳本或假稱它已有下列能力。

未來離線驗收必須為每個attempt保留三欄，另保存stored validated output的重判：

| 欄位 | 內容 |
|---|---|
| H：historical | 原始JSONL raw verdict/reasons、repair_codes、final verdict/reasons，永不覆寫 |
| B：baseline replay | 用凍結的修改前validator/repair，對原compacted_packet＋raw model_output重跑raw及實際repair路徑 |
| C：candidate replay | 用候選validator與同一repair，對同一packet/output重跑；不得換fresh DB／新版重壓縮packet |

另列baseline及candidate對historical validated_model_output的validator結果，
不要把「重判歷史已修文字」和「從raw重新走現行repair」混成一個結果。
baseline不需改裝工作樹：在修復前先跑完保存B；修復後同輸入跑C，以source hash綁定。
兩階段只讀歷史資料，不呼叫模型，不把前後版本來源來回覆寫到runtime目錄。

replay具體要求：

1. 全50筆保留，包含invalid_json、全部reject與任何exception；沒有「只取每題第一筆pass」。
2. 與production一致：raw先判；失敗時執行既有repair。repaired非None且repair_codes非空時才驗修復後結果，
   **且只有修復後passed=True才替換service最終validator**；修復後仍reject時保留raw的最終理由。
   分開記service_repair_attempted、service_used_repair與無條件after-repair診斷，不把診斷當service最終結果。
   原文位置：review_src/services/line_model_shadow_service.py:491–495。本條補正未改production。
3. 逐筆分開報H→B與B→C；H/B不同代表歷史版本差異，不可全歸功本次修復。
4. B→C按unchanged_pass、unchanged_reject、new_expected_reject、new_unexpected_reject、unexpected_accept、exception分類；
   每筆有全文、IDs/facts、raw/repair理由及人工依據，分類總數必須等於50。
5. 各question/scenario一併列attempts、pass、reject、exception、repair使用次數／拒絕率及理由分布。
   invalid_json屬reject分母，exception另列但仍在attempts內；不得丟棄後重新計算好看的比例。
6. C01/C02/C03派生片段另列，不計入歷史50筆的接受率；原整則拒絕仍可同時证明其中一段比較合法。
7. 所有pass→reject必須逐案解釋；不得要求50筆全部保留舊pass，也不得先接受任何新unknown而不列影響。
8. 每個新採用的文法正例都要有只改一個關鍵metadata／尾句的負例；不能用歷史全文allowlist通關。
9. 驗收gate以實際B/C矩陣與逐案契約計算，不以腳本exit=0或歷史counts推斷通過。

## 7. 離線硬門檻與證據包

### 7.1 下一個「只新增測試」子步

開始前核對原S1-R、audit來源、主要兩份JSONL的hash；讀全部341來源manifest與11 protected。
同時記錄新檔使來源集合增加的差異，不能往後仍硬寫341檔而漏編譯新測試。
用專案虛擬環境完整py_compile review_src/scripts/tests全部Python，以及完整pytest tests -q，保存stdout/stderr/exit。
可攜命令中的python代表已選定的專案環境，不硬編碼任何使用者家目錄；執行前列出直譯器版本／來源。
compile輸出可放獨立artifact目錄；不啟動app、模型、DB修復或LINE端點。

新增測試後再次全量編譯＋完整pytest；預期舊八個紅燈仍存在，新契約也可能產生真實紅燈。
報告不能寫全綠，不能加xfail/skip或改guard讓紅燈暫時消失；尚未核准修復時到此停止。
針對已有完整可執行的歷史replay讀取邏輯，先審查無外部副作用才在離線子步使用；不能呼叫實機A/B/D runner。

### 7.2 之後另核准修復時的硬門檻

| Gate | 必須滿足；任一不符不宣稱修復驗收完成 |
|---|---|
| G1 範圍 | 只有核准檔案差異；原13cases/IDs/hash未變；無新xfail/skip、無改弱斷言 |
| G2 精確語意 | S1-R八案raw/repair均[A]、五正例全文保留；14 helper自測全過 |
| G3 相容性 | 第4節凍結manifest每案結果正確；C01兩正例、C02比較正例、C03 guard內正例誤拒數=0 |
| G4 反例 | 無關ok、雙absence、錯比較、未知尾句、跨surface、改標repair不可偷渡；錯理由不能算pass |
| G5 舊規則 | 日期44＋4負例原碼不變、4日期組正例不誤拒、A/B/C與PE修復保留、audit53期望全符 |
| G6 完整回歸 | 修改前後全量py_compile＋pytest tests -q原始stdout/exit齊全；修復後完整套件0 failed/0 error，無新增skip/xfail |
| G7 歷史影響 | H/B/C全50筆無遺漏；unexplained verdict change=0、unexpected_accept=0；新增unknown全部列原文經審查 |
| G8 人工逐讀 | 讀全部B→C變更及至少10個候選pass（不足10則全讀並標不足）；包括混合比較、缺失、PE/修復；不以此稱實機品質驗收 |
| G9 保護邊界 | 11/11 protected一致、非白名單來源hash不變、input/packet/原歷史證據bytes不變 |
| G10 無副作用 | 真實模型/LINE呼叫=0、部署/重啟=0、canonical DB寫入=0；只執行已審查離線工具 |

通過G1–G10只能宣稱「有限absence-only修復的離線契約與相容性驗收完成」。
不能據此宣稱新模型拒絕率降低、P95合格、GPU排程安全、Phase 1核准或V2已改善LINE回答。
guard耗時只另記離線實測，不設定憑空SLA，也不拿CPU測試時間代替端到端LINE deadline gate。

### 7.3 必交artifact與原文

使用新的logs/line_model_shadow/absence_only_compat_<run-id>/，每次run-id唯一，已存在即停止而非覆蓋。
證據包至少含：

- manifest：source/test/corpus SHA-256、13案及新增case內容hash、Python/OS環境、開始／完成時間與命令。
- before/after完整compile及pytest stdout、stderr、exit code；完整測試碼快照，不只列函式名稱。
- 每案完整packet、原output、repair後output、raw/repair結果、rendered、精確原因及input未變證據。
- historical H/B/C逐案JSON、統計與人工重讀表放同一份報告；原失敗／拒絕與exception不可刪除。
- 每條人工判定附原文span、fact_id/domain/field/value/unit/period/as_of/quality/use_scope及為何足夠／不足。
- protected 11檔逐檔expected/actual與非白名單來源差異；新增檔單獨列。
- 尚未完成清單與實際副作用紀錄。沒量服務telemetry時，不以「未做重啟」冒充測得零service gap。

## 8. 本次文件交付的實際驗收

本次只做讀碼、歷史原文／hash核對及三份Markdown變更；沒有新增測試碼或production。
最後完整實測仍是logs/line_model_shadow/absence_only_s1r_20260830_2316/post_pytest.txt：

```text
8 failed, 731 passed in 90.42s (0:01:30)
```

該八個失敗是現有語意漏洞的紅燈，不是這份文件完成後已轉綠；本輪未重跑compile/pytest。
當前來源未變，舊證據保留歷史效力；將來validator變更後，舊pass不能作新版本驗收，必須重跑。
本次文件風險低；未來validator拒絕邊界改變風險中。maintainable-refactor使本次保持單步與referee/DB邊界。
完成的是下一步規格與驗收契約，不是LINE bot升級；下一停點仍為先核准離線測試子步。

## 9. 2026-08-31：已核准的離線測試子步交付

本節取代第8節的「尚未新增測試」現況，但不覆寫其歷史紀錄。
本輪只新增tests/test_line_model_absence_only_compatibility_contract.py，更新本文件及review packet，並保存新證據。
完整原文、命令輸出、逐案JSON、人工檢視與失敗處理見：
[離線交付報告](../logs/line_model_shadow/absence_only_compat_20260831_010509/REPORT.md)。

### 9.1 凍結案例與結果

- C01–C21具體展開70個案例，51紅／19綠；其中19綠包含15個預期最終pass及4個舊規則拒絕控制。
- 保留原S1-R的13案例及內容來源；既有8個absence-only紅燈沒有移除、改弱、xfail或skip。
- 新檔共134個展開測試：70相容性＋50歷史baseline replay＋13驗收helper自測＋1真實service採用規則對照。
- 原始來源匯入audit build_cases；新增案例以完整packet/output、expected理由、rendered及來源資訊的SHA-256鎖定。
  六種漂移（缺漏／重複／未知ID／文字／packet／expected）及錯理由、錯順序、殘留違規rendered均有自測。
- C21兩個舊fixture輸入透過AST讀取原函式字面值，保留原始負例，另列合成正例，沒有修改舊測試檔。
- C21/C22既有test_line_model_v2_contracts.py全檔與三個repair測試另外重跑：69 passed。
  既有48個負例（44個日期／期間參數案及4個其他數字／日期案）、日期正例及新packet pipeline A/B/C仍通過；
  audit53仍8 mismatch，保留原八個語意紅燈。

完整測試不是全綠：修改前8 failed／744 passed；最後59 failed／827 passed，新增51個契約紅燈。
本輪來源實際起點已含前一輪網頁修復，review_src/scripts/tests共有343 Python（不是舊文件341），
連同start_dashboard.py共344個編譯；新增測試後共345個全部編譯成功。
11/11 protected、5/5 corpus/validator/audit/S1-R、全部既有Python與launcher/HTML不變；私有設定僅核對hash且不變。

### 9.2 真實歷史資料與驗收程式更正

全50筆H/B replay保存原packet/output，不重壓縮、不查新DB；raw19pass/31reject，final26pass/24reject。
H/B verdict及原因碼差異0；B21/B23 invalid_json仍在分母。repair嘗試14筆，修復後pass而採用7筆。
C尚未實作／未獲授權，所有candidate欄位明列null；不能稱B/C相容性已通過。

兩個驗收程式錯誤已修正，舊失敗輸出保留：

1. C02比較正例原expected字串誤保留整數float的「.0」；現有renderer會移除它。
   只修正測試預期格式，packet/output/判定規則不變；frozen_case_manifest_v2.json記錄完整前後差異。
2. 初版replay把仍被拒絕的repair結果當作service最終結果，導致5筆人工差異。
   第6節已依production原文補上passed條件；新增對照真實service、僅mock模型傳輸的測試驗證。
   summary.json屬中間錯誤版本，final_summary.json才是本輪最終H/B報告；原始差異不刪除、不歸因於模型。

### 9.3 停點與未完成

本輪完成的是新增離線測試與紅綠現況證據，不是validator修復，不是LINE升級。
尚未完成：新A/U原因碼、有限語意guard、候選C重播、全部B/C變更逐讀與修復後完整套件全綠。
current_assessment映射仍空；C16混合證據例預期unknown屬設計限制，不能當成已具備主張語意映射。
下一步必須先由使用者審查本輪51個新紅燈與相容性取捨，再另核准validator範圍。
不重啟、不部署、不呼叫真實模型或LINE，不進V2接線；本輪未量服務telemetry，不宣稱測得零停機。

## 10. 2026-08-31：validator候選修復實測

使用者另行核准validator修復後，只修改review_src/core/line_model_validation.py：在全部舊規則通過後，
加入有限absence主張文法及證據能力配對。原70個相容性案例、13個S1-R案例、audit來源與預期均未修改。
完整證據與逐案原文見
[候選修復報告](../logs/line_model_shadow/absence_validator_fix_20260831_065100/REPORT.md)。

- S1-R 13/13、相容性70/70、audit53/53與V2 contracts 66/66通過；11/11 protected一致。
- 修改前完整套件59 failed／827 passed；修改後2 failed／884 passed。
- 剩餘兩個失敗是舊C21測試仍要求「缺topic metadata」與「未證明coverage卻宣稱不足」的修復文字通過；
  不以放寬guard或未授權修改舊測試換取全綠。
- 歷史H/B/C重播50/50完整：B service-final 26 pass，C 0 pass；26個pass→reject均保存原文，unexpected_accept=0。
  其中7筆是舊raw失敗曾由repair採用、但repair後文字未通過新guard，因此C保留原raw理由；其餘是新語意拒絕。
- current_assessment映射仍空；歷史缺失／估值／覆蓋率文法尚未逐項核准，故G6與G7不通過。

因此本輪只能稱「安全候選guard及紅綠證據已完成」，不能稱validator驗收完成、LINE bot升級完成或可部署。
