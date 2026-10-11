# MANUS OWNER TASK — 2026-09-28

STATUS: ACTIVE
OWNER_REQUEST: continue nonstop; ChatGPT supervises and rechecks live production.
MASTER_CONTEXT: sentinelx_context_sxc_EHE399B0
LIVE_ROOT: /root/BORIS
LIVE_HEAD_AT_ASSIGNMENT: f752362022b3249dd7ea5bed66e0f6435d05dbc8
BRANCH: avito-money-core-production-20260909

## Non-negotiable execution contract

Read AGENTS.md, WORKSTREAM_OWNERSHIP.md, AI_HANDOFF/INDEX.md, matching workstream checkpoint and SentinelX context before mutation.

Do not repeat closed PASS work unless fresh live evidence proves regression.
Do not create a second implementation owned by another workstream.
Diagnose -> minimal fix -> affected tests -> safe deploy -> runtime evidence -> checkpoint.
No force/reset/clean/skip-verify.
Do not create spend or client-facing messages merely for testing.
Do not publish test ads merely to prove a flow.
Blocker in one scope must not stop independent scopes.

## Live account identity — already revalidated from production DB 2026-09-28

- Garik: garik_plitka_mo_58647
- Timofey building materials SPb: greench84_48016 (production name: "Тимофей строит материалы"; created 2026-08-29 18:32:01)
- Maria: mbroker_27859
- Artashes / PBI Lemo: pbi_artashes_plitka_mo_83993
- Evgeniy Steklostroy partitions: evgeniy_peregorodki_12447

Never substitute a similar account.

## Live KPI/marketer facts — revalidated 2026-09-28 around 13:45 UTC

### Garik
- KPI target_leads_per_day=4
- hard_max_bid_rub=150
- autopilot mode=goal_auto
- canonical marketing entitlement=UNKNOWN, no explicit current paid period
- virtual marketer runtime=BLOCKED / SAFE_WAIT because paid period is not confirmed
- budget brake=within_budget
Root issue: KPI exists but money execution is fail-closed on missing entitlement.

### Timofey
- no kpi_settings
- no autopilot_settings
- no canonical marketer entitlement
- marketing_inventory_audit exists
Root issue: account is not provisioned into the full KPI/autopilot contour.

### Maria
- KPI target_leads_per_day=4
- hard_max_bid_rub=150
- autopilot mode=always_auto
- canonical entitlement active at check time, paid_until 2026-09-28T16:17:46Z
- rollout preflight budget=4000, spent=250, active ads=27
- runtime mode=CONTENT_RESCUE
- runtime evidence: fact 0/4, expected 2.83; no proven converting ads safe for bid raise
- health=ok non-money cycle
Root issue: no proven converting content; content + campaign quality is current bottleneck, not blind bid increase.

### Artashes / PBI Lemo
- KPI target_leads_per_day=4
- hard_max_bid_rub=150
- autopilot mode=goal_auto
- canonical entitlement active at check time, paid_until 2026-09-28T22:18:11Z
- rollout preflight budget=3000, spent=1795, active ads=214
- runtime mode=RED_CPL_RECOVERY
- account health deferred reason=non_money_money_mutation_fence
- account slot connected/tariff_ok=true but messenger sync error: Avito HTTP 402 subscription required
Root issues: paid period expires today; red CPL recovery active; Messenger API 402 is an external dependency and must remain fail-closed.

### Evgeniy
- KPI target_leads_per_day=5
- hard_max_bid_rub=150
- autopilot mode=goal_auto
- canonical entitlement active at check time, paid_until 2026-09-28T22:18:11Z
- rollout preflight budget=3000, spent=2291, active ads=706
- runtime mode=MEASUREMENT_WAIT
- 2/2 money measurement slots occupied
- account health deferred until a full clean post-publication day; complete_after=2026-09-29T00:00:00Z
- Browser Gateway exact-account state remains stale/offline: extension 2.1.11 while packaged current is 3.0.0
Root issues: do not bypass measurement wait; HAG exact-account browser gateway remains external/human/browser gate for provider write/readback tests.

## Universal MOP rule requested by owner

Use last 2 months of available conversation history to form confirmed account-specific knowledge.
AI handles the dialog until a human employee replies in that dialog.
Human reply => immediate handoff/pause for that dialog.
No AI reply after human reply, no duplicate answer, no repeated greeting, no human-vs-AI writer race.
Keep cross-account isolation.
WS04 was already accepted PASS on autonomy/duplicate/CRM handoff. Do NOT rewrite it blindly; perform only a focused regression against this exact human-handoff requirement and fix only if live evidence demonstrates a gap.

## MANUS 1 assignment
URL: https://manus.im/app/4QNA9c9nHMr9adPGAP7yvI
Primary workstreams: WS02 / WS03 / WS05

### A. Timofey content
Source priority:
1. https://spb.grandline.ru/katalog/krovlya/metallocherepitsa/
2. facade
3. fence

For greench84_48016:
- inventory first, no duplicates;
- 5 materially different ads for roofing/metal tile;
- 5 materially different ads for facade;
- 5 materially different ads for fence;
- minimum 15 total;
- each ad gets a separate banner file with CTA + verified benefit;
- "price from" only from verified source data;
- if product photos are insufficient, use only the currently allowed low-cost media pipeline with explicit generation cost guard;
- validate source/product facts -> copy -> media -> banner -> preview -> publication readiness;
- do not fake publication; use READY_TO_PUBLISH where external publish is not safely authorized.

