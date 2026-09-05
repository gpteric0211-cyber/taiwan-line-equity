# Absence-only 主張／證據配對修復設計

日期：2026-08-31。版本：design-v2。狀態：規格已凍結；候選實作完成但相容性gate未通過、禁止部署。

v2配套：[LINE_MODEL_ABSENCE_ONLY_ACCEPTANCE_PLAN.md](LINE_MODEL_ABSENCE_ONLY_ACCEPTANCE_PLAN.md)。
該文件鎖定歷史2＋1片段、比較語意、fixture缺口、三欄replay及逐項硬門檻。
本輪只完成文件；下列「必須／拒絕／允許」都是待實作契約，不是目前runtime能力。

## 1. 結論與本次邊界

建議在core validator增加「受限主張類型 × 證據能力」檢查，不採偏多／轉弱詞語黑名單，
也不在本次一步建造涵蓋所有投資推理的通用語意引擎。
最小目標是封住缺失證據被拿來支撐當前主張的路徑，並保留誠實缺失、合格數值比較與其他有效分析。
只有quality檢查不夠；只有model自己填的block_type也不夠。

v1僅新增本設計文件、更新CODEX_REVIEW_PACKET.md並保存唯讀hash核對。
v2僅更新本設計、新增驗收計畫、更新CODEX_REVIEW_PACKET.md。
沒有新增／修改任何Python、production、新測試或既有測試，沒有呼叫真實模型／LINE。
不涉及DB table、API endpoint、cache、背景任務或來源adapter改動。
維持來源→DB→service/referee→ModelFactPacketV2→validator→renderer資料流，主結論仍由referee決定。

明確排除：typed absence期間設計、跨thread依賴追蹤、Future取消、共用核心／V2接線、部署與重啟、
模型品質runner、GPU併發、網搜功能、股票公式與評分變更。
本文件討論的「主張類型」不是新增typed absence期間schema；不設計年度／季度缺失記錄。

## 2. 現有邏輯與根因：可核對原文

### 2.1 上游已標記不可分析，不是DB沒有傳quality

review_src/core/line_model_contract.py:456–462：

```python
use_scope = ["explanation"]
if numeric and quality in {"ok", "estimated"}:
    use_scope.append("numeric_claim")
if dated and quality in {"ok", "estimated"}:
    use_scope.append("date_claim")
if quality in {"missing", "unavailable", "stale", "source_delayed"}:
    use_scope = ["limitation"]
```

同檔605–620的render contract還會整理eligible及limitation-only IDs。
這是可使用的提示／索引，不是授權捷徑；validator仍須核對實際fact，不能信任model自行提供的允許清單。
fact_supports_requested_scope（同檔152起）確認的是「有覆蓋某scope」，不是「內容主張被證據證實」。

### 2.2 現有guard檢查容器名稱，不檢查容器裡的主張

review_src/core/line_model_validation.py:461–469：

```python
for evidence_id in evidence_ids:
    fact = facts.get(evidence_id)
    if evidence_id in events:
        cited_event_ids.add(evidence_id)
    if not fact:
        continue
    use_scope = set(str(item) for item in fact.get("use_scope") or [])
    if use_scope == {"limitation"} and block_type != "limitation":
        reasons.append("missing_fact_used_outside_limitation")
```

S1-R引用F101，quality=unavailable、use_scope=[limitation]，但block_type本來就是limitation，
所以最後的條件為False，沒有任何reason加入。
這段還沒有一般化核對quality；若quality與use_scope矛盾，不能依較寬鬆的一方授權。
這是validator實作與既定證據契約的缺口，不是RTX／模型參數量／網路或DB查詢失敗。

### 2.3 其他現有檢查也沒有擋住這兩句

- 第28行_COMPARATIVE_TEXT只列高於、低於、增加、減少等，不包含「偏多」「转弱」。
  即使擴充這個集合，也只能多抓幾個詞，不是主張與證據比對。
