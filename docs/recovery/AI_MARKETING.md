# AI MARKETING / KPI AUTOPILOT — STABLE PRODUCT MODE — PASS

Date: 2026-08-27
Final acceptance: `AI_MARKETING_KPI_AUTOPILOT=PASS`
External Avito upload during acceptance: `NO`

## Authoritative modules

- KPI/API/state machine: `/root/BORIS/backend/app/api/avito.py`
- KPI runner: `/root/BORIS/backend/kpi_goal_runner.py`
- Campaign/Feed Factory API: `/root/BORIS/backend/app/api/campaigns.py`
- Campaign model: `/root/BORIS/backend/app/models/campaign.py`
- CampaignItem model: `/root/BORIS/backend/app/models/campaign_item.py`
- Canonical identity resolver: `/root/BORIS/backend/app/services/campaign_identity.py`
- CampaignItem service: `/root/BORIS/backend/app/services/campaign_service.py`
- Identity migration: `/root/BORIS/backend/migrations/004_campaign_item_identity.sql`
- AI Marketing UI/action memory: `/root/BORIS/frontend/app/dashboard/page.tsx`

Do not create a second KPI engine, second Feed Factory, or parallel advertisement table.

## Canonical CampaignItem / Avito identity contract

`CampaignItem` is authoritative for Feed Factory content. Identity is account-scoped and reversible:

`account_id + CampaignItem.id + feed_identity + avito_item_id`

Canonical fields on `campaign_items`:

- `account_id`
- `campaign_id`
- `external_item_id` — legacy compatibility; historically often the XML/feed `<Id>`
- `feed_identity` — stable Feed Factory/XML identity
- `avito_item_id` — real numeric Avito listing id
- `identity_status`
- `source`
- `source_ref`

Database guarantees:

- unique `(account_id, avito_item_id)` when Avito id is present;
- unique `(account_id, feed_identity)` when feed identity is present;
- existing unique campaign/source/source_ref protection remains in place.

Resolver entry point:

`app.services.campaign_identity.resolve(db, account_id, avito_item_id)`

No fuzzy title matching is authoritative. Historical recovery may use deterministic exact evidence only. Exact spin alternative + exact price + compatible category is accepted; multiple exact candidates remain unresolved.

## Existing production inventory recovery

Acceptance account: `prodazha_bytovok_25677`.

Fresh Avito inventory: 77 active items.

Identity reconciliation on 2026-08-27:

- linked/resolvable: 11;
- ambiguous: 0;
- unresolved legacy items: 66.

Unresolved legacy rows remain explicitly unresolved; they must not be linked by similarity. The unresolved list is persisted in `daily_stats:<date>.identity_reconciliation`.

Deterministic proof used for E2E:

- Avito item `8253533734`
- live title at original selection: `Бытовка усиленный каркас`
- price `170700`
- category `Ремонт и строительство`
- exact CampaignItem spin alternative in `CampaignItem #9`
- stable feed identity `planb-sale-06`
- result: `identity_status=resolved_exact_content`

A legacy live recovery object exists for Avito `2782697101` with `identity_status=external_linked_feed_unresolved`. It must not be represented as proof that its historical feed identity is known.

## Future publication identity

Feed Factory XML emits `feed_identity` as `<Id>`.

After a real publisher receives/knows the Avito item id it must persist the result through the canonical publication binding contract:

`bind_publication(db, account_id, feed_identity, avito_item_id, published_at, publication_version)`

HTTP callback is available on the existing campaign API:

`POST /api/campaigns/{campaign_id}/publication_identity`

This callback records an already-returned publication identity only. It does not upload anything to Avito. It is account-scoped, idempotent, conflict-safe and refuses an Avito id already bound to another CampaignItem.

## KPI contract and dynamic thresholds

KPI values are per-account/client from `kpi_settings`; examples such as 5 leads/day or CPL 500 are not universal defaults.

Root-cause evidence thresholds are derived from the account's observed distribution instead of hardcoded `10 views / 2% conversion`.

Acceptance evidence for `prodazha_bytovok_25677` during recovery included dynamic item traffic and conversion thresholds derived from the live account distribution.

## Mutation lifecycle

Authoritative internal lifecycle:

`ACTIVE KPI -> KPI GAP -> weak/winner detection -> AI proposal -> Fact Guard -> resolve CampaignItem -> mutate CampaignItem -> version -> Feed Factory validation -> safe feed -> external permission boundary -> publication identity -> later Avito statistics -> observation/effect -> KEEP/REVERT/ITERATE`

Title mutations now bridge through the same CampaignItem-aware `kpi_apply_changes` path. The old `kpi_apply_title` legacy `feed_items` precheck is no longer authoritative.

Allowed internal mutation identity is general and supports CampaignItem fields used by title/description/media/banner/price/geo/replacement workflows. Individual autonomous permissions/validators remain field-specific; identity lookup must not be reimplemented per action type.

