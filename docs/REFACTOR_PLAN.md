# REFACTOR_PLAN.md 鈥?鍒嗛殠娈甸噸妲嬭▓鐣?
> 鏈枃浠跺彧鍋氳鍔冿紝涓嶄唬琛ㄤ换浣曢殠娈靛凡闁嬪瀵︿綔銆? 
> 瀵︿綔浠讳綍闅庢鍓嶏紝浣跨敤鑰呭繀闋堟槑纰鸿锛氥€岄枊濮嬬 N 闅庢銆嶃€?
## 1. 閲嶆绺藉師鍓?
- 涓嶅仛 big-bang refactor銆?- 涓嶆敼璁婃棦鏈夋キ鍕欓倧杓€?- 涓嶆敼鍏紡銆佷笉鏀硅鍒嗛倧杓€佷笉鏀逛富绲愯珫瑕忓墖銆?- 姣忓€嬮殠娈甸兘蹇呴爤璁?app 鍙暉鍕曘€?- 姣忓€嬮殠娈甸兘蹇呴爤鑳界崹绔嬫脯瑭︺€?- 姣忓€嬮殠娈电殑銆岄枊濮嬪煼琛屻€嶉兘闇€瑕佷娇鐢ㄨ€呮槑纰鸿銆岄枊濮嬬 N 闅庢銆嶆墠鑳藉嫊鎵嬨€?- 涓嶅緱鑷寰?`REFACTOR_PLAN.md` 璺冲埌瀵︿綔銆?- 涓嶅緱涓€娆″煼琛屽鍊嬮殠娈点€?- 涓嶅緱鍦ㄨ▓鐣枃浠朵腑鎵胯宸插畬鎴愬皻鏈浣滅殑鏋舵銆?- 鏈璀夌殑鍏у蹇呴爤妯欒 `Unverified / Not found in current source`銆?
鐩墠渚?`docs/ARCHITECTURE.md` 鍙⒑瑾嶏細

- `review_src/app.py` 瀛樺湪锛岀磩 6001 琛岋紝鏄洰鍓嶆渶澶ф灦妲嬮ⅷ闅€?- `app.py` 娣峰悎 API route銆佽硣鏂欐姄鍙栥€丏B 鎿嶄綔銆佸垎鏋愰倧杓€乧ache銆乥ackground task銆?- GET route 鍙兘鏈夊壇浣滅敤锛屼緥濡?`enqueue_missing=True` 鑸囪儗鏅璩囨枡 thread銆?- `core/data_quality.py`: Unverified / Not found in current source銆?- `api/`銆乣service/`銆乣repository/`銆乣adapter/`銆乣analysis/`銆乣model/`銆乣schema/`銆乣task/`: Unverified / Not found in current source銆?
## 2. 姣忓€?Phase 鐨勫浐瀹氭牸寮?
姣忓€?Phase 閮藉繀闋堝寘鍚細

- 闅庢鍚嶇ū
- 鐩
- 鐐轰粈楹艰鍋?- 闋愯▓鏂板鎴栦慨鏀瑰摢浜涙獢妗?- 鏄庣⒑涓嶄慨鏀瑰摢浜涙キ鍕欓倧杓?- 鏈殠娈电禃灏嶄笉纰?- 棰ㄩ毆绛夌礆锛氫綆 / 涓?/ 楂?- 椹楄瓑鏂瑰紡
- 瀹屾垚妯欐簴
- 鍥炴痪鏂瑰紡

姣忓€?Phase 鐨勩€屾湰闅庢绲曞皪涓嶇銆嶈嚦灏戝寘鍚細

- scoring 鍏紡
- practical status / referee 涓荤祼璜栭倧杓?- RSI 鑸囨妧琛撴寚妯欒▓绠?- 鏀拹澹撳姏瑷堢畻
- 绫岀⒓鎴愭湰浼扮畻
- 鍒嗗児閲忚鍒?- 闅旀棩灞曟湜閭忚集
- API response 鏍煎紡
- 鏃㈡湁 endpoint 璺緫
- 璩囨枡搴?schema 鐮村鎬ц畩鏇?
## Phase 0锛氬缓绔嬪熀婧栭璀夎垏淇濊绶?
### 鐩

- 寤虹珛鍙噸瑜囧煼琛岀殑 smoke test 鍩烘簴銆?- 瑷橀寗鐩墠 app 鍟熷嫊鏂瑰紡銆?- 瑷橀寗鐩墠闂滈嵉 API 椹楄瓑鏂瑰紡銆?- 涓嶆敼浠讳綍妤嫏閭忚集銆?
### 鐐轰粈楹艰鍋?
鐩墠 `review_src/app.py` 绱?6001 琛岋紝寰岀簩浠讳綍鎷嗗垎閮芥湁鍥炴棰ㄩ毆銆傚厛寤虹珛鍩烘簴椹楄瓑锛岃兘閬垮厤鎷嗗垎寰屼笉鐭ラ亾鏄惁鐮村鍟熷嫊銆侀闋併€佸垪琛ㄦ垨瑭崇窗闋?API銆?
### 闋愯▓鏂板鎴栦慨鏀瑰摢浜涙獢妗?
- 闋愯▓鏂板 `docs/smoke_test.sh` 鎴栫瓑鍍硅叧鏈€?- 瑕?Windows 鍩疯闇€姹傦紝鍙湪鍚岄殠娈佃鍔?`docs/smoke_test.ps1`锛屼絾闇€浣跨敤鑰呮槑纰哄悓鎰忓緦鎵嶅缓绔嬨€?- 鍙洿鏂?`docs/WORKFLOW.md` 瑁滃厖濡備綍鍩疯 smoke test锛涜嫢瑕佷慨鏀癸紝闇€鍦ㄨ┎闅庢闁嬪鍓嶅啀娆＄⒑瑾嶃€?
### 鏄庣⒑涓嶄慨鏀瑰摢浜涙キ鍕欓倧杓?
- 涓嶄慨鏀逛换浣?Python 妤嫏閭忚集銆?- 涓嶄慨鏀逛换浣曞墠绔鐐恒€?- 涓嶄慨鏀逛换浣?API response銆?- 涓嶄慨鏀硅硣鏂欏韩 schema 鎴栬硣鏂欍€?
### 鏈殠娈电禃灏嶄笉纰?
- scoring 鍏紡
- practical status / referee 涓荤祼璜栭倧杓?- RSI 鑸囨妧琛撴寚妯欒▓绠?- 鏀拹澹撳姏瑷堢畻
- 绫岀⒓鎴愭湰浼扮畻
- 鍒嗗児閲忚鍒?- 闅旀棩灞曟湜閭忚集
- API response 鏍煎紡
- 鏃㈡湁 endpoint 璺緫
- 璩囨枡搴?schema 鐮村鎬ц畩鏇?
### 棰ㄩ毆绛夌礆

浣庛€?
### 椹楄瓑鏂瑰紡

Phase 0 鐨勮叧鏈嚦灏戣鍔冨寘鍚細

```bash
python -m py_compile review_src/app.py
```

涓﹁閷勬垨椹楄瓑锛?
- app 鍟熷嫊鏂瑰紡銆?- `GET /`
- `GET /api/quotes?mode=watchlist`
- `GET /api/quotes?mode=tw50`
- `GET /api/stock/{code}/detail`

鑻ユ煇浜?API 闇€瑕佺壒瀹?DB 璩囨枡銆乣.env`銆丗inMind/Fugle token 鎴栧競鍫存檪闁撴墠鑳介€氶亷锛屽繀闋堝湪鑵虫湰鎴栨枃浠朵腑妯欒ɑ銆?
### 瀹屾垚妯欐簴

