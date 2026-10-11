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

## Shared access and ownership

Both Manus 1 and Manus 2 must read this file, INDEX.md, UNIVERSAL_PROTOCOL.md, MASTER_HANDOFF.md and all six WORKSTREAM files.

Default primary ownership to avoid two agents editing the same scope at once:
- Manus 1 primary: sidebar #1 / WS02, #7 / WS03, #9 / WS05.
- Manus 2 primary: sidebar #6 / WS01, #8 / WS04, #10 / WS06.

Either Manus may take over any of the six only after the current primary has checkpointed RESULT / TESTS / EVIDENCE / BLOCKERS / NEXT_ACTION. Do not run two writers against the same files/scope.

## Live server baseline — authoritative

Canonical production workspace: /root/BORIS
Branch: avito-money-core-production-20260909
Live committed HEAD: f752362022b3249dd7ea5bed66e0f6435d05dbc8
GitHub remote tracking SHA: d9e9f30873ea115fd262b6e948ef88cab30fda8a
Current working tree at bridge creation: 6 tracked modified files, 460 untracked, 0 staged.

CRITICAL: the GitHub branch is behind the live server. A GitHub connection by itself does NOT mean Manus sees current production code. Never overwrite/rebase/reset the live server from GitHub. Before any code mutation, compare the exact live files/HEAD/ownership relevant to the workstream. If Manus has only GitHub access and no live-server access, restrict work to analysis/docs/isolated patch preparation until the live-server delta is reconciled.

## Mandatory execution rule

READ CHECKPOINT -> VERIFY ONLY VOLATILE STATE -> CONTINUE NEXT_ACTION -> MINIMAL FIX -> AFFECTED TESTS -> REVIEW -> SAFE DEPLOY IF AUTHORIZED -> RUNTIME RECEIPT -> CHECKPOINT.

Do not repeat broad audits already completed.
Preserve foreign workstream changes.
No force, no skip-verify, no blind reset/checkout/clean.
Do not disable MONEY_CONTOUR_PROTECTED.
Do not create client spend or publish to clients merely for testing.
MOP foreign/OpenAI calls must remain disabled where the no-OpenAI contract applies.
Content API cost remains zero unless the owner explicitly changes that rule.
Do not change approved BORIS concept/design/IA/terminology without explicit owner approval.

## Current NEXT_ACTION by scope

### Manus 1

WS02 / sidebar #1 — Изучить изменения БОРИС
Classify current live source deltas by workstream, separate generated/runtime noise, and publish a fresh live-release vs working-tree vs GitHub map. Revalidate against current HEAD f752362 and current dirty state; do not rely on the older 0c629768 checkpoint.

WS03 / sidebar #7 — Стабилизировать раздел объявлений
Run a fresh authenticated real-user production browser pass for selected-account persistence and one complete campaign UI path. Backend mass-edit proof belongs to WS05.

WS05 / sidebar #9 — Довести модули объявлений
Run sanctioned real-account Browser Gateway battle tests and capture before/after/verification receipts for existing non-BORIS ads, safe mass edit, Strategy Engine deficit behavior, and remaining price/parameter mappings. Do not create spend just to prove the flow.

### Manus 2

WS01 / sidebar #6 — Изучить БОРИС и собрать модули
Take one real content item through semantic/banner validation -> canonical XML -> existing publication lifecycle -> receipt, while preserving local-first/API-cost-zero policy and avoiding unnecessary external publication.

WS04 / sidebar #8 — Аудит автономности МОП
Determine why production sees active MOP accounts = 0. Then run replay-quality plus one safe active-account acceptance with OpenAI unavailable; verify knowledge gaps, CRM/handoff, duplicate safety and no cross-account data contamination.

WS06 / sidebar #10 — Провести аудит проекта БОРИС
Resolve/attribute each current P1 and timer/guard failure, separate external waits from internal defects, and finalize the audit with evidence-backed priorities. No blind cleanup.

## Checkpoint rule for both Manus agents

After each meaningful completed stage, update:
1. the matching SentinelX context;
2. the matching /root/BORIS/docs/AI_HANDOFF/WORKSTREAM_XX.md;
3. this bridge only if ownership or global baseline changes.

If SentinelX is not available inside Manus, update the repository-visible handoff copy and clearly mark SENTINELX_NOT_AVAILABLE; the next authorized agent will reconcile it into the canonical server context.