## Version and action memory

Each CampaignItem mutation stores `optimization_versions` in the authoritative CampaignItem payload with:

- operation id;
- timestamp;
- reason;
- changed fields and old/new values;
- KPI before;
- observation window;
- real Avito item id;
- feed identity;
- result/decision;
- KPI after/observations after publication.

`kpi_apply_log` is the AI Marketing action-memory projection used by UI. UI endpoint:

`GET /api/avito/kpi_action_memory?account_id=...`

Acceptance operation:

- operation `identity-e2e-8253533734-v1`;
- Avito `8253533734`;
- Campaign `2`;
- CampaignItem `9`;
- feed identity `planb-sale-06`;
- version `1`;
- old title `{Усиленная бытовка 6 метров|Бытовка зимняя утеплённая|Бытовка усиленный каркас}`;
- new title `Бытовка с усиленным каркасом`;
- KPI before: 6 views, 0 contacts, 0% conversion in the selected snapshot;
- status `safe_feed_ready`;
- `changed_avito=false`;
- effect `waiting_external_permission`.

## Measurement linkage

`collect_stats` resolves each real Avito id back to CampaignItem and calls the shared observation linker. Measurement is fail-closed before publication: current internal draft returns `waiting_publish` and is not credited with live traffic.

After `bind_publication` records `resolution=publication_result`, later real Avito statistics are attached to the active CampaignItem optimization version. Observation records include snapshot date, views, contacts and conversion. After the configured observation window the version can resolve to `KEEP`, `REVERT`, or `ITERATE`.

Rollback-only QA used real stored statistics for Avito `8253533734` and proved:

- observation 2026-08-26: 17 views, 0 contacts -> `observing`;
- observation 2026-08-27: 6 views, 0 contacts -> `no_clear_effect`, decision `ITERATE`;
- QA transaction was rolled back because the title mutation was intentionally not externally published. Production attribution therefore remains truthful (`waiting_publish`).

## Feed Factory validation and safe export

A real internal mutation on CampaignItem #9 passed the existing category contract after normalizing canonical snake_case `ad_type`/`condition` to Avito contract fields `AdType`/`Condition` at validation/export time. No duplicate state was introduced.

Validation proof:

- CampaignItem `9` valid: true;
- errors: 0;
- warnings: 0;
- contract version `a1eb0c75e121`.

Safe export proof:

- file `backend/data/feed_exports/ai_marketing_identity_e2e_valid_20260827_173632.xml`;
- SHA-256 `2d2a923d62f0cf58377cad6b259f16226c19022c3d7e492ed4c826956cb139f8`;
- XML contains `<Id>planb-sale-06</Id>`;
- XML contains required `AdType` and `Condition`;
- external upload: false.

## UI

AI Marketing tab shows real action memory, including:

- Avito item;
- CampaignItem when useful for technical diagnostics;
- version;
- reason;
- KPI before;
- old/new values;
- mutation status;
- observation/effect status;
- safe-feed status;
- KPI after when a real post-publication observation exists.

Frontend build on 2026-08-27: PASS. `boris-frontend.service`: active. Runtime `/dashboard` on production frontend port 3002 returned HTTP 200.

## Services / runtime

- `boris-backend.service`: active after identity changes.
- `boris-frontend.service`: active after production build/restart.
- `kpi_goal_runner.py`: operational goal_auto runner; UID-scoped lock prevents SentinelX/root ownership collision.
- goal_auto account count at acceptance baseline: 11.

## Proven root causes and fixes

1. Analytics used numeric Avito ids while Feed Factory used factory ids (`planb-*`), with no canonical reverse mapping.
   - Fixed by account-scoped CampaignItem identity fields, DB uniqueness, resolver and reconciliation.
2. Legacy `kpi_apply_title` checked only `feed_items` before the CampaignItem resolver could run.
   - Fixed by bridging title apply to canonical `kpi_apply_changes`.
3. Future Feed Factory publications had no explicit persisted callback from stable feed id to returned Avito id.
   - Fixed by `bind_publication` and campaign publication-identity callback.
4. Measurement could not safely attribute later Avito statistics to a CampaignItem version.
   - Fixed by publication-gated `record_observation` called from `collect_stats`.
5. Feed validation read `AdType`/`Condition` only from category params while canonical payload stored `ad_type`/`condition`.
   - Fixed by normalization at validation/XML serialization without duplicating authoritative payload fields.
6. Safe internal CampaignItem operations could be mistaken for externally publishable operations.
   - CampaignItem KPI mutations now use `safe_feed_ready` / `waiting_external_permission`; they are not active publish operations.

Previously proven fixes retained: dynamic thresholds, UID lock, AI prepare, title proposals, Fact Guard, and removal of unsupported `prepare_weak_descriptions` from executable prepare queue.

## Known debt

