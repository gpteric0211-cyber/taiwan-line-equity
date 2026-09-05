"""台股個股對應美股 / ETF / 海外情緒來源。

設計原則：
- 不使用通用 DEFAULT；沒有明確關聯就回空。
- 每檔股票的 reason / usage 依個股營收、客戶、產業鏈獨立撰寫，不共用同一段文字。
- relevance 給前端閱讀，weight 給排序用。
"""
from __future__ import annotations
from typing import Any

REVIEW_VERSION = "2026-06"


def asset(ticker: str, name: str, typ: str, relevance: str, weight: float, reason: str, usage: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "name": name,
        "market": "US",
        "type": typ,
        "relevance": relevance,
        "weight": float(weight),
        "reason": reason,
        "usage": usage,
    }

SMCI_CAUTION = "SMCI波動性高且曾有財務/治理疑慮，只作AI伺服器情緒參考，不宜單獨依賴。"

US_RELATION_MAP: dict[str, dict[str, Any]] = {
    # 水泥 / 建材
    "1101": {"category":"水泥 / 建材 / 儲能題材", "note":"台泥主要受台灣與亞洲建設需求、水泥報價影響；全球建材同業只作中低權重參考，另因儲能布局可低權重觀察電力管理題材。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("CX","Cemex","水泥同業","medium",0.65,"全球水泥龍頭之一，作為水泥/建材景氣情緒參考。","只反映全球水泥景氣方向，不能直接推論台泥短線。"),
        asset("VMC","Vulcan Materials","建材同業","low",0.35,"美國建材與骨材公司，作為建設需求低權重參考。","低權重參考。"),
        asset("MLM","Martin Marietta Materials","建材同業","low",0.35,"美國建材/骨材公司，作為建設需求輔助參考。","低權重參考。"),
        asset("ETN","Eaton","電力/儲能情緒","low",0.25,"台泥有儲能與能源轉型題材，ETN僅作電力管理情緒低權重參考。","只有在市場交易儲能題材時參考。"),
    ]},
    "1102": {"category":"水泥 / 建材", "note":"亞泥受台灣與亞洲水泥需求、建設景氣影響；美股建材同業僅作中低權重參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("CX","Cemex","水泥同業","medium",0.6,"全球水泥景氣參考。","只作景氣情緒參考。"),
        asset("VMC","Vulcan Materials","建材同業","low",0.35,"美國建材需求參考。","低權重參考。"),
        asset("MLM","Martin Marietta Materials","建材同業","low",0.35,"建材與骨材需求參考。","低權重參考。"),
    ]},
    "1216": {"category":"食品 / 民生消費 / 通路", "note":"統一是防禦型民生消費與通路股，美股主要作消費防禦情緒參考，相關性中低。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("XLP","Consumer Staples Select Sector SPDR","民生消費ETF","medium",0.55,"民生消費類股風險情緒參考。","防禦型消費股情緒參考，不能直接推論統一營收。"),
        asset("WMT","Walmart","零售通路","low",0.35,"大型通路與消費景氣參考。","低權重參考。"),
        asset("GIS","General Mills","食品同業","low",0.3,"食品公司景氣參考。","低權重參考。"),
        asset("KHC","Kraft Heinz","食品同業","low",0.3,"食品品牌公司情緒參考。","低權重參考。"),
    ]},
    # 塑化 / 化工
    "1301": {"category":"石化 / 塑化", "note":"台塑受油價、乙烯/丙烯等石化報價與全球塑化循環影響；DOW/LYB是同業景氣參考，XLE/USO偏原料成本參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("DOW","Dow Inc.","石化同業","medium",0.65,"全球化工龍頭，反映塑化/化工循環景氣。","DOW走弱通常代表化工需求或利差情緒偏弱。"),
        asset("LYB","LyondellBasell","石化同業","medium",0.65,"全球聚烯烴大廠，與塑化鏈利差/需求相關。","觀察聚烯烴需求與塑化景氣。"),
        asset("XLE","Energy Select Sector SPDR","能源/油價情緒","low",0.35,"油價影響石化原料成本。","低權重看成本端情緒。"),
        asset("XLB","Materials Select Sector SPDR","材料ETF","low",0.3,"原物料族群整體情緒。","低權重補充。"),
        asset("USO","United States Oil Fund","油價ETF","low",0.3,"油價成本端參考。","油價上漲對石化利差不一定正面，需結合產品報價。"),
    ]},
    "1303": {"category":"塑化 / 化工 / 電子材料", "note":"南亞受塑化、化工、電子材料與PCB材料需求影響；DOW/LYB看化工循環，NVDA/AI硬體只能低權重反映電子材料需求。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("DOW","Dow Inc.","化工同業","medium",0.6,"全球化工循環景氣參考。","觀察化工需求與利差情緒。"),
        asset("LYB","LyondellBasell","塑化同業","medium",0.55,"聚烯烴與塑化需求參考。","中權重參考塑化景氣。"),
        asset("XLB","Materials Select Sector SPDR","材料ETF","low",0.3,"材料族群情緒參考。","低權重補充。"),
        asset("NVDA","NVIDIA","AI硬體需求","low",0.25,"AI硬體需求對電子材料題材有間接影響。","低權重，不能當主要判斷。"),
    ]},
    "1326": {"category":"化纖 / 石化", "note":"台化受化纖、芳香烴與石化循環影響，美股化工同業只作中低權重景氣參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("DOW","Dow Inc.","化工同業","medium",0.55,"化工循環景氣參考。","中低權重參考。"),
        asset("LYB","LyondellBasell","石化同業","medium",0.5,"塑化與石化景氣參考。","中低權重參考。"),
        asset("EMN","Eastman Chemical","特用化學同業","low",0.35,"特用化學與材料需求參考。","低權重補充。"),
        asset("XLB","Materials Select Sector SPDR","材料ETF","low",0.3,"材料族群情緒。","低權重補充。"),
    ]},
    "6505": {"category":"煉油 / 石化 / 油價", "note":"台塑化受油價、煉油利差、成品油需求與石化循環影響；USO/XLE看油價與能源情緒，VLO/PSX看煉油同業。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("USO","United States Oil Fund","油價ETF","medium",0.65,"油價是煉油與石化成本/售價核心變數。","需搭配煉油利差判讀，油價上漲不一定等於利多。"),
        asset("XLE","Energy Select Sector SPDR","能源ETF","medium",0.55,"能源股與油價風險情緒參考。","中權重參考。"),
        asset("VLO","Valero Energy","煉油同業","medium",0.55,"美國煉油公司，成品油利差與需求參考。","觀察煉油同業情緒。"),
        asset("PSX","Phillips 66","煉油同業","low",0.4,"煉油與成品油需求參考。","低中權重補充。"),
    ]},
    "2002": {"category":"鋼鐵", "note":"中鋼受全球鋼價、鐵礦砂/焦煤報價與亞洲需求影響；NUE/STLD是美股鋼鐵景氣參考，但中鋼更受亞洲市場與中國供需影響。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("NUE","Nucor","鋼鐵同業","medium",0.65,"美國大型鋼鐵公司，反映鋼鐵景氣情緒。","觀察全球鋼鐵需求情緒。"),
        asset("STLD","Steel Dynamics","鋼鐵同業","medium",0.6,"美國鋼鐵同業，作為鋼鐵族群情緒參考。","中權重參考。"),
        asset("X","U.S. Steel","鋼鐵同業","low",0.35,"美國鋼鐵情緒指標。","低權重補充。"),
        asset("SLX","VanEck Steel ETF","鋼鐵ETF","low",0.35,"全球鋼鐵ETF。","低權重補充。"),
    ]},
    "1590": {"category":"氣動元件 / 工業自動化", "note":"亞德客是氣動元件龍頭，受全球工業自動化與製造業資本支出影響；PH/ROK是較接近的美股參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("PH","Parker-Hannifin","氣動/工業設備同業","medium",0.75,"Parker-Hannifin與氣動、液壓、工業控制相關，是較接近亞德客的美股對照。","觀察全球氣動/工業元件需求。"),
        asset("ROK","Rockwell Automation","工業自動化同業","medium",0.65,"工業自動化景氣參考。","觀察全球自動化需求情緒。"),
        asset("HON","Honeywell","工業科技","low",0.4,"工業科技與自動化需求參考。","低權重補充。"),
        asset("ITW","Illinois Tool Works","工業設備","low",0.35,"工業設備景氣參考。","低權重補充。"),
        asset("XLI","Industrial Select Sector SPDR","工業ETF","low",0.3,"工業類股景氣。","低權重補充。"),
    ]},
    "2207": {"category":"汽車通路 / Toyota代理", "note":"和泰車主要看Toyota品牌在台銷售與汽車需求；TM ADR最直接，但仍需回到台灣車市與匯率。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("TM","Toyota Motor ADR","品牌/主要代理來源","high",0.9,"Toyota ADR，是和泰車代理品牌最直接海外對照。","Toyota銷售與匯率情緒會影響和泰車評價。"),
        asset("HMC","Honda Motor ADR","日系車同業","low",0.35,"日系車需求情緒參考。","低權重補充。"),
        asset("CARZ","First Trust NASDAQ Global Auto ETF","汽車ETF","low",0.3,"全球汽車族群情緒。","低權重補充。"),
    ]},
    # 半導體晶圓 / IC設計
    "2330": {"category":"半導體晶圓代工 / 先進製程", "note":"台積電是全球先進製程核心，同時受AI需求、主要客戶資本支出、設備投資與半導體景氣影響；TSM ADR是最直接海外對照。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("TSM","TSMC ADR","ADR","high",1.0,"台積電ADR，最直接海外定價對照。","TSM ADR溢價可用來觀察台積電隔日開盤情緒。"),
        asset("NVDA","NVIDIA","主要客戶/AI需求","high",0.95,"AI GPU需求是台積電先進製程重要成長動能。","NVDA強弱反映AI先進製程需求情緒。"),
        asset("AMD","AMD","客戶需求/HPC","medium",0.65,"HPC與AI晶片需求參考。","觀察AI/HPC需求情緒。"),
        asset("AVGO","Broadcom","客戶需求/ASIC","medium",0.65,"AI ASIC與網通晶片需求。","觀察ASIC與高階網通需求。"),
        asset("ASML","ASML","設備/資本支出","medium",0.6,"EUV設備與先進製程資本支出情緒。","ASML走弱可能反映先進製程資本支出疑慮。"),
        asset("SMH","VanEck Semiconductor ETF","半導體ETF","medium",0.5,"半導體族群整體情緒。","觀察整體半導體風向。"),
        asset("SOXX","iShares Semiconductor ETF","半導體ETF","low",0.35,"半導體ETF輔助參考。","低權重補充。"),
    ]},
    "2303": {"category":"半導體晶圓代工 / 成熟製程", "note":"聯電以成熟製程為主，受車用、工控、消費性電子景氣影響；UMC ADR是最直接海外對照，與台積電先進製程邏輯不同。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("UMC","United Microelectronics ADR","ADR","high",1.0,"聯電ADR，最直接海外對照。","UMC ADR溢價可觀察聯電隔日情緒。"),
        asset("NXP","NXP Semiconductors","車用/成熟製程需求","medium",0.65,"車用半導體需求與成熟製程景氣參考。","觀察車用晶片需求。"),
        asset("QCOM","Qualcomm","手機晶片需求","medium",0.55,"手機晶片與成熟製程需求參考。","觀察手機晶片景氣。"),
        asset("ON","ON Semiconductor","車用/工控半導體","low",0.4,"車用與工控半導體需求參考。","低權重補充。"),
        asset("SMH","VanEck Semiconductor ETF","半導體ETF","low",0.3,"半導體整體情緒。","低權重補充。"),
    ]},
    "2454": {"category":"IC設計 / 手機晶片 / AI邊緣運算", "note":"聯發科主要受手機晶片景氣、AI邊緣運算需求與競爭對手QCOM影響；ETF只作輔助。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("QCOM","Qualcomm","直接競爭同業","high",1.0,"手機晶片最直接競爭對手，景氣與市場份額互相影響。","QCOM走強通常反映手機晶片需求回升，也可能代表競爭壓力。"),
        asset("AVGO","Broadcom","ASIC/IC設計同業","medium",0.65,"AI ASIC與網通IC設計景氣參考。","觀察高階IC設計需求情緒。"),
        asset("NVDA","NVIDIA","AI需求情緒","medium",0.55,"AI邊緣運算與AI晶片需求情緒參考。","觀察AI題材是否帶動IC設計族群。"),
        asset("AMD","AMD","IC設計同業","low",0.35,"IC設計族群情緒參考。","低權重參考。"),
        asset("SMH","VanEck Semiconductor ETF","半導體ETF","low",0.3,"半導體族群情緒參考。","低權重補充。"),
    ]},
    "3034": {"category":"面板驅動IC / IC設計", "note":"聯詠受面板、消費電子與IC設計景氣影響，海外直接對照較弱；QCOM/AMD/SMH只作IC設計與半導體情緒參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("QCOM","Qualcomm","IC設計情緒","medium",0.45,"IC設計與手機/消費電子景氣參考。","中低權重參考。"),
        asset("AMD","AMD","IC設計情緒","low",0.35,"IC設計族群風險偏好。","低權重參考。"),
        asset("SMH","VanEck Semiconductor ETF","半導體ETF","low",0.3,"半導體族群情緒。","低權重補充。"),
    ]},
    "3443": {"category":"ASIC / 半導體IP服務", "note":"創意受ASIC、先進製程與CSP客製晶片需求影響，主要看TSM、AVGO、NVDA與CSP資本支出。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("TSM","TSMC ADR","晶圓代工/先進製程","high",0.8,"先進製程需求與ASIC投片情緒。","觀察ASIC供應鏈與先進製程景氣。"),
        asset("AVGO","Broadcom","AI ASIC需求","high",0.8,"AI ASIC與網通晶片需求參考。","AVGO走強通常有助ASIC題材情緒。"),
        asset("NVDA","NVIDIA","AI需求","medium",0.65,"AI運算需求與CSP投資情緒。","中權重參考。"),
        asset("MSFT","Microsoft","CSP資本支出","medium",0.5,"Azure AI資本支出情緒。","觀察CSP自研/客製晶片投資。"),
        asset("GOOGL","Alphabet","CSP資本支出","medium",0.5,"Google Cloud/TPU/AI基礎建設資本支出參考。","觀察CSP需求。"),
    ]},
    "3661": {"category":"ASIC / IC設計服務 / AI客製晶片", "note":"世芯受AI ASIC、CSP客製晶片與先進製程需求影響，與台積電、AVGO、NVDA及CSP資本支出高度相關。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("TSM","TSMC ADR","晶圓代工/先進製程","high",0.85,"先進製程投片與ASIC需求情緒。","觀察先進製程與ASIC供應鏈情緒。"),
        asset("AVGO","Broadcom","AI ASIC需求","high",0.8,"AI ASIC需求與客製晶片景氣參考。","AVGO強弱可反映ASIC題材熱度。"),
        asset("NVDA","NVIDIA","AI運算需求","medium",0.65,"AI運算需求與CSP資本支出情緒。","中權重參考。"),
        asset("MSFT","Microsoft","CSP資本支出","medium",0.55,"Azure AI基礎建設投資參考。","觀察客製ASIC需求。"),
        asset("GOOGL","Alphabet","CSP資本支出","medium",0.55,"Google Cloud/TPU投資情緒。","觀察CSP需求。"),
    ]},
    "5274": {"category":"伺服器管理晶片 / BMC", "note":"信驊受伺服器、資料中心與AI伺服器出貨影響，主要看CSP資本支出、DELL/HPE與NVDA需求。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("NVDA","NVIDIA","AI伺服器需求","high",0.75,"AI伺服器需求帶動資料中心硬體出貨。","觀察AI伺服器需求情緒。"),
        asset("DELL","Dell Technologies","伺服器需求","medium",0.65,"企業與AI伺服器需求參考。","中權重參考。"),
        asset("HPE","Hewlett Packard Enterprise","伺服器需求","medium",0.6,"企業伺服器與AI基礎建設需求。","中權重參考。"),
        asset("MSFT","Microsoft","CSP資本支出","medium",0.5,"雲端資本支出參考。","觀察資料中心需求。"),
        asset("AMZN","Amazon","CSP資本支出","medium",0.5,"AWS資本支出參考。","觀察雲端需求。"),
    ]},
    # 封測 / 測試
    "3711": {"category":"封測 / 半導體後段", "note":"日月光投控受封測景氣、先進封裝與半導體循環影響；ASX ADR是直接對照。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("ASX","ASE Technology ADR","ADR","high",1.0,"日月光投控ADR，最直接海外對照。","ASX ADR可觀察日月光海外定價情緒。"),
        asset("TSM","TSMC ADR","先進製程/先進封裝需求","medium",0.65,"台積電先進製程與封裝需求會影響封測鏈。","觀察先進封裝與半導體景氣。"),
        asset("NVDA","NVIDIA","AI晶片需求","medium",0.55,"AI晶片需求帶動先進封裝與測試需求。","中權重參考。"),
        asset("SMH","VanEck Semiconductor ETF","半導體ETF","low",0.35,"半導體族群情緒。","低權重補充。"),
    ]},
    "2449": {"category":"半導體測試 / 封測供應鏈", "note":"京元電受AI/HPC晶片測試需求、晶圓代工景氣、封測族群與測試設備需求影響；不是單純看半導體ETF。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("ASX","ASE Technology ADR","封測同族群ADR","high",0.8,"日月光ADR，封測族群最直接海外對照。","ASX走勢可反映封測族群海外情緒。"),
        asset("TER","Teradyne","測試設備同業","high",0.75,"半導體測試設備龍頭，測試需求景氣參考。","TER走弱可能暗示測試需求或設備支出轉弱。"),
        asset("TSM","TSMC ADR","晶圓製造景氣","medium",0.65,"晶圓製造與先進製程需求會影響後段測試需求。","中權重參考。"),
        asset("NVDA","NVIDIA","AI/HPC測試需求","medium",0.65,"AI/HPC晶片出貨帶動測試需求。","觀察AI晶片測試需求情緒。"),
        asset("AMD","AMD","HPC/AI需求","medium",0.5,"HPC與AI晶片測試需求參考。","中低權重參考。"),
        asset("AVGO","Broadcom","ASIC/網通晶片需求","medium",0.5,"ASIC與網通晶片需求。","中低權重參考。"),
        asset("SMH","VanEck Semiconductor ETF","半導體ETF","low",0.35,"半導體族群情緒。","低權重補充。"),
    ]},
    "2360": {"category":"測試量測 / 半導體與電子測試", "note":"致茂受半導體、電動車與電池/電源測試需求影響；TER/KEYS是測試量測情緒參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("TER","Teradyne","半導體測試設備","medium",0.7,"半導體測試設備龍頭，測試景氣參考。","觀察半導體測試需求。"),
        asset("KEYS","Keysight Technologies","電子量測設備","medium",0.65,"電子量測設備公司，量測需求景氣參考。","觀察測試量測設備景氣。"),
        asset("A","Agilent","量測/檢測設備","low",0.35,"量測檢測設備情緒參考。","低權重補充。"),
        asset("TSLA","Tesla","EV/電池測試需求","low",0.3,"電動車與電池測試題材低權重參考。","低權重，不可直接推論。"),
    ]},
    "7769": {"category":"半導體測試設備 / 測試介面", "note":"鴻勁受半導體測試需求、AI/HPC晶片測試與封測資本支出影響；TER/ASX/TSM是主要海外參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("TER","Teradyne","測試設備同業","high",0.8,"半導體測試設備龍頭，測試需求景氣參考。","觀察測試設備景氣。"),
        asset("ASX","ASE Technology ADR","封測族群","medium",0.65,"封測族群景氣參考。","中權重參考。"),
        asset("TSM","TSMC ADR","晶圓製造景氣","medium",0.55,"晶圓製造需求影響後段測試。","中權重參考。"),
        asset("NVDA","NVIDIA","AI/HPC測試需求","medium",0.55,"AI/HPC晶片測試需求情緒。","中權重參考。"),
    ]},
    # AI server / EMS / PC ODM
    "2382": {"category":"AI伺服器 / 雲端伺服器代工", "note":"廣達是AI伺服器重要代工廠，主要受NVIDIA平台出貨與CSP資本支出影響，比鴻海更純粹偏AI伺服器邏輯。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("NVDA","NVIDIA","AI伺服器需求核心","high",1.0,"AI GPU平台出貨直接帶動廣達AI伺服器訂單。","NVDA財報與出貨指引是廣達最重要外部指標。"),
        asset("MSFT","Microsoft","CSP資本支出","medium",0.7,"Azure資本支出方向影響AI伺服器需求。","觀察CSP資本支出情緒。"),
        asset("GOOGL","Alphabet","CSP資本支出","medium",0.65,"Google Cloud資本支出參考。","觀察雲端資本支出情緒。"),
        asset("AMZN","Amazon","CSP資本支出","medium",0.65,"AWS資本支出參考。","觀察雲端需求。"),
        asset("DELL","Dell Technologies","AI伺服器出貨情緒","medium",0.6,"AI/企業伺服器出貨情緒。","觀察伺服器需求。"),
        asset("SMCI","Super Micro Computer","AI伺服器情緒","low",0.35,"AI伺服器高波動情緒指標。",f"只當情緒參考。{SMCI_CAUTION}"),
    ]},
    "6669": {"category":"AI伺服器 / Hyperscaler 供應鏈", "note":"緯穎客戶集中於超大規模雲端業者，對NVDA平台與CSP資本支出高度敏感。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("NVDA","NVIDIA","AI伺服器需求核心","high",1.0,"AI GPU需求直接影響緯穎出貨量。","最重要外部觀察指標。"),
        asset("MSFT","Microsoft","CSP資本支出","high",0.85,"Azure資本支出影響AI伺服器需求。","觀察CSP資本支出指引。"),
        asset("GOOGL","Alphabet","CSP客戶/資本支出","medium",0.65,"Google Cloud資本支出參考。","觀察CSP需求。"),
        asset("AMZN","Amazon","CSP客戶/資本支出","medium",0.65,"AWS資本支出參考。","觀察CSP需求。"),
        asset("META","Meta Platforms","AI基礎建設資本支出","medium",0.6,"Meta AI基礎建設資本支出參考。","觀察Meta AI伺服器需求。"),
    ]},
    "2317": {"category":"電子代工 / Apple供應鏈 / AI伺服器", "note":"鴻海需雙軌觀察：Apple消費性電子是核心營收主線，AI伺服器是成長主線；不能只看NVDA或只看AAPL。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("AAPL","Apple","主要客戶","high",1.0,"iPhone與消費性電子組裝需求，是鴻海重要營收來源。","AAPL大跌或iPhone需求轉弱時，鴻海消費電子線可能承壓。"),
        asset("NVDA","NVIDIA","AI伺服器需求","high",0.9,"AI GPU平台需求影響鴻海AI伺服器成長線。","NVDA走強通常有助AI伺服器供應鏈情緒。"),
        asset("DELL","Dell Technologies","AI/企業伺服器","medium",0.6,"伺服器出貨需求參考。","觀察AI伺服器與企業硬體需求。"),
        asset("HPE","Hewlett Packard Enterprise","伺服器","medium",0.55,"企業伺服器與資料中心需求。","中權重參考。"),
        asset("SMCI","Super Micro Computer","AI伺服器情緒","medium",0.45,"AI伺服器高波動情緒指標。",f"只當情緒參考。{SMCI_CAUTION}"),
        asset("QQQ","Nasdaq 100 ETF","科技風險偏好","low",0.3,"大型科技風險偏好。","低權重參考。"),
    ]},
    "3231": {"category":"AI伺服器 / EMS / 筆電代工", "note":"緯創同時有AI伺服器、EMS與筆電代工邏輯，AI純度低於緯穎但高於傳統PC代工。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("NVDA","NVIDIA","AI伺服器需求","high",0.8,"AI伺服器需求影響緯創成長線。","觀察AI伺服器需求情緒。"),
        asset("DELL","Dell Technologies","伺服器/PC需求","medium",0.6,"伺服器與PC需求參考。","中權重參考。"),
        asset("HPE","Hewlett Packard Enterprise","伺服器需求","medium",0.55,"企業伺服器需求參考。","中權重參考。"),
        asset("SMCI","Super Micro Computer","AI伺服器情緒","low",0.35,"AI伺服器高波動情緒。",f"只作情緒參考。{SMCI_CAUTION}"),
        asset("AAPL","Apple","消費電子/EMS情緒","low",0.3,"消費電子供應鏈情緒參考。","低權重參考。"),
    ]},
    "2356": {"category":"伺服器 / 筆電代工", "note":"英業達受傳統伺服器、AI伺服器與筆電需求影響，但AI純度低於廣達/緯穎。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("DELL","Dell Technologies","伺服器/PC需求","medium",0.65,"企業伺服器與PC需求參考。","觀察硬體需求。"),
        asset("HPE","Hewlett Packard Enterprise","伺服器需求","medium",0.6,"企業伺服器需求參考。","中權重參考。"),
        asset("NVDA","NVIDIA","AI伺服器需求","medium",0.55,"AI伺服器需求提供成長題材。","中權重參考。"),
        asset("MSFT","Microsoft","PC/雲端生態","low",0.35,"Windows/雲端需求參考。","低權重補充。"),
        asset("AMZN","Amazon","雲端資本支出","low",0.35,"AWS需求參考。","低權重補充。"),
    ]},
    "2357": {"category":"PC / Gaming / AI PC", "note":"華碩主要看PC、gaming、AI PC與消費性電子需求；NVDA/AMD/INTC反映GPU/CPU與PC硬體情緒。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("NVDA","NVIDIA","GPU/Gaming/AI PC需求","medium",0.7,"GPU與AI PC/gaming硬體需求參考。","觀察高階PC與AI PC情緒。"),
        asset("AMD","AMD","CPU/GPU需求","medium",0.65,"PC CPU/GPU需求參考。","中權重參考。"),
        asset("INTC","Intel","PC CPU需求","medium",0.55,"PC CPU與AI PC生態參考。","中權重參考。"),
        asset("MSFT","Microsoft","Windows/AI PC生態","medium",0.5,"Windows與AI PC生態需求參考。","中權重參考。"),
        asset("DELL","Dell Technologies","PC同業需求","low",0.35,"PC硬體需求參考。","低權重補充。"),
        asset("QQQ","Nasdaq 100 ETF","科技風險偏好","low",0.25,"大型科技風險偏好。","低權重參考。"),
    ]},
    "4938": {"category":"EMS / Apple供應鏈", "note":"和碩以Apple供應鏈與消費電子代工為主，AI伺服器題材若有也應低於Apple主線。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("AAPL","Apple","主要客戶/Apple供應鏈","high",1.0,"Apple消費性電子需求是和碩最重要海外參考。","AAPL走弱或iPhone需求降溫通常對和碩情緒不利。"),
        asset("QQQ","Nasdaq 100 ETF","科技風險偏好","low",0.3,"大型科技風險偏好。","低權重參考。"),
        asset("NVDA","NVIDIA","AI伺服器/EMS情緒","low",0.25,"若市場交易和碩AI伺服器題材，NVDA只作低權重情緒參考。","不可取代AAPL主線。"),
    ]},
    "2324": {"category":"筆電代工 / PC ODM", "note":"仁寶主要看筆電與PC需求，AI伺服器權重低於廣達/緯穎；HPQ/DELL/MSFT/INTC較有參考性。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("HPQ","HP Inc.","PC需求","medium",0.7,"PC/筆電需求參考。","觀察全球PC需求情緒。"),
        asset("DELL","Dell Technologies","PC/企業硬體","medium",0.65,"PC與企業硬體需求參考。","中權重參考。"),
        asset("MSFT","Microsoft","Windows/PC生態","medium",0.5,"Windows與AI PC生態需求參考。","中權重參考。"),
        asset("INTC","Intel","PC CPU需求","low",0.4,"PC CPU需求參考。","低權重補充。"),
        asset("AMD","AMD","PC CPU/GPU需求","low",0.35,"PC硬體需求參考。","低權重補充。"),
    ]},
    # Power / components / networking / PCB / thermal
    "2308": {"category":"電源管理 / AI資料中心電源 / 工業自動化", "note":"台達電主要看AI資料中心電源需求、電力管理投資、工業自動化與能源基礎建設，不應與PCB族群混用。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("NVDA","NVIDIA","AI資料中心需求","high",0.8,"AI伺服器功耗提升帶動資料中心電源與散熱需求。","NVDA強弱可作AI資料中心需求情緒。"),
        asset("ETN","Eaton","電力管理/資料中心電源","medium",0.75,"全球電力管理與資料中心電力基礎建設參考。","觀察電源管理族群情緒。"),
        asset("VRT","Vertiv","資料中心電力/散熱","medium",0.65,"資料中心電力與散熱設備需求參考。","中權重參考。"),
        asset("SMCI","Super Micro Computer","AI伺服器","medium",0.45,"AI伺服器出貨情緒參考。",f"高波動情緒指標。{SMCI_CAUTION}"),
        asset("DELL","Dell Technologies","伺服器需求","medium",0.45,"AI/企業伺服器需求參考。","中低權重參考。"),
        asset("ROK","Rockwell Automation","工業自動化","low",0.35,"工業自動化需求情緒參考。","低權重參考。"),
        asset("HON","Honeywell","工業自動化/工業科技","low",0.3,"工業科技與自動化情緒參考。","低權重參考。"),
        asset("TSLA","Tesla","EV/充電情緒","low",0.25,"EV充電與電動車需求情緒參考。","只作EV充電題材輔助。"),
    ]},
    "2301": {"category":"電源供應 / 電子零組件 / 光電", "note":"光寶科受電源、雲端/AI硬體、汽車電子與電子零組件需求影響；NVDA/ETN/VRT偏AI資料中心電源，APH/TEL偏電子零組件。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("NVDA","NVIDIA","AI硬體需求","medium",0.55,"AI伺服器與電源需求情緒。","中權重參考。"),
        asset("ETN","Eaton","電力管理","medium",0.5,"電源管理與資料中心電力需求參考。","中低權重參考。"),
        asset("VRT","Vertiv","資料中心電力/散熱","medium",0.5,"資料中心電力與散熱需求參考。","中低權重參考。"),
        asset("APH","Amphenol","電子零組件同業","low",0.35,"電子連接與零組件需求參考。","低權重補充。"),
        asset("TEL","TE Connectivity","電子零組件同業","low",0.3,"連接器與汽車電子需求參考。","低權重補充。"),
    ]},
    "2327": {"category":"被動元件 / MLCC", "note":"國巨受被動元件景氣、消費電子、車用與工控需求影響；美股直接對照弱，以電子零組件與半導體需求低權重參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("APH","Amphenol","電子零組件同業","low",0.4,"電子零組件需求參考。","低權重補充。"),
        asset("TEL","TE Connectivity","電子零組件同業","low",0.35,"連接器與車用電子需求參考。","低權重補充。"),
        asset("NXP","NXP Semiconductors","車用電子需求","low",0.3,"車用電子景氣參考。","低權重補充。"),
        asset("SMH","VanEck Semiconductor ETF","半導體ETF","low",0.25,"半導體景氣間接參考。","低權重，不可作主要判斷。"),
    ]},
    "2345": {"category":"網通交換器 / 資料中心網路", "note":"智邦受資料中心交換器、白牌交換器與高速網路需求影響；ANET/CSCO是最重要美股參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("ANET","Arista Networks","資料中心交換器同業","high",0.9,"Arista是資料中心高速交換器龍頭，與智邦白牌交換器題材高度相關。","ANET財報與指引對智邦情緒很重要。"),
        asset("CSCO","Cisco","網通設備同業","medium",0.6,"傳統網通設備龍頭，網通景氣參考。","中權重參考。"),
        asset("HPE","Hewlett Packard Enterprise","企業網通/伺服器","low",0.4,"企業網通與伺服器需求參考。","低權重補充。"),
        asset("AVGO","Broadcom","網通晶片需求","medium",0.55,"網通與交換器晶片需求參考。","中權重參考。"),
    ]},
    "2368": {"category":"PCB / AI伺服器高階板", "note":"金像電受AI伺服器PCB需求影響，核心看NVDA平台、資料中心網路與伺服器出貨情緒。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("NVDA","NVIDIA","AI伺服器需求","high",0.85,"AI伺服器需求牽動高階PCB。","NVDA是最重要外部情緒。"),
        asset("ANET","Arista Networks","資料中心網路","medium",0.65,"高速交換器需求帶動高速傳輸PCB。","觀察資料中心網路設備情緒。"),
        asset("AVGO","Broadcom","網通/ASIC需求","medium",0.55,"網通晶片與ASIC需求影響高速PCB。","中權重參考。"),
        asset("DELL","Dell Technologies","伺服器出貨情緒","low",0.35,"AI伺服器出貨量參考。","低權重補充。"),
    ]},
    "3037": {"category":"PCB / 載板 / AI伺服器PCB", "note":"欣興受AI伺服器PCB、載板與高速傳輸需求影響；核心看NVDA、ANET、AVGO與伺服器需求。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("NVDA","NVIDIA","AI伺服器需求","high",0.8,"AI伺服器PCB需求核心動能。","NVDA出貨與財報是重要外部指標。"),
        asset("ANET","Arista Networks","高速網路設備需求","medium",0.65,"資料中心交換器需求帶動高速傳輸PCB。","觀察資料中心網路情緒。"),
        asset("AVGO","Broadcom","網通/ASIC需求","medium",0.55,"網通晶片需求影響高速PCB。","中權重參考。"),
        asset("DELL","Dell Technologies","伺服器出貨情緒","low",0.35,"AI伺服器出貨量參考。","低權重補充。"),
    ]},
    "8046": {"category":"IC載板 / PCB", "note":"南電受ABF載板、AI/HPC與半導體景氣影響；NVDA/AVGO/AMD反映高階晶片需求，SMH只作族群輔助。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("NVDA","NVIDIA","AI/HPC需求","high",0.75,"AI/HPC晶片需求影響高階載板需求。","觀察AI/HPC載板需求情緒。"),
        asset("AVGO","Broadcom","ASIC/網通晶片需求","medium",0.55,"高階ASIC與網通晶片需求參考。","中權重參考。"),
        asset("AMD","AMD","HPC需求","medium",0.5,"HPC晶片需求參考。","中低權重參考。"),
        asset("SMH","VanEck Semiconductor ETF","半導體ETF","low",0.3,"半導體族群情緒。","低權重補充。"),
    ]},
    "2383": {"category":"CCL / 高速材料 / AI伺服器", "note":"台光電受AI伺服器高速材料、PCB與資料中心網路需求影響；NVDA/ANET/AVGO是主要海外參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("NVDA","NVIDIA","AI伺服器需求","high",0.8,"AI伺服器需求牽動高階CCL/PCB材料。","觀察AI硬體需求。"),
        asset("ANET","Arista Networks","資料中心網路","medium",0.65,"高速交換器需求帶動高速材料。","觀察資料中心高速傳輸需求。"),
        asset("AVGO","Broadcom","網通/ASIC需求","medium",0.55,"高速網通/ASIC需求參考。","中權重參考。"),
        asset("JBL","Jabil","電子製造/硬體供應鏈","low",0.3,"硬體供應鏈景氣參考。","低權重補充。"),
    ]},
    "4958": {"category":"PCB / 蘋果供應鏈 / 伺服器板", "note":"臻鼎同時受Apple消費電子PCB與伺服器/AI硬體PCB需求影響，需雙軌觀察AAPL與AI硬體。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("AAPL","Apple","主要客戶/消費電子PCB","high",0.85,"Apple產品出貨影響臻鼎消費電子PCB需求。","AAPL走弱可能壓抑Apple供應鏈情緒。"),
        asset("NVDA","NVIDIA","AI伺服器PCB需求","medium",0.55,"AI硬體需求影響伺服器PCB題材。","中權重參考。"),
        asset("ANET","Arista Networks","高速網路PCB需求","low",0.35,"資料中心高速網路PCB需求參考。","低權重補充。"),
        asset("AVGO","Broadcom","網通/ASIC需求","low",0.35,"網通/ASIC需求參考。","低權重補充。"),
    ]},
    "3665": {"category":"連接器 / 線材 / EV充電 / AI伺服器電源線", "note":"貿聯受AI伺服器電源連接器需求與EV充電需求雙軌影響，全球連接器同業APH/TEL是重要參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("APH","Amphenol","連接器全球同業","medium",0.75,"全球連接器龍頭，景氣與需求高度參考。","觀察全球連接器需求。"),
        asset("TEL","TE Connectivity","連接器同業","medium",0.7,"連接器與車用電子需求參考。","觀察連接器景氣。"),
        asset("NVDA","NVIDIA","AI伺服器電源需求","medium",0.55,"AI伺服器電源線與連接器需求。","觀察AI伺服器供應鏈情緒。"),
        asset("TSLA","Tesla","EV充電情緒","low",0.35,"EV充電需求情緒參考。","低權重，只看EV充電題材。"),
        asset("ANET","Arista Networks","資料中心網路","low",0.3,"資料中心高速傳輸連接需求輔助。","低權重補充。"),
    ]},
    "3653": {"category":"散熱 / 機構件 / AI伺服器", "note":"健策受AI伺服器散熱與高功耗晶片需求影響，主要看NVDA、AI伺服器出貨與資料中心電力/散熱情緒。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("NVDA","NVIDIA","AI伺服器需求","high",0.85,"高功耗AI晶片推升散熱與機構件需求。","NVDA是健策最重要海外需求情緒。"),
        asset("VRT","Vertiv","資料中心電力/散熱","medium",0.65,"資料中心電力與散熱設備需求參考。","中權重參考。"),
        asset("SMCI","Super Micro Computer","AI伺服器情緒","low",0.35,"AI伺服器高波動情緒指標。",f"只作情緒參考。{SMCI_CAUTION}"),
        asset("DELL","Dell Technologies","伺服器需求","low",0.35,"AI/企業伺服器需求參考。","低權重補充。"),
    ]},
    # 光學 / 工業電腦 / 記憶體 / 電信
    "3008": {"category":"光學鏡頭 / Apple供應鏈", "note":"大立光主要看手機鏡頭需求，尤其iPhone出貨與鏡頭規格升級；半導體ETF對它關聯低，不應放NVDA/SMH作主參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("AAPL","Apple","主要需求來源","high",1.0,"iPhone出貨與鏡頭規格升級會直接影響手機鏡頭供應鏈情緒。","AAPL走弱或iPhone需求降溫通常壓抑光學供應鏈評價。"),
        asset("COHR","Coherent","光學/光電同業參考","low",0.35,"光學與光電元件情緒參考，但與手機鏡頭直接關聯有限。","低權重參考，不可直接推論大立光。"),
    ]},
    "2395": {"category":"工業電腦 / IoT / 邊緣運算", "note":"研華主要看工業電腦、IoT、工業自動化與邊緣AI需求，不應與PCB族群混用。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("ROK","Rockwell Automation","工業自動化","medium",0.7,"工業自動化需求與景氣參考。","觀察全球工業自動化情緒。"),
        asset("HON","Honeywell","工業科技/自動化","medium",0.65,"工業科技、IoT與自動化情緒參考。","觀察工業科技需求。"),
        asset("PH","Parker-Hannifin","工業設備","low",0.4,"工業設備需求參考。","低權重參考。"),
        asset("NVDA","NVIDIA","邊緣AI需求","low",0.35,"邊緣AI與工業AI題材情緒參考。","低到中權重，只看AI邊緣題材。"),
        asset("XLI","Industrial Select Sector SPDR","工業ETF","low",0.3,"工業類股景氣。","低權重補充。"),
    ]},
    "2408": {"category":"記憶體 / DRAM", "note":"南亞科以DRAM景氣為核心，MU是最直接美股對照；WDC/STX偏儲存需求，SMH/SOXX僅作族群情緒。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("MU","Micron","記憶體同業","high",1.0,"美光是最主要記憶體美股對照，DRAM/NAND景氣直接參考。","MU財報與指引是記憶體族群最重要風向。"),
        asset("WDC","Western Digital","儲存/Flash同業","low",0.4,"NAND與儲存需求情緒參考。","低權重補充。"),
        asset("STX","Seagate","儲存需求","low",0.35,"資料中心與儲存需求參考。","低權重補充。"),
        asset("SMH","VanEck Semiconductor ETF","半導體ETF","low",0.3,"半導體族群情緒參考。","低權重補充。"),
    ]},
    "2344": {"category":"記憶體 / NOR Flash / DRAM", "note":"華邦電以NOR Flash等利基記憶體為主，受消費電子、IoT、車用電子與記憶體景氣影響；MU仍是最重要情緒參考但不是完全同產品。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("MU","Micron","記憶體同業","high",0.85,"美光是最主要記憶體美股對照。","MU財報與指引是記憶體族群重要風向，但華邦電產品結構不同，需降權解讀。"),
        asset("WDC","Western Digital","儲存/Flash同業","low",0.4,"NAND Flash需求情緒參考。","低權重補充。"),
        asset("NXP","NXP Semiconductors","車用半導體需求","low",0.35,"車用電子需求參考。","低權重補充。"),
        asset("SMH","VanEck Semiconductor ETF","半導體ETF","low",0.3,"半導體族群情緒。","低權重補充。"),
    ]},
    "2412": {"category":"電信 / 防禦型高殖利率", "note":"中華電是防禦型電信股，美股科技與半導體對它關聯低；主要看CHT ADR，通訊服務ETF只作弱關聯參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("CHT","Chunghwa Telecom ADR","ADR","high",1.0,"中華電信ADR，最直接海外對照。","觀察海外投資人對中華電信的定價與防禦型電信股情緒。"),
        asset("IYZ","iShares U.S. Telecommunications ETF","電信ETF","low",0.35,"美國電信產業情緒參考。","低權重參考，不可直接推論中華電。"),
        asset("VOX","Vanguard Communication Services ETF","通訊服務ETF","low",0.25,"通訊服務ETF含大型平台公司，與中華電關聯較弱。","只作弱關聯情緒參考。"),
    ]},
    "3045": {"category":"電信 / 防禦型股", "note":"台灣大是台灣電信股，無直接高相關美股對照；IYZ/VOX只作弱關聯通訊服務情緒參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("IYZ","iShares U.S. Telecommunications ETF","電信ETF","low",0.35,"美國電信產業情緒參考。","低權重參考。"),
        asset("VOX","Vanguard Communication Services ETF","通訊服務ETF","low",0.25,"通訊服務ETF含大型平台公司，與台灣電信股關聯較弱。","弱關聯參考。"),
    ]},
    "4904": {"category":"電信 / 防禦型股", "note":"遠傳是台灣電信股，沒有直接美股ADR；美國通訊ETF僅作弱關聯參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("IYZ","iShares U.S. Telecommunications ETF","電信ETF","low",0.35,"美國電信產業情緒參考。","低權重參考。"),
        asset("VOX","Vanguard Communication Services ETF","通訊服務ETF","low",0.25,"通訊服務ETF與台灣電信股關聯弱。","弱關聯參考。"),
    ]},
    # 金融
    "2881": {"category":"壽險型金控", "note":"富邦金受壽險海外資產、利率、匯率與台股市場影響大；TLT/UUP是利率與匯率proxy，美國金融ETF只能低權重參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("TLT","20+ Year Treasury Bond ETF","利率/美債參考","medium",0.65,"長天期美債價格與殖利率反向，影響壽險海外債券評價與利率情緒。","TLT下跌通常代表長債殖利率上升，需留意壽險評價與匯率風險。"),
        asset("UUP","US Dollar Index Bullish Fund","美元/匯率參考","medium",0.55,"美元強弱與台幣匯率會影響壽險海外資產與避險成本。","美元偏強時需留意台幣與外資資金流。"),
        asset("XLF","Financial Select Sector SPDR","金融ETF","low",0.3,"美國金融股情緒參考，但非直接同業對照。","低權重參考。"),
    ]},
    "2882": {"category":"壽險型金控", "note":"國泰金受壽險海外資產、長債殖利率、台幣匯率與台股市場影響大；TLT/UUP比美國銀行ETF更直接。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("TLT","20+ Year Treasury Bond ETF","利率/美債參考","medium",0.65,"長天期美債價格與殖利率反向，影響壽險海外債券評價。","TLT下跌通常代表長債殖利率上升，需留意壽險評價與匯率風險。"),
        asset("UUP","US Dollar Index Bullish Fund","美元/匯率參考","medium",0.55,"美元強弱與台幣匯率會影響壽險海外資產與避險成本。","美元偏強時需留意台幣與外資資金流。"),
        asset("XLF","Financial Select Sector SPDR","金融ETF","low",0.3,"美國金融股情緒低權重參考。","低權重參考。"),
    ]},
    "2880": {"category":"銀行型金控", "note":"華南金以銀行業務為主，主要看台灣利率、放款品質與金融市場；美國銀行ETF只作低權重情緒參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("KBE","SPDR S&P Bank ETF","美國銀行ETF","low",0.35,"全球銀行業風險情緒參考。","低權重，不可直接推論台灣銀行股。"),
        asset("XLF","Financial Select Sector SPDR","金融ETF","low",0.3,"金融股風險情緒參考。","低權重補充。"),
    ]},
    "2883": {"category":"證券/銀行混合型金控", "note":"凱基金受台股成交量、資本市場風險偏好、銀行/證券業務影響；SCHW/IBKR只作券商情緒參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("SCHW","Charles Schwab","券商/資本市場情緒","low",0.4,"券商與資本市場風險情緒參考。","低權重參考，台灣證券業還需看台股成交量。"),
        asset("IBKR","Interactive Brokers","券商/交易量情緒","low",0.35,"交易活絡度與券商情緒參考。","低權重參考。"),
        asset("XLF","Financial Select Sector SPDR","金融ETF","low",0.3,"金融股風險情緒參考。","低權重補充。"),
    ]},
    "2884": {"category":"銀行型金控", "note":"玉山金以銀行業務為主，主要看台灣信貸、手續費、利率與資產品質；美國銀行ETF只低權重參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("KBE","SPDR S&P Bank ETF","美國銀行ETF","low",0.35,"全球銀行業風險情緒參考。","低權重參考。"),
        asset("KRE","SPDR S&P Regional Banking ETF","美國區域銀行ETF","low",0.25,"區域銀行風險情緒參考。","弱關聯參考。"),
        asset("XLF","Financial Select Sector SPDR","金融ETF","low",0.3,"金融股情緒參考。","低權重補充。"),
    ]},
    "2885": {"category":"證券 / ETF / 資本市場型金控", "note":"元大金受台股成交量、ETF/財富管理、資本市場風險偏好影響；SCHW/IBKR比銀行ETF更有參考性，但仍為低權重。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("SCHW","Charles Schwab","券商/財富管理","medium",0.5,"券商與財富管理情緒參考。","中低權重，仍需看台股成交量。"),
        asset("IBKR","Interactive Brokers","券商/交易量情緒","low",0.4,"交易量與券商情緒參考。","低權重參考。"),
        asset("SPY","SPDR S&P 500 ETF","市場風險偏好","low",0.25,"整體市場風險偏好會影響交易量與ETF需求。","低權重補充。"),
        asset("XLF","Financial Select Sector SPDR","金融ETF","low",0.25,"金融股情緒參考。","低權重補充。"),
    ]},
    "2886": {"category":"銀行型金控", "note":"兆豐金以銀行業務為主，受台灣利率、放款、匯率與全球金融風險情緒影響；美國銀行ETF只作低權重情緒參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("KBE","SPDR S&P Bank ETF","美國銀行ETF","low",0.35,"全球銀行業風險情緒參考。","低權重，不可直接推論台灣銀行股。"),
        asset("XLF","Financial Select Sector SPDR","金融ETF","low",0.3,"全球金融股風險情緒參考。","低權重補充。"),
        asset("UUP","US Dollar Index Bullish Fund","美元/匯率參考","low",0.25,"匯率與美元強弱參考。","低權重補充。"),
    ]},
    "2887": {"category":"銀行型金控", "note":"台新金以銀行/消金與財富管理為主，主要仍看台灣利率與信貸景氣；美國銀行ETF低權重參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("KBE","SPDR S&P Bank ETF","美國銀行ETF","low",0.35,"全球銀行業情緒參考。","低權重參考。"),
        asset("XLF","Financial Select Sector SPDR","金融ETF","low",0.3,"金融股情緒參考。","低權重補充。"),
    ]},
    "2890": {"category":"銀行型金控", "note":"永豐金受銀行業務、台股資本市場與財富管理影響；美國金融ETF只低權重參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("KBE","SPDR S&P Bank ETF","美國銀行ETF","low",0.3,"銀行業風險情緒參考。","低權重參考。"),
        asset("SCHW","Charles Schwab","券商/資本市場情緒","low",0.25,"資本市場與券商情緒參考。","低權重補充。"),
        asset("XLF","Financial Select Sector SPDR","金融ETF","low",0.3,"金融股情緒參考。","低權重補充。"),
    ]},
    "2891": {"category":"銀行型金控 / 消金", "note":"中信金以銀行與消金業務為主，也受海外市場與匯率影響；美國銀行ETF只作低權重情緒參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("KBE","SPDR S&P Bank ETF","美國銀行ETF","low",0.35,"全球銀行業風險情緒參考。","低權重參考。"),
        asset("KRE","SPDR S&P Regional Banking ETF","美國區域銀行ETF","low",0.25,"區域銀行風險情緒。","弱關聯參考。"),
        asset("XLF","Financial Select Sector SPDR","金融ETF","low",0.3,"金融股情緒參考。","低權重補充。"),
    ]},
    "2892": {"category":"銀行型金控", "note":"第一金以銀行業務為主，主要看台灣利率、放款品質與金融環境；美國銀行ETF低權重參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("KBE","SPDR S&P Bank ETF","美國銀行ETF","low",0.35,"銀行業風險情緒參考。","低權重參考。"),
        asset("XLF","Financial Select Sector SPDR","金融ETF","low",0.3,"金融股情緒參考。","低權重補充。"),
    ]},
    "5880": {"category":"銀行型金控", "note":"合庫金以銀行業務為主，主要看台灣利率、放款品質與金融環境；美國銀行ETF低權重參考。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("KBE","SPDR S&P Bank ETF","美國銀行ETF","low",0.35,"銀行業風險情緒參考。","低權重參考。"),
        asset("XLF","Financial Select Sector SPDR","金融ETF","low",0.3,"金融股情緒參考。","低權重補充。"),
    ]},
    # 航運
    "2603": {"category":"貨櫃航運", "note":"長榮主要受全球貨櫃運價、供需景氣與油價成本影響；ZIM是最直接美股情緒對照，半導體/科技股與長榮無直接關聯。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("ZIM","ZIM Integrated Shipping","貨櫃航運同業","high",0.9,"ZIM是在美上市貨櫃航運公司，景氣情緒直接對照。","觀察全球貨櫃航運情緒。"),
        asset("MATX","Matson","航運/物流","medium",0.55,"航運與物流需求情緒參考。","輔助觀察。"),
        asset("BOAT","SonicShares Global Shipping ETF","全球航運ETF","low",0.4,"全球航運ETF，作為航運族群低權重參考。","低權重補充；yfinance偶有資料延遲，若顯示資料暫無屬正常。"),
        asset("BDRY","Breakwave Dry Bulk Shipping ETF","乾散貨運價ETF","low",0.25,"乾散貨運價情緒參考，與貨櫃不完全相同。","低權重，只看運價整體情緒。"),
        asset("USO","United States Oil Fund","油價成本","low",0.25,"油價影響航運燃油成本。","低權重，只看成本端情緒。"),
    ]},
    "2609": {"category":"貨櫃航運", "note":"陽明主要受全球貨櫃運價、供需景氣與油價成本影響；ZIM是最直接美股情緒對照。", "last_reviewed": REVIEW_VERSION, "assets":[
        asset("ZIM","ZIM Integrated Shipping","貨櫃航運同業","high",0.9,"ZIM是在美上市貨櫃航運公司，景氣情緒直接對照。","觀察全球貨櫃航運情緒。"),
        asset("MATX","Matson","航運/物流","medium",0.55,"航運與物流需求情緒參考。","輔助觀察。"),
        asset("BOAT","SonicShares Global Shipping ETF","全球航運ETF","low",0.4,"全球航運ETF，作為航運族群低權重參考。","低權重補充；yfinance偶有資料延遲，若顯示資料暫無屬正常。"),
        asset("BDRY","Breakwave Dry Bulk Shipping ETF","乾散貨運價ETF","low",0.25,"乾散貨運價情緒參考，與貨櫃不完全相同。","低權重。"),
        asset("USO","United States Oil Fund","油價成本","low",0.25,"油價影響航運燃油成本。","低權重。"),
    ]},
}


def related_us_assets_for_code(code: str) -> dict[str, Any]:
    code = str(code).zfill(4)[:4]
    return US_RELATION_MAP.get(code, {
        "category": "未分類",
        "note": "暫無明確高相關美股對照；系統不使用預設科技股模板，避免誤導。",
        "last_reviewed": None,
        "assets": [],
    })


def relation_coverage(codes: list[str]) -> dict[str, Any]:
    normalized = [str(c).zfill(4)[:4] for c in codes]
    mapped = set(US_RELATION_MAP.keys())
    return {
        "tw50_count": len(normalized),
        "mapped_current_count": len([c for c in normalized if c in mapped]),
        "missing": [c for c in normalized if c not in mapped],
        "extra_not_in_current_tw50": sorted([c for c in mapped if c not in set(normalized)]),
        "review_version": REVIEW_VERSION,
    }