- smoke test 鑵虫湰鍙噸瑜囧煼琛屻€?- 鑵虫湰涓嶄慨鏀?DB 璩囨枡銆?- 鑵虫湰鏄庣⒑妯欑ず闇€瑕佺殑鐠板姊濅欢銆?- 鑵虫湰澶辨晽鏅傝兘鐪嬪嚭鏄摢鍊?endpoint 鎴栨椹熷け鏁椼€?
### 鍥炴痪鏂瑰紡

- 鍒櫎鏈殠娈垫柊澧炵殑 smoke test 鑵虫湰銆?- 鑻ユ湁鏇存柊鏂囦欢锛屼娇鐢?patch 鍥炲京瑭叉枃浠惰畩鏇淬€?
## Phase 1锛歝ore + data_quality 楠ㄦ灦

### 鐩

- 寤虹珛鎴栨暣鐞?core 鍩虹灞ゃ€?- 寤虹珛 `core/data_quality.py` 鐨勬渶灏忛鏋躲€?- 鍙畾缇╄硣鏂欑媭鎱嬪瀷鍒ャ€佸父鏁歌垏 helper锛屼笉鎺ュ叆鐝炬湁鍒嗘瀽閭忚集銆?
### 鐐轰粈楹艰鍋?
`docs/ARCHITECTURE.md` 宸茬⒑瑾嶈硣鏂欏搧璩垽鏂风洰鍓嶆暎钀芥柤 `app.py`銆乣price_volume.py`銆乣chip_cost_engine.py`銆乣scoring.py`銆乣market/futures.py` 绛変綅缃€傚厛寤虹珛鍏辩敤閭婄晫锛屽緦绾屾墠鑳介€愭鏀舵杺锛屼笉鏈冨湪鍚勫垎鏋愭ā绲勫悇鑷櫦鏄庤硣鏂欏彲淇″害瑕忓墖銆?
### 闋愯▓鏂板鎴栦慨鏀瑰摢浜涙獢妗?
- 鏂板 `review_src/core/data_quality.py`
- 瑕栭渶瑕佹柊澧炴渶灏忔脯瑭︽垨鏂囦欢瑷昏В锛屼絾涓嶆帴鍏ョ従鏈夋祦绋嬨€?
### 鏄庣⒑涓嶄慨鏀瑰摢浜涙キ鍕欓倧杓?
- 涓嶆惉绉荤従鏈夎硣鏂欏搧璩垽鏂枫€?- 涓嶆敼鐝炬湁鍒嗘瀽绲愭灉銆?- 涓嶆敼 scoring銆?- 涓嶆敼 API response銆?- 涓嶆敼 DB schema銆?
### 鏈殠娈电禃灏嶄笉纰?
- scoring 鍏紡
- practical status / referee 涓荤祼璜栭倧杓?- RSI 鑸囨妧琛撴寚妯欒▓绠?- 鏀拹澹撳姏瑷堢畻
- 绫岀⒓鎴愭湰浼扮畻
- 鍒嗗児閲忚鍒?- 闅旀棩灞曟湜閭忚集
- API response 鏍煎紡
- 鏃㈡湁 endpoint 璺緫
- 璩囨枡搴?schema 鐮村鎬ц畩鏇?
### 棰ㄩ毆绛夌礆

浣庛€?
### 椹楄瓑鏂瑰紡

- `python -m py_compile review_src/core/data_quality.py`
- 鑻ユ湁淇敼 import 鐩搁棞妾旀锛屽繀闋堝悓姝?py_compile 琚慨鏀圭殑 Python 妾斻€?- app 鍟熷嫊 smoke test 浠嶉渶閫氶亷銆?
### 瀹屾垚妯欐簴

