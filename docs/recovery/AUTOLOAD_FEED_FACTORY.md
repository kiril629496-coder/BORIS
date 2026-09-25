# AUTOLOAD / FEED FACTORY

# MODULE PURPOSE
Клиентский product flow вкладки «Объявления»: человеческое ТЗ → уточнения → план → CampaignItem → тексты/media/banner/category/price/geo → validation → preview → approval → safe feed. Реальная загрузка в Avito не входит в этот contract.

# CURRENT KNOWN GOOD STATE
LAST_VERIFIED=2026-08-27
AUTOLOAD_FEED_FACTORY_E2E=PASS
AUTOLOAD_PRODUCT_MODE=PASS
REAL_AVITO_UPLOAD=NO
REAL_AD_PUBLICATION=NO

# AUTHORITATIVE COMPONENTS
- Campaign / CampaignItem — authoritative campaign/item state.
- Campaign.settings_json.feed_factory — persisted wizard state.
- existing category templates + category tree + feed_contract.
- existing media library / MediaLink and banner modules.
- existing draft/feed storage, campaign bridge, validation, approval, preview, safe_export.
- frontend entry: /dashboard/campaign; workspace: /dashboard/campaign/[id].
- No parallel Feed Factory exists or is allowed.

# PRODUCTION DATA FLOW
Объявления → «Создать кампанию / автозагрузку» → brief → smart questions → generation plan → materialize CampaignItem → content/media/banner/category/attributes/price/geo mutations → validation → human preview → approve selected/all → safe_export of approved+valid items only.

# IDENTITIES AND CONTRACTS
- CampaignItem is the mutation layer for Feed Factory and AI Marketing Autopilot.
- category template_id remains internal; client UI shows human category paths/questions, not technical IDs/JSON.
- MediaAsset is reused through MediaLink; no second media library.
- safe_export includes only status=ready items that still validate.

# BUSINESS LOGIC CONTRACTS
- Ask only data actually missing after brief + account/company/profile + category contract.
- Existing answers are not asked again.
- Missing required category values are phrased as human questions.
- Plan is visible before mass generation and shows directions, item count, geo, category, text/media/price readiness.
- Price/geo can be applied to selected items, one direction, or the campaign.
- Validation gives item-specific messages and a direct «Исправить здесь» action.
- User can approve all ready, approve selected, or return selected to draft.

# FIXES APPLIED
- Humanized product layer over already-proven stateful workflow; no workflow replacement.
- /dashboard/campaign now shows real campaign cards with item counts, ready/action-needed state, last update, Continue CTA, Create CTA.
- campaign_public exposes updated_at for campaign-list UX.
- workspace hides CampaignItem/template_id/category JSON technical controls from normal client presentation.
- added human plan summary, bulk price/geo controls, real media thumbnails/replace action, per-item text regenerate action, detailed validation UX and card preview.

# IMPORTANT RUNTIME CONTRACTS
- Wizard state reloads from backend on page mount; closing/reloading does not discard progress.
- Production Next frontend and backend remain protected by normal auth.
- Safe export is local preparation only; it performs no external Avito upload/publication.

# QA / ACCEPTANCE EVIDENCE
2026-08-27 production build: Next.js compile PASS, TypeScript PASS, static/dynamic route generation PASS.
Browser UI QA through production-built frontend + real backend:
ADS_ENTRY_UX=PASS
CREATE_CAMPAIGN_UX=PASS
BRIEF_UX=PASS
SMART_QUESTION_UX=PASS
PLAN_UX=PASS
TEXT_UX=PASS
MEDIA_UX=PASS
BANNER_UX=PASS
CATEGORY_ATTRIBUTES_UX=PASS
PRICE_GEO_UX=PASS
ITEM_PREVIEW_UX=PASS
APPROVAL_UX=PASS
VALIDATION_UX=PASS
RELOAD_RESUME_UI=PASS
CAMPAIGN_LIST_UX=PASS
REAL_DATA_ONLY=PASS
NO_PARALLEL_FEED_FACTORY=PASS
FRONTEND_BUILD=PASS
BACKEND_RUNTIME=PASS
PRODUCTION_RUNTIME=PASS
AUTOLOAD_PRODUCT_MODE=PASS