- 66 of 77 legacy live ads in the acceptance account have no deterministic historical feed link and remain unresolved. Do not infer them from title similarity.
- Legacy recovery CampaignItems with `external_linked_feed_unresolved` are useful as live inventory records but do not prove the original historical XML/feed identity.
- External Avito publication was intentionally not performed in acceptance. A real future publisher must call the publication identity callback with the returned/known Avito item id before measurement attribution starts.
- Autonomous permissions for price/media/geo/deactivation remain governed by their own safety validators; shared identity does not grant mutation permission.

## Paths that must not return

- Do not restore hardcoded universal 10-view / 2%-conversion loser rules.
- Do not treat `external_item_id` as always meaning numeric Avito id; legacy semantics are mixed.
- Do not use fuzzy title matching as canonical identity.
- Do not silently bind ambiguous historical listings.
- Do not mutate legacy `feed_items` by numeric Avito id when a CampaignItem mapping exists.
- Do not create a second Feed Factory or a second ad identity table.
- Do not credit pre-publication statistics to an internal draft/version.
- Do not auto-upload a `safe_feed_ready` operation to Avito without a separate permission path.
- Do not rewrite Campaign/CampaignItem stateful workflow without new runtime evidence.

## Final acceptance

- `AVITO_CAMPAIGN_ITEM_IDENTITY=PASS`
- `ACCOUNT_SCOPED_MAPPING=PASS`
- `PERSISTENT_MAPPING=PASS`
- `IDEMPOTENT_MAPPING=PASS`
- `EXISTING_AD_RESOLUTION=PARTIAL_WITH_UNRESOLVED_LIST`
- `FUTURE_PUBLICATION_MAPPING=PASS`
- `AI_MARKETING_RESOLVER=PASS`
- `KPI_GAP_ENGINE=PASS`
- `DYNAMIC_THRESHOLDS=PASS`
- `WINNER_LOSER_DETECTION=PASS`
- `PROPOSAL=PASS`
- `FACT_GUARD=PASS`
- `CAMPAIGN_ITEM_RESOLUTION=PASS`
- `TITLE_MUTATION_E2E=PASS`
- `VERSION_HISTORY=PASS`
- `ACTION_MEMORY=PASS`
- `MEASUREMENT_LINKAGE=PASS`
- `FEED_FACTORY_INTEGRATION=PASS`
- `SAFE_FEED_AFTER_MUTATION=PASS`
- `CLOSED_LOOP_OPTIMIZATION=PASS` — publication/measurement leg proven transactionally with real stored statistics; production remains correctly waiting for external publication permission.
- `REAL_EXTERNAL_AVITO_UPLOAD=NO`
- `AI_MARKETING_UI_ACTION_VISIBILITY=PASS`
- `AI_MARKETING_KPI_AUTOPILOT=PASS`


## 2026-08-27 — Production Autopilot activation contract

Production evidence: repeated DRY-RUN was not an account-level safety requirement. Authoritative activation is Storage autopilot_settings mode=goal_auto plus valid client kpi_settings. The --apply runner still discovers only explicitly goal_auto accounts; always_ask, always_auto and dates are excluded.

Root cause: authoritative root crontab had kpi_goal_runner.py --dry-run every 10 minutes although 11 accounts were already goal_auto. This was scheduler configuration left in safe/pre-activation mode, not a KPI-engine requirement. No environment activation flag exists.

Minimal fix: existing root-crontab entry changed in place from --dry-run to --apply; no second runner. Temporary duplicate /etc/cron.d verification entry removed. Backup: /root/BORIS/_backups/root_crontab_before_kpi_apply_20260827_1801.

Natural 18:00 UTC evidence: APPLY discovered exactly 11 goal_auto accounts and processed at most one state-machine transition per account. Garik KPI met 9/6 -> no action; Andrey accounts advanced through safe executor; Dinara/Evgeniy advanced to prepare; other accounts stopped at existing blocked/unsupported gates. No artificial KPI gap and no external Avito publication.

Product UI gap closed: dashboard Autopilot settings exposes Автопилот по KPI (goal_auto), with individual KPI section already present. Frontend TypeScript QA PASS; .next build contains the control; frontend service active and /dashboard HTTP 200.

Status:
- SCHEDULER_AUTONOMOUS=PASS
- GOAL_AUTO_ACCOUNT_DISCOVERY=PASS
- INDIVIDUAL_KPI=PASS
- PRODUCTION_EXECUTION_MODE=PASS
- PRODUCTION_AUTOPILOT_MODE=PASS
- AI_MARKETING_FIRST_REAL_OPTIMIZATION=PENDING_REAL_EVENT
- external Avito QA publication remains forbidden.

## 2026-08-27 — KPI activation + practical product readiness (production-proven)

