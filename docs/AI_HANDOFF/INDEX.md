# Universal AI shared handoff for BORIS

Master SentinelX context: `sentinelx_context_sxc_EHE399B0`

This directory is the repository-visible continuity layer for Codex, ChatGPT, Manus and any subsequent AI agent. SentinelX contexts are the cross-chat/model state layer. Every agent must also read `UNIVERSAL_PROTOCOL.md`, `MASTER_HANDOFF.md`, `NEXT_AI_TASK.md`, and `CROSS_AI_SYNC.md`. For the 2026-09-26 Codex -> Manus 1/2 handoff of sidebar chats #1 and #6–10, also read `MANUS_BRIDGE.md`.

## Mandatory protocol

1. Before working, read `/root/BORIS/AGENTS.md`, this file, and the matching workstream file.
2. Resume the matching SentinelX context and revalidate current filesystem/database/service state.
3. Continue from the recorded NEXT_ACTION; do not repeat completed work unless live evidence invalidates it.
4. After each meaningful stage, update BOTH:
   - the matching SentinelX context;
   - the matching `WORKSTREAM_XX.md` file.
5. Record only concise operational state:
   RESULT / WHAT_CHANGED / TESTS / EVIDENCE / BLOCKERS / NEXT_ACTION / FILES_OR_COMMITS.
6. Before handing work to another model/chat, checkpoint first.
7. Historical chat text is not source of truth; live system state remains authoritative.

## Workstreams

- 01: Изучить БОРИС и собрать модули — `sentinelx_context_sxc_VJF6FSWR` — file `WORKSTREAM_01.md`
- 02: Изучить изменения БОРИС — `sentinelx_context_sxc_A0S0X3M8` — file `WORKSTREAM_02.md`
- 03: Стабилизировать раздел (название в sidebar обрезано) — `sentinelx_context_sxc_4EJ6EZGF` — file `WORKSTREAM_03.md`
- 04: Аудит автономности МОП — `sentinelx_context_sxc_95ER53DT` — file `WORKSTREAM_04.md`
- 05: Довести модули объявлений — `sentinelx_context_sxc_WJC053HW` — file `WORKSTREAM_05.md`
- 06: Провести аудит проекта БОРИС — `sentinelx_context_sxc_T9BGAT43` — file `WORKSTREAM_06.md`
- 07: Оценить разработку агрегатор (название в sidebar обрезано) — `sentinelx_context_sxc_CCPSKPKH` — file `WORKSTREAM_07.md`

## Handoff direction

Codex stops/limit reached → checkpoint → Manus/ChatGPT/next authorized AI resumes latest context → checkpoint → next agent resumes.

Any AI stops → checkpoint first → next AI revalidates volatile facts and continues NEXT_ACTION.

Provider/model chat history is never the canonical state.

Never create a second independent implementation for the same production behavior.

- Clients510 execution 2026-10-08: `sentinelx_context_sxc_CBWCVS36` — file `WORKSTREAM_CLIENTS510_EXECUTION_20261008.md`
- Social reliability — Ocean / Маникюр / От Души: `sentinelx_context_sxc_EN8PA8XY` — file `WORKSTREAM_SOCIAL_RELIABILITY.md`

- Crowd SEO delivery recovery: `sentinelx_context_sxc_WVA36AZZ` — file `WORKSTREAM_CROWD_SEO.md`
