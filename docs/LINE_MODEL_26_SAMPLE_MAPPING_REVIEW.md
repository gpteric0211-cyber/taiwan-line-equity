# 26 筆歷史回答：語句／證據映射審查

日期：2026-08-31，Asia/Taipei。性質：離線原始語料審查，不是 validator 修復或模型重測。
使用者要求：先審查這 26 筆，再另行核准 C21 兩個舊測試契約；詳細檢查後開始專案清理。
本輪不改 production、測試、fixture、golden hash、DB、env、模型、排程或部署。

## 1. 結論與重要更正

不能把 26 筆全部視為誤拒，也不能把它們全部視為模型胡說。
本輪閱讀 26 筆完整診斷 repair 輸出中的 **125 個 block、139 個頂層 missing/research 項目及各 block conditions**，
逐筆對照原 packet、引用 fact/event、coverage 與原／新拒絕理由。已保存完整原始 output，不刪失敗或裁掉不利尾句。

下列數量是**互相重疊的問題／能力集合**，不是相加等於 26 的通過率：

| 可核對集合 | 筆數 | 意義 |
|---|---:|---|
| 有成本樣本天數／需求天數雙邊 typed facts | 18 | 均為同日 `18 < 60`，只支持外資近期增量成本估算的樣本限制 |
| 有新聞／事件 verification metadata | 18 | 各 6 筆事件均 unverified；支持「本包所附事件尚未查證」，不證明新聞內容或機構數據真假 |
| 有明確相對估值不可用狀態 `relative_value_assessment` | 8 | 可以設計有限狀態映射；不能因 `quality=ok` 就把狀態值當成相對估值已成立 |
| 有外資成本 `quality_state=unavailable`、`estimated_cost=null` | 6 | 支持成本數字不可用；`is_estimated=true` 不表示已有可顯示估值 |
| 引用 missing/limitation 的方向主張 | 至少 5 | A02、A17、B05、B14、B20；其中後兩案現行只回 U，不代表沒方向風險 |
| 完全沒有 technical domain 卻說目前均線偏多 | 1 | A18 block2：全球／夜盤 evidence 不能支持個股均線判斷 |
| 明確把外資成本限制延伸到其他指標／對象 | 至少 3 | A04 block3 混同淨買賣超；B22 block4、B28 block5 延伸到投信 |
| 有合格且已引用的 technical.trend / moving_average_trend / technical_status | 0 | 本 26 筆無可直接填入 v1 current_assessment 映射的這三類 typed fact |

**更正前輪過於籠統的說法：**

1. A16 的 coverage 真有部分省略 metadata，不能說沒有證據；但 `omitted_sections=[]`，不代表整章刪除。
   新聞省略原因是 `profile_event_count_limit`，不能一概說所有省略都來自 token 預算。
2. A11 不只存在 missing 的 F045 文字：整個 packet 另有 **F034** 明確相對估值不可用狀態，
   但 block2 **沒有引用 F034**。必須區分「整包存在」、「這句已引用」、「該作用域獲准」三件事。
3. B12 的「部分新聞未經驗證」不必然是假話：全包未驗證可以蘊含至少一部分未驗證。
   問題是作用域不明及容易讓人誤以為其餘已驗證；應限定本包事件集合，不能判定為與資料矛盾。
4. `unknown` 只代表現規則未建立對應，不能自動歸類成幻覺；同樣不能因存在任意 ok fact 就放行整句。

本輪**沒有給任何一筆「整則可直接發布」的簽核**。這是保留未驗證語意，不是宣稱 26 筆全錯。

## 2. 原始證據、來源綁定及重播界線

本輪完整逐筆證據：`logs/absence_mapping_review_20260831/samples/A02.json` 至 manifest 列出的 26 檔。
不是連續 26 個編號，完整 ID 清單見下表。每檔包含：

- 原 corpus 路徑、SHA-256、原 JSONL 行號（等於 attempt_index）。
- `original_record`：未修改原 packet、原模型 output 文字與歷史結果。
- `current_replay.raw / after_repair / service_final`：原樣保留全部拒絕理由、rendered、repair 是否採用。
- `block_evidence_mapping`：每個 block 全文、conditions、原 evidence_ids，以及對應完整 fact/event metadata。