- KPI self-service remains authoritative in `Storage:kpi_settings`; autonomous activation remains authoritative in `Storage:autopilot_settings.mode=goal_auto`. No second settings store or mode was introduced.
- Proven product defect fixed: `set_kpi_settings` previously rewrote `autopilot_settings` to `always_ask/always_auto` from the bid-autopilot checkbox. A normal KPI edit could therefore silently disable `goal_auto`. KPI save now preserves the existing autopilot mode unless the user explicitly supplies `goal_auto_enabled`; explicit true maps to the existing `goal_auto`, explicit false to `always_ask`.
- KPI simple-save no longer silently zeros omitted `daily_budget_limit_rub` / `bid_autopilot`; omitted optional fields preserve the existing business settings.
- Product UX now presents plan leads + red CPL in business language and an explicit “Автоматически достигать KPI” consent control. Existing accounts were not mass-changed: production `goal_auto` count remained 11 after deployment.
- Added read-only `ai_marketing_readiness` projection over existing Account, kpi/autopilot Storage, detected_category, AccountSlot, CampaignItem identity, feed state, KPI check and existing action memory. It is not a second source of truth.
- Category type is based only on stored `detected_category` from `real_items_api`: exact `Предложение услуг` => services; other proven Avito categories => goods; missing evidence => unknown. Product requirement projects required tariff as services=`Расширенный`, goods=`Максимальный`.
- AVITO_TARIFF_DETECTION=UNAVAILABLE_FROM_CURRENT_DATA for exact commercial tariff name. Read-only Avito `/autoload/v2/profile` was proven to return autoload configuration (`autoload_enabled`, feed URL, schedule, uploadMode) but not the commercial tariff name. BORIS must not infer “Расширенный/Максимальный” from a successful autoload call. Readiness therefore exposes `required_tariff_active=null` and an explicit blocker until deterministic tariff evidence exists.
- Command Center/Home now has an AI Маркетолог block using real KPI/readiness/action-memory data, plus a non-aggressive attention card when the required Avito tariff cannot be confirmed. Animation honors `prefers-reduced-motion`. No invented Avito URL is shown.
- Customer timeline hides internal CampaignItem identifiers and reads existing action/version/observation memory; no second action log was created.
- Strategy registry is a projection of proven capabilities only: title optimization active; CPX bid adjustment active when evidence/gates permit; guarded replacement exists; first-image test is a guarded candidate; description optimization is explicitly marked not executable rather than falsely advertised.
- Read-only matrix over the 11 active `goal_auto` accounts proved: KPI configured 11/11, Avito credentials 11/11, Feed Factory evidence 11/11. Canonical mapping coverage varies by account and is reported honestly; no fuzzy backfill was performed.
- Production QA: backend py_compile PASS; frontend TypeScript PASS; Next production build PASS; `boris-backend` active; `boris-frontend` active; public `/dashboard/command-center` HTTP 200. Scheduler remained APPLY and the 18:20 natural cycle still discovered 11 goal_auto accounts.
- Guardian evidence at 18:20 UTC: KPI-met accounts continued NO ACTION; active prepare/apply states advanced one transition only; no mass activation and no QA publication were performed.
- Canonical `/dashboard/marketing` route was also updated (not only legacy dashboard tab): it loads authoritative autopilot/readiness state, saves explicit `goal_auto_enabled` consent with KPI, shows the human KPI sentence and tariff-unconfirmed warning, and hides CampaignItem technical IDs from the customer timeline. Final rebuild and production HTTP checks for `/dashboard/marketing` and `/dashboard/command-center` both PASS (200).

## 2026-08-27 — Account tariff evidence + capability-scoped readiness + publication bridge

