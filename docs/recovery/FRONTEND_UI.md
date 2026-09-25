# BORIS Frontend UI — Unified Product Mode

Status: **PASS**
Date: **2026-08-27**

## Authoritative shell and navigation

- Global product shell: `frontend/app/components/shell/AppShell.tsx`.
- Legacy-compatible route wrapper: `frontend/app/components/shell/LegacyShellAdapter.tsx`.
- Global sidebar/topbar styles: `frontend/app/components/shell/shell.module.css`.
- Global logout control: `frontend/app/components/shell/GlobalLogoutButton.tsx`, `data-testid="global-logout"`.
- Bare `/dashboard` is an entry route and redirects to canonical `/dashboard/command-center`.
- Query-driven legacy `/dashboard?...` flows remain compatibility paths; do not make bare `/dashboard` a second product shell.
- Product navigation contains and production QA rendered `Создание сайтов` and `Тарифы и финансы`.
- Campaign workspace contains the existing import-site path `/dashboard/import-site`; do not create a second import product surface.

## Canonical visual and interaction contract

- Canonical BORIS tokens/interactions live in `frontend/app/components/boris-ui/boris-ui.css` and `frontend/app/globals.css`.
- Shared product primitives are in `frontend/app/ui/`.
- Current production interaction acceptance: `CARD_HOVER=PASS`, `ICON_HOVER=PASS`, `BUTTON_HOVER=PASS`, `FOCUS_STATE=PASS`, `TOUCH_BEHAVIOR=PASS`, `REDUCED_MOTION=PASS`.
- Interactive card transforms are visual transforms only; they must not grow layout boxes or create horizontal overflow.
- Touch layouts must not depend on desktop hover.
- Keyboard focus stays visible; reduced-motion disables/minimizes animation.
- Do not create per-route replacement design systems when shared tokens/primitives already cover the element.

## Responsive/product acceptance

Production-proven targeted acceptance for this release:

- `/dashboard` -> `/dashboard/command-center`: PASS.
- `/ai` at tablet width 820: PASS, horizontal overflow 0.
- `/dashboard/campaign/144` at mobile width 390: PASS, horizontal overflow 0, owner-review content rendered.
- `/messages`: PASS, real dialog opens `CRMContextPanel`.
- App shell count on targeted screens: exactly 1.
- `Создание сайтов`, `Тарифы и финансы`, and global `Выйти` rendered in production shell.
- `PAGE_ERRORS=0`, `CRITICAL_CONSOLE_ERRORS=0`, `MISSING_CHUNKS=0`, failed critical API 5xx = 0 on targeted post-deploy browser QA.

Previous full UI evidence retained from the same track and not to be repeated without new regression evidence:

- Browser matrix 60/60 PASS.
- Messages responsive 4/4 PASS.
- Mobile core routes 7/7 PASS.
- `NESTED_SHELL=0` and horizontal overflow 0 on accepted matrices.

## Release composition

The accepted release contains the production routes for:

- unified AppShell/navigation;
- Command Center / dashboard redirect;
- AI employees;
- Campaign workspace and Campaign detail;
- Messages manager workspace and CRMContextPanel;
- Business Scenarios;
- import-site;
- Sitebuild (`Создание сайтов`);
- Billing (`Тарифы и финансы`);
- Campaign banners;
- Social;
- Messages setup;
- ROP;
- Analytics.

Do not rebuild an older snapshot that removes these already composed product surfaces.

## Authoritative immutable deployment contract

Verification command: `frontend/scripts/verify-frontend-deploy-contract.sh`.

Verified result on 2026-08-27:

- `FRONTEND_DEPLOY_CONTRACT=PASS`.
- Production service reads `.boris-builds/current`.
- Builds use unique isolated source snapshots/workspaces.
- Successful builds publish immutable `READY` artifacts under `.boris-builds/artifacts/` with `BORIS_ARTIFACT.json`, `BUILD_ID`, and `snapshot_source_hash`.
- Deploy uses `scripts/boris-frontend-deploy.sh`, deploy lock, atomic `.boris-builds/current` symlink switch, one `boris-frontend` restart, local/public health, and rollback on failure.
- Direct build/mutation of production project-root `.next` is forbidden.

Critical rule: **an immutable source snapshot does not have to equal the continuously moving repository HEAD at deploy time**. Later unrelated source writes belong to a later release. They do not invalidate a self-consistent READY artifact. Rebuild only when a proven P0/P1 change is required in the same release or deploying the candidate would remove production-proven functionality.

## Accepted production release

- `ACTIVE_BUILD_ID=ZWIW6dHA1EEUMCw8bnN3J`.
- `SNAPSHOT_HASH=017a26718e89295987e6fbcfd49df4ea094bd374b89543ad832971b0fcc38b0e`.
- Artifact: `/root/BORIS/frontend/.boris-builds/artifacts/20260827T205408Z-1640007-22564`.
- `ARTIFACT_STATUS=READY`, immutable = true.
- `ATOMIC_SWITCH=PASS`.
- `ATOMIC_DEPLOY=PASS`.
- Local health = 200; public health = 200.
- `boris-frontend=active`, `NRestarts=0` after deploy.
- Fresh journal after the acceptance restart: ChunkLoadError=0, missing module/manifest=0, ENOENT=0, ReferenceError=0, TypeError=0, frontend HTTP 500=0.

