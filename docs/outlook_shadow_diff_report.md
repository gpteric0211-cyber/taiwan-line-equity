# Legacy vs Shadow Outlook Diff Report

## Summary

* generated_at: `2026-06-19T18:59:44+08:00`
* base_url: `http://127.0.0.1:8057`
* total codes: `5`
* success: `0`
* skip: `5`
* warn: `0`
* fail: `0`

## Important Note

This report is read-only. It compares the formal detail API output with the debug shadow calendar/freshness context.

Shadow context does not take over production `next_day_outlook`. It currently focuses on calendar, freshness, and target trade date context. If `shadow_score` is `N/A`, that is expected and means the shadow adjusted score has not been implemented in this phase.

## Per-Code Results

| Code | Formal Status | Shadow Status | Formal Score | Formal Direction | Shadow Score | Shadow Target Trade Date | Calendar Summary | Difference Note |
| ---- | ------------- | ------------- | ------------ | ---------------- | ------------ | ------------------------ | ---------------- | --------------- |
| 2317 | ok | SKIP: ENABLE_SHADOW_OUTLOOK is false | 0.22 | 中性 | N/A | N/A | N/A | SKIP: ENABLE_SHADOW_OUTLOOK is false; shadow score not implemented; calendar/freshness only |
| 2330 | ok | SKIP: ENABLE_SHADOW_OUTLOOK is false | 11.36 | 偏多 | N/A | N/A | N/A | SKIP: ENABLE_SHADOW_OUTLOOK is false; shadow score not implemented; calendar/freshness only |
| 2382 | ok | SKIP: ENABLE_SHADOW_OUTLOOK is false | 10.69 | 中性 | N/A | N/A | N/A | SKIP: ENABLE_SHADOW_OUTLOOK is false; shadow score not implemented; calendar/freshness only |
| 2454 | ok | SKIP: ENABLE_SHADOW_OUTLOOK is false | 9.4 | 中性 | N/A | N/A | N/A | SKIP: ENABLE_SHADOW_OUTLOOK is false; shadow score not implemented; calendar/freshness only |
| 2308 | ok | SKIP: ENABLE_SHADOW_OUTLOOK is false | 6.64 | 中性 | N/A | N/A | N/A | SKIP: ENABLE_SHADOW_OUTLOOK is false; shadow score not implemented; calendar/freshness only |

## Shadow Calendar Details

### 2317

* status: SKIP: ENABLE_SHADOW_OUTLOOK is false
* TWSE status: N/A
* US status: N/A
* TAIFEX status: N/A
* override_active: N/A
* source: N/A
* confidence: N/A
* target_trade_date: N/A

### 2330

* status: SKIP: ENABLE_SHADOW_OUTLOOK is false
* TWSE status: N/A
* US status: N/A
* TAIFEX status: N/A
* override_active: N/A
* source: N/A
* confidence: N/A
* target_trade_date: N/A

### 2382

* status: SKIP: ENABLE_SHADOW_OUTLOOK is false
* TWSE status: N/A
* US status: N/A
* TAIFEX status: N/A
* override_active: N/A
* source: N/A
* confidence: N/A
* target_trade_date: N/A

### 2454

* status: SKIP: ENABLE_SHADOW_OUTLOOK is false
* TWSE status: N/A
* US status: N/A
* TAIFEX status: N/A
* override_active: N/A
* source: N/A
* confidence: N/A
* target_trade_date: N/A

### 2308

* status: SKIP: ENABLE_SHADOW_OUTLOOK is false
* TWSE status: N/A
* US status: N/A
* TAIFEX status: N/A
* override_active: N/A
* source: N/A
* confidence: N/A
* target_trade_date: N/A

## Factor Freshness Details

### 2317

* N/A

### 2330

* N/A

### 2382

* N/A

### 2454

* N/A

### 2308

* N/A

## Warnings / Skips

* 2317 shadow: SKIP: ENABLE_SHADOW_OUTLOOK is false
* 2330 shadow: SKIP: ENABLE_SHADOW_OUTLOOK is false
* 2382 shadow: SKIP: ENABLE_SHADOW_OUTLOOK is false
* 2454 shadow: SKIP: ENABLE_SHADOW_OUTLOOK is false
* 2308 shadow: SKIP: ENABLE_SHADOW_OUTLOOK is false

## Formal API Protection

* 2317: PASS no forbidden shadow keys found in formal detail response.
* 2330: PASS no forbidden shadow keys found in formal detail response.
* 2382: PASS no forbidden shadow keys found in formal detail response.
* 2454: PASS no forbidden shadow keys found in formal detail response.
* 2308: PASS no forbidden shadow keys found in formal detail response.

Forbidden keys checked:
* `shadow_outlook`
* `freshness_adjusted_outlook_shadow`
* `factor_freshness`
* `calendar_status`
* `calendar_debug`
* `debug_shadow`
* `override`
* `market_calendar`

## Production Safety

* No DB writes are performed by this script.
* No API or frontend files are modified by this script.
* No external data sources are fetched by this script.
* No production outlook takeover is performed.
* No background task, scheduler, or server is started by this script.

## Machine Summary

```json
{
  "base_url": "http://127.0.0.1:8057",
  "counts": {
    "fail": 0,
    "skip": 5,
    "success": 0,
    "warn": 0
  },
  "formal_api_protection_failures": 0,
  "generated_at": "2026-06-19T18:59:44+08:00",
  "total": 5
}
```