- Commercial Avito tariff remains intentionally `UNAVAILABLE_FROM_CURRENT_DATA`: `/autoload/v2/profile` proves autoload configuration/capability but does not expose the commercial tariff name. No inference from API success is allowed.
- Added account-scoped tariff confirmation inside the existing `Storage:autopilot_settings` contract (no new model/store). Evidence fields: `state` (`CONFIRMED_ACTIVE|CONFIRMED_NOT_ACTIVE|NOT_CONFIRMED`), `required_tariff`, `actual_tariff`, `source=user_confirmation`, `confirmed_at`. `goal_auto` mode is preserved. A mismatched manually entered tariff cannot become `CONFIRMED_ACTIVE`.
- `/dashboard/marketing` now provides the explicit user action “Да, нужный тариф подключён” / “Нет, ещё не подключён”, labels the source as user confirmation, and never represents it as Avito API evidence.
- Activation remains explicit: KPI numbers alone do not enable advertising mutation. The existing checkbox “Автоматически достигать KPI” is the consent that maps to existing `autopilot_settings.mode=goal_auto`; button copy becomes “Сохранить и запустить” only when selected.
- Readiness is capability-scoped. Missing tariff does not block statistics, KPI-gap detection, or safe-change preparation. External feed publication is gated by confirmed required tariff plus deterministic publication mapping. Measurement requires Avito + mapping. No second permissions engine was introduced.
- Human readiness checklist reports Avito, KPI, autopilot, tariff confirmation, Feed Factory and deterministic ad coverage as “BORIS знает X из Y ваших объявлений”. Internal CampaignItem/resolver identifiers are not exposed.
- Mapping semantics were corrected: `publication_mapping_ready` now means exact CampaignItem `feed_identity` -> authoritative `feed_items.id`, not merely presence of an Avito item id. Separate `avito_identity_items` is retained for diagnostics.
- Production read-only matrix over all 11 goal_auto accounts after correction: KPI 11/11; Avito connection 11/11; tariff confirmation 0/11 (honest NOT_CONFIRMED); Feed Factory evidence 11/11. Exact publication mapping coverage: 3411770 0/0; Andrey Penza 151/313; Andrey Moscow 152/354; Dinara 34/39; Evgeniy 0/1; Evacuators 40/40; Garik 899/920; Georgiy 20/28; otdushi 70/140; PBI 0/2; prodazha_bytovok 10/12.
- Production publication-leg defect found: CampaignItem mutations ended at `safe_feed_ready`, while `kpi_goal_tick` only knew legacy `feed_applied -> kpi_publish_changes -> feed_send_to_avito`. Thus safe Feed Factory versions had no state-machine bridge into the authoritative account feed.
- Fixed by adding a fail-closed bridge `safe_feed_ready -> exact feed_identity match -> validate full existing FeedItem set -> regenerate existing account feed -> feed_applied`. It performs no Avito call. Imported live-only CampaignItems without exact feed identity remain blocked instead of fuzzy-matched.
- Existing external publication path is reused unchanged after the bridge: `feed_applied -> kpi_publish_changes -> _kpi_publish_adapter -> feed_send_to_avito -> official /autoload/v1/upload -> publish_requested -> kpi_confirm_publish`. External publication now additionally fails closed unless the required commercial tariff has explicit account-scoped confirmation.
- `bind_publication()` remains the authoritative identity contract for publication-returned identities and observation starts only from `publication_result`. Existing live-item confirmation still marks KPI apply operations published only after the expected live value is observed. No QA publication was executed.
- Action timeline is translated into entrepreneur-facing stages (safe variant, working feed, sent to Avito, published/collecting statistics, improved/worse only when evidence exists). Existing action memory remains the sole log.
- Production QA: backend `py_compile` PASS; frontend TypeScript PASS; Next production build PASS; backend/frontend active; `/dashboard/marketing` HTTP 200; OpenAPI exposes tariff-confirmation and readiness endpoints. Existing `goal_auto` count remained exactly 11.
- Natural 18:30 UTC scheduler cycle continued in APPLY mode with 11 goal_auto accounts while this work proceeded. No manual external publication was used for acceptance.

Status:
- KPI_ACTIVATION_UX=PASS
- TARIFF_CONFIRMATION_UX=PASS
- TARIFF_EVIDENCE_PERSISTENCE=PASS (contract + existing Storage persistence; no fabricated confirmation inserted for production accounts)
- NO_FAKE_TARIFF_DETECTION=PASS
- ACCOUNT_READINESS_MATRIX=PASS
- CAPABILITY_SCOPED_GATES=PASS
- AUTOPILOT_ACCOUNT_CONTROL=PASS
- ACTION_TIMELINE=PASS
- PUBLICATION_LEG_CONTRACT=PASS
- EXISTING_11_GOAL_AUTO_UNCHANGED=PASS
- REAL_DATA_ONLY=PASS
- PRODUCTION_AUTOPILOT_MODE=PASS
- FIRST_REAL_OPTIMIZATION=PROGRESS
- REAL_PUBLICATION_MAPPING=PROGRESS (deterministic coverage exists, incomplete on some accounts)
- POST_ACTION_MEASUREMENT=PENDING
- LEARNING_LOOP=PENDING

## 2026-08-27 — Account activation operationalization + deterministic mapping maximization