UI QA used an existing production account and existing campaign 135 for read-only validation/preview checks. A temporary campaign created through the real UI for create/brief/materialize/reload QA was deleted after the test. No paid AI/media generation was used in product QA. No feed was sent to Avito and no real advertisement was published.

# FAILED APPROACHES / DO NOT REPEAT
- Do not expose template_id / Category attributes JSON / CampaignItem terminology to client UX.
- Do not replace integrated workflow with links to separate generators.
- Do not create a second media library or second Feed Factory.
- Do not treat generic «VALIDATION FAILED» as adequate client UX.

# DO NOT REWRITE WITHOUT NEW EVIDENCE
Campaign/CampaignItem stateful workflow, category/feed contract integration, approval contract and safe_export are known-good. Do not rewrite them without a new demonstrated production defect.

# CURRENT WORK IN PROGRESS
CURRENT_TARGET=AUTOLOAD / Feed Factory product mode
CURRENT_STAGE=stable product mode
CURRENT_BLOCKER=NONE
ALREADY_COMPLETED=stateful E2E + client product UX + production browser QA
FILES_CURRENTLY_CHANGED=backend/app/services/campaign_service.py; frontend/app/dashboard/campaign/page.tsx; frontend/app/dashboard/campaign/[id]/page.tsx
QA_ALREADY_PASSED=all product acceptance listed above
QA_STILL_REQUIRED=NONE for AUTOLOAD_PRODUCT_MODE
NEXT_ACTION=operate in stable product mode; investigate only new runtime/product evidence
FINAL_ACCEPTANCE=AUTOLOAD_PRODUCT_MODE=PASS

# NEXT SESSION BOOTSTRAP
Read AGENTS.md, docs/recovery/INDEX.md and this file. Preserve AUTOLOAD_FEED_FACTORY_E2E=PASS and AUTOLOAD_PRODUCT_MODE=PASS unless new runtime evidence proves regression. Do not create a second Feed Factory. Do not publish to Avito merely for QA.


## 2026-08-27 — PLANETA first real client acceptance started

Real client/account: `planetazayavki_65985` / owner_user_id=60 / PLANETA (Планета Мебели).
This is the first natural real-client Feed Factory workflow, not a QA campaign.

Runtime-proven facts:
- existing account identity and niche were reused; before this run there were 0 Campaign, 0 CampaignItem, 0 canonical media_assets and 0 client_facts for this account;
- owner brief + uploaded PLANETA materials were persisted into authoritative account profile/client_facts without deleting historical data;
- real campaign `144` = `PLANETA — первая автозагрузка 36 объявлений`;
- matrix persisted: 4 directions × 3 variants × 3 cities = 36 CampaignItem;
- direction counts: kitchens=9, hallways=9, wardrobes=9, bathroom furniture=9;
- geo counts: Ufa=12, Izhevsk=12, Sterlitamak=12;
- 36 descriptions/titles persisted; no paid AI/image generation used;
- free Pexels discovery reused existing BORIS stock module; 12 originals + 24 safe derived city variants registered in canonical account-scoped media library and linked 1:1 to CampaignItems;
- authoritative photo rule applied: NO ROTATION/NO TILT. Existing `uniquify_image` had a proven rotation defect; minimal fix removed rotation. `uniquify_image_variant` uses crop/tonal transforms only. 24 derived assets persist `NO ROTATION` transform evidence;
- existing stock discovery had proven stale-proxy defect (`407 Proxy Authentication Required`); minimal fix made this free provider path use direct production transport, then Pexels discovery returned 3/3 images for all four direction queries;
- category candidate defect proven for kitchens and bathroom furniture (generic token scoring selected kitchen appliances / unrelated furniture). Minimal fix now resolves exact furniture leaves: Kitchens template 95712; Hallways 68080; Wardrobes 68081; Bathroom furniture 68104;
- current market benchmark persisted in workflow/client facts. Kitchen benchmark uses local Ufa/Izhevsk/Sterlitamak public offers; wardrobe/hallway benchmark likewise. Bathroom template 68104 has no PriceType field, therefore bathroom price is not falsely labelled per linear meter;
- reload/restart proof after backend restart: campaign 144, matrix=36, item_ids=36, CampaignItems=36, media_links=36, step=validation, HTTP /dashboard/campaign/144=200;
- real Avito upload=NO; real publication=NO.