`manifest.json` 綁定 26 檔內容 hash、兩份 corpus hash 與 validator hash。
匯出工具 `logs/absence_mapping_review_20260831/export_evidence.py` 完整碼可審查：只讀 frozen corpus，
與原 record 作物件相等及原始整檔 SHA 核對，只以 exclusive create 寫新證據，不碰 DB／網路／模型。
current replay 來自上一輪既有離線實測，匯出前重新核對 validator SHA 相等：
`428cc3224405ea52f989578e52fa51a9fb6dad0453968dd930432e7c43e2568c`。

這不是新 26 次模型呼叫；不是新的 A20/B30、不是目前 DB 現況，也不是暖冷／併發／LINE 延遲證據。
未重建或補寫 historical packet；因此可以查明原輸入支不支持原回答，不能反推整個 DB 當時沒有資料。

原始結果：19 筆 raw 曾通過，另 7 筆靠 deterministic repair 才曾通過。
七筆為 A04、A11、B02、B04、B10、B13、B28。現在 repair 全拒絕，service-final 保留 raw 理由。
以下 U=`unverifiable_limitation_claim`，A=`absence_only_evidence_cannot_support_claim`，
N=`ungrounded_numeric_or_date_claim`，M=`missing_fact_used_outside_limitation`，T=`too_many_explanation_blocks`。
縮寫僅供此報告閱讀；完整 reason_code 字串保留在各 JSON，沒有改應用程式碼。

## 3. 現行 guard 的確切邊界

`review_src/core/line_model_validation.py:400`：

```python
def _restricted_limitation_fact(fact: dict[str, Any]) -> bool:
    # Consumer projection only: never recompute DB quality or trust a value's prose.
    scopes = {str(item) for item in fact.get("use_scope") or []}
    return fact.get("quality") not in {"ok", "estimated"} or not scopes or scopes == {"limitation"}
```

同檔 `:558` 附近 `_absence_related_reasons`：

```python
protected = any(_restricted_limitation_fact(f) for f in facts) or (not ids and block["block_type"] == "limitation")
if not protected:
    continue
```

頂層兩個字串陣列則一律走 `metadata_only=True`，但函式只取得 facts/events，**沒有取得 coverage**。
所以此版不可能僅靠擴充 token 省略同義詞就安全完成 coverage 映射，需另案定義 backend-owned 能力輸入。

同檔 `:406` 起主題 alias 只涵蓋 support_resistance、fundamentals、以及 restricted 的
valuation.relative_value_assessment；F034 為 ok/explanation，因此這個 alias 分支根本不會採用它。
同檔 `:483` 起採 fullmatch 有限文法；沒有 current_assessment 的允許表。
同檔 `:776` 原始數字/schema/scope 等錯誤先返回，只有原規則全部通過才執行 absence guard。

**本輪新發現的限制：** eligible-only block 不受此 guard 全文審查；例如 A18 的錯誤跨領域引用，
不能期待「把 limitation 文法修好」就一併修復。必須列為另一路句子—證據語意契約，不能隱藏。

`review_src/core/line_model_contract.py:268` `_projection_quality` 讀 section 層的 quality/status，
`:441` 再把這個品質套到 scalar leaves；這解釋為什麼有
`quality=ok` 的 `quality_state="unavailable"`／`estimated_cost=null`。
狀態 metadata 本身可以是可信的，**不等於它描述的成本數值可用**。
單靠 field 名含 cost 也會產生布林值標 TWD 的情況（如 is_total_holding_cost），本輪只記錄，不改投影器。

## 4. 共通映射候選：必須限定欄位、作用域與意義

### 4.1 成本樣本限制（18 筆）

12 筆技術／基本面複合包：A04、A10、A13、A16、A19、B01、B04、B10、B13、B22、B25、B28。
各包 F056=`institutional_context.canonical_costs.foreign_estimated.required_days=60`，
F057=`...sample_days=18`；canonical_db、ok、explanation/numeric_claim、count、daily、
as_of=trade_date=2026-08-28。F054 reason=`official_history_below_60_sessions`。