- Continued from the existing KPI autopilot baseline; no second engine/publisher/resolver/settings store and no mass tariff confirmation.
- Canonical `/dashboard/marketing` now shows a prominent account-scoped warning when goal_auto is active but external publication is tariff-gated: autopilot analysis/safe preparation continues, while publication remains stopped until explicit user confirmation.
- Legacy/Home marketing surface now loads the same authoritative `ai_marketing_readiness` projection and shows operational business fields: plan, obtained leads, red CPL, current CPL, autopilot state, current BORIS work, today's last action, blockers and the direct tariff-confirmation CTA for the selected account.
- Readiness now reports legacy feed-only accounts honestly: if CampaignItem inventory is absent but authoritative feed identities exist, deterministic feed coverage is shown from the feed itself rather than misleading `0/0`. Production proof: `3411770_94346` is now `30/30`; this does not fabricate an Avito live identity.
- Deterministic mapping debt pass was executed over all 11 goal_auto accounts using exact authoritative feed identity/content evidence only. No unique additional links were provable (`DETERMINISTIC_UPDATES=0`). 70 exact-content candidates in `otdushi` were explicitly ambiguous because repeated generated content maps to multiple feed IDs; they were left unresolved. No fuzzy mapping was used.
- Current deterministic coverage: 3411770 30/30 feed identities; Andrey Penza 151/313; Andrey Moscow 152/354; Dinara 34/39; Evgeniy 0/1; Evacuators 40/40; Garik 899/920; Georgiy 20/28; otdushi 70/140; PBI 0/2; prodazha_bytovok 10/12. Live-only unresolved records for Dinara/Evgeniy/Bытовки had no Avito ID inside authoritative feed data and remain unresolved rather than guessed.
- All 11 remain KPI active + goal_auto + Avito connected. Tariff evidence remains honestly NOT_CONFIRMED for all 11, therefore external publication remains capability-gated while analysis/preparation continues.
- Production frontend TypeScript PASS and Next production build PASS; public `/dashboard/marketing` HTTP 200; backend active after reload. Existing goal_auto account count remains 11.
- The natural 18:40 scheduler slot overlapped a frontend/build deployment interval and saw backend connection-refused for that one slot. This is recorded as deployment overlap, not KPI logic evidence; no operation was duplicated and no QA publication occurred. Next normal cron remains authoritative guardian evidence.

Status additions:
- ACCOUNT_ACTIVATION_PRODUCT_FLOW=PASS
- TARIFF_CONFIRMATION_FLOW=PASS
- DETERMINISTIC_MAPPING_MAXIMIZED=PASS
- DUPLICATE_ACTION_LOOP=NO
- FUZZY_MAPPING=NO
- FAKE_TARIFF_DETECTION=NO
- REAL_DATA_ONLY=PASS
- QA_PUBLICATION=NO
- FIRST_REAL_AUTONOMOUS_PUBLICATION=PENDING_USER_TARIFF_EVIDENCE
- LIVE_VERSION_CONFIRMATION=PENDING_PUBLICATION
- POST_ACTION_MEASUREMENT=PENDING_PUBLICATION
- KPI_AFTER=PENDING_OBSERVATION_WINDOW
- LEARNING_LOOP=PENDING_FIRST_KPI_AFTER

### 2026-08-27 — first natural production publication request + operational queue
- `prodazha_bytovok_25677`, operation `kpi-ai-prepare_weak_titles-97880faf4dc7:apply:8253463410`, CampaignItem 5 / `planb-sale-02` / version 1 reached the existing scheduler publication leg naturally at 20:20 UTC.
- Production feed precheck proved the public feed contains exactly one `<Id>planb-sale-02</Id>` with title `Блок-контейнер длиной 5 метров`; the old title is absent from that feed item.
- Account tariff evidence remained `CONFIRMED_ACTIVE`, source `user_confirmation`, required/actual tariff `Максимальный`; publication capability was allowed.
- Official Avito autoload accepted the feed; operation moved `feed_applied -> publish_requested` at `2026-08-27T20:20:04.632721`, while `changed_avito=false` remained correct until live proof.
- Natural 20:30 scheduler cycle executed `confirm_publish`; Avito still returned the old live title `Бытовка 5х2,35 от производителя`, so the operation correctly remained `publish_requested / waiting_avito_confirmation`. No duplicate publication was created.
- Post-publication contract is autonomous: scheduler `publish_requested -> kpi_confirm_publish`; exact live match -> conflict-safe `bind_publication()` -> `published_at` -> observation starts next complete calendar day; `published -> kpi_effect_check`; after sufficient complete days -> effect verdict -> `kpi_effect_resolve` (`keep`, `wait`, `try_next_action`, or rollback). No owner action is required on this leg.
- AI Marketing Product UI now includes a real-data work queue derived from existing `kpi_action_memory`: waiting publication, measuring, effective, ineffective, and next action. No second action log/storage was created. Production frontend build `pzN_h_V-qxzMwUjyNsBsm` deployed atomically; `/dashboard/marketing` returned HTTP 200.
- Next read-only optimization candidate for sale account (not activated): Avito `8253233731`, CampaignItem 4 / `planb-sale-01`; deterministic mapping exists, 5 views / 0 contacts across the available 8-day snapshots, and it has no active KPI operation. This is a candidate only; current volume is weak evidence and the decision engine must still re-evaluate before any action.

## 2026-08-27 — Daily Virtual Marketer product workspace

Production-proven additions on top of the existing KPI autopilot (no second engine/storage/publisher/attribution system):

