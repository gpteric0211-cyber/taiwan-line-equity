# LINE Messaging API Integration Rules

Last verified against official LINE documentation: 2026-08-29.

Read this for webhook ingress, reply/push delivery, loading animation, Flex payloads, quota, signature verification, or retry behavior. Re-check official docs when behavior, SDKs, or plans change.

## Contents

1. Webhook ingress and idempotency
2. Reply-token delivery decision
3. Loading animation
4. Message objects and Flex
5. Push quota and throttling
6. Security, failures, and retries
7. Official sources
8. Local-model handoff boundary

## 1. Webhook Ingress And Idempotency

- Verify `x-line-signature` against the exact raw request body before parsing or processing.
- Accept empty `events=[]` verification requests with HTTP 200.
- LINE records a webhook `request_timeout` when it does not receive the response within about two seconds. This is separate from reply-token validity.
- Keep the HTTP ingress path bounded: validate, parse, deduplicate, accept/dispatch, and return promptly. Do not block acceptance on market-source I/O, model generation, heavy computation, or DB repair.
- Use `webhookEventId` for idempotency. Inspect `deliveryContext.isRedelivery` and event `timestamp`; redelivery can be duplicated/out of order and is not guaranteed.
- A webhook POST is allowed to enqueue bounded event work or make explicit idempotent subscription changes. It is not governed by the ordinary GET no-side-effect rule.
- Return 2xx only after the event has been accepted by the execution mechanism appropriate to the workflow. Do not claim success if enqueue/persistence failed.
- Reject invalid signatures and do not process them.
- Process-level background tasks are not durable across crashes. Do not use them for workflows that require guaranteed later push delivery without explicitly accepting or fixing that reliability risk.

## 2. Reply-Token Delivery Decision

Officially, a reply token:

- Can be used only once.
- Should be used as soon as possible.
- Must be used within one minute after receiving the webhook for guaranteed behavior; use beyond one minute is not guaranteed.
- Has a time limit that may change and may be shortened by network delay.

Do not design to the last second. Use a configurable internal budget below the platform window and measure end-to-end latency.

Delivery decision:

| Situation | Delivery |
|---|---|
| Result is ready from local cache/read-only DB within the internal budget | Send the final result with one reply call |
| One-to-one task is bounded and expected to finish within the internal budget | Optionally show loading animation, then reply as soon as ready |
| Completion time is uncertain or exceeds the reply budget | Reply once with an acknowledgment, then push the result only if push eligibility, consent, quota, and retention requirements are satisfied |
| Push is unavailable or not approved | Do not promise automatic delivery; return an honest pending/retry instruction |

A text acknowledgment consumes the reply token. The final result then cannot be sent as another reply. Loading animation does not consume the reply token and is not a message acknowledgment.

A background event worker may call the authenticated local read-only Bot API. It must not duplicate financial formulas or trigger hidden repair/write flows. Any live single-stock read fallback must be explicitly bounded, non-persistent, quality-labeled, and proven to fit the internal reply budget.

## 3. Loading Animation

- Supported only for one-to-one chats, not group or multi-person chats.
- Duration is 5–60 seconds.
- It is visible only when the user is currently viewing the chat and only on supported LINE clients.
- An accepted API response does not prove the user saw the animation.
- It does not replace a reply/push message or extend reply-token validity.

Always have a delivery path that does not depend on the animation being visible.

## 4. Message Objects And Flex

- Reply and push requests accept at most five message objects per request.
- If content needs more structure, prefer one concise Flex message/carousel rather than many bubbles, subject to current Flex limits.
- Use LINE's validate-message endpoint for the actual delivery type before shipping a changed payload.
- Build each card type through a shared builder so sanitization, quality labels, alt text, and disclaimer cannot be skipped.
- Apply reference 05's canonical sanitization to every text field and reference 07's disclaimer policy to every stock-specific interpretation.
- Do not hide stale/estimated/unavailable state in low-visibility text.
- Validate exact JSON, alternate text, object count, character limits, and supported component fields.

## 5. Push Quota And Throttling

- Reply messages do not count toward the monthly message allowance.
- Push, multicast, broadcast, and narrowcast messages do count.
- Counting is based on recipients per request, not the number of message objects.
- Plan limits vary by country/region and account plan. Exceeding the allowed monthly limit returns an error and does not queue the message automatically.
- Quota consumption is an estimate and can include messages sent through Official Account Manager.
- Check monthly limit/consumption before enabling or expanding push triggers. Reserve capacity for user-requested follow-ups and operational messages.
- Batch nearby alerts where possible; apply per-user caps and quiet hours from reference 09.
- Do not treat HTTP 200 from push as a delivery receipt. A blocked, deleted, or non-friend user may not receive the message even when the API accepts it.

## 6. Security, Failures, And Retries

- Channel access tokens and channel secrets come from protected config/environment only.
- Never log full raw webhook bodies by default; they can contain personal messages and identifiers.
- Apply reference 09 to any stored user/message data.
- Classify quota exhaustion, rate limiting, invalid payload, authentication failure, and transient network failure separately.
- Use `X-Line-Retry-Key` for eligible push/multicast/broadcast/narrowcast retries. Keep the payload and recipient unchanged, use bounded exponential backoff, and do not retry permanent 4xx/quota failures blindly.
- Reply-token reuse is invalid; event idempotency must prevent duplicate replies.
- Handle `unfollow` events to deactivate subscriptions. Do not wait for a push error to infer blocking.
- Do not mark a message delivered merely because LINE accepted the API request.
- Failure logs use correlation/pseudonymous IDs, never raw userId, reply token, message body, or secrets.

## 7. Official Sources

- [Messaging API reference](https://developers.line.biz/en/reference/messaging-api/)
- [Receive webhook events](https://developers.line.biz/en/docs/messaging-api/receiving-messages/)
- [Webhook error statistics](https://developers.line.biz/en/docs/messaging-api/check-webhook-error-statistics/)
- [Verify webhook signatures](https://developers.line.biz/en/docs/messaging-api/verify-webhook-signature/)
- [Display a loading animation](https://developers.line.biz/en/docs/messaging-api/use-loading-indicator/)
- [Messaging API pricing](https://developers.line.biz/en/docs/messaging-api/pricing/)
- [Retry failed API requests](https://developers.line.biz/en/docs/messaging-api/retrying-api-request/)

## 8. Local-Model Handoff Boundary

This reference owns LINE platform transport, reply-token, push, quota, and delivery rules. Read
[10-line-model-research-and-reasoning.md](10-line-model-research-and-reasoning.md) for request
classification, FACTS projection, context profiles, controlled research, evidence grounding, GPU
queueing, structured model output, validation, and model release gates.

- Start the internal reply deadline at webhook ingress and propagate it through the model/research
  path; do not restart the clock when a background handler begins.
- `reply_only` must not perform a separate 27B/28B classification pre-pass before final synthesis.
- Loading animation does not extend the deadline or authorize a second reply.
- `durable_followup` is unavailable until reference 09's consent, protected delivery identity,
  quota, retention, cancellation, durable-job, and retry requirements are implemented and tested.
- A transport fallback must preserve reference 10's coverage and omission metadata; it must not
  silently claim that a reduced answer is comprehensive.