- `core/data_quality.py` 瀛樺湪銆?- 鑷冲皯瀹氱京鐙€鎱嬶細`ok`銆乣stale`銆乣source_delayed`銆乣estimated`銆乣unavailable`銆乣missing`銆?- 娌掓湁浠讳綍鐝炬湁 API response 鎴栧垎鏋愮祼鏋滄敼璁娿€?
### 鍥炴痪鏂瑰紡

- 鍒櫎 `review_src/core/data_quality.py`銆?- 鍥炲京浠讳綍鍚岄殠娈垫枃浠舵垨 import 璁婃洿銆?
## Phase 2锛欸ET 鍓綔鐢ㄦ竻鐞?
### 鐩

- 鎵惧嚭 GET route 鍏х殑 `enqueue_missing=True`銆乣background_tasks`銆乣asyncio.create_task`銆乣threading.Thread` 绛夊壇浣滅敤銆?- 灏囧鍏ユ垨鎺掔▼琛岀偤绉诲嚭璁€鍙栬矾寰戙€?- 闇€瑕佽璩囨枡鏅傦紝鏀圭敱 `task/`銆佹槑纰?POST endpoint銆佹槑纰?repair/update endpoint 铏曠悊銆?- GET 缂鸿硣鏂欐檪鍙洖鍌?`unavailable` / `stale` / `source_delayed` / `estimated`锛屼笉瑙哥櫦瑁滆硣鏂欍€?
### 鐐轰粈楹艰鍋?
`docs/ARCHITECTURE.md` 宸茬⒑瑾?GET route 鏈夊壇浣滅敤棰ㄩ毆锛屼緥濡傦細

- `review_src/app.py:5824`
- `review_src/app.py:6434`
- `review_src/app.py:6276`
- `review_src/app.py:6280`
- `review_src/app.py:6466`

閫欐渻璁撹畝鍙?API 閫犳垚瀵叆鎴栬儗鏅璩囨枡锛岃垏 AGENTS.md 鐨?GET read-only 瑕忓墖琛濈獊銆?
### 闋愯▓鏂板鎴栦慨鏀瑰摢浜涙獢妗?
- `review_src/app.py`
- 鍙兘鏂板 `review_src/task/` 鎴栫瓑鍍逛綅缃紝浣嗚嫢瑭茬洰閷勪粛鏈缓绔嬶紝闇€鍙仛鏈€灏忓繀瑕佹媶鍒嗐€?- 鍙洿鏂?smoke test 鑵虫湰椹楄瓑 GET 涓嶈Ц鐧间慨寰╀换鍕欍€?
### 鏄庣⒑涓嶄慨鏀瑰摢浜涙キ鍕欓倧杓?
- 涓嶆敼鍒嗘瀽鍏紡銆?- 涓嶆敼涓荤祼璜栭倧杓€?- 涓嶆敼鏃㈡湁 endpoint 璺緫銆?- 涓嶆敼 response 娆勪綅绲愭锛岄櫎闈炲彧鏄洿鏄庣⒑妯欑ず缂鸿硣鏂欑媭鎱嬨€?
### 鏈殠娈电禃灏嶄笉纰?
- scoring 鍏紡
- practical status / referee 涓荤祼璜栭倧杓?- RSI 鑸囨妧琛撴寚妯欒▓绠?- 鏀拹澹撳姏瑷堢畻
- 绫岀⒓鎴愭湰浼扮畻
- 鍒嗗児閲忚鍒?- 闅旀棩灞曟湜閭忚集
- API response 鏍煎紡
- 鏃㈡湁 endpoint 璺緫
- 璩囨枡搴?schema 鐮村鎬ц畩鏇?
### 棰ㄩ毆绛夌礆

涓€?
### 椹楄瓑鏂瑰紡

- `python -m py_compile review_src/app.py`
- 鍩疯 Phase 0 smoke test銆?- 鍛煎彨 `GET /api/quotes?mode=watchlist`銆乣GET /api/quotes?mode=tw50`銆乣GET /api/stock/{code}/detail` 寰岋紝纰鸿獚涓嶆渻鍟熷嫊鏂扮殑 repair/background thread銆?- 纰鸿獚鏄庣⒑ POST update endpoint 浠嶈兘鍟熷嫊鏇存柊浠诲嫏銆?
### 瀹屾垚妯欐簴

