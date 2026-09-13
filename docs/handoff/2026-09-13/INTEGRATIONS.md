# INTEGRATIONS.md

## Evidence status legend

- **[CODE]** — подтверждено текущим кодом/файлами, прочитанными на production-host.
- **[TEST]** — подтверждено фактическим запуском теста; если запуск был раньше текущего аудита, это явно отмечено.
- **[LOG]** — подтверждено текущими журналами/systemd status.
- **[PROD]** — подтверждено текущим production runtime/service state.
- **[PLAN]** — запланировано/ожидает выполнения.
- **[HYPOTHESIS]** — предположение, требующее проверки.
- **[UNKNOWN]** — доступа или свежего доказательства недостаточно.


## Confirmed integration families

### Avito
- **[CODE]** Large production integration in `backend/app/api/avito.py`, Messenger, campaigns/feed, CPX and stats code.
- **[CODE]** Root `integrations/avito/` exists but is not the complete production integration.
- **[CODE]** Publication, messaging, stats, spend, bids and media have separate contracts.
- **[LOG]** One current suppressed RuntimeError fingerprint was observed at `app.api.avito` line 6391.
- **[UNKNOWN]** Avito provider live auth was not probed.

### Telegram
- **[CODE]** Telegram bot, Telegram direct transport, sales/prospecting/reactivation modules and tests exist.
- **[TEST]** `test_telegram_sales_regression.py` is present; historical work previously reported a large regression suite, but not rerun in this handoff.
- **[UNKNOWN]** Current account/session auth health was not probed.

### VK / social
- **[CODE]** Social/VK posting, queues, UTM, entitlement, retry and provider tests are present.
- **[UNKNOWN]** Current live external provider health not probed.

### Email
- **[CODE]** email queue, delivery events, sent-copy lifecycle, open tracking and owner outreach are implemented.
- **[CODE]** recent migrations 050–057 focus heavily on canonical owner-email transports/Yandex/Mail.ru authority.
- **[UNKNOWN]** Current SMTP/provider auth and mailbox state not probed.

### Telephony
- **[CODE]** MCN/Telphin/SIP/calltracking code, migrations and extensive tests exist.
- **[CODE]** Telephony hard rule: service/process up is not enough; call -> CRM post-condition must be proven.
- **[UNKNOWN]** Current real SIP registration and live call acceptance were not checked here.

### AI providers
- **[CODE]** OpenAI.
- **[CODE]** DeepSeek via PlusVibe-compatible endpoint in current dirty admin config.
- **[CODE]** Gemini CLI.
- **[CODE]** Claude Code / Anthropic API paths.
- **[CODE]** local Ollama/local Qwen recovery paths.
- **[CODE]** MiniMax live-test/admin code is present.
- **[UNKNOWN]** Current balances, quotas and credentials intentionally not read/probed.

### Browser / web automation
- **[PROD]** API journal shows frequent `/api/browser-gateway/transport/next-job` 200 responses.
- **[CODE]** Browser gateway isolation tests are present.
- **[UNKNOWN]** Exact current worker/client behind those polls was not identified.

### Payments / wallet
- **[CODE]** `payments.py`, `wallet.py`, billing/entitlements exist.
- **[CODE]** dirty source adds stronger owner-scoped wallet access and refund-on-activation-failure behavior.
- **[UNKNOWN]** Current payment provider production state not probed.

### Sber
- **[CODE]** `app/api/sber_client.py` exists and loads tokens.
- **[UNKNOWN]** Exact active product use and live connectivity not checked.

## Secret policy

- **[CODE]** Secrets are expected through environment/root-only `.env` and provider-specific configuration.
- **[PROD]** No secret value was copied into this handoff.
- **[PLAN]** Next agent must preserve this: record only secret variable names/locations, never secret contents.

## External-action safety

- **[CODE]** Paid/external calls must be idempotent.
- **[CODE]** Provider ambiguity must not automatically replay an external mutation.
- **[CODE]** External success needs provider/readback proof, not request acceptance only.
- **[PLAN]** Any integration audit should start read-only; explicit write/live-send test needs separate approval.