Current real-client validation evidence:
- 18/36 items valid in internal Feed Factory validator (hallways + bathroom furniture);
- 18/36 blocked (kitchens + wardrobes) by category-contract required product-specific fields that are not safely inferable from the owner brief/stock photos, including physical dimensions and product/material/color fields;
- the same templates also mark wholesale-detail fields required even when WholesaleType is `Нет`; this is recorded as a real contract/conditionality blocker and was NOT bypassed with invented values;
- unsupported params were removed deterministically from categories whose contract does not define them;
- no external Avito xmlcheck was claimed because the 36-item feed is not yet internally green/approved.

Status:
`AUTOLOAD_PRODUCT_MODE=PASS` preserved.
`PLANETA_FIRST_REAL_CLIENT_ACCEPTANCE=IN_PROGRESS`.
Do not declare PASS until remaining required attributes are truthfully resolved, all 36 validate, owner approval is obtained, preview/feed readiness is proven, and external xmlcheck is run only through the real existing path if applicable.

## 2026-08-27 — PLANETA first real client acceptance advanced

New runtime-proven facts only:
- real campaign `144` remains the same natural client workflow (`feed_factory` / `real_client`); no QA campaign was created;
- conditional category validation defect was proven and corrected in the shared Feed Factory contract: `required=true` fields now respect authoritative `depends_on` semantics instead of being treated as globally required;
- preserved Avito template evidence confirms wholesale detail fields are conditional on `WholesaleType=Да`, kitchen physical/material fields are not applicable for `KitchenType=На заказ`, and wardrobe dimensions/material/color become mandatory for `Availability=В наличии`;
- regression proof after the validator correction: custom kitchens validate without fake finished-product attributes, ready-made kitchens still require their physical/material fields, made-to-order wardrobes validate without invented dimensions, in-stock wardrobes still require dimensions/material/color, and wholesale=yes still requires wholesale order fields; all 661 saved category templates completed regression without validator exceptions;
- bathroom template `68104` had a separate enum-contract data defect: `GoodsType` was stored as two split values instead of the authoritative single value `Сантехника, водоснабжение и сауна`; the template enum was minimally corrected;
- current Campaign 144 state after these corrections: `CampaignItems=36`, internal Feed Factory validation=`36/36`, `draft=36`, `ready=0`, `hero_media=36/36`, unique hero media=`36/36`, geo matrix=`Уфа 12 / Ижевск 12 / Стерлитамак 12`, direction matrix=`9×4`;
- workflow state is persisted at `step=preview`; `last_export=None`; therefore no owner approval has occurred and safe export has not been produced;
- persisted preview XML exists for the same 36 CampaignItems at `/root/BORIS/backend/images/planetazayavki_65985/feed_factory_validation/campaign_144_check.xml`; this is preview/check preparation only, not an Avito upload;
- real Avito upload remains `NO`; real publication remains `NO`;
- no natural production evidence was observed for mass-edit scoping or individual-override behavior during this acceptance step, so no new PASS claim was added for those controls.

Current acceptance:
`AUTOLOAD_FEED_FACTORY_E2E=PASS` preserved.
`AUTOLOAD_PRODUCT_MODE=PASS` preserved.
`PLANETA_FIRST_REAL_CLIENT_ACCEPTANCE=IN_PROGRESS`.
`INTERNAL_VALIDATION=36/36`.
`PREVIEW_STATE=PERSISTED`.
`OWNER_APPROVAL=PENDING_NATURAL_ACTION`.
`SAFE_EXPORT=NOT_RUN`.
`AVITO_XML_CHECK=NOT_YET_PROVEN_IN_THIS_GUARDIAN_RUN`.


## 2026-08-27 — PLANETA campaign 144 green feed / ready for owner approval

