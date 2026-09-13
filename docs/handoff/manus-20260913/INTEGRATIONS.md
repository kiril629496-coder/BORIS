# BORIS AI — INTEGRATIONS

No secret values are included in this document.

## Evidence labels

`[подтверждено кодом]` · `[подтверждено тестом]` · `[подтверждено логами]` · `[подтверждено production]` · `[предположение]` · `[неизвестно]`

## 1. Avito

BORIS contains a substantial Avito integration surface under `backend/app/api/avito.py`, campaign/feed services, provider transport, browser-gateway code and many Avito-specific tests. **[подтверждено кодом]**

Functional areas visible in code/tests include account/provider access, stats, listing/feed/publication flows, CPX/bid/budget control, category/template mapping, autoload, browser-gateway assistance and campaign state. **[подтверждено кодом]**

The backend journal during this handoff showed live Avito-related API traffic returning HTTP 200, including `/api/avito/director_overview`. **[подтверждено логами]**

A suppressed `RuntimeError` fingerprint was also logged from `app.api.avito` line 6391 while services remained active; this needs scoped triage rather than being treated as a proven outage. **[подтверждено логами]**

## 2. AI providers

Central provider routing is implemented in `backend/app/ext_api/aiprov.py`. **[подтверждено кодом]**

Integrated/provider concepts include:

- Claude Code subscription-style execution. **[подтверждено кодом]**
- OpenAI API for text and image-capable paths. **[подтверждено кодом]**
- Anthropic API as a paid fallback for supported capabilities. **[подтверждено кодом]**
- Gemini CLI for development coding capability. **[подтверждено кодом]**
- local renderer for banner fallback. **[подтверждено кодом]**
- DeepSeek-related sales routing/tests/branches exist in the repository history/current code surface, but its exact live priority at handoff time was not exhaustively revalidated. **[подтверждено кодом]** / **[неизвестно]**

Provider credentials are environment/secret material and were not inspected. **[подтверждено production]**

## 3. Telegram

BORIS contains multiple Telegram contours: Social autoposting, sales/outreach, reporting, routes and delivery reconciliation. **[подтверждено кодом]**

Recent production recovery confirmed a live `От Души` Telegram post with message id 131. **[подтверждено production]**

Telegram delivery must preserve idempotency/message IDs to avoid duplicate publication during retries. **[подтверждено кодом]**

## 4. VK

VK Social publication is implemented in `posting_runner.py` and associated recovery/readiness logic. **[подтверждено кодом]**

The transport explicitly recognizes VK error 9/29 and arms a shared cooldown/backoff rather than retrying rapidly. **[подтверждено кодом]**

During the most recent Social recovery, VK was in an external Flood Control state and undelivered channel parts were retained as `partial`/`waiting_external`. This state is time-sensitive and must be freshly checked. **[подтверждено логами]**

## 5. Telephony

The codebase contains telephony routes/services/tests for MCN, Telphin, Asterisk-related bridging, call ingest/analysis, recording and phone runtime safety. **[подтверждено кодом]**

The frontend depends on `sip.js` 0.21.2. **[подтверждено кодом]**

The GitHub default phone checkpoint records 114/114 phone tests and 208/208 telephony tests for that checkpoint. **[подтверждено тестом]**

Those results are checkpoint evidence, not a fresh proof that every telephony provider is currently registered/online. **[неизвестно]**

## 6. Email / mailboxes / outreach

The backend contains `email_service.py`, `mailer.py`, `email_queue.py`, mailbox services, tracking, prospect campaigns and outreach workers/runners. **[подтверждено кодом]**

Email credentials/app passwords are secrets and must not be copied into issues, handoff files or Manus prompts. **[подтверждено code policy]**

## 7. Browser Gateway

Production backend logs showed active `GET /api/browser-gateway/transport/next-job` and heartbeat traffic returning HTTP 200. **[подтверждено логами]**

The browser gateway is part of BORIS's automation path for browser-mediated operations where direct APIs are insufficient or not used. **[подтверждено кодом]**

Do not treat browser automation as permission to bypass provider terms, captchas, authorization or money guards. **[предположение]**

## 8. Yandex Direct / marketing channels

Files such as `direct_autopilot_runner.py` and Direct-related frontend/backend surfaces establish a Yandex Direct/marketing automation contour. **[подтверждено кодом]**

Its exact current production account connectivity and live spend state were not audited in this handoff. **[неизвестно]**

## 9. CRM

BORIS includes an internal CRM/messenger/re-activation domain rather than relying solely on an external CRM connector. **[подтверждено кодом]**

There are also integration/automation requirements around external business systems in project history, but no claim is made here that a specific external CRM such as Bitrix24 is live unless a scoped integration check proves it. **[неизвестно]**

## 10. Pexels / media sources

Social posting code contains a Pexels media source path alongside AI and reuse paths. **[подтверждено кодом]**

Whether it is enabled for any current production project was not checked. **[неизвестно]**

## 11. Instagram / YouTube

Video Factory exists in the repository. **[подтверждено кодом]**

A native BORIS OAuth/publishing pipeline for Instagram Reels/YouTube Shorts was not proven live during this handoff audit. Do not state that native publishing is working without a fresh route/token/E2E proof. **[неизвестно]**

The owner has used Metricool externally from ChatGPT for Instagram scheduling, but that operator-side connected service is not established by this repository audit as a native BORIS integration. **[неизвестно]**

## 12. Secret/configuration policy

- Production configuration uses environment/service configuration; secret values were intentionally not read. **[подтверждено production]**
- Source code and docs should refer to secret names/capabilities, never copy live values. **[подтверждено code policy]**
- Protected secret directories/files on the server are access-restricted, which is expected and was not bypassed. **[подтверждено production]**
- Any Manus task that requires a missing OAuth/client secret must stop at a precise external blocker rather than inventing or exposing credentials. **[предположение]**

## 13. Integration failure taxonomy Manus should preserve

For every external provider, distinguish at least:

- authorization/permission failure; **[подтверждено кодом]**
- billing/quota exhaustion; **[подтверждено кодом]**
- rate/Flood Control; **[подтверждено кодом]**
- transport/network failure; **[подтверждено кодом]**
- unsupported capability/location; **[подтверждено кодом]**
- ambiguous outcome where blind retry may duplicate a paid/external side effect; **[подтверждено кодом]**
- internal deterministic failure. **[подтверждено кодом]**

A provider outage must not be mislabeled as an internal code regression if the internal contour is healthy and the dependency is explicitly waiting. **[подтверждено кодом]**
