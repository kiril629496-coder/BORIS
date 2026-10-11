# WS-OUTREACH continuation — 2026-09-27
AGENT: Codex
RESULT: CANDIDATE_TESTS_PASS; BOTH_PRODUCTION_ACCEPTANCE_NOT_PASS; NOT_DEPLOYED
CURRENT_SCOPE: Existing canonical email_queue, campaign 9 BORIS four-day trial and campaign 22 software development; no money/content/social changes.
LAST_VERIFIED_HEAD: f752362022b3249dd7ea5bed66e0f6435d05dbc8
BRANCH: avito-money-core-production-20260909
ACTIVE_RELEASE_BOTH_API_REPLICAS: 704c825d8831f1795dece52e4122ce844973f70b4598f0d2656731759fbf47c5
RUNTIME: email timer runs working backend; prospect_campaigns differs from immutable API release. Preserve 08:57 trigger fix.
LIVE_1015UTC: campaign9 active, ready43, sent_today4, latest queue3394 member25231 variantJ sent09:57:57UTC; mailbox2 SMTP/IMAP healthy; 4/4 emails have one tracking pixel. Campaign22 paused/paused pain_analysis_hardening, ready15 legacy (not evidence-ready), sent_today0; today's shortlist8 analysis_required/0 evidence-pass,37 platform_or_crm direct demand. Timers active; both APIs active. Since09:00UTC email journal InternalError/body-lock/enqueue failures=0.
WHAT_CHANGED: only isolated candidate and continuity artifacts. No application source, production DB, campaign status or service configuration changed by this task.
CANDIDATE: /root/BORIS/backend/run/changes/email-outreach-20260927-codex-1015
PATCH_SHA256: d698b055255646a8c88db2c2e1b58adb4f4aea032e070a002b6b1c2563e0c07b
TESTED_QA: /tmp/boris-email-qa-20260927-codex-v3
TESTS:145/145 PASS; isolated PostgreSQL schema-only plus synthetic rows; real stale trigger repair A-J, shared20 cap with21st blocked for either funnel, fairness22/9/22/9, email/reply/tracking/mailbox/opt-out;16/16 copy/banner JPEG/CID contracts.8/8 candidate hashes and source baselines matched.
SHADOW: actual corporate sites4hands score86/pass, BORODACH82/pass, ERSTA50/blocked. No real queue writes or SMTP in shadow. Evidence fixes prevent contacts/practical text becoming act/documents and interview booking becoming customer requests.
SAFETY_FINDING: live DB volume guard excludes development because account_idNULL; Python feeder currently shares owner cap20. Candidate extends existing DB guard to owner mailbox without increasing ramp/pacing.
BLOCKERS:
- Canonical patchcli is human-only. reviewed-auto requires executor author plus ACCEPT review, and new files require human bootstrap. Never forge human/executor/approval or use raw patch/apply as bypass. User was asked to choose authorized executor/review route or owner bootstrap; no response yet.
- Public discovery degraded: DDG challenges; Bing returns directories/noise. Four shadow queries yielded no accepted new business. Existing verified reserve can be analyzed, but automatic120-ready refill not proven.
LIMITATIONS: reporting has separate1/7/30day funnel stats/zero reasons; historical rejected-discovery count not persisted and explicitly unavailable; email-registration attribution unverified. Full discovery telemetry remains incomplete. No claim of complete production acceptance.
DO_NOT_REPEAT: no broad audit. Use scoped manifest, existing QA, live proof. Do not resubmit old identity-gate workarounds.
NEXT_ACTION:
1. Resolve legitimate release/bootstrap provenance using user's chosen path; do not activate22 yet.
2. Verify manifest hashes; candidate staged source must match tested after hashes. Include existing worker-only trigger fix when producing scoped immutable release from active release. Do not deploy full dirty tree.
3. Complete discovery telemetry through canonical schema/workstream if proceeding toward full reporting acceptance.
4. Run canonical analysis/refill on deployment; require several real evidence-pass buyers and zero ready contamination.
5. Set campaign9 canonical niche pool via actual control path; activate22 via set_status only after hardening acceptance.
6. Let minute worker send verified real buyer under shared cap20 and pacing20minutes; collect queue/member/banner/provider proof. Verify both replicas,worker,timers and persisted cursor.
7. Checkpoint again.
FILES:8 exact paths and before/after/release-before hashes are in candidate-manifest.json. No Git commit/push by this task.
