# FRONTEND UX RECOVERY

## MODULE PURPOSE

Authoritative production UX contract for the BORIS cabinet. Scope: route/navigation model, single global shell, Home Command Center, product accessibility, real-data UI contracts, responsive behavior, safe frontend build/deploy and runtime QA.

## CURRENT KNOWN GOOD STATE

LAST_VERIFIED=2026-08-27
FRONTEND_UX_RECOVERY=PASS
HOME=PASS
ADS=PASS
MESSAGES=PASS
CRM=PASS
SALES_ROP=PASS
TASKS=PASS
ANALYTICS=PASS
INTEGRATIONS=PASS
NAVIGATION=PASS
UNIFIED_SHELL=PASS
NO_ACCIDENTAL_DUPLICATE_UI=PASS
NO_OLD_UI_EMBEDDED_IN_NEW_UI=PASS
REAL_DATA_CONTRACTS=PASS
LOADING_STATES=PASS
EMPTY_STATES=PASS
ERROR_STATES=PASS
RESPONSIVE_LAYOUT=PASS
FRONTEND_BUILD=PASS
PRODUCTION_RUNTIME=PASS
LOCAL_ROUTE_MATRIX=PASS
PUBLIC_ROUTE_MATRIX=PASS
RECENT_FRONTEND_CRITICAL_ERRORS=0

## AUTHORITATIVE COMPONENTS

- `/root/BORIS/frontend/app/components/shell/AppShell.tsx` — authoritative global cabinet shell.
- `/root/BORIS/frontend/app/components/shell/Sidebar.tsx` — authoritative global navigation renderer.
- `/root/BORIS/frontend/app/components/shell/TopBar.tsx` — authoritative top bar/account context.
- `/root/BORIS/frontend/app/components/shell/LegacyShellAdapter.tsx` — compatibility adapter that delegates to AppShell; it must not create a second visual generation of the cabinet.
- `/root/BORIS/frontend/app/ui/menu.ts` — authoritative production menu/route model. `MenuItem.href` is required.
- `/root/BORIS/frontend/app/dashboard/command-center/page.tsx` — authoritative Home / Command Center.
- `/root/BORIS/frontend/app/dashboard/command-center/page.module.css` — Home responsive layout contract.

## PRODUCTION ROUTE CONTRACT

Core routes verified in production:

- `/` — public root.
- `/dashboard/home` — compatibility entry to Home.
- `/dashboard/command-center` — authoritative Home.
- `/dashboard/campaign` — Ads / Feed Factory product entry.
- `/dashboard/campaign/<real campaign id>` — campaign workspace; verified with real Campaign `142` on 2026-08-27.
- `/messages` — Messages.
- `/messages/setup` — account connections.
- `/dashboard/crm` — CRM.
- `/dashboard/crm/workspace` — CRM workspace/tasks context.
- `/dashboard/crm/integrations` — integrations.
- `/rop` — Sales / AI ROP.
- `/dashboard/monitoring` — monitoring product route.
- `/dashboard/analytics` — canonical analytics route.
- `/dashboard/work-mode` — Work mode/autopilot adapter.
- `/dashboard/marketing` — Marketing adapter.
- `/dashboard/import-site` — site import adapter.
- `/dashboard/sitebuild` — sitebuild adapter.
- `/dashboard/billing` — billing adapter.
- `/dashboard/company` — company settings adapter.
- `/dashboard/campaign/banners` — banners/images.
- `/social` — social posting product screen.
- `/command` — Ask BORIS.
- `/dashboard/launch-status` — client launch status.
- `/dashboard/scenarios` — business scenarios.

## HOME INFORMATION ARCHITECTURE

The Home page is an executive Command Center, not a decorative KPI dashboard. Current structure uses:

1. operational state / launch readiness;
2. real account/client context;
3. real KPI plan/fact when backend provides it;
4. clients/accounts;
5. important blockers/alerts;
6. direct actions into Ads, Messages, CRM, ROP, Tasks, Marketing, Analytics and Integrations;
7. relevant next-action links.

No fake revenue, average-check or conversion values are allowed. Missing backend metrics must render as unavailable/empty state rather than being invented.

## REAL-DATA CONTRACTS

Home uses real account context and real backend contracts, including:

- account context from BORIS `AccountContext`;
- `/api/factory/launches` for client/launch list data;
- `/api/client-launch/plan` for launch state;
- `/api/avito/kpi_check` for real KPI state.