## Known compatibility debt / forbidden regressions

- `frontend/app/dashboard/page.tsx` remains a legacy/query-compatible surface. Bare `/dashboard` must continue to redirect to Command Center and must not regain a second shell.
- Do not add duplicate AppShell/Header/Sidebar inside product pages.
- Do not re-embed old product UI inside the canonical shell.
- Do not replace real data with demo KPI/client/deal/chart data for visual QA.
- Do not weaken auth for browser QA; use an existing authorized session/token and the real backend path.
- Do not use direct `next build` in the production tree or mutate live `.next`.
- Do not reopen already PASS interaction/responsive/navigation tracks without new production regression evidence.


## Build architecture root-cause closure — 2026-08-28

Root cause eliminated: project-root `.next` is no longer a build destination or the production runtime source.

Permanent contract:

- `npm run build` executes `scripts/boris-frontend-build.sh` and always builds from a private snapshot under `.boris-builds/work/<key>`.
- direct `next build` in `/root/BORIS/frontend` fails closed with `BORIS_SAFE_BUILD_REQUIRED` before it can clean/replace live runtime files.
- isolated build output is published only after successful compile/type/static generation as immutable `READY` artifact under `.boris-builds/artifacts/<key>` with `BUILD_ID`, `snapshot_source_hash`, `created_at`, `status=READY`, and `immutable=true`.
- production runtime uses `NEXT_DIST_DIR=.boris-builds/current`; `.boris-builds/current` is an atomic symlink to one immutable READY artifact.
- `scripts/boris-frontend-deploy.sh` is the authoritative release path; it validates artifact metadata/source hash, takes deploy lock, atomically switches `current`, restarts only `boris-frontend`, performs local/public health, and retains previous release for rollback.
- legacy `NEXT_DIST_DIR` values supplied by old workers are ignored inside the authoritative build wrapper; the private workspace always builds its own `.next`.
- parallel frontend builds are allowed and proven safe; they no longer mutate the running release. There is no requirement to wait for builders=0 merely to protect production runtime.

Root-cause proof:

- Before the fix, authenticated browser QA reproduced `This page couldn’t load` with HTTP 500 for missing chunk `/_next/static/chunks/2cv3npwy__s77.js`; project-root `.next/BUILD_ID` had disappeared during default live builds.
- After the fix, multiple concurrent `npm run build` processes ran from unique `.boris-builds/work/...` directories while the production BUILD_ID, live chunks and `/dashboard/command-center` remained available.
- direct `./node_modules/.bin/next build` now returns rc=1 with `BORIS_SAFE_BUILD_REQUIRED`; production BUILD_ID remains unchanged.
- `ISOLATED_BUILD_DOES_NOT_TOUCH_LIVE_RUNTIME=PASS`.

Accepted product UI proof after immutable-runtime migration:

- exact-current READY release was produced and deployed through the authoritative path with build/source guard; `ATOMIC_DEPLOY=PASS` and health local/public 200.
- subsequent concurrent releases changed production only through atomic deploy, not via build-time mutation of runtime files.
- current verified production release during final browser acceptance: `BUILD_ID=2PlzPNRyfLYkSi4K7WpfZ`, immutable artifact `/root/BORIS/frontend/.boris-builds/artifacts/20260828T125850Z-1916854-6465`.
- service: active/running, `NRestarts=0`; fresh critical frontend journal patterns=0.
- authenticated browser production proof: Command Center, Messages, CRM, Campaign, Sitebuild, Billing, AI Marketing and ROP = 8/8 document HTTP 200; Next error boundary=0; browser page errors=0; HTTP 5xx product/chunk responses=0.
- the only repeated 403 in browser console was optional `GET /api/nps/pending?account_id=prodazha_bytovok_25677`; it is not a critical product API and did not break any accepted screen.
- historical missing-chunk regression did not reproduce after the immutable runtime contract was active.

Final status:

`LIVE_NEXT_BUILD_FORBIDDEN=PASS`
`ISOLATED_BUILD_DEFAULT=PASS`
`SAFE_DEPLOY_PATH=PASS`
`ISOLATED_BUILD_DOES_NOT_TOUCH_PRODUCTION=PASS`
`EXACT_CURRENT_TREE_DEPLOY=PASS`
`PRODUCTION_BUILD_ID_STABLE=PASS`
`MISSING_CHUNK_REGRESSION=NO`
`FRONTEND_HTTP_500=0`
`BORIS_UNIFIED_PRODUCT_UI=PASS`
`BORIS_PRODUCT_UI_E2E=PASS`

Mode after this point: `FRONTEND_PRODUCT_GUARDIAN`. Do not reopen or cosmetically rewrite PASS product UI without new runtime/product defect evidence.