- GET route 涓嶅啀浣跨敤 `enqueue_missing=True`銆?- GET route 涓嶇洿鎺ュ暉鍕?`threading.Thread`銆乣asyncio.create_task` 鎴栬硣鏂欎慨寰╂帓绋嬨€?- 缂鸿硣鏂欐檪鍥炲偝娓呮鐙€鎱嬶紝涓嶅亣閫犳暩瀛椼€?- POST update / repair endpoint 琛岀偤淇濈暀銆?
### 鍥炴痪鏂瑰紡

- 閭勫師鏈殠娈靛皪 `review_src/app.py` 鎴?`task/` 鐨?patch銆?- 鎭㈠京鍘熸湰 `enqueue_missing` 鍛煎彨浣嶇疆銆?
## Phase 3锛歛dapter 璩囨枡渚嗘簮闅旈洟

### 鐩

- MIS銆乀WSE銆丗inMind銆丗ugle銆乊ahoo銆乀AIFEX 绛夊閮ㄨ硣鏂欎締婧愰€愭鎶介洟銆?- adapter 鍙矤璨姄鍙栬垏鏍煎紡姝ｈ鍖栥€?- adapter 涓嶅仛瑭曞垎銆佷笉鍋?API response shaping銆?
### Phase 3B-2 status note

- `review_src/us_relations.py` was reviewed as a local/static data-source
  adapter candidate.
- Decision: do not create `review_src/adapter/us_relations.py`.
- Reason: `review_src/us_relations.py` already behaves as a clean read-only
  static-data module with `US_RELATION_MAP`, `related_us_assets_for_code()`, and
  `relation_coverage()`.
- It does not call external APIs, read/write DB, write cache, call
  `set_status()`, import FastAPI/app.py, or start background work.
- Creating another wrapper would add an unnecessary import layer without reducing
  coupling.

### 鐐轰粈楹艰鍋?
`app.py` 鐩墠娣锋湁澶氬€嬭硣鏂欐簮 adapter-like code锛屼緥濡?MIS銆乀WSE銆丗ugle銆丗inMind銆乊ahoo锛汿AIFEX 宸查儴鍒嗗湪 `market/futures.py`銆傛娊闆?adapter 鑳借畵璩囨枡婧愯畩鍕曟檪涓嶇壗鍕?route 鑸囧垎鏋愰倧杓€?
### 闋愯▓鏂板鎴栦慨鏀瑰摢浜涙獢妗?
- 鍙兘鏂板锛?  - `review_src/adapter/mis.py`
  - `review_src/adapter/twse.py`
  - `review_src/adapter/finmind.py`
  - `review_src/adapter/fugle.py`
  - `review_src/adapter/yahoo.py`
  - `review_src/adapter/taifex.py`
- 鎴栬嫢姹哄畾娌跨敤鐝炬湁鍛藉悕锛屽彲浣跨敤绛夊児璩囨枡渚嗘簮鐩寗锛涚洰鍓?`adapter/`: Unverified / Not found in current source銆?- 淇敼 `review_src/app.py` 鐨?import 鑸囧懠鍙綅缃€?
### 鏄庣⒑涓嶄慨鏀瑰摢浜涙キ鍕欓倧杓?
- 涓嶆敼璩囨枡婧愬劒鍏堥爢搴忋€?- 涓嶆敼娆勪綅瑷堢畻鍏紡銆?- 涓嶆敼璩囨枡瀵叆 schema銆?- 涓嶆敼 API response銆?
### 鏈殠娈电禃灏嶄笉纰?
- scoring 鍏紡
- practical status / referee 涓荤祼璜栭倧杓?- RSI 鑸囨妧琛撴寚妯欒▓绠?- 鏀拹澹撳姏瑷堢畻
- 绫岀⒓鎴愭湰浼扮畻
- 鍒嗗児閲忚鍒?- 闅旀棩灞曟湜閭忚集
- API response 鏍煎紡
- 鏃㈡湁 endpoint 璺緫
- 璩囨枡搴?schema 鐮村鎬ц畩鏇?
### 棰ㄩ毆绛夌礆

涓埌楂橈紝瑕栨瘡娆℃娊闆㈢瘎鍦嶈€屽畾銆傛瘡娆″彧鑳芥娊涓€鍊嬭硣鏂欐簮銆?
### 椹楄瓑鏂瑰紡

- 灏嶆瘡鍊嬭鎶藉嚭鐨?adapter 鍩疯 `py_compile`銆?- 鍩疯 Phase 0 smoke test銆?- 鑻ユ娊 MIS锛岄璀夎嚜閬歌偂 quote 涓嶈烦 source銆?- 鑻ユ娊 TWSE/FinMind/Yahoo锛岄璀?readiness 鑸囨洿鏂?POST endpoint銆?- 鑻ユ娊 Fugle/price-volume锛岄璀?`/api/debug/price-volume/{code}`銆?- 鑻ユ娊 TAIFEX锛岄璀?detail 闋?futures block 浠嶈兘鍥炲偝鐩稿悓娆勪綅銆?
### 瀹屾垚妯欐簴

- 姣忔鎶介洟寰?app 浠嶅彲鍟熷嫊銆?- 鎶介洟鍓嶅緦 API response schema 涓嶈畩銆?- 鎶介洟鍓嶅緦璩囨枡婧愬懠鍙鐐轰笉璁娿€?- `app.py` 琛屾暩閫愭涓嬮檷銆?
### 鍥炴痪鏂瑰紡

- 鍥炲京瑭茶硣鏂欐簮鎶介洟 patch銆?- 鍒櫎瑭查殠娈垫柊澧?adapter 妾斻€?- 灏?import 鑸囧懠鍙仮寰╁埌 `app.py` 鍘熶綅缃€?
## Phase 4锛歳epository DB 璁€瀵殧闆?
### 鐩

- 鎶?DB reads/writes 寰?`app.py` 涓€愭鎶藉嚭銆?- repository 鍙檿鐞嗚硣鏂欏韩璁€瀵€?- repository 涓嶅仛缍茶矾鍛煎彨銆佷笉鍋?UI formatting銆佷笉鍋氳鍒嗐€?
### 鐐轰粈楹艰鍋?
鐩墠 `app.py` 鐨勮硣鏂欐姄鍙栥€乺ow 绲勮銆佸垎鏋愯垏 API route 閮界洿鎺ユ帴瑙?DB銆傛媶鍑?repository 鑳介檷浣?DB schema 鑸囨キ鍕欓倧杓簰鐩歌€﹀悎锛屼篃鑳芥敼鍠勬湭渚?SQLite lock / transaction 绠＄悊銆?
### 闋愯▓鏂板鎴栦慨鏀瑰摢浜涙獢妗?
- 鍙兘鏂板锛?  - `review_src/repository/watchlist_repo.py`
  - `review_src/repository/history_repo.py`
  - `review_src/repository/valuation_repo.py`
  - `review_src/repository/institution_repo.py`
  - `review_src/repository/price_volume_repo.py`
  - `review_src/repository/status_repo.py`
- 淇敼 `review_src/app.py` 浣跨敤 repository銆?- 鐩墠 `repository/`: Unverified / Not found in current source銆?
### 鏄庣⒑涓嶄慨鏀瑰摢浜涙キ鍕欓倧杓?
- 涓嶆敼 SQL 绲愭灉瑾炴剰銆?- 涓嶆敼 API response銆?- 涓嶆敼璩囨枡琛ㄦ瑒浣嶃€?- 涓嶆敼璩囨枡婧愭洿鏂伴爢搴忋€?
### 鏈殠娈电禃灏嶄笉纰?
- scoring 鍏紡
- practical status / referee 涓荤祼璜栭倧杓?- RSI 鑸囨妧琛撴寚妯欒▓绠?- 鏀拹澹撳姏瑷堢畻
- 绫岀⒓鎴愭湰浼扮畻
- 鍒嗗児閲忚鍒?- 闅旀棩灞曟湜閭忚集
- API response 鏍煎紡
- 鏃㈡湁 endpoint 璺緫
- 璩囨枡搴?schema 鐮村鎬ц畩鏇?
### 棰ㄩ毆绛夌礆

涓€?
### 椹楄瓑鏂瑰紡

- `py_compile` 鎵€鏈夋柊澧?repository 鑸囪淇敼 Python 妾斻€?- Phase 0 smoke test銆?- 閲濆皪 watchlist銆乹uotes銆乨etail銆乨ebug readiness endpoint 姣斿皪娆勪綅鏄惁缍寔銆?
### 瀹屾垚妯欐簴

- DB access 閫愭绉诲嚭 `app.py`銆?- repository 鐒＄恫璺懠鍙€?- repository 鐒?UI formatting銆?- app 琛岀偤涓嶈畩銆?
### 鍥炴痪鏂瑰紡

- 鍥炲京 repository 鎺ュ叆 patch銆?- 鍒櫎瑭查殠娈垫柊澧?repository 妾斻€?- 鎭㈠京 `app.py` 鍘?SQL 鏌ヨ銆?
## Phase 5锛歛nalysis 绱旇▓绠楁暣鐞?
### 鐩

- 鏁寸悊 scoring銆乸ractical status / referee銆丷SI銆佹妧琛撴寚妯欍€佹敮鎾愬鍔涖€佺睂纰兼垚鏈€佸垎鍍归噺銆佹湡璨ㄥ鐩よ▕铏熴€侀殧鏃ュ睍鏈涖€?- 鍏堟惉绉昏垏灏佽锛屼笉鏀瑰叕寮忋€佷笉鏀规瑠閲嶃€佷笉鏀逛富绲愯珫瑕忓墖銆?
### 鐐轰粈楹艰鍋?
鐩墠閮ㄥ垎鍒嗘瀽宸叉湁鐛ㄧ珛妾旀锛屼緥濡?`scoring.py`銆乣chip_cost_engine.py`銆乣price_volume.py`銆乣market/futures.py`锛涗絾 practical status銆佹敮鎾愬鍔涖€侀殧鏃ュ睍鏈涜垏閮ㄥ垎鎴愭湰 orchestration 浠嶉泦涓湪 `app.py`銆?
### 闋愯▓鏂板鎴栦慨鏀瑰摢浜涙獢妗?
- 鍙兘鏂板锛?  - `review_src/analysis/practical_status.py`
  - `review_src/analysis/support_resistance.py`
  - `review_src/analysis/next_day_outlook.py`
  - `review_src/analysis/chip_factors.py`