Analytics uses real Avito contracts including `/api/avito/items_overview`, `/api/avito/kpi_settings`, `/api/avito/kpi_check`.

CRM uses `/api/crm/product/*` contracts. Integrations uses `/api/crm/integration/*`. ROP continues to use the existing calltracking/ROP contracts. Feed Factory business logic was not rewritten by this recovery.

## RESPONSIVE CONTRACT

- Global shell has desktop sidebar and mobile drawer behavior; mobile shell breakpoint is <=900px.
- Home responsive breakpoints verified in source at 1180px, 900px, 640px and 420px.
- Home cards use `minmax(0, ...)`, wrapping and `overflow-wrap:anywhere` for long content.
- Mobile actions/cards collapse to single-column layouts; status/alert rows reflow instead of relying on fixed desktop widths.
- CRM kanban uses horizontal overflow for column layout; flexible content containers use `min-width:0` where required.
- Shell content uses `min-width:0`; sidebar labels use bounded ellipsis; touch/coarse-pointer rules exist in shell CSS.
- Runtime route and client-asset sweeps after the final deploy showed no missing assets or 5xx errors.

## ROOT CAUSES FOUND

1. Main menu entries without real `href` could fall back to `/dashboard?tab=...`, embedding the legacy monolith in the recovered product shell.
2. The global AppShell exposed an old-interface switcher that could route users back to the legacy monolith.
3. `/messages` had incorrect active navigation (`slots`) for the normal message screen.
4. CRM subtree did not consistently share one global AppShell.
5. ROP and campaign detail had duplicate-shell patterns during recovery: route layout shell plus page-local shell.
6. Home contained its own parallel chrome/navigation and attempted to infer decorative KPI values from loosely shaped payload fields.
7. Concurrent frontend work repeatedly reintroduced already-fixed navigation/build details; one observed regression changed Marketing back to `/dashboard?tab=marketing` and was corrected only after fresh evidence.
8. Compile blockers during final recovery included a wrong design token (`color.border`) and a local Campaign Item type missing the already-used `segment` field.

## FIXES APPLIED

- Normalized `app/ui/menu.ts` so every production menu entry has an explicit real route.
- Removed authoritative Sidebar generation of `/dashboard?tab=...`.
- Removed old-interface switch mounting from AppShell.
- Rebuilt Home inside one AppShell with real-data-only metrics/state.
- Corrected Messages active navigation for `/messages` vs `/messages/setup`.
- Unified CRM subtree under one AppShell with contextual active keys for CRM/tasks/integrations.
- Reduced ROP, training and campaign detail to one authoritative shell each.
- Added/used modern route adapters for Work Mode, Marketing, site import, sitebuild, billing and company settings without rewriting their backend business contracts.
- Added/retained loading, empty and error UI states in product routes.
- Fixed frontend-only TypeScript contract for campaign workspace `Item.segment` without backend schema/database changes.
- Corrected Marketing UI token from nonexistent `color.border` to existing `color.line`.

## BUILD / DEPLOY CONTRACT

Historical risk: concurrent `next build` against the live `.next` artifact can corrupt the production artifact and lead to missing client manifests / HTTP 500. Do not repeat this.

Safe contract used and proven on 2026-08-27:

1. check for active `next build` / `npm run build` before build/edit/deploy;
2. do not kill a normal concurrent build merely to proceed;
3. build into an isolated dist directory via `NEXT_DIST_DIR=<isolated-dir> npm run build`;
4. require successful compile, TypeScript and complete static generation plus `BUILD_ID`;
5. verify source files did not change after the isolated build began;
6. verify no other builder remains active before switch;
7. atomically move live `.next` to a timestamped previous artifact and move the complete isolated artifact to `.next`;
8. restart only `boris-frontend.service`;
9. verify service ActiveState/SubState/NRestarts;
10. run local/public route matrix, client static-asset sweep and fresh journal error scan.

Do not build directly over live `.next` during concurrent work.

## QA / ACCEPTANCE EVIDENCE

Final isolated build after the last navigation fix:

- Next.js 16.2.9 compile: PASS.
- TypeScript: PASS.
- static generation: 60/60 pages PASS.
- final production artifact BUILD_ID existed before switch.
- source files newer than final build start: 0 before switch.
- source files newer than final production deploy at verification: 0.

Final production service evidence:

- `boris-frontend.service`: ActiveState=active, SubState=running.
- NRestarts=0 after final deploy.
- no active frontend build processes after deploy.

