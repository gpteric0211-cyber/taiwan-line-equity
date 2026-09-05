# Data Quality Gate Regression Report

## Summary

* generated_at: `2026-06-19T19:14:29+08:00`
* base_url: `http://127.0.0.1:8057`
* total codes: `5`
* with_formal: `5`
* missing: `0`
* warn: `0`
* fail: `0`

## Coverage Regression

| Metric | Value |
| --- | --- |
| before_D3B_coverage | 2/5 |
| after_D3B_coverage | 5/5 |
| current_coverage | 5/5 |

## Per-Code Checks

| Code | Formal Outlook | Data Quality Warning | Blocking Issue | Shadow Key Leak | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| 2317 | present | not_exposed | none_detected | none | PASS | ok |
| 2330 | present | not_exposed | none_detected | none | PASS | insufficient_api_evidence |
| 2382 | present | not_exposed | none_detected | none | PASS | ok |
| 2454 | present | not_exposed | none_detected | none | PASS | insufficient_api_evidence |
| 2308 | present | not_exposed | none_detected | none | PASS | insufficient_api_evidence |

## Source-Delayed Verification

| Code | Formal Present | API Evidence | Interpretation |
| --- | --- | --- | --- |
| 2308 | yes | not_exposed | insufficient_api_evidence |
| 2330 | yes | not_exposed | insufficient_api_evidence |
| 2454 | yes | not_exposed | insufficient_api_evidence |

## Static Gate Review

| Check | Result | Evidence |
| --- | --- | --- |
| D3B gate scoped to front_close_mode | PASS | function line 6420; found front_close_mode branches |
| non-front-close retains blocking behavior | PASS | non-front-close else branches still append stale-date issues |
| true row shortage remains blocking | PASS | row-count shortage issue strings remain present |
| missing price remains blocking | PASS | price None appends issue |
| insufficient K-line rows remain blocking | PASS | hist_count threshold issue remains |
| technical readiness remains blocking | PASS | technical issue strings remain |
| support/resistance unavailable remains blocking | PASS | support/resistance issue string remains |
| valuation incomplete remains blocking | PASS | valuation issue string remains |
| required numeric cells remain blocking | PASS | numeric validation block remains present |
| missing source trace remains blocking | PASS | source trace issues still extend issues |
| global volume-unit issue remains blocking | PASS | volume audit still present in readiness function |
| expected-delay warnings are metadata | PASS | D3B warning metadata block found |
| UNAVAILABLE / MISSING not converted to neutral score | UNKNOWN | readiness gate does not synthesize scores |
| no fabricated financial values in D3B block | PASS | D3B block only records metadata |

## Formal API Protection

* forbidden shadow/debug key leaks: `0`
* forbidden keys checked: `shadow_outlook`, `freshness_adjusted_outlook_shadow`, `factor_freshness`, `calendar_status`, `calendar_debug`, `debug_shadow`, `override`, `market_calendar`

## Production Safety

* no DB writes performed by this script
* no API schema changes
* no frontend changes
* no external data source calls
* no formal score takeover
* no background task created
* no production module imported