- 鍙兘瑾挎暣鏃㈡湁锛?  - `review_src/scoring.py`
  - `review_src/chip_cost_engine.py`
  - `review_src/price_volume.py`
  - `review_src/market/futures.py`
- 鐩墠 `analysis/`: Unverified / Not found in current source銆?
### 鏄庣⒑涓嶄慨鏀瑰摢浜涙キ鍕欓倧杓?
- 涓嶆敼浠讳綍鍏紡銆?- 涓嶆敼娆婇噸銆?- 涓嶆敼 status 鍒嗘敮姊濅欢銆?- 涓嶆敼涓荤祼璜栦締婧愩€?- 涓嶆敼娆勪綅鍚嶇ū鎴?API response銆?
### 鏈殠娈电禃灏嶄笉纰?
- scoring 鍏紡
- practical status / referee 涓荤祼璜栭倧杓?- RSI 鑸囨妧琛撴寚妯欒▓绠?- 鏀拹澹撳姏瑷堢畻
- 绫岀⒓鎴愭湰浼扮畻
- 鍒嗗児閲忚鍒?- 闅旀棩灞曟湜閭忚集
- API response 鏍煎紡
- 鏃㈡湁 endpoint 璺緫
- 璩囨枡搴?schema 鐮村鎬ц畩鏇?
### 棰ㄩ毆绛夌礆

楂樸€傚垎鏋愰倧杓槸鏍稿績閲戣瀺鍒ゆ柗锛屽繀闋堜竴娆″彧鎼竴鍊嬬磾鍑藉紡鎴栦竴灏忕祫寮风浉闂滃嚱寮忋€?
### 椹楄瓑鏂瑰紡

- `py_compile` 淇敼妾斻€?- Phase 0 smoke test銆?- 灏嶆寚瀹氳偂绁ㄦ娊妯ｏ紝姣斿皪閲嶆鍓嶅緦锛?  - `main_status`
  - RSI
  - 鏀拹 / 璩ｅ
  - 绫岀⒓鎴愭湰娆勪綅
  - 鍒嗗児閲?status / score metadata
  - 闅旀棩灞曟湜娆勪綅
- 姣斿皪鏅傚彧鎺ュ彈瀹屽叏绛夊児鎴栨槑纰烘瑷樼殑鏍煎紡宸暟銆?
### 瀹屾垚妯欐簴

- 鎼Щ寰屽悓涓€绲勮几鍏ュ緱鍒板悓涓€绲勮几鍑恒€?- API response 涓嶈畩銆?- `app.py` 鍙繚鐣?orchestration 鍛煎彨锛屼笉鍐嶄繚鐣欏ぇ娈电磾瑷堢畻銆?
### 鍥炴痪鏂瑰紡

