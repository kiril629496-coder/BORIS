# Universal continuation protocol for Manus 1/2

1. Live filesystem/database/service state is authoritative.
2. Read the matching checkpoint before changing code.
3. Revalidate only volatile facts.
4. Continue NEXT_ACTION; do not restart broad analysis.
5. Preserve foreign workstream changes and current approved product/design/IA/terminology.
6. Never force/reset/clean or skip verification to make a result look green.
7. Never create client spend or external publication merely for testing.
8. Keep MONEY_CONTOUR_PROTECTED enabled.
9. For MOP no-OpenAI acceptance, foreign/OpenAI calls remain zero.
10. Checkpoint before stopping or transferring ownership.

Checkpoint fields:
AGENT / TIMESTAMP_UTC / RESULT / CURRENT_SCOPE / LAST_VERIFIED_HEAD / BRANCH / DIRTY_OR_UNCOMMITTED_RELEVANT_FILES / WHAT_CHANGED / TESTS / EVIDENCE / DEPLOYED_STATE / RUNTIME_STATE / BLOCKERS / DO_NOT_REPEAT / NEXT_ACTION / FILES_OR_COMMITS.