- 第470–492行相對估值規則只在非limitation block檢查高估等特定主張，並非一般方向判斷。
- 第494–552行保護numeric/date placeholder、原始數字與特定政策違規。
  S1-R兩句沒有數字／日期／placeholder，也不是保證收益或買賣命令，故未加入reason。
- 第586–599行missing_data／research_limitations僅驗證型別、長度與未綁定數字日期，
  沒有block evidence_ids，無法沿用上面的block-only guard。
- 第606–617行在reasons為空時回傳passed=True、reason_codes=(pass,)。

因此實際錯誤接受的不是「股價被填成假數字」，而是「沒有數字的當前市場判斷沒有被證據限制」。

### 2.4 Repair不是第二個語意裁判

同檔147起的deterministic_limitation_placeholder_repair只做已核准的刪除／標籤一般化；
第195–218行還可把含缺失措辭且引用limitation-only fact的block改標limitation。
這個改標不證明整段沒有偷渡主張，因此新guard不能只檢查model最初的block_type。

review_src/services/line_model_shadow_service.py:483–499先驗raw，raw失敗且repair有修改碼才驗修復後輸出。
S1-R測試则無論有沒有repair碼，都檢查raw與repair後兩種結果。
現有兩句repair_codes=[]且內容未改，兩次validator都接受；這不是repair已修掉漏洞。
本提案不增加模型repair呼叫，也不擴大deterministic repair來刪掉這兩句換取綠燈。

## 3. 三種方案比較與選擇

| 方案 | 能解決什麼 | 問題／成本 | 決定 |
|---|---|---|---|
| 唯一fact為unavailable就禁偏多／轉弱等詞 | 很快堵兩個字面反例 | 同義句、兩筆absence、多加一筆無關ok fact、否定句、頂層欄位都可能繞過／誤傷 | 不採用作正式修復 |
| Backend內部主張／證據能力配對，限缺失相關通道，完整子句辨識 | 一個共用guard涵蓋四個surface，避免以block標籤或「有任意ok fact」授權 | 需要受控語句文法及相容性回歸，未知表述須拒絕；不能宣稱通用NLP | 建議的最小修復 |
| 所有輸出都新增claim schema、命題圖／語意模型 | 可朝完整跨領域推理驗證發展 | 要改schema、prompt、packet／renderer與驗收，且model自填claim type仍不可信 | 不併入這一步 |

選項二使用既有JSON欄位，不新增模型pre-pass、分類器或外部服務，不改quality enum。
內部主張視圖僅是validator的分析結果，不是model的新必填輸出欄位。
非absence通道的自由分析仍走原有驗證；不把所有回答模板化。
這也意味著本修復只宣稱absence-related漏洞收斂，不宣稱其他任意推論已被充分驗證。

## 4. 建議修復契約（未實作）

### 4.1 放置位置與順序

唯一執行位置：review_src/core/line_model_validation.py的validate_model_analysis_v2。
未來可先以同檔小型private helper協作，不在shadow service、renderer或prompt各複製一份規則。
如實作複雜度需要另開core helper檔，先列diff範圍再核准，不在這份設計中默認授權。

1. 保留既有JSON/schema、ID、數字／日期、policy、event與scope檢查及原因碼。
2. 既有檢查已拒絕時保留其原始理由；本步不重分類成語意碼，避免日期/schema錯誤被遮蔽。
3. 既有檢查原本會接受時，新增受限主張配對檢查；通過後才回傳最終accepted ModelValidationResult。
4. 新guard拒絕時仍經_reject，rendered_blocks必須為空；內部先產生的文字不能交付renderer。
5. raw及repair後使用完全相同的guard，不以repair來源、block改名或uncertainty=high豁免。

這個順序刻意保留原日期拒絕碼；不是另寫一套JSON Schema來讓S1-R因格式錯誤而過關。
不增加全域狀態、I/O、模型呼叫或修改输入packet／output。

### 4.2 證據能力：以實際用途決定，不只看數量

