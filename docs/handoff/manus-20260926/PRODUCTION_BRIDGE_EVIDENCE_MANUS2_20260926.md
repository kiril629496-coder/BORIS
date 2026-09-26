# PRODUCTION BRIDGE EVIDENCE — Manus 2

Created UTC: 2026-09-26T15:18Z
Source of truth: live /root/BORIS via SentinelX
Mode: read-only evidence collection; no deploy/restart/provider call/client send/publication/spend.

## Global live baseline

- branch: avito-money-core-production-20260909
- live HEAD: f752362022b3249dd7ea5bed66e0f6435d05dbc8
- GitHub remote tracking SHA: d9e9f30873ea115fd262b6e948ef88cab30fda8a
- working tree: 6 tracked modified / 461 untracked / 0 staged
- GitHub is behind live production.

## WS01 evidence

Deployed file SHA-256:
- backend/app/api/avito.py: f18dd9b8378422b863e2cf97f42478f782c447e13f6316e2330c2939526a1185
- backend/app/api/banners.py: 0254b030be48ad38aef62f633933e1ec542c5fba94e3056bad66b2cf008407b0
- backend/app/api/parser.py: cffb12071cc8cbde9b6855ab902b3d1caf8d089247df514e02974b37c4b7ab76
- backend/scripts/avito_feed_lifecycle_guard.py: 4ee1ae480f083b5afe23c5e18c3c7b95d1f04e2e8962a0092d62d921ac25c973
- backend/app/api/feed_contract.py: 8a6c3fcfd1a53482d9c91162a1b1114b375eae855afc452febb066c54e944bad
- backend/app/models/campaign_item.py: 1c1f128d86809d23dbf7e78642303927f468d304e64c5a51733bdac971933fa9

Current production code path exists in:
- backend/app/services/jobs.py (campaign content/media/banner/publication lifecycle)
- backend/app/api/campaigns.py (media semantic validation and banner QA)
- backend/app/api/avito.py (canonical feed and publication)
- backend/scripts/avito_feed_lifecycle_guard.py
- backend/app/models/campaign_item.py

DB read-only search found no current rows containing the exact historical handoff strings banner_not_canonically_reviewed / publication_eligible / semantic_validation. Therefore the old handoff sample is historical/stale evidence, not a currently locatable persisted row by those exact markers.

Recent live campaign items show current semantic evidence and publication identities. Several published rows have banner_qa empty/null while semantic media evidence exists; do NOT mutate them from this evidence alone. One deterministic example is redacted item_hash 28479e269858 / account_hash aca1c10ae692: status=published, identity_status=published_identity_bound, banner present, banner_qa={}, semantic evidence count=8. This is not proof of a defect by itself; trace current canonical acceptance code before proposing a patch.

NEXT: reconcile historical handoff sample semantics with current jobs.py/campaigns.py contracts and determine whether WS01 should close the stale blocker or identify a current reproducible missing receipt. No new item/publication/provider call.

## WS04 evidence

Critical correction to stale clone interpretation:

1. backend/mop.log prints "аккаунтов с активным МОПом: 0", but that log belongs to legacy messenger_runner.py.
2. messenger_runner.accounts_with_mop explicitly excludes accounts where mop_modes.contour='new'.
3. Production DB has 8 MOP ai_bindings and all 8 are contour='new'; owner_enabled=true.
4. Therefore legacy mop.log = 0 is expected and MUST NOT be treated as proof that production MOP is inactive.

Current canonical poller:
- backend/app/api/messenger.py lines ~6040-6190
- boris-background-worker.service: active/running
- reliability heartbeat messenger/poller: state=ok, fresh
- separate mop_send_failed_selfheal heartbeat reported active_mop_accounts=6 on the current poller path.

Bound-account entitlement snapshot at evidence time:
- 4 of the 8 ai_binding accounts were currently package-active + owner-enabled.
- canonical poller may include additional reactivation-enabled accounts, explaining heartbeat active_mop_accounts > bound-active count.

No-OpenAI current live call graph:
- messenger.generate_ai_draft_reply calls app.services.sales_ai_router.generate_text(... module='mop' ...)
- sales_ai_router._mop_provider_order intentionally excludes OpenAI.
- Current observed provider_order for every bound MOP account: ["gigachat","ollama"].
- sales_ai_router._gigachat_default_call is a direct GigaChat call and explicitly has "No hidden OpenAI fallback."
- The generic backend/gigachat_pool.py chat_with_fallback still contains OpenAI fallback for other BORIS callers, but canonical MOP sales_ai_router does not route through that generic fallback.
- backend/tests/test_mop_no_openai_autonomy.py contains explicit no-OpenAI contract coverage.

Conclusion: DO NOT patch MOP based on the stale clone claim "direct OpenAI route/general GigaChat fallback can return to OpenAI" unless you prove a current canonical MOP call path into it. Current live source evidence contradicts that proposed defect.

NEXT: revise WS04 diagnosis around the canonical new contour. Continue with replay/quality/knowledge-gap/CRM-handoff/duplicate/isolation acceptance using current call graph, not legacy mop.log.

## WS06 evidence

Current timer/service facts:
- boris-system-brain.service latest result: success / exit 0.
- boris-brain-acceptance.service currently exits 2 because status is NOT_READY; score remains 100.0.
- latest observed hard gates: OPEN_P0=0, OPEN_INTERNAL_P1=4, CRITICAL_DRIFT=0, CRITICAL_STALE_EVIDENCE=0, STATE_READ_ERRORS=0.
- waiting dependencies: OWNER_MONEY_POLICY=1, EXTERNAL_P1=2.
- boris-guardian-quick.service currently exits 2.
- boris-avito-visual-guard.service currently exits 97.

Current open P1 rows = 7:
- 1 MONEY_GUARD_MISSING_LIMIT / marketing / owner-policy dependency.
- 1 SYSTEM_INVARIANT_FAILED / control_plane / internal.
- 5 OWNER_SOCIAL_DAILY_PLAN_FAILED / social.
  - 2 old social incidents have dependency_state=waiting_external.
  - 3 newer social incidents have no waiting_external marker and are counted internal.
Thus 7 = 1 owner policy + 2 external + 4 internal, matching Brain acceptance.

Critical correction to stale watchdog candidate:
- backend/scripts/avito_visual_guard_watchdog.sh currently uses:
  set +e
  "$@"
  local rc=$?
  set -e
- It already preserves the child exit code correctly.
- The stale hypothesis "if ! command; rc=$? loses the child rc" does NOT match current live script and must not be patched.

NEXT:
- classify/fix only the 4 current internal P1 causes.
- keep owner money policy as OWNER ACTION, not an internal defect.
- keep the 2 waiting_external social incidents out of internal-P1 remediation.
- inspect current avito visual guard rc=97 by exact failing step from live evidence before any patch; do not patch exit-code handling.
- inspect guardian-quick exit 2 similarly.
- do not restart services merely to collect evidence.

## Required Manus 2 response

Update WS01/WS04/WS06 checkpoints from this live evidence.
Do not repeat broad audit.
Do not patch stale-clone hypotheses contradicted by production.
Return:
1. REVISED_WS01_DIAGNOSIS
2. REVISED_WS04_DIAGNOSIS
3. REVISED_WS06_DIAGNOSIS
4. EXACT_REMAINING_EVIDENCE_REQUESTS only where still needed
5. MINIMAL_PATCH_PLAN only for defects proven against current live source
6. NEXT_ACTION for each scope.

Do not write Task completed while any acceptance criterion remains unproven.
