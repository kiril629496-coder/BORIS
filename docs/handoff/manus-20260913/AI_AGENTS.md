# BORIS AI — AI AGENTS, MODELS, PROMPTS AND RIGHTS

## Evidence labels

`[подтверждено кодом]` · `[подтверждено тестом]` · `[подтверждено логами]` · `[подтверждено production]` · `[предположение]` · `[неизвестно]`

## 1. Core rule

BORIS is designed so that AI proposes/generates/classifies, while deterministic code enforces hard safety, money, tenant, idempotency, permission and privacy rules. **[подтверждено кодом]**

This is a constitutional rule, not merely an implementation preference. **[подтверждено кодом]**

## 2. Provider router

Canonical provider-routing logic is in `backend/app/ext_api/aiprov.py`. **[подтверждено кодом]**

Capabilities defined by the router:

- `text_generation`
- `mop`
- `rop`
- `classification`
- `summarization`
- `dev_coding`
- `image_generation`
- `banner_generation`

All are **[подтверждено кодом]**.

Configured preference order at snapshot time:

| Capability | Provider order | Evidence |
|---|---|---|
| text / MOP / ROP / classify / summary | `claude_code` → `openai` → `anthropic_api` | **[подтверждено кодом]** |
| development coding | `openai` → `claude_code` → `gemini_cli` → `anthropic_api` | **[подтверждено кодом]** |
| image generation | `openai` | **[подтверждено кодом]** |
| banner generation | `openai` → `local_renderer` | **[подтверждено кодом]** |

Cost-policy weights in the same router are `claude_code=0`, `local_renderer=0`, `gemini_cli=1`, `openai=2`, `anthropic_api=3`. These are routing-policy weights, not a billing price list. **[подтверждено кодом]**

Provider state machine includes `AVAILABLE`, `RATE_LIMITED`, `UNAVAILABLE_BILLING`, `AUTH_ERROR`, `UNSUPPORTED_LOCATION`, `TEMP_ERROR`, `DISABLED_BY_OWNER`. **[подтверждено кодом]**

The router explicitly differentiates billing exhaustion from rate limits, authentication failure, geographic restriction and temporary transport failure to avoid wasteful retries. **[подтверждено кодом]**

Gemini free-tier short quotas and daily reset evidence, and Claude weekly-limit reset parsing, are handled explicitly. **[подтверждено кодом]**

Exact live balance/availability of every provider was not re-queried during this documentation-only handoff. **[неизвестно]**

## 3. Agent registry

`backend/app/ext_api/agents.py` contains the central agent registry/selection behavior. **[подтверждено кодом]**

Static specialization order:

- `code`, `implementation`, `refactor`, `qa`, `review`: prefer `claude`, then `chatgpt`. **[подтверждено кодом]**
- `architecture`, `planning`, `business`, `analysis`: prefer `chatgpt`, then `claude`. **[подтверждено кодом]**

Supported selection modes: `auto`, `claude_first`, `chatgpt_first`. **[подтверждено кодом]**

Agent heartbeat statuses are `available`, `limited`, `unavailable`; stale status becomes `unknown`. **[подтверждено кодом]**

Autonomous server daemons named by code:

- `gpt-executor` → systemd units `boris-executor`, `boris-executor-2` … `boris-executor-8`. **[подтверждено кодом]**
- `claude-lead` → systemd unit `boris-lead`. **[подтверждено кодом]**

The autonomous picker is designed to prefer a live daemon and not treat a human/chat session as a reliable long-running worker merely because it once reported availability. **[подтверждено кодом]**

The exact current DB registry of dynamic API actors/keys was not enumerated during this handoff because secrets/credentials were intentionally not inspected. **[неизвестно]**

Manus is not named in the static specialization or daemon mapping in `agents.py`. A dynamic registration could exist outside that static mapping; it was not proven. **[подтверждено кодом]** / **[неизвестно]**

## 4. Important AI service modules

The following active source areas are directly relevant to AI behavior:

