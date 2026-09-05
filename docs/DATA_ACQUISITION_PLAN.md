# Data Acquisition Plan

本表只列出未來可合法補齊「可靠融資成本」與「券商分點主力成本」所需資料。現階段不得用未授權來源、Goodinfo scraping、captcha bypass、Cloudflare bypass，亦不得用成交量殘差假裝主力買賣。

| 資料名稱 | 用途 | 是否免費 | 預估費用 | 官方/授權來源名稱 | 官方/授權來源 URL | 資料格式 | 是否支援720天歷史 | 是否可自動匯入 | 匯入器骨架檔名 | 目前狀態 | 下一步 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| financing_buy_amount | 估算當日融資新增買進金額與融資新增部位成本 | 待確認 | 待來源報價 | TWSE/TPEx 官方授權資料或券商授權檔 | 待使用者提供正式來源 | CSV/XLSX/ZIP | 待確認 | 是，需使用者提供合法檔案 | scripts/import_margin_financing_amount.py | missing_required_source | 取得官方或授權欄位後建立欄位 mapping |
| financing_loan_amount | 估算融資放款均額；不得由 margin_balance 替代 | 待確認 | 待來源報價 | TWSE/TPEx 官方授權資料或券商授權檔 | 待使用者提供正式來源 | CSV/XLSX/ZIP | 待確認 | 是，需使用者提供合法檔案 | scripts/import_margin_financing_amount.py | missing_required_source | 取得官方或授權欄位後建立欄位 mapping |
| financing_balance_amount | 搭配融資餘額股數估算可靠融資餘額金額成本 | 待確認 | 待來源報價 | TWSE/TPEx 官方授權資料或券商授權檔 | 待使用者提供正式來源 | CSV/XLSX/ZIP | 待確認 | 是，需使用者提供合法檔案 | scripts/import_margin_financing_amount.py | missing_required_source | 取得官方或授權欄位後建立欄位 mapping |
| broker_branch_buy_shares | 券商分點買進股數，用於主力分點成本 | 否或需授權 | 待來源報價 | 合法授權券商分點資料 | 待使用者提供正式來源 | CSV/XLSX/ZIP | 待確認 | 是，需使用者提供合法檔案 | scripts/import_broker_branch_trades.py | missing_required_source | 取得授權來源後建立欄位 mapping |
| broker_branch_sell_shares | 券商分點賣出股數，用於主力分點淨部位 | 否或需授權 | 待來源報價 | 合法授權券商分點資料 | 待使用者提供正式來源 | CSV/XLSX/ZIP | 待確認 | 是，需使用者提供合法檔案 | scripts/import_broker_branch_trades.py | missing_required_source | 取得授權來源後建立欄位 mapping |
| broker_branch_buy_amount | 券商分點買進金額，用於主力分點平均成本 | 否或需授權 | 待來源報價 | 合法授權券商分點資料 | 待使用者提供正式來源 | CSV/XLSX/ZIP | 待確認 | 是，需使用者提供合法檔案 | scripts/import_broker_branch_trades.py | missing_required_source | 取得授權來源後建立欄位 mapping |
| broker_branch_sell_amount | 券商分點賣出金額，用於主力分點分布判斷 | 否或需授權 | 待來源報價 | 合法授權券商分點資料 | 待使用者提供正式來源 | CSV/XLSX/ZIP | 待確認 | 是，需使用者提供合法檔案 | scripts/import_broker_branch_trades.py | missing_required_source | 取得授權來源後建立欄位 mapping |

## Current Free-Data Boundary

- 外資估算成本：可用 `institution_daily.foreign_net`、`history_price`、`foreign_shareholding` 做公開資料估算，最高 confidence 為 medium，不可稱為真實外資成本。
- 投信估算成本：可用 `institution_daily.trust_net` 與 `history_price` 做公開資料估算，缺投信持股校準，最高 confidence 為 medium。
- 融資新增部位估算成本：必須先確認 `margin_balance` 單位；單位未知時輸出 `unavailable / unit_unknown`。
- 可靠融資成本：需要 `financing_buy_amount`、`financing_loan_amount` 或 `financing_balance_amount`，目前只能輸出 `missing_required_source`。
- 主力參考成本區：只能使用既有 POC / price-volume profile 作 proxy，不是券商分點主力成本。
- 主力分點成本：需要券商分點買賣股數與買賣金額，目前只能輸出 `missing_required_source`。