### B. Maria content gap closure
For mbroker_27859 inventory and complete each direction to 5 distinct ads:
- mortgage approval assistance
- refinancing and consolidation
- loan secured by real estate
- sale and purchase of real estate
- new buildings

Each ad: own banner + relevant images.
No invented rates, guaranteed approval or guaranteed financial result.
Return matrix: direction | existed | added | final | IDs | banner IDs/files | readiness.
Maria runtime currently CONTENT_RESCUE 0/4, so prioritize content evidence, not unsafe bid growth.

### C. WS05
Preserve existing PARTIAL_HOLD_93 blocker. Do not bypass Browser Gateway.
Only after exact verified HAG gateway 3.0.0 is connected may WS05 run the sanctioned reversible provider pilot.

Checkpoint all results back to matching workstreams/SentinelX.

## MANUS 2 assignment
URL: https://manus.im/app/BqtRHt4lpdGhZ2oc4coyXj
Primary workstreams: WS01 / WS04 / WS06 plus coordination with WS-AVITO-MONEY owner. Do not duplicate money-policy implementation.

### A. Garik
For garik_plitka_mo_58647:
- focused MOP human-handoff regression only; reuse accepted WS04 behavior;
- restore/activate commercial service period through canonical billing/entitlement path through 2026-10-05 inclusive, without inventing payment evidence or bypassing billing ownership;
- if owner-authorized manual commercial extension path already exists, use that; otherwise report exact missing authorized path and continue independent work;
- after entitlement becomes active, verify goal_auto resumes and show runtime evidence;
- KPI=4/day, cap=150 are current live facts.

### B. Artashes / PBI Lemo
For pbi_artashes_plitka_mo_83993:
- focused MOP regression only;
- extend/restore package through 2026-10-05 through canonical commercial path;
- do not bypass external Avito Messenger 402; keep MOP fail-closed where provider entitlement is missing;
- verify KPI 4/day and RED_CPL_RECOVERY;
- do not mass-disable promotion; use guarded recovery and evidence.

### C. Evgeniy
For evgeniy_peregorodki_12447:
- do not alter MOP unless live regression appears;
- KPI/autopilot only;
- respect 2/2 measurement wait and complete_after 2026-09-29T00:00:00Z;
- do not force a new raise while measurement slots are full;
- do not bypass Browser Gateway requirement for external provider verification;
- continue independent non-provider diagnostics, and after the clean-day gate use canonical marketer flow.

### D. Systemic KPI failure analysis
Across paid/active production accounts, distinguish:
- missing entitlement;
- missing KPI provisioning;
- content deficit;
- red CPL recovery;
- measurement saturation;
- provider/API external blocker;
- scheduler/worker/runtime failure.
Do not flatten all cases into one fix.

### E. MAX reporting contour
First inspect real existing MAX integration/API capability.
Target per client:
1. BORIS | <Client> | РАБОТА — lead/human-needed/overdue/error/operational events.
2. BORIS | <Client> | РУКОВОДСТВО — daily KPI plan/fact, leads, CPL/CPX, budget/spend, ads/campaigns, problem -> action -> result, 7-day trend.

Canonical registry:
account_id -> client_key -> employee_max_chat_id -> management_max_chat_id -> recipients/roles -> status -> last_delivery_at.

Must be idempotent.
If MAX API cannot create groups, implement owner-assisted bind flow rather than pretending it can.
Prove one safe internal delivery to both routes before rollout.

## Marketing policy the owner wants preserved

- about 90% positions at minimum bid;
- 5–10% elevated tests;
- daily budget mandatory;
- target 10–30 views/day where applicable;
- hard max CPX 150 RUB unless more restrictive account truth applies;
- do not remove ads merely to optimize;
- add new ads as content policy permits;
- KPI reviewed daily;
- conversion >=20%: bounded increase up to +100% of minimum view price under all canonical money guards;
- if leads continue after increase: subsequent +20% daily steps only when evidence and CPL/budget guards allow;
- every 3–7 days recalculate red CPL and never cross it;
- aim to use agreed daily budget while still optimizing lead KPI.
Canonical WS-AVITO-MONEY guards remain authoritative. Do not create parallel logic just from this text.

## Required final format from each Manus agent

RESULT
WHAT_CHANGED
TESTS
RUNTIME_EVIDENCE
ACCOUNT_STATUS_TABLE
BLOCKERS
NEXT_ACTION
FILES_OR_COMMITS

No "100%" without live runtime evidence.

## EVENING OWNER OVERRIDE — 2026-09-28

MANUS LIGHT ONLY. Continue the EXISTING task/chat; do not create replacement Manus tasks.

Before any further work, read the complete section:
`OWNER FULL CONTINUATION — 2026-09-28 EVENING — MANUS LIGHT ONLY`
in:
`/root/BORIS/docs/AI_HANDOFF/MANUS_SUPERVISOR_OUTBOX.md`

That section is the full owner instruction and intentionally contains the complete client/content/MAX/Planeta/Crowd/outreach/manicure/UI/UASAAAP continuation. Short earlier “continue” notes are not sufficient and must not be used as the task definition when they omit requirements from the full section.

For Manus 1 (4QNA9c9nHMr9adPGAP7yvI): execute its complete Timofey + Maria + WS05 section.
For Manus 2 (BqtRHt4lpdGhZ2oc4coyXj): execute its complete Garik + Artashes + Evgeniy + KPI + MAX section.
Checkpoint actual live results; do not stop at analysis or a report.
