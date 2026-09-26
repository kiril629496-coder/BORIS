# MANUS 1/2 BRIDGE — BORIS Codex chats #1 and #6–10

UPDATED_UTC: 2026-09-26T14:22:00Z

## Purpose

Codex limit is exhausted. Continue the six requested BORIS chats through Manus without restarting work from chat history.

Requested sidebar chats:
- #1 — Изучить изменения БОРИС -> WORKSTREAM_02 -> sxc_A0S0X3M8
- #6 — Изучить БОРИС и собрать модули -> WORKSTREAM_01 -> sxc_VJF6FSWR
- #7 — Стабилизировать раздел объявлений -> WORKSTREAM_03 -> sxc_4EJ6EZGF
- #8 — Аудит автономности МОП -> WORKSTREAM_04 -> sxc_95ER53DT
- #9 — Довести модули объявлений -> WORKSTREAM_05 -> sxc_WJC053HW
- #10 — Провести аудит проекта БОРИС -> WORKSTREAM_06 -> sxc_T9BGAT43

WORKSTREAM_07 / агрегатор is NOT part of this handoff request.

## Both Manus agents see all six scopes

Default primary ownership only prevents concurrent edits:
- Manus 1: sidebar #1 / WS02, #7 / WS03, #9 / WS05.
- Manus 2: sidebar #6 / WS01, #8 / WS04, #10 / WS06.

Either Manus may take over another scope only after its current owner checkpoints RESULT / TESTS / EVIDENCE / BLOCKERS / NEXT_ACTION.

## Live server baseline — authoritative

Canonical production workspace: /root/BORIS
Branch: avito-money-core-production-20260909
Live committed HEAD: f752362022b3249dd7ea5bed66e0f6435d05dbc8
GitHub remote tracking SHA: d9e9f30873ea115fd262b6e948ef88cab30fda8a
Working tree at bridge creation: 6 tracked modified files, 460 untracked, 0 staged.

CRITICAL: the GitHub branch is behind the live server. GitHub access alone does not mean Manus sees current production code. Do not overwrite/rebase/reset the live server from GitHub. Before any code mutation, compare the exact live files/HEAD/ownership for that workstream. If Manus has only GitHub access, use this branch for continuity/context and isolated patch preparation until the live-server delta is reconciled.

## Mandatory sequence

READ CHECKPOINT -> VERIFY ONLY VOLATILE STATE -> CONTINUE NEXT_ACTION -> MINIMAL FIX -> AFFECTED TESTS -> REVIEW -> SAFE DEPLOY IF AUTHORIZED -> RUNTIME RECEIPT -> CHECKPOINT.

Do not repeat completed broad audits.
Preserve foreign workstream changes.
No force, no skip-verify, no blind reset/checkout/clean.
Do not disable MONEY_CONTOUR_PROTECTED.
Do not create client spend or publish to clients merely for tests.
MOP foreign/OpenAI calls remain disabled where the no-OpenAI contract applies.
Content API cost remains zero unless the owner explicitly changes that rule.
Preserve the approved BORIS concept/design/IA/terminology.

## Where to continue

Read the matching WORKSTREAM_0X.md in this directory, then continue its NEXT_ACTION.

Canonical live handoff on server:
- /root/BORIS/docs/AI_HANDOFF/INDEX.md
- /root/BORIS/docs/AI_HANDOFF/UNIVERSAL_PROTOCOL.md
- /root/BORIS/docs/AI_HANDOFF/MASTER_HANDOFF.md
- /root/BORIS/docs/AI_HANDOFF/MANUS_BRIDGE.md
- /root/BORIS/docs/AI_HANDOFF/WORKSTREAM_01.md ... WORKSTREAM_06.md
