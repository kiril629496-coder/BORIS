# Maria MOP AI provider recovery — final production bundle

Scope: mbroker_27859 / MOP combat training and shared sales AI provider reliability.

Verified production behavior on 2026-09-08:
- OpenAI 429 credit_balance_exhausted is classified as billing, not short rate-limit.
- OpenAI is latched UNAVAILABLE_BILLING for a bounded cooldown; sales order becomes Gemini -> Ollama.
- Gemini plain tenant timeout no longer opens a global circuit for every BORIS customer.
- Shared Gemini quota/location/5xx/network failures remain eligible for global protection.
- Gemini CLI error classification exposes only safe markers; raw CLI/customer text is not copied into reliability errors.
- Final mixed-replica Maria QA: start on API1, specific price turn on API2 -> gemini-2.5-flash, cost 0, no OpenAI fallback.
- Finish succeeded with manager_turns=2; QA session removed; real Maria session preserved.
- Guardian one-shot: Result=success, ExecMainStatus=0.
- Regression suite: 60 passed for sales router + realtime guard + tenant circuit isolation + MOP external recovery.
- Both backend replicas were restarted by canonical boris-deploy.service; manual non-service rolling path remained blocked.

External state at acceptance:
- OpenAI remains externally blocked by empty credit balance.
- Gemini is the primary free sales provider while available.
- Local Qwen remains emergency fallback.
- Deterministic safe handoff remains final no-502 degradation.

This bundle is intentionally stored as an ops patch rather than replacing repository backend files:
the production checkout contains a much newer/dirty server state than the repository default branch.