Final route matrix after final deploy:

- local `http://127.0.0.1:3002`: 22/22 routes HTTP 200.
- public `https://boris-ai.pro`: 22/22 routes HTTP 200.
- includes real Campaign route `/dashboard/campaign/142`.

Client asset integrity after final deploy:

- 21 key authenticated routes produced 48 unique `/_next/static/*` assets.
- local assets: 48/48 HTTP 200.
- public assets: 48/48 HTTP 200.

Fresh frontend journal after final deploy:

- Next ready successfully.
- no fresh `Traceback`, `MODULE_NOT_FOUND`, `ENOENT`, `InvariantError`, `Unhandled`, `ReferenceError`, `TypeError`, missing client manifest, or HTTP 500 evidence.

Single-shell source invariants passed for Home, Ads root/detail, Messages, CRM, Tasks, Integrations, ROP, Training and Analytics. No direct Sidebar/TopBar imports exist in the audited product route trees outside the authoritative shell layer.

## FORBIDDEN LEGACY PATHS / PATTERNS

Do not reintroduce into authoritative navigation/shell:

- `/dashboard?tab=<legacy-key>` as a main navigation destination;
- menu items without explicit production `href`;
- global `InterfaceSwitcher` / “old interface” escape from AppShell;
- page-local Sidebar or TopBar inside a route already wrapped by AppShell;
- page-local AppShell/Shell when its route layout already supplies the global shell;
- fake KPI values synthesized only to fill a dashboard card;
- a second Feed Factory or second CRM/ROP product UI just to modernize visuals.

## FAILED APPROACHES / DO NOT REPEAT

APPROACH=Main navigation fallback to `/dashboard?tab=...`
WHY_FAILED=It preserves the legacy monolith as a second cabinet generation inside the recovered product UX.
USE_INSTEAD=Explicit modern route or a thin route adapter inside the single AppShell.

APPROACH=Build directly against live `.next` during concurrent frontend development
WHY_FAILED=Historical production incident caused artifact/manifest inconsistency and HTTP 500 risk.
USE_INSTEAD=Isolated `NEXT_DIST_DIR`, completed artifact verification, atomic switch, frontend-only restart.

APPROACH=Duplicate route shell in both layout and page
WHY_FAILED=Produces nested Sidebar/Header/global chrome.
USE_INSTEAD=Exactly one authoritative shell per route subtree.

## KNOWN HISTORICAL DEBT

- `/dashboard` monolith still exists as historical implementation/business-logic source. It is not an authoritative main-navigation destination.
- `LegacyShellAdapter` remains for compatibility but must delegate to the same AppShell and must not render a separate visual shell generation.
- Some product pages still contain older inline styling/business UI implementation. Visual redesign is separate from this recovery unless usability/workability evidence requires a change.
- Browser-devtools console capture was not available on the SentinelX host; critical frontend runtime acceptance in this recovery used successful production build/typecheck, client asset integrity, 22/22 local/public route sweeps and fresh Next journal with zero critical patterns.

## DO NOT REWRITE WITHOUT NEW EVIDENCE

Do not redesign or replace the recovered Home, menu model, AppShell/navigation foundation, CRM shell, ROP shell or Feed Factory shell unless new production evidence proves a defect. Current production runtime has priority over this memory if a future regression is demonstrated.

## CURRENT WORK IN PROGRESS

CURRENT_TARGET=Frontend UX Recovery
CURRENT_STAGE=Completed production acceptance
CURRENT_BLOCKER=NONE
ALREADY_COMPLETED=Home, navigation normalization, legacy fallback removal, unified shell, compile fixes, isolated build, deploy, route QA, client asset QA, journal QA
FILES_CURRENTLY_CHANGED=No active recovery edit after final deploy
QA_ALREADY_PASSED=Build, TypeScript, static generation, service, local/public routes, shell invariants, client assets, fresh journal
QA_STILL_REQUIRED=NONE for FRONTEND_UX_RECOVERY acceptance; future visual-polish work is separate
NEXT_ACTION=Monitor only new production evidence; do not reopen recovery without regression evidence
FINAL_ACCEPTANCE=FRONTEND_UX_RECOVERY=PASS

## NEXT SESSION BOOTSTRAP

Read `/root/BORIS/AGENTS.md`, `/root/BORIS/docs/recovery/INDEX.md`, this file and `/root/BORIS/docs/recovery/GLOBAL_LESSONS.md` if present. Treat current production runtime as authoritative. If there is no new regression evidence, do not repeat the Home/navigation/shell recovery audit.

