# UNIVERSAL AI CONTINUITY PROTOCOL

This file is mandatory for every AI agent working on BORIS.

## Goal

Any agent — Codex, ChatGPT, Manus, or another AI — must be able to continue from the exact last verified point without restarting the task from chat history.

## Source of truth order

1. Live filesystem / database / service state.
2. Repository ownership and project contracts.
3. Matching SentinelX continuity context.
4. Matching WORKSTREAM_XX.md checkpoint.
5. Chat history only as supplemental historical context.

## Start protocol

Before changing production code:

1. Read /root/BORIS/AGENTS.md.
2. Read /root/BORIS/docs/AI_HANDOFF/INDEX.md.
3. Read /root/BORIS/docs/AI_HANDOFF/MASTER_HANDOFF.md.
4. Identify the owning workstream.
5. Read its WORKSTREAM_XX.md.
6. Resume its SentinelX context.
7. Revalidate live HEAD, dirty files, active writers/ownership, services and the exact runtime evidence relevant to the task.
8. Continue from NEXT_ACTION. Do not repeat completed work unless fresh evidence invalidates it.

## Stop / handoff protocol

Before stopping for any reason — completion, model switch, credit/token exhaustion, tool limitation, user interruption or blocker — checkpoint first.

Update BOTH:
- matching SentinelX context;
- matching WORKSTREAM_XX.md.

The checkpoint must contain:

AGENT
TIMESTAMP_UTC
RESULT
CURRENT_SCOPE
LAST_VERIFIED_HEAD
BRANCH
DIRTY_OR_UNCOMMITTED_RELEVANT_FILES
WHAT_CHANGED
TESTS
EVIDENCE
DEPLOYED_STATE
RUNTIME_STATE
BLOCKERS
DO_NOT_REPEAT
NEXT_ACTION
FILES_OR_COMMITS

Never write PASS when the acceptance criterion was not actually tested.

## Agent takeover protocol

The next AI must NOT start with a fresh broad audit.

It must:
- read the checkpoint;
- verify only volatile facts;
- continue NEXT_ACTION;
- preserve foreign workstream changes;
- avoid duplicate implementations;
- checkpoint again before it stops.

## Credit / limit exhaustion

No provider-specific automatic credit detector is assumed.

If an agent detects that it cannot continue because of provider credits, rate limits, context limits or tool access:
1. stop starting new implementation work;
2. persist the exact current state;
3. record the next executable action;
4. leave the tree safe;
5. hand off.

## Provider neutrality

The protocol is intentionally provider-neutral:
Codex -> checkpoint -> Manus -> checkpoint -> ChatGPT/other AI -> checkpoint -> Codex.

No agent chat is the canonical memory.

## Concept / design continuity lock

All agents, including Manus, must preserve the existing approved product concept and visual system.

Before changing UI, UX, IA, flows, copy structure, naming, visual language, component behavior, or product concept:
1. inspect the current implemented product and the latest approved design/context artifacts;
2. identify the existing design system, interaction patterns, information hierarchy, terminology, and user flows;
3. continue that system rather than inventing a new one.

Forbidden without explicit owner approval:
- redesigning the product because the agent prefers another aesthetic;
- changing navigation structure or core flows for stylistic reasons;
- replacing established terminology, product positioning, module names, or visual language;
- introducing a new design system, component library, layout paradigm, or UX concept that conflicts with the current one;
- removing existing functionality in order to simplify the interface;
- changing the product concept, audience, monetization logic, or module hierarchy based on an agent's own product opinions.

When the agent sees a possible improvement:
- keep the current approved implementation intact;
- record the idea under SUGGESTIONS;
- explain the benefit and impact;
- do not implement it until explicitly approved if it changes concept/design direction.

Design takeover rule:
CURRENT APPROVED PRODUCT + CURRENT DESIGN SYSTEM + CURRENT FLOWS
→ preserve
→ extend consistently
→ verify visually and functionally
→ only then hand off.

The next agent must inherit both engineering state and product/design intent, not just code state.
