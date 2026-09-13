# BORIS AI — KNOWN PROBLEMS

This file distinguishes verified problems from assumptions and unknowns. It must not be used to claim a problem still exists without rechecking time-sensitive external states.

## Evidence labels

`[подтверждено кодом]` · `[подтверждено тестом]` · `[подтверждено логами]` · `[подтверждено production]` · `[предположение]` · `[неизвестно]`

## P0/P1 — Source and release provenance fragmentation

The live server Git working tree is on `avito-money-core-production-20260909`, head `e9bde526b13c`, is ahead of remote by 3 commits and is heavily dirty (192 unstaged, 1549 untracked in the snapshot). **[подтверждено production]**

The production API does not run from that mutable tree; it runs from immutable release hash `d12da37ffb36d01bacb4f0c2bb52c00fcc6a2981df4d5bd5d6ca2ecb8a46e5df`. **[подтверждено production]**

The GitHub default branch is an isolated Phone/MCN checkpoint rather than the full production source state. **[подтверждено кодом]**

**Risk:** an agent can patch the wrong branch/tree and believe it modified production-equivalent code. **[предположение]**

**Required action:** establish immutable release provenance and the correct workstream base before code changes. **[предположение]**

## P1 — No proven independent staging runtime

Staging directories exist under `/root/BORIS/.boris-staging`, `/root/BORIS/backend/.boris-staging` and `/root/BORIS/updates/staging`. **[подтверждено production]**

A separately running staging frontend/API URL/service was not established in this audit. **[неизвестно]**

**Risk:** “tested on staging” may mean tested in a build workspace rather than a real deployment. **[предположение]**

## P1 — Documentation drift

`docs/architecture.md` describes Docker Compose orchestration. Current production is systemd + immutable release launcher + Nginx upstream switching. **[подтверждено production]**

**Risk:** a new agent can choose the wrong deployment procedure. **[предположение]**

## P1 — Current Avito suppressed exception signal

A fresh production backend journal entry recorded:

`legacy_suppressed_exception module=app.api.avito line=6391 type=RuntimeError fingerprint=209915dd887d`

**[подтверждено логами]**

Surrounding Avito/browser-gateway requests continued returning HTTP 200 and backend services remained active. **[подтверждено логами]**

**Status:** signal requires scoped diagnosis; it is not proven to be an active user-facing outage. **[неизвестно]**

## P1/P2 — Social VK Flood Control/backoff

During the most recent Social recovery window, VK was in a shared external rate-limit/Flood Control state. **[подтверждено логами]**

The code correctly preserves partial deliveries and marks the VK part `waiting_external` rather than retrying aggressively. **[подтверждено кодом]**

**Status:** time-sensitive. It may already have cleared; recheck current readiness before intervention. **[неизвестно]**

## P1 — Internal current task queue not fully captured

GitHub has zero open Issues and zero open PRs at the handoff snapshot. **[подтверждено production]**

BORIS has a separate internal A2A/control-plane task system. **[подтверждено кодом]**

The exact current active internal A2A orders/tasks were not fully extracted during this audit. **[неизвестно]**

**Risk:** Manus may duplicate work already owned by another agent/workstream. **[предположение]**

## P2 — Large live-tree historical/backup noise

The production tree contains many `.bak`, `before_*`, temporary, staged, generated and historical recovery artifacts adjacent to current code. **[подтверждено production]**

**Risk:** search results can surface obsolete implementations that should not be edited or resurrected. **[предположение]**

**Rule:** identify the imported/runtime file and workstream authority; do not select code by filename recency alone. **[предположение]**

## P2 — Requirements/dependency inventory may be incomplete

`backend/requirements.txt` is only 11 lines while the codebase imports and operates many subsystems. **[подтверждено кодом]**

Whether all dependencies are intentionally supplied outside that requirements file or the file is incomplete was not established. **[неизвестно]**

**Required verification:** clean isolated environment install/build before changing dependency declarations. **[предположение]**

## P2 — Historical PASS evidence can be misread as current PASS

Recovery docs record broad historical acceptance and targeted passing suites. **[подтверждено тестом]**

No fresh full-suite result for the exact current production release was generated during this handoff. **[неизвестно]**

**Risk:** reporting 100% based on stale acceptance. **[предположение]**

## P2 — Native Instagram/YouTube publishing not proven

Video Factory exists. **[подтверждено кодом]**

A native production OAuth/publish/readback pipeline for Instagram and YouTube was not proven in this audit. **[неизвестно]**

Do not claim this capability as live without route, token/config and external publish/readback evidence. **[предположение]**

## P2 — Social configuration complexity

Social behavior spans `posting_runner.py`, transport readiness/rescue jobs, client editorial authority/config and systemd timers. **[подтверждено кодом]**

Recent repairs were needed because project mode/transport state/editorial guards can interact. **[подтверждено логами]**

**Risk:** fixing only one layer can be reverted by an authority/guard or leave delivery blocked. **[предположение]**

## P2 — External dependency states must not be “fixed” internally

The system has explicit external waiting states for AI providers and VK. **[подтверждено кодом]**

Retry storms, paid replays or bypassing cooldowns can worsen the incident or duplicate side effects. **[подтверждено кодом]**

## P3 — Old generic architecture docs need consolidation

Multiple recovery/runbook files accurately describe specific historical checkpoints, but there is no single freshly regenerated architecture/state document that cleanly reconciles current GitHub, immutable release, server source and internal task ownership. **[подтверждено кодом]**

This handoff package is a first consolidation, not a replacement for automated provenance/state reporting. **[предположение]**

## Things explicitly NOT proven broken

- Backend primary: active. **[подтверждено production]**
- Backend replica: active and health 200 observed. **[подтверждено production]**
- Frontend: active. **[подтверждено production]**
- systemd failed units: zero at snapshot. **[подтверждено production]**
- Telegram Social as a whole: recent live delivery exists. **[подтверждено production]**

Do not turn a known warning, blocked external provider or historical incident into a blanket “BORIS is down” conclusion. **[подтверждено кодом]**