6 筆外部市場複合包：A09、A18、B09、B12、B27、B30。對應為 F027/F028；另有
F008 calculation_state=insufficient_history、F014 estimated_cost=null、F018 is_estimated=true、
F019 is_total_holding_cost=false、F021 metric_id=foreign_incremental_position_avg_price_estimate、
F024 quality_state=unavailable。F018 不是可顯示成本的證明。

允許候選：同實體、同 metric prefix、同時間與 count 單位，sample < required 才能說樣本不足。
限制句要說「外資近期增量成本估算」；不可變成外資全部持股／淨買賣超／投信資料皆不足。
頂層可由 backend 在同包解析唯一完整配對，不靠模型給的 label 指派對象；多對、日期不同、只剩 required、
sample>=required、錯對象皆不得通過。數字仍用 typed binding，不能改放行原始數字。

### 4.2 相對估值與缺失（8 筆有 typed 狀態、18 筆沒有）

8 筆技術風險包：A02、A11、A17、B02、B05、B11、B14、B20。
F034=`valuation.relative_value_assessment="unavailable_without_peer_or_historical_baseline"`，
canonical_db、ok、explanation、daily、as_of=trade_date=2026-08-28。
它可支持「本包缺少可用的相對估值比較基準」，不能推論具體是哪個同業／哪段歷史不存在。
block 必須實際引用 F034；A11 block2/B02 block3 並未引用，不能自動替它們補引用後假稱原輸出通過。
頂層未具 claim ID，須另定唯一狀態映射；不能借同包任何數值就補足證據。

12 筆技術基本面包雖有 PE/PB/yield，但 F034 沒有保留；valuation 是 partially_omitted/token_budget。
這只能支持「本包未附比較基準」，不證明 DB 缺資料。
6 筆外部市场包完全不包含 valuation domain，request 也未要求估值，coverage 更未列 valuation 缺失。
不能說同業／歷史估值 unavailable。原語料須保留；未來產生新回答時應不擴張到未提供的估值領域。

### 4.3 事件與省略

18 筆有事件的包，E001–E006 均 unverified（原標題完整保留，但不是本輪查證的新聞事實）。
block 的「所引事件」使用實際 cited event IDs；頂層明訂「本次提供事件」才可使用 packet event 集合。
不能把未驗證事件轉成財報、機構持倉或已發生的因果；也不能由標題討論直接推定市場關注度已上升。

coverage 省略須區分 whole-section/partial-section 與 token_budget/profile_event_count_limit。
只說「部分未附」不等於 DB 不存在、來源不可靠或整章未提供；檢索根本没要求的 scope 更不能列成 DB 缺失。
不得把判斷藏回 prompt 文案，應從 backend coverage/事件狀態產生受限能力再驗證文字。

### 4.4 數值比較、方向與 null

F006（或 F043）15025832 shares < F030 22143102 shares：同 daily/date、canonical/ok/numeric_claim；
A17 block3、B02 block4、B14 block4 的雙邊量值關係可核對。
不能因此把「量能縮減」說成比昨天減少，也不能把「趨勢一定／已轉弱」附帶放行；
B14 的風險尾句不是數學比較本身，須獨立審查其條件與不確定性。

F046/F048/F054 等 evidence_summary 的文字雖看似方向性結論，但 quality=missing/use_scope=limitation；
不可因為 backend 生成過文字就自行升級。可另案給模型合格 typed referee/technical 證據，不能改舊 packet。
這 26 筆目前沒有合格已引用的 trend/moving_average_trend/technical_status，可加 mapping 的實例數為 0。

null 欄位可以說「本包未提供該數值」，但不能推定「来源不支援」或市場流動性變差；
reported_transaction_count 不等於法人買賣超，foreign_incremental_cost 不等於外資總持倉。
缺失原因要有明確 availability_reason，不是模型從 null 自行猜原因。

## 5. 26 筆逐案判讀（全文與 facts 見同名 JSON）

表中 block 從 1 起算；md=missing_data，rl=research_limitations。
「可映射」只指特定語句／metadata，不代表整則可通過。所有既有錯誤原樣保留。