- 鍥炲京瑭插垎鏋愭ā绲勬惉绉?patch銆?- 鍒櫎鏂板 `analysis/` 妾旀銆?- 鎭㈠京 `app.py` 鍘熷嚱寮忋€?
## Phase 6锛歴ervice 鐢ㄤ緥绶ㄦ帓

### 鐩

- 寤虹珛 build stock row銆乥uild stock detail銆乨ata readiness銆乺epair orchestration 绛?service銆?- service 璨犺铂涓?repository / adapter / analysis銆?- service 涓嶇洿鎺ヨ檿鐞?HTTP request / response銆?
### 鐐轰粈楹艰鍋?
鐩墠 `_build_row_uncached()`銆乣api_stock_detail()`銆乣data_readiness_for_items()` 绛夊嚱寮忓湪 `app.py` 涓贩鍚堟煡 DB銆佽窇鍒嗘瀽銆佺祫 response銆佽Ц鐧艰璩囨枡銆俿ervice 灞ゅ彲鎶?use case 绶ㄦ帓闆嗕腑锛岃畵 route 璁婅杽銆?
### 闋愯▓鏂板鎴栦慨鏀瑰摢浜涙獢妗?
- 鍙兘鏂板锛?  - `review_src/service/quote_service.py`
  - `review_src/service/detail_service.py`
  - `review_src/service/readiness_service.py`
  - `review_src/service/repair_service.py`
- 淇敼 `review_src/app.py` 鏀瑰懠鍙?service銆?- 鐩墠 `service/`: Unverified / Not found in current source銆?
### 鏄庣⒑涓嶄慨鏀瑰摢浜涙キ鍕欓倧杓?
- 涓嶆敼 API response銆?- 涓嶆敼 endpoint 璺緫銆?- 涓嶆敼璩囨枡渚嗘簮闋嗗簭銆?- 涓嶆敼鍒嗘瀽鍏紡鎴栦富绲愯珫銆?
### 鏈殠娈电禃灏嶄笉纰?
- scoring 鍏紡
- practical status / referee 涓荤祼璜栭倧杓?- RSI 鑸囨妧琛撴寚妯欒▓绠?- 鏀拹澹撳姏瑷堢畻
- 绫岀⒓鎴愭湰浼扮畻
- 鍒嗗児閲忚鍒?- 闅旀棩灞曟湜閭忚集
- API response 鏍煎紡
- 鏃㈡湁 endpoint 璺緫
- 璩囨枡搴?schema 鐮村鎬ц畩鏇?
### 棰ㄩ毆绛夌礆

涓埌楂樸€?
### 椹楄瓑鏂瑰紡

- `py_compile` 鏂板 service 鑸囦慨鏀规獢妗堛€?- Phase 0 smoke test銆?- 姣斿皪 list/detail JSON top-level keys銆?- 纰鸿獚 GET route 涓嶅啀瑙哥櫦瑁滆硣鏂欏壇浣滅敤銆?
### 瀹屾垚妯欐簴

- route 灞ゅ彧鍋?request 鍙冩暩瑙ｆ瀽鑸?service 鍛煎彨銆?- service 涓嶇洿鎺?import frontend 鎴?route object銆?- app 琛岀偤涓嶈畩銆?
### 鍥炴痪鏂瑰紡

- 鍥炲京 service 鎺ュ叆 patch銆?- 鍒櫎鏂板 service 妾斻€?- 鎭㈠京 `app.py` 鍘?orchestration銆?
## Phase 7锛欰PI routes 鏈€寰屾媶鍒?
### 鐩

- 鏈€寰屾墠绉诲嫊 route銆?- 涓嶆敼 API response 鏍煎紡銆?- 涓嶆敼 endpoint 璺緫銆?- 涓嶆敼涓荤祼璜栨瑒浣嶄締婧愩€?
### 鐐轰粈楹艰鍋?
route 鏄閮ㄤ娇鐢ㄨ€呰垏鍓嶇渚濊炒鐨勯倞鐣屻€傜瓑 adapter銆乺epository銆乤nalysis銆乻ervice 閮界┅瀹氬緦鍐嶆惉 route锛屽彲闄嶄綆涓€娆℃€х牬澹為ⅷ闅€?
### 闋愯▓鏂板鎴栦慨鏀瑰摢浜涙獢妗?
- 鍙兘鏂板锛?  - `review_src/api/pages.py`
  - `review_src/api/config.py`
  - `review_src/api/watchlist.py`
  - `review_src/api/quotes.py`
  - `review_src/api/stock_detail.py`
  - `review_src/api/update.py`
  - `review_src/api/debug.py`
- 淇敼 `review_src/app.py` 鍙繚鐣?lifespan銆乻tatic mount銆乺outer include銆?- 鐩墠 `api/`: Unverified / Not found in current source銆?
### 鏄庣⒑涓嶄慨鏀瑰摢浜涙キ鍕欓倧杓?
- 涓嶆敼 endpoint path銆?- 涓嶆敼 method銆?- 涓嶆敼 response schema銆?- 涓嶆敼涓荤祼璜栨瑒浣嶄締婧愩€?- 涓嶆敼璩囨枡鎶撳彇鑸囧垎鏋愭祦绋嬨€?
### 鏈殠娈电禃灏嶄笉纰?
- scoring 鍏紡
- practical status / referee 涓荤祼璜栭倧杓?- RSI 鑸囨妧琛撴寚妯欒▓绠?- 鏀拹澹撳姏瑷堢畻
- 绫岀⒓鎴愭湰浼扮畻
- 鍒嗗児閲忚鍒?- 闅旀棩灞曟湜閭忚集
- API response 鏍煎紡
- 鏃㈡湁 endpoint 璺緫
- 璩囨枡搴?schema 鐮村鎬ц畩鏇?
### 棰ㄩ毆绛夌礆

涓€?
### 椹楄瓑鏂瑰紡

