# BORIS AI — PROJECT STATE

Snapshot time: 2026-09-13 22:48 MSK (+03:00).

This document is a read-only handoff snapshot. No production mutation, data deletion, secret access, restart, deploy, or irreversible operation was performed while preparing this package.

## Evidence labels

- **[подтверждено кодом]** — directly observed in repository/server source or configuration.
- **[подтверждено тестом]** — supported by a named test/acceptance result.
- **[подтверждено логами]** — supported by current or archived runtime logs/journals.
- **[подтверждено production]** — directly observed in the live production runtime/state.
- **[предположение]** — reasoned interpretation that must be revalidated.
- **[неизвестно]** — not established by this handoff audit.

## Resource inventory

| Resource | State | Evidence |
|---|---|---|
| GitHub connection | Connected as repository owner account; repository read/write, branch and PR operations available. | **[подтверждено production]** |
| Repository | `kiril629496-coder/BORIS` is accessible. | **[подтверждено production]** |
| GitHub default branch | `phone-entitlement-production-20260908`; its README explicitly describes an isolated Phone/MCN checkpoint rather than the whole dirty production tree. | **[подтверждено кодом]** |
| Broader repository branch | `avito-money-core-production-20260909` contains frontend/backend/docs/ai/database/docker and matches the current server Git branch name. | **[подтверждено кодом]** |
| Handoff branch | `handoff/manus-boris-ai-20260913`, branched from `avito-money-core-production-20260909`. | **[подтверждено production]** |
| Production server | `/root/BORIS` is accessible read-only for this audit through SentinelX. | **[подтверждено production]** |
| Staging workspace | `/root/BORIS/.boris-staging`, `/root/BORIS/backend/.boris-staging`, `/root/BORIS/updates/staging` exist. Recent staged tree: `ocean-utm-20260912T093614Z`. | **[подтверждено production]** |
| Independent staging service/URL | No independently running staging host/service was established in this audit. The observed staging is a build/deploy workspace. | **[неизвестно]** |
| Documentation | `docs/` contains Constitution, workstream ownership, architecture and recovery/runbook files. | **[подтверждено кодом]** |
| Logs | Runtime logs exist under `backend/logs/`, top-level backend `*.log`, and systemd journal. | **[подтверждено production]** |
| Tests | `backend/tests/` contains a large suite spanning architecture, AI safety, Avito, social, telephony, CRM/MOP and deployment. | **[подтверждено кодом]** |
| Current tasks | GitHub open Issues = 0 and open PRs = 0 at snapshot time. BORIS also has an internal A2A/control-plane task system. Exact current internal queue was not fully extracted in this audit. | **[подтверждено production]** / **[неизвестно]** |
| AI prompts/rules | File prompts, DB prompt models, AI provider router, agent registry, social editorial authority and versioned rule/ownership docs are present. | **[подтверждено кодом]** |

## Repository versus production truth

- The production working tree is on Git branch `avito-money-core-production-20260909`, head `e9bde526b13c`. **[подтверждено production]**
- The server branch is ahead of its remote by 3 commits at snapshot time. **[подтверждено production]**
- The production working tree is heavily dirty: 192 unstaged files and 1549 untracked files were reported by the repository snapshot, with approximately 74,934 insertions and 10,184 deletions in tracked modifications. **[подтверждено production]**
- Therefore GitHub does **not** currently constitute a byte-for-byte representation of the live mutable server tree. **[подтверждено production]**
- Production APIs do not execute directly from that mutable tree. They execute from an immutable release directory under `/root/BORIS/releases/backend/<release-hash>/backend`. **[подтверждено production]**
- The handoff branch is documentation-only and deliberately does not attempt to commit or synchronize the dirty production tree. **[подтверждено production]**

## Current production runtime

Observed current backend release:

`/root/BORIS/releases/backend/d12da37ffb36d01bacb4f0c2bb52c00fcc6a2981df4d5bd5d6ca2ecb8a46e5df/backend`

| Runtime | Current state | Evidence |
|---|---|---|
| `boris-backend.service` | active/running, Uvicorn, port 8000, immutable release launcher preflight passed. | **[подтверждено production]** |
| `boris-backend-replica.service` | active/running, same immutable backend release, loopback port 8001, `/health` returned HTTP 200 in journal. | **[подтверждено production]** |
| `boris-frontend.service` | active/running, Next.js 16.2.9, port 3002. | **[подтверждено production]** |
| systemd failed units | `0 loaded units listed`. | **[подтверждено production]** |
| deploy mechanism | `boris-deploy.service` journal shows bounded Nginx upstream switching, `nginx -t`, reload, backend termination/restart and rollback-file handling. | **[подтверждено логами]** |

