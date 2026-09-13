# AI_AGENTS.md

## Evidence status legend

- **[CODE]** — подтверждено текущим кодом/файлами, прочитанными на production-host.
- **[TEST]** — подтверждено фактическим запуском теста; если запуск был раньше текущего аудита, это явно отмечено.
- **[LOG]** — подтверждено текущими журналами/systemd status.
- **[PROD]** — подтверждено текущим production runtime/service state.
- **[PLAN]** — запланировано/ожидает выполнения.
- **[HYPOTHESIS]** — предположение, требующее проверки.
- **[UNKNOWN]** — доступа или свежего доказательства недостаточно.


## Agent registry

### Chat agents
- **[CODE] `chatgpt`** — registered/expected in `app/ext_api/agents.py`.
  - specialization preference: architecture, planning, business, analysis.
  - availability comes from heartbeat/key state; no availability claim is made here.
- **[CODE] `claude`** — registered/expected.
  - specialization preference: code, implementation, refactor, QA, review.
  - availability comes from heartbeat/key state; no availability claim is made here.

### Server daemons
- **[CODE] `gpt-executor`** — autonomous executor identity.
  - mapped to systemd units `boris-executor`, `boris-executor-2` ... `boris-executor-8`.
  - **[PROD][LOG]** units #1 and #2 were checked and are inactive/dead.
  - **[UNKNOWN]** #3..#8 not checked.
- **[CODE] `claude-lead`** — review/lead daemon.
  - mapped to `boris-lead`.
  - **[PROD][LOG]** checked inactive/dead; last start logged provider `None`.

## Agent selection logic

- **[CODE]** `agents.py` modes: `auto`, `claude_first`, `chatgpt_first`.
- **[CODE]** `auto` specialization:
  - code/implementation/refactor/qa/review -> Claude then ChatGPT.
  - architecture/planning/business/analysis -> ChatGPT then Claude.
- **[CODE]** Autonomous routing with `require_alive=True` prefers actual daemon processes and does not blindly use chat agents.

## Provider routing

### Generic ext_api router
- **[CODE]** `aiprov.py` capability order:
  - TEXT/MOP/ROP/CLASSIFY/SUMMARY: `claude_code`, `openai`, `anthropic_api`.
  - CODING: `openai`, `claude_code`, `gemini_cli`, `anthropic_api`.
  - IMAGE: `openai`.
  - BANNER: `openai`, `local_renderer`.
- **[CODE]** This generic list is not the only runtime router; product-specific routers may override order.

### Development-provider policy
- **[CODE]** `aiprov.py` explicitly denies paid development providers in its free-development path (`openai`, `codex`, `anthropic_api` in deny set).
- **[CODE]** Free development candidates are centered on `gemini_cli` and `claude_code`.
- **[CODE]** Gemini can be reserved for client sales/MOP via `BORIS_RESERVE_GEMINI_FOR_SALES`.
- **[CODE]** Provider states are stored in `ext_ai_providers`; failure classes/cooldowns and durable quota reset evidence are modeled.

### MOP / Messenger
- **[CODE]** Current workspace `messenger.py` documents router intent:
  `OpenAI when usable -> DeepSeek -> free Gemini CLI -> local Ollama`.
- **[CODE]** workspace admin configuration contains PlusVibe base URL and a DeepSeek model default `deepseek-v4-flash-0731:free`.
- **[CODE]** MiniMax test code references `minimax-m2.7:free`.
- **[UNKNOWN]** Do not infer which provider answered the most recent real customer turn without reading the call/audit row.

### ROP/call analysis
- **[CODE]** `calltracking.py` documents one ROP boundary:
  OpenAI -> free Gemini -> local Qwen.
- **[CODE]** defaults observed in source include OpenAI model `gpt-5.4` and Gemini model `gemini-2.5-flash`.
- **[UNKNOWN]** Actual currently selected model/provider per call depends on environment/provider state and was not live-probed.

### Images/banners
- **[CODE]** OpenAI image paths are present; current dirty source references `gpt-image-2` for visual generation, with local text rendering/safety paths.
- **[UNKNOWN]** Billing/account balance/provider availability was not probed.

## Prompt/instruction locations

- **[CODE]** `backend/app/api/chat.py` — `BORIS_CONTEXT`, general in-product assistant context.
- **[CODE]** `backend/app/api/messenger.py` — MOP generation, policy guards, qualification/handoff logic.
- **[CODE]** `backend/app/models/messenger_prompt.py` + `api/messenger_prompts.py` — account prompt records and CRUD.
- **[CODE]** `backend/app/models/prompt_template.py`, `saved_prompt.py` — prompt template/storage entities.
- **[CODE]** `backend/app/mop_core.py`, `mop_training.py`, `mop_combat_training.py` — MOP behavior/training boundaries.
- **[CODE]** `backend/telegram_sales_replies.py` and related Telegram sales code — owner sales/prospecting conversational logic.
- **[CODE]** `AGENTS.md`, `CHATGPT_OPERATING_RULES.md`, `docs/BORIS_CONSTITUTION.md` — system-level operating constraints for development agents.
- **[CODE]** `backend/data/chat_task_ownership.json` — cross-agent/workstream mutation ownership.
- **[UNKNOWN]** Prompt rows stored in the live DB were not exported in this handoff.

## Hard constraints for every next agent

- **[CODE]** Do not invent PASS from compile-only evidence.
- **[CODE]** Hard money, tenant, permission, idempotency and privacy guards are deterministic code boundaries; AI may advise but cannot bypass them.
- **[CODE]** One external action = one canonical writer.
- **[CODE]** One account's data/action must not leak to another.
- **[CODE]** Provider ambiguity must not cause unsafe replay of paid/external operations.