- `py_compile` 鎵€鏈夋柊澧?route 妾旇垏 `app.py`銆?- Phase 0 smoke test銆?- 姣斿皪鎵€鏈夊凡鍒楁柤 `docs/ARCHITECTURE.md` 鐨?endpoint 鏄惁浠嶅瓨鍦ㄣ€?- 鍓嶇 `static/index.html` 鑸?`static/detail.html` 鍘?API 鍛煎彨涓嶉渶淇敼銆?
### 瀹屾垚妯欐簴

- `app.py` 鏄庨’绺皬锛屽彧淇濈暀 app 寤虹珛銆乴ifespan銆乻tatic mount銆乺outer include銆?- 鏃㈡湁 endpoint 瀹屽叏淇濈暀銆?- response schema 涓嶈畩銆?
### 鍥炴痪鏂瑰紡

- 鍥炲京 route 鎷嗗垎 patch銆?- 鍒櫎鏂板 `api/` 妾旀銆?- 鎭㈠京 route 鍒?`app.py`銆?
## 4. 绗竴闅庢寤鸿

### 寤鸿绗竴鍊嬪浣滈殠娈?
寤鸿绗竴鍊嬪浣滈殠娈垫槸 **Phase 0锛氬缓绔嬪熀婧栭璀夎垏淇濊绶?*銆?
### 鐐轰粈楹煎厛鍋氬畠

寰岀簩鎵€鏈夐殠娈甸兘鏈冪鍒?`review_src/app.py` 鎴栧叾渚濊炒銆傜従鍦?`app.py` 绱?6001 琛屼笖璨换娣烽洔锛屽鏋滄矑鏈?smoke test 鍩烘簴锛屽緢瀹规槗鍦ㄦ媶鍒嗛亷绋嬩腑鐮村棣栭爜銆佸垪琛ㄣ€乨etail API 鎴?app 鍟熷嫊鑰屾矑鏈夌珛鍗崇櫦鐝俱€?
### 棰ㄩ毆绛夌礆

浣庛€?
### 鏈冧慨鏀瑰摢浜涙獢妗?
Phase 0 闋愯▓鏂板锛?
- `docs/smoke_test.sh` 鎴栫瓑鍍硅叧鏈?
鍙兘瑕?Windows 鍩疯闇€姹傝鍏咃細

- `docs/smoke_test.ps1`