The old `docs/architecture.md` statement that all services are orchestrated by Docker Compose is stale for current production. **[подтверждено production]**

## Product areas present in code

The following major contours exist in the codebase; existence does not imply that every feature has been freshly end-to-end validated during this handoff:

- Avito integration, Feed Factory, campaigns, content, publication, bids/CPX/KPI and marketing control. **[подтверждено кодом]**
- MOP / AI sales-manager, messenger, CRM and reactivation. **[подтверждено кодом]**
- ROP / sales-management analysis and call/dialogue analysis. **[подтверждено кодом]**
- Social VK/Telegram generation, scheduling, delivery, retry and statistics. **[подтверждено кодом]**
- Telephony with MCN/Telphin/Asterisk-related runtime and browser SIP support. **[подтверждено кодом]**
- Email/prospecting/outreach and lead radar. **[подтверждено кодом]**
- AI provider routing, cost/safety guards, agent registry and internal A2A execution. **[подтверждено кодом]**
- System Brain/control plane, reliability events, guardians and self-heal. **[подтверждено кодом]**
- Video Factory exists. Native Instagram/YouTube autonomous publishing was not proven live by this audit. **[подтверждено кодом]** / **[неизвестно]**

## Fresh operational observations

- A current backend journal line recorded `legacy_suppressed_exception module=app.api.avito line=6391 type=RuntimeError fingerprint=209915dd887d`; surrounding HTTP requests still returned 200 and services remained healthy. Treat this as a signal requiring scoped diagnosis, not proof of an outage. **[подтверждено логами]**
- Recent Social recovery work established Telegram delivery and external VK Flood Control handling. VK error/rate-limit states are designed to be `waiting_external` rather than retry storms. **[подтверждено логами]** / **[подтверждено кодом]**
- `От Души` had a live Telegram delivery with message id 131 during the most recent recovery pass. **[подтверждено production]**
- `Маникюр Евпатория` and `От Души` social editorial configuration was moved to automatic publication with zero moderation delay in the canonical client authority; a later guard check preserved `mode=auto`, `auto_publish_hours=0`. **[подтверждено production]**
- At that same recovery window, VK delivery remained externally rate-limited and pending rather than internally failed. This is time-sensitive and must be rechecked before acting. **[подтверждено логами]**

## Historical acceptance evidence that remains useful, but is not a current global retest

- `docs/recovery/PRODUCTION_CONTROLLER.md` records `BORIS_SYSTEM_PRODUCTION=PASS` for a broad acceptance on 2026-08-27. **[подтверждено тестом]**
- The same recovery record documents 29/29 targeted reactivation transport tests on 2026-09-07 and 13/13 targeted Social transport tests on 2026-09-08. **[подтверждено тестом]**
- The phone checkpoint branch README records 114/114 phone tests and 208/208 telephony tests for that isolated checkpoint. **[подтверждено тестом]**
- These historical passes must not be presented as proof that the entire 2026-09-13 production tree has been freshly retested. **[подтверждено кодом]**

## Highest handoff risks

1. **Production provenance drift:** live mutable source, GitHub branches and immutable runtime release are three different states. **[подтверждено production]**
2. **Branch fragmentation:** the GitHub default branch is a specialized phone checkpoint while the server is on another branch. **[подтверждено кодом]**
3. **Large amount of backup/temp/history material in the live backend tree:** it increases discovery noise and makes naïve “search everything and patch” workflows dangerous. **[подтверждено production]**
4. **No independent staging runtime was established:** staging directories exist, but a stable staging deployment endpoint is not proven. **[неизвестно]**
5. **Exact active A2A/control-plane work queue was not fully extracted:** Manus must read it before claiming ownership of an active workstream. **[неизвестно]**
6. **Some documentation is stale:** especially the old Docker-based architecture description. **[подтверждено production]**

## Current priority for the next agent

The first priority is not feature coding. It is to establish exact source/release/task provenance without changing production: identify the immutable production release provenance, compare it to remote branches and current workstreams, then pick a single owned workstream and only then modify code in an isolated branch/worktree. **[предположение]**