| ID：raw→repair | 原文與位置 | 已引用／包內證據、審查決定 |
|---|---|---|
| A02：U+A→U+A | b4「均線結構偏多但走勢維持穩定」；md「歷史估值分位數」 | b4 F046/F048 missing/limitation，不得支持方向；F081只證支撐不可用，F044只是一段 restricted 品質說明。b3已引F034可設計有限估值限制；b1價格/MA數值關係另有證據，但不能跨block借給b4。保留拒絕。 |
| A04：N→U | b3「無法精確計算外資淨買賣超」；md「外資淨買賣超精確數值」 | F056/F057與F054只描述成本估算樣本，不證淨買賣超缺失。b1未引F034且包內也無；事件E001–3可標未驗證。數值修復不是語意修復，原N保留，不能只補同義詞轉綠。 |
| A09：U→U | b2「目前無法顯示具體成本數字」；b4「部分交易狀態資料如成交金額與筆數缺失」 | b2 F027/F028、F008/F014支持成本受限。b4 F041/F042 null支持本包金額/筆數缺失；不證來源失效。估值基準全包無證；b3以全球/夜盤偏多推可能回測當日低點，是情境而非已驗證支撐，勿升格。 |
| A10：U→U | b3「外資估算樣本天數…低於所需天數」；rl「外資估算樣本不足」 | F056/F057支持成本樣本限制；「無法精確評估外資近期進出動向」比成本限制廣，不能由此認定全部外資交易不可用。事件未驗證可映射；相對估值缺失需改成限本包，不能推DB缺失。 |
| A11：M→U | b2「估值方面，本益比為…」＋比較基準缺失；b3「支撐區間需由多日官方數據產生」 | b2 F031–33數值合格，F045 restricted；整包F034可用但未引用，不能冒充已綁定。b3 F081支持不可用，但F044文字不能證特定資料不足原因；b5「來源不支援」從null推不出。原M保留。 |
| A13：U→U | rl「部分技術指標省略」；b5「因token預算限制而部分省略」 | coverage.technical與institutional_context確為partial/token_budget，存在合理映射；F056/F057支持成本樣本不足，不代表外資部位估算值已存在。事件未驗證可映射，估值比較基準未附不能說DB沒有。 |
| A16：U→U | rl「部分章節因token預算限制而省略」「外資持股為估算值」 | coverage支持部分內容省略，whole omitted=[]；news_radar原因是event_count而非token。F056/F057只支援成本樣本不足，不支援外資持股變化值已可用；全部事件unverified可映射。保留這三種不同結論，不整案標誤拒。 |
| A17：U+A→U+A | b3量比較；b5「均線結構偏多但走勢穩定」 | b3 F006/F030比較正確，F081支持不可用；前綴F044仍需typed原因。b5只有F046/F048 restricted，不可支持方向；F041筆數缺失不能推法人買賣力道無法評估。不能因比較正確放行b5。 |
| A18：U→U | b2「目前均線結構偏多，走勢維持穩定」 | b2 F031–36均為global/night，無technical fact；此句不受restricted guard覆蓋，仍是獨立語意漏洞。成本null/unavailable支持b1/b4的成本限制；估值全包缺證；新聞標題不能證市場關注度「提升」。 |
| A19：U→U | rl「外資籌碼估算樣本不足」「新聞事件未驗證」 | F056/F057支持限定成本metric的樣本不足，E001–6支持所附事件未驗證；b3/b4有雙邊引用。rl「缺少同業估值基準」包內無typed狀態。b5是有條件量價情境，不能当已發生的趨勢延續。 |
| B01：U→U | b5「部分章節因token預算被省略」；md「完整機構持股明細」 | coverage只partial，新聞因count上限，不是整章token丟棄；F054/F056/F057限成本樣本，不能證所有機構持股。b2 ATR單值不能單獨建立「可控範圍」的風險基準。事件限制可映射，不整案放行。 |
| B02：M→U | b4量比較；b5「若價格跌破中期均線…」 | b4 F006/F030比較及F081缺失可核對，F044原因仍restricted。b5 F023/F025有值但F045缺失混合，scenario被repair改成limitation不使條件推論自動合格；b3 PE/PB/yield沒引用F034。保留原M，不以新增語法掩蓋block型別問題。 |
| B04：N→U | md「已驗證的財務報表細節」；b4「如與所示」 | 包內沒有財報absence metadata；不以新聞未驗證推財報不存在。F056/F057只支持成本樣本；repair去掉ID/數字留下不流暢「如與所示」，即使驗證放行也不是品質合格。原N與原文均保留。 |
| B05：U+A→U+A | b4「均線結構偏多…技術證據偏多」 | b4 F046/F048 missing，F081只有absence；不能借b1/b2的合格技術指標替此段背書。b3已引用F034，估值限制可設計；b5條件情境不表示未來漲跌已成立。 |
| B09：U→U | md「完整交易金額與筆數」；b2成本不可用 | F027/F028、F008/F013支持成本不足；整包F041金額/F042筆數null，但b5只引F040/F041，未引F042，claim-level要補設計。沒有估值metadata；不能把完整交易狀態均判缺失。 |
| B10：N→U | rl「外資估算樣本不足」；b3「僅能參考現有估算值」 | F056/F057雙邊可核對，F044/F045是起訖日期不是估算值。不能由日期與样本不足聲稱存在可參考成本／持股值。新聞與範圍限制有mapping機會，原N不消失。 |
| B11：U→U | md「detailed_trading_state_metrics」；b4「報告交易筆數缺失」 | 任意英文code未綁backend registry，不應泛化放行。全包F041=null，但b4引用F044–48/F081，沒有F041；其缺失與影響市場參與度須分句審查。b3F034支援有限估值限制。 |
| B12：U→U | rl「部分新聞未經驗證」；md「同業估值基準」 | 事件全unverified不矛盾，但需指明本包事件、不暗示其餘已驗證。成本F014=null/F024=unavailable支持本包成本缺失；估值全包無證。b3夜盤/美股可作背景，不能保證個股開盤稳定；b2「關注度提升」亦無時間序列。 |
| B13：T→U | rl「外資持股數據為估算值」；b5多項限制 | F054/F056/F057是成本樣本，不是已產生的持股數據；原6blocks靠repair整併為5，不是模型一開始合規。原T保留；事件未驗證可限定mapping，其他缺失仍需唯一主題metadata。 |
| B14：U→U | b4「量能縮減可能影響趨勢持續性判斷」；b5「技術面雖偏多但量能不足」 | b4 F043/F030只證低於均量，不證比昨天縮減；尾句是風險假設，不可當數學關係直接放行。b5 F046/F048 missing，沒有合格方向／量比較引用；雖未命中A，仍須保留拒絕。 |
| B20：U→U | b3「技術面證據顯示可用技術證據偏多」 | F054 evidence_summary.technical_observation missing/limitation，屬方向支撐不足；不能因較長前綴只回U就當純同義詞問題。F081支持支撐不可用；b2有F034估值狀態可映射。 |
| B22：U→U | b4「無法精確判斷外資與投信的籌碼變化」 | F054/F056/F057全是foreign_estimated，不能支持投信缺失；md財報缺失也無typed理由。b2 ATR單值不能證「波動性適中」；事件未驗證與成本樣本可分開映射，不整句放行。 |
| B25：U→U | rl「外資成本估算樣本不足」；b5「無法提供精確的外資持股變動分析」 | F056/F057支持前者，不必然支持全部持股變動均不可分析；量值F006/F030關係成立，OBV只有單值不能推出資金已流入／流出。事件mapping可限定，估值DB缺失不成立。 |
| B27：U→U | b4/rl「部分機構資料與新聞事件未經驗證」 | b4只引E002/E004/E005/E006，不能以事件unverified支援機構資料未驗證；成本不足與未驗證是不同quality語意。b1「機構持股動態」也不由其E001/E003薪資分紅標題支援。不能整句套事件同義詞。 |
| B28：N→U | b5「無法完整評估外資與投信部位變化」；rl樣本不足 | b5引F014/F015/F016是technical輸入日期/筆數，沒有成本sample配對；整包也只foreign_estimated，沒有投信。b3成本配對可核對，不能挪作b5證據。原N與角色混用需保留。 |
| B30：U→U | b4「部分外部事件未顯示」；md「歷史估值序列」 | coverage只標news_radar partial/count，沒有external_event_context省略證明；不能泛稱外部事件缺失。成本F027/F028配對可核對，新聞unverified可限定mapping；估值domain不存在，不能宣稱DB無歷史估值。 |

