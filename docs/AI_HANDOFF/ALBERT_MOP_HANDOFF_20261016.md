# ALBERT MOP / BUYER-TURN RESTORE — 2026-10-16

## Scope
Albert MOP primary acceptance. Shared MOP code only, no account configuration changes. Do not enable Timofey or change other client settings.

## Proven canonical evidence
On 2026-10-15 four Albert drafts switched to human_required after new buyer messages followed unattributed seller outgoing. On 2026-10-16 MOP paid, auto_send active, 24/7 time allowed, new contour; only an Avito system created-chat nudge rather than a new buyer message. One missing internal Telegram route caused 2,156 redundant route_missing event records.

## Patch
- Preserve exact Avito v3 incoming creation epoch from the same buyer message and pass through to core takeover fence. Local stored epoch only fallback. Later real buyer turn reopens AI unless a named employee explicitly claimed the conversation.
- Missing Telegram notification route: record only the first route_missing event for a given draft. Do not guess recipient, avoid cross-account sends, and allow normal notification when route is later set.
- No new external sends, historical retries, account/entitlement changes, or provider bypasses.

## QA evidence
- Separate branch chatgpt/albert-mop-buyer-turn-reopen-20261016
- 6 new targeted offline unit/source-flow tests PASS.
- 25 relevant existing regression tests PASS.
- Python compileall and git diff --check PASS.

## Integration requirements
Canonical /root/BORIS is dirty with concurrent changes in backend/app/api/messenger.py. Development branch must be reconciled by the authorized integration manager against live canonical and released by standard release train. No direct merge, production restart, or speculative rollback from this task.

## Pending
- Real new Avito inbound after deployment and successful send not yet observed, end-to-end provider acceptance NOT CHECKED.
- Manager outcomes from 15 October phone handoffs unconfirmed; do not blindly send again.
- Missing Telegram route remains; dedup fixes noise, not the routing configuration.
- Human-acknowledgement SLA must retain explicit human takeover, permissions and no-duplicate guards.
- Planeta 24/7 time gate verified separately (00:00-24:00 all seven days); this patch does not change it.