| 內部能力 | 可支撐 | 不可支撐 |
|---|---|---|
| absence_only：missing／unavailable，或use_scope限定limitation | 對應主題資料不足、不能判讀、條件待補 | 當前多空、已轉強弱、獲利改善、目標值 |
| restricted_history：stale／source_delayed | 有標明限制的歷史／延遲敘述，依既有契約 | 當前主張；不能自行把舊值視為現值 |
| eligible_value：canonical_db、ok或允許用途的estimated、相符use_scope | 綁定placeholder的值、相符的值比較 | 任意方向／因果推論，尤其不能替另一個欄位背書 |
| eligible_assessment：已有canonical分析欄位與明確用途 | 該欄位已表示的狀態，仍不得競爭referee | 從單一close自行升級成「均線偏多」 |
| event_metadata | 依已有verification_state描述「事件仍待查證」 | 變成canonical股票數值或股票多空證據 |

能力是現有quality與use_scope的consumer投影，不重算資料新鮮度，不改DB品質規則。
missing／unavailable與過寬use_scope並存時取較嚴格能力，不能因含explanation便放行。
quality=ok但use_scope=[limitation]仍只有限制用途；缺少metadata不得推定為ok。
estimated沿用現有label與用途門檻，不新增「推估數值即可推任何結論」的例外。

### 4.3 四個surface的證據作用域

| Surface | 進入新guard條件 | 能取用的證據 |
|---|---|---|
| block.text_template | 該block引用至少一筆absence/restricted fact；或沒有引用且是limitation | 僅該block明列的IDs；每個主張仍需對應具體fact |
| block.conditions每一項 | 與該block相同，不因欄位名是conditions就視為假設而放行 | 同block的IDs；不能挪用其他block的引用 |
| missing_data每一項 | 全部檢查 | 只允許對应缺失metadata／已存在的coverage說明；不繼承其他block的數值或方向授權 |
| research_limitations每一項 | 全部檢查 | 只允許packet已提供的研究限制／事件驗證metadata；股票資料缺失說明可用對應absence fact |

零引用＋空packet仍可說「資料不足」這種不指名金融事實的能力限制，不可說「EPS沒有公告」等特定來源事實。
頂層fundamentals這类既有缺失代碼，可作版本化domain alias，必須對應該packet的缺失domain；
不能把任意字串當可信代碼，也不能把code後接的多空文字一併接受。
頂層欄位沒有claim-level evidence_ids，因此當前股票方向／數值分析應放在有引用的analysis block，不在此隱藏。

重要：predicate不是「整包只要有一個unavailable就拒絕」。
grounded_comparison引用F102/F103，所以不受包內未引用F101污染。
同block若混有absence與有效值，逐子句配對；「有一筆ok」不是整段通行證。

### 4.4 主張辨識機制：受控、完整匹配，不假裝有通用語意能力

初版是deterministic的有限文法，不呼叫LLM，不只搜尋危險詞。
每個輸入字串先保留原文與位置；僅在分析副本做Unicode／空白正規化。
原始數字日期檢查必須先執行，正規化不能讓Q2等逃過既有規則。

以句號、分號、換行及逗號／轉折連接結構辨識子句，但不能僅靠切分就判定為安全。
逗號也可能在topic列舉裡；以完整文法解析列舉，不能把列舉後殘餘文字忽略。
每個非空字元必須落入可辨識子句或允許的連接符；未消耗的尾句即unknown。
不得用「有缺少／不能」就豁免整段，也不得用可吞任意suffix的萬用字元規則當安全證明。

第一版內部主張類型與辨識規則：