以上不是投資建議，也不是新股票分析；金融語句均為被審查的歷史模型原文。

## 6. 下一步設計與驗收建議（尚未實作／未核准）

優先順序不再是「把兩個紅燈改掉就上線」：

1. **先限定 backend metadata 能力**：成本 metric 雙邊樣本比較、事件集合驗證狀態、coverage 真實省略、
   relative_value_assessment 狀態。保留完整作用域／as_of，不新增競爭性股票結論。
2. **分開修不合格生成內容**：成本≠持股≠淨買賣超，外資≠投信，新聞≠機構資料，
   packet未附≠DB缺失；typed null狀態≠已有估算值。不要從 missing 自然語言生成合格 facts。
3. **保留舊語料作反例，新回答另存**：不在審查時刪句或補ID提高26筆通過率。
   定義「應保留拒絕」與「語句正確但 mapping 缺失」的成對案例，審查後才新增測試／修validator。
4. C21 仍待獨立核准；現有兩失敗保留。將來只對齊舊斷言不代表上述問題已修復。

下一輪應要求的紅綠驗收：

- 同metric/day/unit 的18<60正例；只有required、錯日/單位、sample>=required、外資換投信負例。
- 全unverified、部分verified、空events、事件集合變更、機構與新聞混句；不能靠出現「未驗證」就通過。
- partial/token與partial/count分流；整章省略、未請求scope、其他原因不能互相頂替。
- F034精確狀態可表達本包比較不可用；缺ID、缺欄位、其他value、同包其他block不可偷借。
- A02/A17/B05/B14/B20 restricted方向及A18跨領域方向仍拒绝；不能只驗證這次的精確短詞。
- 原125 blocks／139頂層項目完整逐案分類、raw/repair/service-final分開，原拒絕不丟失。
- 完整 pytest／compile、11 protected與語料SHA；品質案例不得僅靠validator pass作結。