- Added read-only `ai_marketing_daily_workspace` projection over existing KPI check/root-cause/orchestrator, action memory/version/effect data, readiness, MOP and CRM. It groups owner-facing work as: `Нужно улучшить`, `BORIS работает`, `Ждём Avito`, `Измеряем результат`, `Стало лучше`, `Не помогло`, `Нужно решение владельца`.
- Marketing UI now consumes that projection and renders business lifecycle language instead of exposing CampaignItem/feed/version state in the primary UX. It also renders a truthful daily summary: problems found, solutions prepared, sent/published, measuring, results received, owner actions required.
- Command Center/Home AI Marketing summary now reduces the module to plan/fact, CPL/red CPL, main bottleneck, current BORIS work, last proven result and whether owner action is required. Synthetic results are forbidden.
- Targeted account-switch reset added to Marketing, Command Center and legacy dashboard Marketing state. Runtime A→B→A proof: `prodazha_bytovok_25677 6/3 + waiting Avito=1` → `garik_plitka_mo_58647 6/9 + waiting=0` → back to Bытовки `6/3 + waiting=1`; previous tenant data is cleared before fetch.
- Existing scheduler remains account-parallel. Natural 21:00 UTC production cycle processed all 11 goal_auto accounts concurrently; one waiting publication did not prevent other accounts from reaching no_action, safe_executor, ai_prepare, safe_feed_promote or goal_met.
- While an item is `publish_requested` or in observation, the existing Goal Runner now performs additional read-only root-cause analysis of OTHER items in the same account. It does not create a second operation or AI call for the waiting item.
- Prepare cycle now excludes any item with an active publication/observation mutation, enforcing one active change per item. Production invariant scan: `MULTI_ACTIVE_ITEM_VIOLATIONS=[]`.
- Publication invariant scan: `MULTI_PUBLICATION_SAME_ITEM_VERSION=[]`. Target `8253463410 / planb-sale-02 / v1` remains exactly one `publish_requested`, no duplicate send.
- Learning guard: an item whose title strategy produced `worse`, rollback, or `try_next_action/no_clear_result` is excluded from another title prepare unless a different proven path is selected. This prevents endless same-failed-title repetition.
- Effect classification was tightened to the business KPI: views/conversion alone cannot make a version EFFECTIVE. `delta_contacts_per_day > 0` is required for `improved`; views/conversion and exact-item MOP/CRM are secondary context. Sales data is read from existing MOP/CRM tables and explicitly marked context-only unless deterministic attribution exists.
- Learning provenance projection stores account/category/item/change fields/reason/KPI before/KPI after/result/decision with `same_account_same_item_same_version_only` provenance and `not_universal`; one successful case is never promoted into a universal rule.
- State-safe pure learning QA: `improved→keep/Стало лучше`, `worse→rollback/Не помогло`, `insufficient_data→wait/Измеряем результат`, `no_clear_effect→try_next_action/Не помогло`.
- Goal-met projection fixed: Garik 9/6 and PBI 4/3 show no next optimization and no owner action for advertising changes; BORIS does not optimize a working account merely because weak items exist.
- Next-candidate projection now requires deterministic canonical identity. Historical unresolved items are not surfaced as executable candidates.
- Second candidate `8253233731`: historical deterministic stats total 25 views / 0 contacts across 22 source-day snapshots, but it is not currently in the account-relative judgeable weak set. Decision is `WAIT_FOR_EVIDENCE`; it becomes eligible only when a current snapshot crosses the dynamic threshold (currently 3 views) with zero contacts/low conversion or other deterministic evidence emerges. No optimization was created.
- For `prodazha_bytovok_25677`, current daily workspace: plan 6, fact 3, 10 evidence-based weak items, 4 prepared existing operations, 1 awaiting Avito live confirmation, 0 measuring/results, 0 owner actions. All currently weak + canonical-mapped items are already active; unresolved historical weak items remain non-executable.
- Existing MOP/CRM sales-funnel context is shown without causal overclaiming. Per-item measurement also reads exact account+Avito item MOP/CRM records where available; no second attribution engine was created.
- Production frontend deploy followed immutable contract. `verify-frontend-deploy-contract.sh=PASS`, isolated build/deploy PASS, and active immutable artifact contains `Работа BORIS` + `Сегодня BORIS`. Marketing and Command Center return HTTP 200. Concurrent isolated builds remained non-blocking.
- QA tariff evidence residue: `QA_TARIFF_ROWS=0`. No QA Avito publication and no real-client send were performed by this product work.
- Natural 21:00 UTC runner: all 11 goal_auto accounts processed; target Bытовки stayed `confirm_publish -> waiting` because Avito live title is still old (`Бытовка 5х2,35 от производителя`) vs expected (`Блок-контейнер длиной 5 метров`). No duplicate publication was created. This external live leg remains guardian-only background evidence and does not block product execution.