- `backend/app/api/ai_bindings.py` — AI bindings/API surface. **[подтверждено кодом]**
- `backend/app/api/prompts.py`, `prompt_check.py`, `messenger_prompts.py` — prompt management/checking. **[подтверждено кодом]**
- `backend/app/services/sales_ai_router.py` — sales AI provider/behavior routing. **[подтверждено кодом]**
- `backend/app/services/mop_dialogue_brain.py` — MOP dialogue brain. **[подтверждено кодом]**
- `backend/app/services/rop_global_brain.py` — ROP global brain. **[подтверждено кодом]**
- `backend/app/services/brain_action_planner.py`, `brain_world_state.py`, `brain_recovery.py`, `brain_acceptance.py`, `brain_e2e_acceptance.py`, `brain_selftest.py`, `brain_modules.py`, `brain_trace.py` — broader system brain/control/evidence. **[подтверждено кодом]**
- `backend/app/services/ai_budget.py`, `ai_guard_audit.py`, `mass_ai_guard.py`, `realtime_ai_guard.py` — spend/safety boundaries. **[подтверждено кодом]**
- `backend/app/services/ai_privacy.py`, `ai_costs_private_access.py` — privacy/access controls. **[подтверждено кодом]**
- `backend/app/services/openai_image_transport.py`, `openai_audio_guard.py` — image/audio provider boundaries. **[подтверждено кодом]**
- `backend/app/video_factory/ai_stills.py` — AI stills for Video Factory. **[подтверждено кодом]**
- top-level `backend/gigachat_pool.py` exists, but its current role relative to canonical provider routing must be confirmed before using it. **[подтверждено кодом]** / **[неизвестно]**

## 5. Prompt storage and prompt sources

Prompt-related SQLAlchemy models include:

- `prompt_templates`
- `saved_prompts`
- `messenger_prompts`
- `messenger_prompt_sources`

**[подтверждено кодом]**

Additional prompt code exists under `ai/llm/prompts.py`. **[подтверждено кодом]**

Social project prompts and editorial rules are also stored in tenant/project configuration used by `posting_runner.py`, with a canonical client authority for the owner Social projects under `backend/client-data/u2/social-editorial/`. **[подтверждено кодом]**

Prompt text may therefore live in several legitimate locations depending on domain. Manus must not consolidate them into a new generic prompt store without first determining the domain source-of-truth and ownership contract. **[предположение]**

## 6. AI image/banner behavior

The Social posting path contains OpenAI image generation and a local/reuse recovery strategy. **[подтверждено кодом]**

For certain Social outage/provider states, deterministic text recovery and reuse of already validated project assets are implemented to preserve service continuity without a second paid AI call. **[подтверждено кодом]**

The Social code explicitly describes OpenAI `gpt-image-2` for its authoritative AI banner path. **[подтверждено кодом]**

Do not infer that every image-generation path in BORIS uses the same model; the repository contains multiple image/banner services and historical implementations. **[неизвестно]**

## 7. AI money and exactly-once boundaries

The test suite contains explicit contracts such as `test_campaign_ai_exactly_once.py`, `test_paid_image_safety.py`, `test_dev_cost_safety.py`, `test_no_legacy_direct_paid_api.py`, `test_ai_guard_audit.py`, `test_ai_privacy.py`, `test_external_ai_privacy_boundaries.py`. **[подтверждено кодом]**

The presence of these tests proves intended contracts, not that they were freshly executed against this handoff branch. **[подтверждено кодом]**

Historical production acceptance records show AI budget/provider safety was part of broader acceptance work, but a current all-AI-provider live test was intentionally not run here. **[подтверждено тестом]** / **[неизвестно]**

## 8. Rights and safe operating model for Manus

Manus should begin **read-only**. **[предположение]**

Permitted development behavior for the handoff task:

- read GitHub repository/history and this handoff package; **[подтверждено production]**
- create a dedicated branch/worktree; **[подтверждено code policy]**
- run isolated/static/targeted tests outside production runtime; **[подтверждено кодом]**
- propose patches and PRs; **[подтверждено кодом]**
- read production logs/state only when necessary for evidence. **[предположение]**

Forbidden without a separately approved deployment/action path:

- reading or copying secrets; **[подтверждено кодом]**
- direct mutable production edits; **[подтверждено кодом]**
- manual production DB mutation; **[подтверждено кодом]**
- blind external retries where the first outcome is ambiguous; **[подтверждено кодом]**
- bypassing money/tenant/idempotency/privacy guards; **[подтверждено кодом]**
- enabling a paid provider merely to unblock a development task without owner authorization. **[подтверждено кодом]**

## 9. Questions Manus must answer before changing AI code

1. Which workstream owns the requested behavior? **[подтверждено кодом]**
2. Which prompt/rule source is authoritative for that domain? **[неизвестно until scoped]**
3. Is the request deterministic enough to enforce in code instead of prompt text? **[предположение]**
4. Does the provider call have durable idempotency/exactly-once protection? **[неизвестно until scoped]**
5. What is the fallback for billing, quota, auth, geo and ambiguous outcome? **[подтверждено кодом as required categories]**
6. Is tenant/account scope carried through DB, cache, queue, lock and audit? **[подтверждено кодом as constitutional requirement]**
7. Which regression test proves the bug cannot return? **[подтверждено кодом as constitutional requirement]**
