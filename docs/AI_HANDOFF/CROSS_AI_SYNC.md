# CROSS-AI ONLINE SYNC PROTOCOL

Purpose: make Codex, ChatGPT, Manus accounts and future AI agents continue the same BORIS/UASAAAP work from the latest verified state.

## Core rule

No AI chat is source of truth.

Shared source of truth:
1. canonical live repository/workspace;
2. remote Git branch/commit where available;
3. project checkpoint/handoff file;
4. live runtime/server evidence;
5. SentinelX continuity context where available.

## Before every work session

The agent MUST:
1. identify project (BORIS or UASAAAP);
2. read project contract and handoff;
3. fetch/read latest remote state when remote access exists;
4. compare local HEAD with remote HEAD;
5. inspect dirty state and active ownership;
6. revalidate only volatile runtime facts;
7. continue NEXT_ACTION, not restart the project.

If local state is behind remote, update safely before coding.
If local state diverges or is dirty, DO NOT overwrite; checkpoint and reconcile first.

## During work

After every meaningful completed scope:
- update checkpoint;
- record exact changed files;
- record tests/evidence;
- record current HEAD/branch or archive identity;
- record next executable action.

## End / handoff

Before stopping for any reason:
- save checkpoint;
- push/commit only when the agent has an authorized Git workspace and safe remote state;
- otherwise export changed files + checkpoint artifact and clearly mark NOT_COMMITTED;
- next agent must start by comparing against latest shared state.

## Design / concept lock

All agents inherit current approved concept/design/UX/IA/naming.
No redesign or product-direction changes without explicit owner approval.
New ideas go to SUGGESTIONS only.

## BORIS

Server/live state remains authoritative for production behavior.
Canonical continuity files:
/root/BORIS/docs/AI_HANDOFF/
Master SentinelX context:
sentinelx_context_sxc_EHE399B0

## UASAAAP

Canonical project branch:
feat/local-demand-marketplace-v2
Canonical handoff:
docs/v2/SYNC_HANDOFF.md
Persistent SentinelX context:
sentinelx_context_sxc_0MQR55VH

When a Git remote is available to the current agent, it must compare remote SHA before writing/pushing.
When only an exported archive is available, it must treat the supplied archive HEAD + SYNC_HANDOFF as the base and must not claim Git commit/push.

## Mandatory takeover sequence

READ SHARED STATE -> COMPARE HEAD/ARTIFACT -> VERIFY DIRTY/OWNERSHIP -> CONTINUE NEXT_ACTION -> TEST -> CHECKPOINT -> SYNC SHARED STATE -> NEXT AGENT

## ACCEPTED ordinary V3 — 2026-10-09T06:49:49.446110+00:00
Релиз `c3b55ea2be818ac802689ec9d4b1cc6ecd08507a1a6877d17c752108c406cee5` accepted `2026-10-09T06:37:16.847646+00:00`; canonical331passed, отдельныйPG110passed, runtimeAPI1/API2/worker совпали. Текущий итог: `/root/BORIS/outputs/clients510-execution-20261008/resume-20261009-domain-v3-f57/HANDOFF_20261009.md`. Старые нижние разделы NOT_INSTALLED/restore31 исторические и отменены этим receipt. Source foreignWIP сохранён, повторное восстановление31 не требуется. Protectedостаток и full_TZ/72h остаются открыты.