## FINAL POST-CONCURRENCY STABILIZATION — 2026-08-27 17:49 UTC

SUPERSEDES_EARLIER_FINAL_DEPLOY_EVIDENCE=YES

During post-memory sanity QA, additional concurrent frontend workers were observed running default `npm run build` / `next build` against the live frontend tree. One such build changed live `.next/BUILD_ID` while the running `boris-frontend` PID still predated that build, proving a real process/artifact mismatch risk even though routes temporarily continued returning HTTP 200.

Observed evidence:

- running server before stabilization: PID remained from the earlier deploy while `.next/BUILD_ID` changed underneath it;
- concurrent builders had no `NEXT_DIST_DIR`, so they were default live `.next` builds;
- later concurrent UI work changed CRM page, CRM integrations and shared visual CSS; those changes preserved the unified-shell/navigation contracts and were not reverted;
- after concurrent work settled, source tree was stable and a new isolated exact-tree build was created in `.next-ux-final5`;
- final5 build: compile PASS, TypeScript PASS, static generation 60/60 PASS;
- final5 BUILD_ID=`Do-jxRxu9MKv2WHm6kvcp`;
- no builders were active before final5 switch;
- no source changes occurred after the current concurrent UI changes and before final5 deployment;
- final5 was atomically moved to live `.next`, then only `boris-frontend.service` was restarted;
- final stable service PID=1529967, ActiveState=active, SubState=running, NRestarts=0;
- 12-second post-deploy stability window: BUILD_ID unchanged, builders=0, source edits after deploy=0;
- final route matrix after final5: local 22/22 HTTP 200, public 22/22 HTTP 200;
- final client assets after final5: 49/49 HTTP 200 locally and 49/49 publicly across 21 key authenticated product routes;
- final journal since 17:49:14 UTC: Next `Ready`, no fresh critical error patterns;
- final single-shell invariants: PASS for Home, Ads root/detail, Messages, CRM, Tasks, Integrations, ROP, Training and Analytics;
- final authoritative navigation invariants: `/dashboard?tab=` count 0, AppShell old-interface switch mount 0, direct nested Sidebar/TopBar imports 0 in audited product route trees.

IMPORTANT_INCIDENT=Default live `.next` builds by concurrent workers can invalidate an otherwise correct deployment without changing source code.

REQUIRED_FUTURE_GUARD=After deploy, verify not only builders=0 but also that live `.next/BUILD_ID` remains unchanged for a short stability window before declaring production acceptance.

FINAL_ACCEPTANCE_AFTER_CONCURRENCY=FRONTEND_UX_RECOVERY=PASS


## PRODUCT UI QA CONTINUATION — 2026-08-27 18:00 UTC

CURRENT_TARGET=BORIS Product UI E2E after navigation recovery
CURRENT_STAGE=Production product-state/navigation/runtime acceptance

PROVEN_FIXES_DURING_PRODUCT_QA:
- Campaign subtree active navigation now distinguishes `/dashboard/campaign/banners` as `webdesign` / «Баннеры и картинки» instead of incorrectly keeping «Объявления» active.
- Home «Маркетинг и контент» now routes to the modern `/dashboard/marketing`, not the legacy dashboard monolith.
- CRM error state clears stale overview/kanban/contacts/analytics and does not render normal empty/KPI states together with an API error.
- Messages does not render KPI zeroes while account/dialog loading has failed.
- Analytics does not render KPI/empty business content while its real API load is in error.
- Launch Status does not leave stale metrics/cards visible together with an API error.
- Business Scenarios now has an explicit recoverable load-error state instead of silently becoming an empty catalog.
- ROP account cards no longer force a 320px minimum wider than a narrow mobile content area.
- CRM tasks/integrations active-key contract remains route-specific without introducing a `useSearchParams` prerender dependency.

AUTHORITATIVE_MENU_CURRENT=all 25 current MENU entries have explicit real routes; `/dashboard?tab=` is not an authoritative menu destination.
NO_HASH_HREF=PASS
INTERFACE_SWITCHER_MOUNTED=NO
SINGLE_SHELL_SOURCE_INVARIANTS=PASS for core product routes audited in this cycle.
REAL_DATA_STATIC_SCAN=PASS for Home/Analytics/Messages/CRM/ROP: no known demo/mock/synthetic KPI literals reintroduced.