New runtime-proven facts only:
- Campaign 144 reused unchanged; 36 existing CampaignItem preserved; no new campaign/items created.
- Final deterministic normalization audit: 36/36 items, 0 normalization issues. Titles are unique and within Avito length limit; descriptions are unique, natural multiline text with blank-line blocks, no Markdown markup, no `в наличии`, no pickup, made-to-order semantics, CTA and `Пример мебели:` linked to the same hero media.
- Price benchmark logic and source list are persisted per CampaignItem; bathroom items explicitly remain project/item price logic because template 68104 does not support linear-meter PriceType.
- City matrix remains Ufa=12 / Izhevsk=12 / Sterlitamak=12; direction matrix remains 9 each for kitchens, hallways, wardrobes, bathroom furniture.
- Media persistence: 36 MediaLink for 36 CampaignItem; 24 derived city variants retain `safe crop/tonal only; NO ROTATION` evidence and `no_rotation` tag. No media was replaced during this finalization.
- Internal Feed Factory validation remains 36/36 after normalization.
- Preview XML for these exact 36 current CampaignItems is `/root/BORIS/backend/images/planetazayavki_65985/feed_factory_validation/campaign_144_check.xml`, SHA-256 `a382a736369f32c18affbe9fea3a7b2c57e55d95e879ab7904466a632da41e87`.
- Official Avito xmlcheck returned `status=ok`, `XML соответствует формату`, checked 36 ads and reported every ad `Соответствует формату` for that exact SHA artifact. A later network/browser checker invocation timed out; no data/schema changes were accepted from the timeout. The current XML was restored/rebuilt to the exact previously green SHA, so the official green result applies exactly to the current preview artifact.
- Workflow persisted at `step=preview`, `ready_for_owner_approval=true`, with official XML check status/pass and SHA stored in campaign settings.
- Owner review APIs return validation 36/36 and preview 36/36 with title, description, image, price, city and category present for all 36.
- Owner-review UI contract on `/dashboard/campaign/144` exposes human preview cards, validation status, price/city/category, photo, selective `Согласовать` and `Вернуть выбранные на исправление`; technical template ids remain internal select values and are not rendered as client-facing identifiers.
- Approval was NOT bypassed: all 36 CampaignItem remain `draft`; POST safe_export returns HTTP 409 `Нет согласованных объявлений для экспорта` with all 36 IDs in `not_approved`.
- Production runtime: boris-backend=active, boris-frontend=active, `/dashboard/campaign/144` HTTP 200.
- REAL_AVITO_UPLOAD=NO; REAL_PUBLICATION=NO.

Current acceptance before owner action:
`CAMPAIGN_144=PASS`
`ITEMS=36/36`
`TITLE_QUALITY=PASS`
`DESCRIPTION_QUALITY=PASS`
`DESCRIPTION_MEDIA_MATCH=PASS`
`PRICE_LOGIC=PASS`
`CITY_MATRIX=PASS`
`CATEGORY_MAPPING=PASS`
`ATTRIBUTE_COMPLETENESS=PASS`
`NO_FAKE_ATTRIBUTES=PASS`
`NO_PICKUP=PASS`
`MADE_TO_ORDER=PASS`
`NO_PHOTO_ROTATION=PASS`
`MEDIA_PERSISTENCE=PASS`
`INTERNAL_VALIDATION=36/36 PASS`
`AVITO_XML_CHECK=PASS`
`PREVIEW=PASS`
`READY_FOR_OWNER_APPROVAL=PASS`
`REAL_AVITO_UPLOAD=NO`
`REAL_PUBLICATION=NO`

Do not move these 36 items to `ready`, safe_export, Avito upload or publication until the owner explicitly reviews/approves them.

## 2026-08-27 — PLANETA owner-review product visibility P0 fixed