本輪能力審查已完成；程式修復、C21調整、實機模型品質、5交易日證據與canary皆未完成。
本輪之後的清理只處理確認可重建快取，與上述授權點相互獨立，不讓清理隱藏失敗案例。

## 7. 本輪回歸及清理後驗收

證據目錄均為 `logs/absence_mapping_review_20260831/`，非 targeted 子集：

| 項目 | 清理前 | 清理後 |
|---|---|---|
| 舊清單 py_compile | 349 凍結來源＋2 本輪 audit helper，0 errors | 同左，0 errors |
| `pytest tests -q` | 2 failed, 941 passed in 29.63s，exit=1 | 2 failed, 941 passed in 35.38s，exit=1 |
| 來源／保護 | 349 個來源與凍結版相同，11/11 protected | 同左 |
| 私有設定 | 只取 SHA，不輸出內容 | hash 未變 |

完整 stdout：`compile_before.txt`、`compile_after.txt`、`pytest_before.txt`、`pytest_after.txt`；
兩個失敗函式的完整原文亦由 pytest 保留在 stdout 中，既有測試原碼不改。
新增／修改測試數 0／0，新增／修改 production 數 0／0。
清理 254 個 tests bytecode，不碰原始語料與驗收 JSON；詳細清單、還原方式見維護計畫最上方增量。

最終另以實際檔案盤點發現舊349清單漏列根目錄 `run_fugle_all_from_xlsx_progress.py`。
已补跑 **350份全部專案Python＋2 audit helper 的py_compile，0錯誤**，見
`compile_full_inventory.txt`／`final_full_source_state.json`。前後349份hash相同的結論不變，
額外runner只有本次hash，不能冒稱它也有前輪hash配對；不重寫先前compile輸出。
清理後四項Web GET smoke均200，時間戳與耗時見 `web_smoke_after.json`；這不是LINE/模型/零停機證據。