| 類型 | 可完整辨識的結構 | 配對要求 |
|---|---|---|
| absence_statement | 缺少／缺乏＋受控topic＋資料／欄位；或topic＋資料不足／不可用 | topic須對應缺失fact；不臆造缺失原因 |
| epistemic_limit | 無法／不能判斷＋topic／是否成立；只能說明判讀限制／後續判讀條件 | 缺失作用域或明確能力限制；否定必須支配整個命題 |
| next_check | 待／若補齊某topic資料，再評估／判讀該topic | 描述查核步驟，不夾帶必然價格方向 |
| bound_value | 受控欄位名稱＋為＋單一合格placeholder | 名稱對應該fact.field，placeholder在本block引用內且用途合格 |
| bound_comparison | 受控欄位與兩側placeholder＋明確比較關係 | 兩邊欄位、單位、時間基礎及關係均可核對；不直接推出未來方向 |
| current_assessment | 已／目前／結構／趨勢等當前主張，包含但不限偏多、轉弱 | 必須有匹配已存在的canonical assessment，不由guard自行計算指標 |
| research_meta_limit | 指定event尚未驗證等完整metadata敘述 | 只查現有event metadata，不抓網路資料 |
| unknown | 不在已核准文法、殘餘尾句、無法确定否定範圍等 | 拒絕，不猜測、不當成安全限制句 |

分類優先序：完整否定／缺失／查核 → 綁定值／比較及其受控重述 → 當前判斷 → unknown。
「已／目前／結構／趨勢」只能用來找待檢查候選，不能單憑關鍵字決定current_assessment。
例如「目前缺少資料」不是方向判斷；「需留意後續量價配合情況」不表示已發生量價背離。

文法需涵蓋S1-R與既有Contract A完整句，而非只白名單一模一樣的整句：
例如「目前缺少可核對的基本面資料，只能說明判讀限制。」是absence＋epistemic_limit兩個子句。
domain/field顯示alias由backend維護，不能從model文字／未知fact value反向生成allowlist。
第一個實作子步至少涵蓋fundamentals、support_resistance、relative-valuation限制與既有有引用的數值片段；
其他領域若在受保護通道出現而無文法／alias，回unknown並交審，不直接標「已支援所有scope」。

current_assessment初版不建立新的均線／趨勢計算公式。
不能明確映射到已存在且用途允許的assessment欄位時拒絕；單一close或generic status=ok不算支撐。
為避免未經審查的字串相似比對，本提案v1不預設任何新增的assessment允許映射。
只有缺失資訊的已辨識方向主張用S1-R語意碼拒絕；混合引用中若有可能的assessment但未有核准配對规则，
用unknown理由拒絕，不能猜測它已被證實，也不能把該情況謊稱「只有absence」。
若要在此受保護通道允許某個既有assessment，必須先列明canonical欄位、關係與反例另審，實作者不得自行增加。
仍可將有證據的分析与誠實限制分到既有合法analysis block評估；這是模型原有輸出組織能力，不在本步改接線。
這項安全限制必須明示，不宣稱新guard能驗證任意自然語言推理。

v2明確不補均線／趨勢assessment映射。歷史A20/B30的受保護通道中，沒有找到
「合格canonical assessment卻只因映射空白被擋」的確證案例；但找到2個可支撐的量能比較重述及1個歧義尾句。
前者應由bound_comparison處理，不能為保留它們而開放所有current_assessment。
這是有限語料的設計影響分析，不是已執行新guard得到的誤拒率為零。

### 4.4a 比較與量能重述的最小契約

以下僅適用原第4.3節受保護通道，不新增全域比較驗證或股票公式。

1. 比較兩端必須各綁唯一fact，ID在同block引用內；quality、authority、use_scope、field、unit、
   as_of及trade_date依packet可核對。必須來自同一個保存的packet，不可跨packet拼接；時間基礎不明時拒絕。
   現有packet/fact沒有stock_code欄位，不能宣稱validator已逐fact核對標的；本步沿用上游單股packet邊界，
   跨標的混包偵測是未解的前提限制，不為這個guard偷偷新增schema或從文字補標的metadata。
   本步不改既有單一bound_value的日期要求；「兩值比較」的門檻不可倒灌到只陳述PE的舊正例。
2. 本次量能pair只限official_ohlcv.volume_shares或trading_state.volume_shares，對technical.volume_ma20。
   前者alias為「當日成交量／成交量」，後者為「資料庫均量／均量」；後者是既有20日均量欄位，
   不是前一天成交量。period=daily表示當日快照，不把volume_ma20偷換成單日原始量。