New runtime-proven facts only:
- P0 symptom: Campaign 144 and all 36 ready-for-review ads existed in backend, but the ordinary `/dashboard/campaign` product surface did not present the result as a finished owner-review workflow.
- Root cause was product/read-path presentation, not campaign data: account-scoped API correctly returned campaign 144 for `planetazayavki_65985`; campaign/items ownership was correct. Frontend list counted only approval status `ready`, so 36 valid draft items were displayed as `готово 0`, CTA was generic `Продолжить`, and detail page placed the 36 prepared cards below obsolete preparation/questions/matrix UI. A second legacy account `inbox_60` can also be the saved selected account, so account-scoped empty state needed to be explicit instead of looking like missing data.
- Minimal frontend-only fix: `/dashboard/campaign` now derives review readiness from persisted Feed Factory state, shows `36 готовы к проверке`, `36 фото`, `Проверка Avito: пройдена`, `Статус: ждёт согласования`, and CTA `Посмотреть и согласовать 36 объявлений`; campaign list explicitly states the selected account and shows an account-scoped empty hint for accounts with no campaigns.
- `/dashboard/campaign/144` now enters owner-review mode immediately when `ready_for_owner_approval=true`: preparation/questions/matrix no longer obscure the prepared result; top summary shows 36 prepared, 36/36 internal validation, 36/36 hero images, Avito check passed, and `Осталось: ваше согласование`.
- Existing 36 preview cards are reused. Each card visibly renders hero image, title, description including `Пример мебели:`, price/unit, city, direction, category, validation status, and owner-decision status. Each card has `Открыть / изменить`, `Согласовать`, and `Вернуть на исправление`; bulk `Выбрать все`, `Согласовать выбранные`, and `Вернуть выбранные на исправление` remain available. No approval control was invoked during QA.
- Authenticated browser proof as real PLANETA user `user_id=60`: ordinary `Объявления` page showed campaign 144 and its review CTA; switching to legacy account `inbox_60` showed no PLANETA campaign (`CROSS_ACCOUNT_CAMPAIGN_LEAKAGE=0`), switching back restored it.
- Authenticated browser proof on owner review: 36/36 cards visible; 36/36 hero images loaded with no broken image; title/text/price/city/direction/category/validation/decision visible on all 36; city counts Ufa=12 / Izhevsk=12 / Sterlitamak=12; direction counts 9×4; 36 per-card approve controls and 36 per-card return controls; Select All checked all 36; edit UI exposes title/price/city/photo replacement/save against existing CampaignItem.
- Reload proof: `/dashboard/campaign/144` restored owner-review state, 36 cards and 36/36 loaded hero images after refresh.
- Production current artifact after parallel safe-deploy: BUILD_ID `_ahlNXKH7wDpR3YevoHFx`; current artifact contains the owner-review UX strings/chunks. The only source changes after that artifact timestamp were unrelated `app/messages/CRMContextPanel.tsx` and an AI backup file, so the campaign owner-review source was included in the live artifact.
- Backend had one unrelated transient systemd stop event during final browser QA (`ECONNREFUSED :8000` while service was stuck `deactivating` with child uvicorn in cgroup); standard `systemctl restart boris-backend.service` restored `active` and port 8000. No Feed Factory data/schema change was needed.
- Post-QA DB proof: all 36 CampaignItem remain `draft`; workflow remains `step=preview`, `ready_for_owner_approval=true`; `real_avito_upload=false`, `real_publication=false`.

Acceptance:
`PLANETA_CAMPAIGN_VISIBLE_IN_ADS=PASS`
`CAMPAIGN_144_VISIBLE=PASS`
`ITEMS_VISIBLE=36/36`
`HERO_IMAGES_VISIBLE=36/36`
`TEXTS_VISIBLE=36/36`
`PRICES_VISIBLE=36/36`
`CITIES_VISIBLE=36/36`
`CATEGORIES_VISIBLE=36/36`
`OWNER_REVIEW_UI=PASS`
`SELECT_ALL=PASS`
`APPROVE_SELECTED_UI=PASS`
`RETURN_FOR_FIX_UI=PASS`
`RELOAD_PERSISTENCE=PASS`
`ACCOUNT_SCOPE=PASS`
`CROSS_ACCOUNT_CAMPAIGN_LEAKAGE=0`
`CROSS_ACCOUNT_MEDIA_LEAKAGE=0`
`REAL_AVITO_UPLOAD=NO`
`REAL_PUBLICATION=NO`
`PLANETA_OWNER_REVIEW_PRODUCT_MODE=PASS`

## 2026-08-28 — PLANETA campaign 144 full commercial content package