鏄惁寤虹珛 `.sh`銆乣.ps1` 鎴栧叐鑰咃紝閮藉繀闋堢瓑浣跨敤鑰呮槑纰鸿銆岄枊濮嬬 0 闅庢銆嶅緦鍐嶆焙瀹氳垏瀵︿綔銆?
### 涓嶆渻淇敼鍝簺閭忚集

- 涓嶄慨鏀?scoring銆?- 涓嶄慨鏀?practical status / referee銆?- 涓嶄慨鏀?RSI銆?- 涓嶄慨鏀规敮鎾愬鍔涖€?- 涓嶄慨鏀圭睂纰兼垚鏈€?- 涓嶄慨鏀瑰垎鍍归噺銆?- 涓嶄慨鏀归殧鏃ュ睍鏈涖€?- 涓嶄慨鏀?API response銆?- 涓嶄慨鏀?DB schema銆?
### 椹楄瓑鏂瑰紡

Phase 0 鑷韩瀹屾垚寰岋紝鍩疯 smoke test 鑵虫湰锛岃嚦灏戦璀夛細

- `python -m py_compile review_src/app.py`
- app 鍟熷嫊鏂瑰紡鍙敤
- `GET /`
- `GET /api/quotes?mode=watchlist`
- `GET /api/quotes?mode=tw50`
- `GET /api/stock/{code}/detail`

### 鍥炴痪鏂瑰紡

- 鍒櫎 Phase 0 鏂板鐨?smoke test 鑵虫湰銆?- 鑻ユ湁鏇存柊杓斿姪鏂囦欢锛屽洖寰╄┎鏂囦欢 patch銆?
## Phase 2 completion addendum

- Status: Completed
- Completion date: 2026-06-12
- Completion summary:
  - `GET /api/quotes` is read-only for high-risk side effects: no repair queue, no background repair, no `mis_quote` status write.
  - `GET /api/stock/{code}/detail` is read-only for high-risk side effects: no enqueue repair, no persisted `next_day_outlook`, no `stock_state_history` write through `build_row()`.
  - `GET /api/debug/price-volume/{code}` is read-only and only reads persisted price-volume score/profile diagnostics.
  - `GET /api/debug/data-readiness?auto_repair=1` does not execute repair and returns read-only metadata plus explicit repair endpoint recommendations.
  - `GET /api/debug/ui-completeness` avoids `save_stock_state()` by using `build_row(..., persist_state=False)`.
- Known residual low / medium risks:
  - In-memory memoization remains: `_row_cache`, `_score_cache`, `_practical_cache`, `yfinance_quote()` cache, and TAIFEX / futures cache.
  - These are not DB write / repair queue / background repair side effects and should be reassessed during Phase 5 / Phase 6.
- Next phase recommendation:
  - Phase 3 adapter data-source isolation.
  - Do not start Phase 3 until the user explicitly says `闁嬪绗?3 闅庢`.
## Phase 3 completion addendum

- Status: Completed for low-risk adapter isolation samples.
- Completion date: 2026-06-12.
- Completion summary:
  - Phase 3A source audit completed in `docs/DATA_SOURCE_ADAPTER_AUDIT.md`.
  - Phase 3B-1 extracted the Yahoo / yfinance read-only US quote path into
    `review_src/adapter/yahoo.py`.
  - Phase 3B-2 confirmed `review_src/us_relations.py` is already an
    adapter-like read-only static-data module; no wrapper was created.
- Known unhandled sources:
  - MIS.
  - TWSE.
  - FinMind.
  - Fugle.
  - TAIFEX.
  - Price-volume profile/update paths.
- Reason unhandled sources remain:
  - These sources are higher risk and may affect main quote values, DB writes,
    data repair flows, futures night signals, next-day outlook, or the final
    user-facing conclusion.
  - They should be handled later as separate small phases, not as part of the
    low-risk sample adapter work.
- Next recommended phase:
  - Phase 4A: repository DB read/write audit.
  - Do not start Phase 4A until the user explicitly says `闁嬪 Phase 4A` or an
    equivalent instruction.

## Phase 4A completion addendum

- Status: Completed as documentation-only audit.
- Completion date: 2026-06-12.
- Audit file: `docs/DB_REPOSITORY_AUDIT.md`.
- Completion summary:
  - Audited DB schema definitions in `review_src/core/db.py` and auth schema in
    `review_src/auth/models.py`.
  - Audited primary DB read/write locations across `review_src/app.py`,
    `review_src/core/status.py`, and `review_src/auth/service.py`.
  - Audited `fetch_status` specially, including schema, `set_status()`,
    `get_status()`, import location, call-site counts, and current GET
    interaction.
  - Confirmed `review_src/repository/` is not present in current source.
- Key finding:
  - `fetch_status` is the safest first repository extraction candidate because
    DB access is already wrapped by `review_src/core/status.py` and does not own
    scoring, practical-status, RSI, support/resistance, chip-cost,
    price-volume, or next-day outlook formulas.
- Next recommended phase:
  - Phase 4B: extract `fetch_status` DB access behind a status repository while
    preserving the existing `set_status()` / `get_status()` public functions.
  - Do not start Phase 4B until the user explicitly says `闁嬪 Phase 4B` or an
    equivalent instruction.
## Phase 4B completion addendum

- Status: Completed.
- Completion date: 2026-06-12.
- Completion summary:
  - Added `review_src/repository/__init__.py`.
  - Added `review_src/repository/status_repository.py`.
  - Updated `review_src/core/status.py` so the existing `set_status()` and
    `get_status()` public functions delegate fetch-status SQL to the repository.
  - Preserved all `review_src/app.py` call sites; `app.py` still imports only
    `get_status` and `set_status` from `core.status`.
- Explicitly unchanged:
  - `review_src/app.py`.
  - API response format.
  - endpoint paths.
  - DB schema and `fetch_status` schema.
  - scoring formulas.
  - practical status / referee logic.
  - RSI and technical indicators.
  - support/resistance calculations.
  - chip-cost estimates.
  - price-volume scoring.
  - next-day outlook logic.
- Validation:
  - `python -m py_compile review_src/core/status.py review_src/repository/status_repository.py review_src/repository/__init__.py` passed.
  - `python -m py_compile review_src/core/db.py review_src/app.py` passed.
  - Phase 0 smoke test passed after starting a local server.
  - Focused `GET /api/status` returned HTTP 200 and included `statuses`.
- Next recommended phase:
  - Phase 4C: consider extracting a `watchlist` repository.
  - Do not start Phase 4C until the user explicitly says `開始 Phase 4C` or an
    equivalent instruction.

## Phase 4C completion addendum

- Status: Completed.
- Completion date: 2026-06-12.
- Completion summary:
  - Added `review_src/repository/watchlist_repository.py`.
  - Updated `review_src/app.py` to delegate global `watchlist` DB reads/writes
    to the repository.
  - Confirmed `app.py` uses the global `watchlist` table while auth uses the
    separate `user_watchlist` table.
  - Did not modify `review_src/auth/*`.
- Explicitly unchanged:
  - DB schema.
  - `watchlist` table schema.
  - `user_watchlist` auth flow.
  - API response format.
  - endpoint paths.
  - scoring formulas.
  - practical status / referee logic.
  - RSI and technical indicators.
  - support/resistance calculations.
  - chip-cost estimates.
  - price-volume scoring.
  - next-day outlook logic.
- Validation:
  - `python -m py_compile review_src/app.py review_src/repository/watchlist_repository.py review_src/repository/__init__.py` passed.
  - Phase 0 smoke test passed after starting a local server.
  - Focused `GET /api/watchlist` returned HTTP 200 and parseable JSON.
  - Focused `GET /api/quotes?mode=watchlist` returned HTTP 200 and parseable JSON.
- Next recommended phase:
  - Phase 4D: choose the next repository extraction only after a separate audit
    and user approval.
  - Do not start Phase 4D until the user explicitly says `開始 Phase 4D` or an
    equivalent instruction.

## Phase 4D completion addendum

- Status: Completed as documentation-only candidate review.
- Completion date: 2026-06-12.
- Completion summary:
  - Reviewed the next repository extraction candidates after completed
    `fetch_status` and global `watchlist` repositories.
  - Updated `docs/DB_REPOSITORY_AUDIT.md` with a Phase 4D candidate matrix.
  - Specifically assessed `mis_quote_snapshot`, including its schema,
    high-frequency daemon writes, in-memory cache, and quote freshness impact.
  - Confirmed `review_src/auth` exists and included `user_watchlist` in the
    assessment as a separate auth/security path.
- Key conclusion:
  - No remaining candidate is as low-risk as `fetch_status` or global
    `watchlist`.
  - `mis_quote_snapshot` is high risk and should not be the Phase 4E target.
  - The least risky remaining practical candidate is `valuation_repository`,
    but it is still medium risk because valuation rows affect valuation display,
    readiness, and practical-status risk tags.
- Phase 4E recommendation:
  - If the user accepts a medium-risk scoped extraction, Phase 4E can target
    `valuation_repository`.
  - If the user requires strictly low-risk repository work, pause Phase 4
    repository extraction and switch to feature work or a new audit.
- Do not start Phase 4E until the user explicitly says `開始 Phase 4E` or an
  equivalent instruction.