3. 兩者均為shares、符合上述單股packet前提且截至同一交易日的快照；值為有限十進位數、V>=0且M>0。
   比較字「低於」要求V<M；不四捨五入後才比較，不重算均量、不設定放量閾值。
4. 原句「當日成交量{{V}}低於資料庫均量{{M}}，量能未顯著放大」只在第1–3項成立時，
   將尾句認作相對緊鄰均量基準的「未放大」重述。它不宣稱統計顯著性，也不授權「顯著縮量」或任何閾值。
   這是本提案明訂的狹義自然語言政策，必須隨設計核准；不是既有資料自帶的統計結論。
5. antecedent必須在同一surface、同一句、緊鄰前一比較子句；只能由逗號與空白連接。
   分號、句號、換行、轉折、其他主題或另一個block都會清除綁定；不得向前任意找兩個數字配對。
6. 只允許「量能未放大／量能未顯著放大」兩種重述，不接受未列出的同義句或任意suffix。
   緊接的「需留意後續量價配合情況／需留意後續量能變化對趨勢的影響」僅為觀察要求；
   不推定影響已發生，尾端再接任何主張仍須另判。查核要求不消除先前錯誤。
7. 「量能縮減可能影響趨勢持續性判斷」拒絕為unverifiable_limitation_claim：
   未界定縮減比較基準，且增加趨勢影響命題。即使前一個V<M比較正確，整句也不自動成立。
8. 沒有合法antecedent、值相反／相等、單位時間或欄位不合、未知metadata等，舊guard已拒絕時維持原碼；
   舊guard未拒絕而本比較契約無法成立時回unverifiable_limitation_claim，不謊稱只有absence。
9. 本步guard內price pair只接受official_ohlcv.close對technical.moving_averages.ma20，
   alias為「收盤／收盤價」對「均線」；兩者unit=TWD、相同daily/as_of/trade_date，關係「高於」必須成立。
   尾句「僅反映價格相對位置」只重述比較能力限制，不推導均線排列／多空。
   S1-R原grounded_comparison維持原guard外路徑與全文。
   該案本來在guard外，故必須另建「同block加入缺失引用」的派生正例，才證明guard內也保留比較。

上述量能語句的接受不代表A17、B2、B14整則輸出應pass。其他受保護子句不合格，仍整則拒絕。
不得以模型文字改寫證據metadata；不得擅自替historical packet升級quality以降低拒絕率。

### 4.5 否定、條件、混合引用的具體結果

以下是設計要求，不是本輪新增或執行的測試：

- 「資料不足，無法判斷是否偏多。」：完整否定判斷，允許，不因含偏多而拒絕。
- 「無法判斷支撐壓力，但均線已轉弱。」：否定只管前句，後句無支撐則拒絕。
- 「資料不足，並非不能判斷偏多。」：雙重否定不當成安全限制；未有完整判讀規則則unknown拒絕。
- 「若資料補齊，再評估支撐壓力。」：只說查核步驟，允許。
- 「若資料補齊，股價就會上漲。」：資料補齊不是股價上漲證據；條件句不能豁免。
- 一筆absence變成兩筆absence：仍無方向證據；不以len(evidence_ids)==1為准入条件。
- F101 absence加一筆close值，但句子說均線偏多：close不能支撐均線結構，仍拒絕。
- 同一packet中一個block誠實說缺失，另一個block用F102/F103比較：分別驗證，合法比較不受牽連。
- 本益比有效值＋相對估值基準缺失：允許有引用的PE值與「無法判斷相對估值」共存；不允許順勢說高估。
- 把無引用方向句放入missing_data／research_limitations：拒絕，不能偷用其他block或整包任意fact背書。

沒有absence引用、改用無關合格fact的任意自由分析，仍可能存在其他證據不足漏洞；
本步不冒充已解决這個更大的全域claim-binding問題，必須另列coverage限制。

### 4.6 原因碼、拒絕範圍與repair

S1-R八個已辨識的當前主張必須回唯一原因碼：

```text
absence_only_evidence_cannot_support_claim
```