New runtime-proven facts only:
- Existing campaign `144` and its same 36 CampaignItem were reused; no campaign/items were recreated and all 36 remain `draft` for owner approval.
- Commercial media package expanded through the existing canonical Media Library / MediaLink pipeline to exactly `252` links = `7/7` per CampaignItem (`36 hero + 216 gallery`). No paid image generation and no second media engine were used.
- Gallery content uses real free Pexels discovery provenance; each ad has seven content-role frames. City derivatives use only safe crop/tonal changes. Rotation invariant is explicit on all 252 linked assets: `NO PHOTO ROTATION`.
- Final media integrity: `252/252` files present, `BROKEN_IMAGES=0`, exact byte duplicates=`0`, unique media sets=`36`, pairwise exact asset overlap=`0`. Source provenance is intentionally reused for safe city derivatives; it is not represented as a different physical product fact.
- 36 titles are unique and 36 descriptions are unique. Deterministic semantic rewrite reduced maximum pairwise description similarity to about `0.799`; city versions use different commercial structures rather than word-order shuffling.
- Every description keeps a `Пример мебели:` block tied to the existing hero visual/source metadata. Exact dimensions, exact RAL, body material and hardware brand are not asserted as visual facts when the source does not prove them.
- Canonical knowledge now separates `CONFIRMED_CLIENT_FACT`, `REFERENCE_MARKET_KNOWLEDGE`, and `EXAMPLE_CONFIGURATION`. PLANETA-confirmed Blum capability and LDSP E1 remain client facts; reusable RAL Classic and furniture-hardware catalogs are reference knowledge and are not automatically attributed to a pictured item.
- Client material evidence reused: PLANETA has more than 30 years of furniture-market history, 8 factories across price segments, and kitchen Blum capability; these are persisted with provenance rather than inferred from stock photos.
- Avito formatting contract for this package is plain text without Markdown markers. BORIS owner preview renders separate visual emphasis badges; export description remains Avito-safe plain text.
- Current exact XML artifact: `/images/planetazayavki_65985/feed_factory_validation/campaign_144_commercial_final.xml`, SHA-256 `53a585620e80923a9c69cd0173fb5106acb3a477df181f49a6320e7206dc1b34`, 36 ads and 252 Image elements.
- Internal Feed Factory validation on that exact current CampaignItem state: `36/36 PASS`.
- Official Avito xmlcheck on that exact URL/artifact returned `status=ok`, `XML соответствует формату`, and all `36/36` ads `Соответствует формату`.
- Owner-review read path now returns the canonical MediaLink gallery (`7/7`) rather than relying only on payload hero refs.
- Production frontend artifact `BUILD_ID=yk0yGMJWCpItT_rsbNZqI` deployed atomically with local/public health `200`.
- Authenticated PLANETA browser proof on the live artifact: Ads list shows `252 фото`; campaign CTA opens `/dashboard/campaign/144`; owner-review renders 36 gallery labels `1/7`, 252 DOM images, `252/252` loaded, `BROKEN=0`, 36 attribute blocks, 36 per-item approve controls and 36 per-item return controls. Reload restores 36 galleries and `252/252` loaded images.
- Approval was not bypassed: all 36 items remain `draft`, `step=preview`, `ready_for_owner_approval=true`; real Avito upload=`NO`, real publication=`NO`.

Acceptance:
`CAMPAIGN_144=PASS`
`ITEMS=36/36`
`MEDIA_PER_ITEM=7/7`
`TOTAL_MEDIA_LINKS=252`
`BROKEN_IMAGES=0`
`MEDIA_CONTENT_RELEVANCE=PASS`
`NO_PHOTO_ROTATION=PASS`
`DESCRIPTION_MEDIA_MATCH=PASS`
`DESCRIPTION_UNIQUENESS=PASS`
`TITLE_UNIQUENESS=PASS`
`SEMANTIC_UNIQUENESS=PASS`
`EXAMPLE_FURNITURE=36/36`
`DIMENSIONS_REFERENCE_SAFE=PASS`
`RAL_REFERENCE_KNOWLEDGE=PASS`
`HARDWARE_REFERENCE_KNOWLEDGE=PASS`
`CONFIRMED_FACTS_NOT_MIXED_WITH_REFERENCE=PASS`
`IMPORTANT_TEXT_ACCENTS=PASS`
`AVITO_FORMATTING_SAFE=PASS`
`PRICE_LOGIC=PASS`
`CATEGORY_MAPPING=PASS`
`ATTRIBUTE_COMPLETENESS=PASS`
`NO_FAKE_ATTRIBUTES=PASS`
`MADE_TO_ORDER=PASS`
`NO_PICKUP=PASS`
`OWNER_GALLERY=PASS`
`OWNER_REVIEW_UI=PASS`
`INTERNAL_VALIDATION=36/36`
`NEW_EXACT_XML=PASS`
`AVITO_XML_CHECK=PASS`
`READY_FOR_OWNER_APPROVAL=PASS`
`REAL_AVITO_UPLOAD=NO`
`REAL_PUBLICATION=NO`
