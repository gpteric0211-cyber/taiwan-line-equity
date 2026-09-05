# Notification, Subscription, And LINE User-Data Rules

Last official-source review: 2026-08-26.

The current repository has a reply-only LINE surface and bounded in-memory conversation context. Persistent push subscriptions, quota dispatch, quiet hours, and notification scheduling are planned, not implemented. Do not create them without a separate schema/data-flow phase.

## Contents

1. Explicit subscription model
2. Scheduling, caps, and batching
3. Alert trigger safety
4. LINE user identifiers and privacy
5. Retention, deletion, unfollow, and unsend
6. Official sources

## 1. Explicit Subscription Model

- Watchlist membership alone does not opt a user into every alert type.
- Require an explicit, revocable opt-in per user, stock, and alert/digest type.
- Record consent/opt-in time, source/action, policy/terms version, timezone, quiet-hours preference, and active state without raw message content.
- Provide discoverable commands/actions to view, pause, and cancel each subscription and to leave the service.
- Do not send unsolicited stock picks or alerts for symbols the user did not explicitly subscribe to.
- User-requested asynchronous answers and recurring promotional/alert subscriptions are different purposes; store and throttle them separately.
- Subscription POST/webhook actions must be idempotent and protected against duplicate/redelivered events.

A future `repository/line_subscription_repository.py` is a suggested ownership boundary, not authorization to add a table.

## 2. Scheduling, Caps, And Batching

- Reuse shared scheduler infrastructure primitives where practical, but keep job windows separate.
- The existing 15:00–23:59 market-data update/retry window applies to that data job, not to pre-market notifications.
- Pre-market, post-close, and intraday notifications each need configurable Asia/Taipei schedules and must wait for the relevant data-readiness gate.
- Quiet hours, per-user daily alert cap, account quota reserve, cooldowns, and exceptions must be configurable and testable; they are product policies, not fixed LINE/legal numbers.
- A user-requested async result should not be silently dropped merely because the recurring-alert cap was reached; enforce a separate bounded follow-up policy.
- Batch nearby alerts for the same user into one message when that preserves clarity.
- Stop dispatch when quota reserve is reached. Do not rely on LINE to queue over-quota messages.

## 3. Alert Trigger Safety

- Reuse the existing signal/referee data-quality and eligibility gate; alerts must not create a competing formula or top-level conclusion.
- Default to no alert for stale, delayed, missing, unavailable, or disqualified input.
- An inherently estimated signal may alert only when that alert type explicitly permits estimates and the message clearly says `推估`.
- Trigger on a state transition/crossing, not on every polling cycle while the condition remains true.
- Define per-user/stock/condition cooldown and an idempotency key so retries or duplicate jobs cannot send duplicates.
- Apply reference 05 sanitization and reference 07 wording/disclaimer policy.
- Audit a fired alert with a pseudonymous subject key, stock, rule/version, trigger time, data date/quality, dispatch status, and fixed retention period. Never store raw userId or full message text in the audit row.

## 4. LINE User Identifiers And Privacy

Treat LINE userId, profile data, watchlists, subscriptions, and conversation content conservatively as personal/LINE User Information.

Before collecting durable user data:

- Publish an always-accessible privacy policy.
- Present a separate first-use privacy notice that identifies the operator, purposes, data categories, use period, region, recipients/processors, methods, user rights/contact method, and consequences of not providing data.
- Determine and document the applicable lawful basis/consent with qualified Taiwan counsel.
- Collect only fields needed for the stated feature.
- Disclose processors/LLM or cloud recipients, cross-border regions, retention, and safeguards when applicable.
- Keep privacy notice/consent separate from the investment-risk disclaimer.

Identifier design:

- Use a keyed pseudonymous subject ID for business tables, joins, logs, analytics, throttles, and alert audits.
- Push delivery requires the original LINE userId; a one-way hash alone cannot replace it.
- Store raw userId only in protected configuration for a tightly scoped allowlist or in a restricted delivery-identity store, encrypted/tokenized at rest with separate key management, rotation, least privilege, and audited access.
- Never place raw userId in normal logs, analytics, docs, reports, commits, URLs, or exception text.
- Hashing/pseudonymization is not automatic legal anonymization when records can still be linked to a person.

Current in-memory hashed conversation behavior must remain bounded and must be re-verified before relying on it as a privacy guarantee.

## 5. Retention, Deletion, Unfollow, And Unsend

- Do not persist full webhook bodies or conversation transcripts by default.
- Define a purpose and fixed retention period for every durable user-data, consent, alert-audit, queue, cache, log, and backup record.
- Under LINE User Data Policy, notify users if LINE User Information other than LINE internal user identifiers will be stored for more than 24 hours. Friend/Group information must not be stored beyond 24 hours even with notice.
- Keep LINE User Information only as long as necessary for the disclosed purpose.
- Provide access/correction/deletion/suspension workflows and propagate deletion to active tables, queues, caches, analytics, logs, and backups according to the documented policy.
- On `unfollow`, immediately deactivate subscriptions and scheduled pushes. Do not infer blocking from push errors.
- On `unsend`, remove or suppress any retained copy of the withdrawn message where the platform event can be correlated, respecting documented legal-retention exceptions.
- When a user withdraws from the service or the service closes, delete LINE User Information unless a documented legal exception applies.
- Do not retain an indefinite “minimal record.” Any opt-out tombstone needs a specific purpose, minimal fields, fixed expiry, disclosure, and legal review.
- If law or an approved contract requires retention, isolate the record, restrict use/access, and document the exception instead of promising unconditional immediate deletion.
- Contractors/processors remain in scope; require equivalent handling and incident obligations.

LINE may accept a push request without delivering it to a blocked/deleted/non-friend user. Use the `unfollow` event and subscription state, not push status, as the primary stop signal.

## 6. Official Sources

- [LINE User Data Policy](https://terms2.line.me/LINE_Developers_user_data_policy?lang=en)
- [LINE user IDs](https://developers.line.biz/en/docs/messaging-api/getting-user-ids/)
- [Receive webhook events, including unfollow and unsend](https://developers.line.biz/en/docs/messaging-api/receiving-messages/)
- [Taiwan Personal Data Protection Act reference compilation](https://www.moj.gov.tw/media/2809/31011536015.pdf?mediadl=true)
- [Personal Data Protection Act Enforcement Rules](https://law.moj.gov.tw/LawClass/LawAll.aspx?PCode=I0050022)

Re-check current law, effective dates, LINE policy, and product-specific obligations before launch.