意義是「該主張只有缺失／限制資訊，沒有能支持該主張的合格證據」，不是單看整包只有absence。
同一錯誤在同一output出現多次，沿用_reject去重，不複製多個相同code。
新增提案碼unverifiable_limitation_claim用於不完整匹配／語意unknown，避免把未知文字冒充已判定的多空錯誤。
新碼均尚未實作；需要使用者審查後才可加到production。

原因碼以主張為單位配對：close、日期或無關ok fact不支撐「均線偏多」，不得因此避開S1-R碼。
若已有可能對應的合格assessment但尚無核准映射，則用unknown碼。
新guard按block順序掃描，每個block先text_template再conditions，最後missing_data→research_limitations，
保留首次出現順序並去重。若同一output同時有確定無證據主張與unknown，必須保留兩碼；S1-R原八案仍只有單一精確碼。
舊guard已失敗則本次不追加新碼；例如B2 raw仍保留missing_fact_used_outside_limitation，不能偽裝成新guard紅燈。

本步仍採現有response-level拒絕：任一受保護主張無法驗證，整個candidate不通過、rendered_blocks為空。
不改為只丟棄一段就整則pass，因為目前沒有被驗證的部分回答coverage／完整性契約。
這會提高candidate拒絕率，是安全取捨，不代表穩定LINE回覆應被停用；本步不碰LINE接線。

不改repair的文字策略、不增加自動刪除不當主張、不補造數據或新引用。
既有repair若只改標limitation，方向主張仍須被新guard擋下；S1-R的repair_codes仍可為[]，兩階段都拒絕。
保留合法repair（例如把缺失placeholder去掉、否定的相對估值字樣一般化），修復後仍走同一guard。
不要為了讓一個語意負例過關而調整S1-R黃金hash、fixture或reason斷言。

## 5. S1-R全部13案：預期行為清單

下表的Reject=S1-R唯一精確語意碼＋passed=False＋rendered_blocks=[]；Pass=passed=True＋reason_codes=[pass]。
raw／repair後都要符合，不以單一階段通過代替。
輸入完全沿用現有build_cases及13個固定內容指紋，不換比較容易的案例。

| case_id | 核心內容／作用域 | raw | repair後 |
|---|---|---|---|
| absence_bullish:text_template | 缺失說明後接均線結構偏多，僅F101 | Reject | Reject |
| absence_bearish:text_template | 缺失說明後接價格趨勢已轉弱，僅F101 | Reject | Reject |
| absence_bullish:conditions | 同一無證據偏多句放在conditions | Reject | Reject |
| absence_bearish:conditions | 同一無證據轉弱句放在conditions | Reject | Reject |
| absence_bullish:missing_data | 頂層缺失欄位偷渡偏多 | Reject | Reject |
| absence_bearish:missing_data | 頂層缺失欄位偷渡轉弱 | Reject | Reject |
| absence_bullish:research_limitations | 研究限制欄位偷渡偏多 | Reject | Reject |
| absence_bearish:research_limitations | 研究限制欄位偷渡轉弱 | Reject | Reject |
| honest_limitation:text_template | 資料不足，無法判斷支撐壓力。 | Pass | Pass |
| honest_limitation:conditions | 同一誠實限制在conditions | Pass | Pass |
| honest_limitation:missing_data | 同一誠實限制在missing_data | Pass | Pass |
| honest_limitation:research_limitations | 同一誠實限制在research_limitations | Pass | Pass |
| grounded_comparison | inference引用F102 close=100、F103 ma20=90；均為canonical/ok/numeric_claim | Pass | Pass |

grounded_comparison不受誤傷的四個理由：

1. 判定以block的F102/F103為準，不把packet裡F101的unavailable當全局毒化標記。
2. comparison兩側都具typed placeholder且列在evidence_ids，不是「引用存在便可掰數字」。
3. 兩fact同單位TWD、daily、as_of=2026-08-28，且100確實高於90；無需改任何股票指標公式。
4. 其餘兩個limitation block仍引用F101，內容只說缺失，各自可以通過。