Acceptance additions:
- AI_MARKETING_DAILY_WORKSPACE=PASS
- REAL_11_ACCOUNT_WORK_QUEUE=PASS
- ACCOUNT_PARALLELISM=PASS
- WAITING_PUBLICATION_DOES_NOT_BLOCK_ACCOUNT=PASS
- SECOND_CANDIDATE_DECISION=PASS
- HUMAN_READABLE_LIFECYCLE=PASS
- PUBLICATION_QUEUE_UX=PASS
- OBSERVATION_QUEUE_UX=PASS
- SUCCESS_RESULT_UX=PASS
- FAILED_RESULT_UX=PASS
- INCONCLUSIVE_RESULT_UX=PASS
- LEARNING_CONTRACT=PASS
- FAILED_STRATEGY_REPEAT_GUARD=PASS
- LEARNING_PROVENANCE=PASS
- SALES_FUNNEL_CONTEXT=PASS
- OWNER_DAILY_REPORT=PASS
- HOME_AI_MARKETING_SUMMARY=PASS
- STALE_ACCOUNT_MARKETING_DATA=0
- CROSS_ACCOUNT_MARKETING_LEAKAGE=0
- NO_SECOND_MARKETING_ENGINE=PASS
- NO_SECOND_ATTRIBUTION_ENGINE=PASS
- REAL_DATA_ONLY=PASS
- QA_PUBLICATION=NO
- REAL_CLIENT_SEND=NO
- QA_RESIDUE=0
- FRONTEND_DEPLOY_CONTRACT=PASS
- PRODUCTION_RUNTIME=PASS

External lifecycle remains truthful:
- FIRST_REAL_AUTONOMOUS_PUBLICATION=PENDING_EXTERNAL_EVENT
- KPI_AFTER=PENDING_OBSERVATION_WINDOW
- LEARNING_LOOP=PENDING_KPI_AFTER


## 2026-08-31 production lifecycle continuation
Fresh production DB proof supersedes the older pending markers above without rewriting historical evidence.
- `planb-sale-02 / Avito 8253463410` completed the first real autonomous publication lifecycle through post-action measurement: publication is bound canonically, complete-day KPI_AFTER evidence was recorded for 2026-08-29..2026-08-30 (`views=2`, `contacts=0`), and learning resolved to `try_next_action` / `effect_no_clear_result`.
- The next canonical operation `identity-e2e-8253533734-v1 / planb-sale-06 / Avito 8253533734` advanced naturally from `publish_requested` to `published`; `changed_avito=true`, `published_at=2026-08-31T19:09:23.217202`, and `publication_binding.status=exists` with `identity_status=published_identity_bound`.
- No second publisher, KPI engine, attribution engine, or duplicate operation was introduced.

Current truthful lifecycle state:
- FIRST_REAL_AUTONOMOUS_PUBLICATION=PASS
- FIRST_REAL_KPI_AFTER=PASS
- FIRST_REAL_LEARNING_LOOP=PASS
- NEXT_PUBLICATION_LIVE_CONFIRMATION=PASS (`planb-sale-06`)
- NEXT_KPI_AFTER=PENDING_OBSERVATION_WINDOW
- NEXT_LEARNING_LOOP=PENDING_KPI_AFTER

## 2026-09-01 — natural hourly DB-pool recovery acceptance
Fresh natural production evidence after the session/pool fixes supersedes the earlier QueuePool failures without rewriting historical logs.
- Natural `boris-ai-marketer.service` cycle started `2026-09-01T18:05:06Z` and completed `2026-09-01T18:12:15Z` with systemd `status=0/SUCCESS`.
- All 9 goal_auto accounts reached the authoritative serial `kpi_goal_runner` path; there were zero `QueuePool TimeoutError` / account exception lines in this cycle.
- Existing CPX portfolio stage completed before KPI goal stage; no second scheduler/engine was introduced.
- A separate item-level Avito CPX capability issue remained visible for `vasyailchenko_82650`: live read-only `cpxpromo/getBids` returned HTTP 403 for the affected new items while normal account stats/balance remained healthy. Existing `apply_one` now classifies this exact item-level 403 as fail-closed `blocked / cpx_promotion_unavailable` instead of a BORIS runtime `error`; no Avito write is attempted on that branch.
- `kpi_goal_runner` observability is now fail-closed for genuine `new_feed_ramp.status=error`: it logs bounded failure reasons and returns account failure so the hourly unit cannot silently report green if a real ramp runtime error remains.
- py_compile PASS; read-only real 403 classification QA PASS; backend replicas restarted and `:8000`, `:8001`, public `/health` all HTTP 200; `systemctl --failed` empty.

Acceptance:
- AI_MARKETING_DB_POOL_RECOVERY=PASS
- NATURAL_HOURLY_POOL_ACCEPTANCE=PASS
- AVITO_CPX_ITEM_403_CLASSIFICATION=PASS
- HIDDEN_RAMP_ERROR_GREEN_STATUS=FIXED
- NEXT_KPI_AFTER=PENDING_OBSERVATION_WINDOW