BUILD_EVIDENCE:
- Multiple direct builds passed compile/TypeScript/static generation during iteration.
- One direct live `.next` build failed with ENOENT while another concurrent worker was writing the same artifact; this reconfirmed the documented concurrent-build incident and was not treated as a source-code failure.
- A later isolated build initially exposed a real cross-route build regression: `useSearchParams` in CRM layout forced a missing Suspense CSR bailout at `/reactivation`. CRM active-state implementation was changed back to pathname + client URLSearchParams without changing CRM business logic.
- Final isolated `.next-productqa-final3` build: compile PASS, TypeScript PASS, static generation 60/60 PASS, SOURCE_CHANGED_DURING_BUILD=0.
- Artifact was atomically switched to live `.next`; previous live artifact retained as `.next-pre-productqa-20260827_175918`; only `boris-frontend.service` restarted.
- Post-deploy 15-second stability window: builders=0, BUILD_ID unchanged, service active.

FINAL_RUNTIME_EVIDENCE_THIS_CYCLE:
- local/public route matrix: 27/27 routes HTTP 200, including core screens and legacy/product entries Help and Training.
- public core route sweep includes Home, Ads, Messages, CRM, ROP, Tasks, Analytics, Integrations, Marketing, Social and Account Setup.
- fresh frontend journal after final restart: no Error, InvariantError, ENOENT, MODULE_NOT_FOUND, missing client manifest, HTTP 500, Unhandled, ReferenceError, TypeError or hydration critical patterns.
- Playwright/Chromium was installed on the SentinelX host for rendered QA. Unauthenticated production browser sweep proves protected routes redirect cleanly to `/login`; login rendered at 1440x900, 768x1024 and 390x844 with no horizontal overflow and zero browser page/console errors after excluding the environment certificate-fetch warning observed before Chromium setup completed.

AUTHENTICATED_BROWSER_QA_LIMITATION:
- No dedicated provisioned QA token/account exists on the server (`QA_TOKEN`, `BORIS_QA_TOKEN`, `TEST_TOKEN` absent).
- Do not create a production user, bypass auth, expose credentials, or mutate client business data merely to manufacture authenticated browser evidence.
- Therefore authenticated rendered desktop/tablet/mobile interaction for every protected product screen remains NOT_VERIFIED in this cycle. Source responsive contracts, build, HTTP runtime and unauthenticated browser auth-boundary rendering are proven, but they are not a substitute for an authenticated browser session.

CURRENT_WORK_IN_PROGRESS:
CURRENT_TARGET=BORIS Product UI E2E
CURRENT_STAGE=Authenticated rendered acceptance remains
CURRENT_BLOCKER=No safe pre-provisioned authenticated QA browser session/token available to SentinelX
ALREADY_COMPLETED=route/product-state fixes, active navigation fixes, error-state exclusivity, isolated exact-tree build, atomic deploy, 27/27 local/public HTTP runtime, fresh journal, Playwright installation, unauthenticated desktop/tablet/mobile rendered auth-boundary QA
QA_ALREADY_PASSED=compile, TypeScript, static generation, production runtime, route matrix, source shell invariants, source responsive contracts, no known synthetic KPI literals, unauthenticated browser console/overflow
QA_STILL_REQUIRED=authenticated browser click/action/state/console QA at 1440x900, 768x1024, 390x844 for protected screens
NEXT_ACTION=Use a legitimate pre-authorized QA session when one becomes available; do not weaken auth or create production side effects for QA
FINAL_ACCEPTANCE=BORIS_PRODUCT_UI_E2E=NOT_YET_PASS because authenticated rendered responsive/action acceptance is not safely available

## Unified Product UI production closure — 2026-08-27

`BORIS_UNIFIED_PRODUCT_UI=PASS` is now authoritative. The permanent UI/deploy contract and production evidence are recorded in `docs/recovery/FRONTEND_UI.md`.

Accepted production release: `BUILD_ID=ZWIW6dHA1EEUMCw8bnN3J`, snapshot hash `017a26718e89295987e6fbcfd49df4ea094bd374b89543ad832971b0fcc38b0e`, immutable READY artifact `/root/BORIS/frontend/.boris-builds/artifacts/20260827T205408Z-1640007-22564`. Deployment uses `.boris-builds/current`, deploy lock, atomic symlink switch, one frontend restart, health and rollback. Continuously moving repository HEAD is not a deploy blocker for an already self-consistent immutable READY snapshot.