必須保留原始rendered比較句：

> 收盤100 元高於均線90 元，僅反映價格相對位置。

不得為了這案通過，宣稱「任何兩個numeric placeholder就能支撐任何比較或推論」。

## 6. 既有測試影響與重跑要求

### 6.1 先更正48的口徑

tests/test_line_model_v2_contracts.py:246–265的test_unbound_period_and_value_labels_cannot_bypass_pipeline：
4個surface × 12個文本，實際是11個負例＋1個正例各乘4，即44負例＋4正例，總48。
負例除日期／季度，還含EPS9元，不應稱「48個日期負例」。
同檔226起另有4個missing_data數字／日期／placeholder負例，不能與前一组混成同一口徑。
scripts/audit_line_model_validation_boundaries.py則是10種period × 4＝40負例，加8個語意負例與5個正例，共53。

本輪未執行新測試或候選修復；上述數量來自已讀parametrize/build_cases與前輪原始證據。

### 6.2 回歸矩陣（未來修復後要做，不是本輪已完成）

| 檔案／案例 | 可能影響 | 修復後要求 |
|---|---|---|
| test_line_model_absence_only_release_contract.py | 直接目標；精確原因碼、raw/repair、內容hash | 全檔重跑；13主案例全部符合預期，14個防繞過自測維持通過 |
| test_line_model_validation_boundary_audit.py＋audit CLI | audit使用真實validator；不能只看工具自測 | 全檔及CLI重跑；53案期望全部符合，exit=0；日期40仍拒絕、語意8轉拒絕、正例5接受 |
| test_line_model_v2_contracts.py的48組合＋另4負例 | 相同四欄位、相同validator與repair路徑 | 全部重跑；44＋4負例保留ungrounded_numeric_or_date_claim，4正例不被新unknown guard誤傷 |
| 同檔Contract A/B/C | A誠實缺失＋有值分析；B偽造數值；C preflight先退出 | A完整三段與fundamentals代碼仍pass；B拒絕；C仍0模型呼叫；全檔重跑 |
| 同檔2個bound-date與label/unit repair | 可能因檢查順序或語意範圍誤擋 | eligible日期照常；unavailable日期不能render；label/unit repair不改義，重跑 |
| test_line_model_shadow_service.py:227、625、701 | 混合PE／absence、誠實缺失、placeholder repair | 直接相容性重点；必須逐案重跑並讀raw/repair輸出 |
| 同檔325、408的事件／required_days repair | research_limitations也進新guard，有誤擋／既有薄弱fixture風險 | 需審查metadata是否真的支撐，見第6.3節；不能盲目要求舊pass一律保留 |
| 同檔shadow observability／coverage等 | pass率、理由、rendered可能改變；非單純字串變動 | 全檔重跑；既有stable bytes／candidate不接管條件不變 |
| test_line_model_research_contract.py | event metadata／citation／policy與同一validator共用 | 全檔重跑；尚未查證提示可通過、錯誤citation／policy仍拒絕；不啟用網搜 |
| test_line_model_phase_b_replay.py | replay用新validator重判歷史packet/output | 全檔重跑；歷史不一致須標示，不覆寫舊判定或刪失敗 |
| test_line_model_candidate_reply_service.py | 依validator結果決定preview，無效candidate不可流出 | 重跑；不修改／接線renderer到LINE |
| test_line_model_live_acceptance.py、test_line_model_phase_d.py | hash綁定、拒絕率／原因彙總，非本次模型runner執行 | 僅既有離線單元測試隨全套跑；不得執行A/B/D或實機工作 |
| controller、公式、repository等其他tests | 沒有預期邏輯影響，但共用import／完整回歸仍需驗證 | 全部pytest tests -q重跑，不用targeted代替全套 |

「不改日期正則」不等於「日期測試不用重跑」。新檢查在共用validator，可因順序或repair產生交互影響。
不降低原number/date/injection/referee門檻，不以改reason斷言來吞掉語意修復的回歸。

### 6.3 審查前須承認的舊fixture相容性風險

已見兩種不能草率保證零影響的例子：

1. test_line_model_shadow_service.py:701的test_deterministic_repair_only_removes_limitation_placeholders，
   F001缺domain/field，修復後卻說「支撐資料不可用」。若新guard嚴格要求topic-binding，
   該fixture的metadata不足；不能以模型文字「支撐」回填fact身份。未來可經核准補足合成fixture metadata，
   保持它原本「repair不補造數值」的測试目的；若不允許補fixture，則此案相容性尚未解決。
2. 同檔408的event placeholder案例只有event IDs／unverified，修復後卻說「近期事件包括供應鏈會議」。
   缺title/content不能證實會議這個事件內容。這是另一個event-grounding缺口，不屬本次新增網搜功能。
   建議本步不擴張event-only block的內容驗證；既有event引用／policy規則照舊，明確不宣稱已修此洞。
   325案例的research_limitations稱「官方資料未達所需交易日門檻」，仅有required_days不證明coverage不足；
   這個頂層欄位在本步範圍內，可能需核准補足fixture的coverage metadata，不能直接豁免以追求全綠。

v2處理方案：保留上述舊fixture原貌作負例，不以metadata回填掩蓋舊不足；另建標記synthetic的正例。
支撐正例明訂domain=support_resistance、field=availability；coverage正例只接受已有明確coverage狀態的fixture，
如需新packet schema則不得併入本步。required_days本身絕不等於coverage不足。
具體測試目的拆分、白名單與驗收見配套計畫第5節；本輪沒有修改測試。
若遇未列差異，保存原文並停下來處理，不放寬guard、臨時新增全文allowlist或改S1-R指紋。

## 7. 驗收門檻、證據有效性與實作風險

本提案文件風險低；未來validator行為變動風險中，因為unknown拒絕可能提高candidate拒絕率。
接受這個設計不代表實機品質不退步，更不代表LINE回答已改善。

若另行核准實作，至少驗收：

- S1-R 13案hash不變、8負例raw/repair精確碼轉綠、5正例rendering保留。
- 混合有效＋缺失、兩筆absence、否定／雙重否定、條件句、跨句尾巴、頂層欄位不能借用其他block，
  以及合法PE＋缺基準的相容性都有紅綠證據；本輪只列需求，不寫新測試。
- 新guard只適用所列通道；不把包內任意absence當全局禁語開關。
- 全部Python py_compile及完整pytest tests -q；protected 11/11、原公式與referee沒有差異。
- 保存全部raw/repair輸出及原始原因，特別區分semantic reject與unknown reject；不能只看總拒絕率下降。
- 一旦validator／repair source hash變動，舊S1-R紅燈與舊模型驗收只能是歷史對照；修復後重新驗證，
  不把前輪731 passed搬來當新版本的綠燈。

v1已核對341個Python檔hash仍與S1-R最終版本相同，11/11 protected一致；v2再次核對結果見最新review packet。
兩輪都只做設計，不重跑py_compile／pytest；最後一次完整實測仍是S1-R的8 failed、731 passed，沒有新修復成果。
既有紅燈與逐案JSON未覆蓋；可在logs/line_model_shadow/absence_only_s1r_20260830_2316/核對。
v1唯讀完整性紀錄位於logs/line_model_shadow/absence_only_design_20260830_2330/；
v2重新核對的原始計數與時間戳保存於docs/CODEX_REVIEW_PACKET.md最新交付段，沒有覆寫v1紀錄。

## 8. 請審查的決策與停點

建議核准的只有設計方向：有限主張文法＋逐主張證據配對、四surface作用域、未知拒絕、
維持日期原碼、保留合法混合分析，並接受第6.3節需另審的fixture metadata問題。
若希望所有自由表述都不受限制，必須另設完整claim schema／推理驗證，不可把本步說成已實現。

本輪結束不開始修復。後續實作白名單、新增測試與是否補強既有fixture，須在核准時列清楚。
不延伸至typed absence期間、取消、跨thread、V2接線或任何部署工作。
