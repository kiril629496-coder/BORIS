# SYSTEMS.AI domain cutover — checkpoint
Date: 2026-09-27, checks approximately 22:24–22:35 Europe/Moscow.
Result: SYSTEMS_AI_DOMAIN_CUTOVER=BLOCKED_DNS. NOT DEPLOYED.

## Confirmed blocker
Both Cloudflare 1.1.1.1 and Google 8.8.8.8 resolve apex and www to 95.163.244.138.
HTTP at that IP serves a REG.RU parking page (meta parking=regru-rdap; "Домен зарегистрирован и припаркован в Рег.ру").
HTTPS fails the handshake.
BORIS public DNS and the connected server eth0 both use 193.160.209.44.
This contradicts the task's assertion that 95.163.244.138 is the BORIS server.
Required owner action: in REG.RU change A @ and A www to 193.160.209.44, retaining the existing nameservers and unrelated records. User was asked to clarify the IP discrepancy; no answer received at checkpoint.
No DNS changes were made.

## Actual architecture
/software-dev/ is NOT a Next.js route. Nginx serves /var/www/software-dev-preview as a React SPA.
Canonical JSX source: /root/BORIS/software-site-build/index.source.html
Source SHA256: 1456935cbfbdb006742e2039c6d9ce9469e6d109b199a8603d13c92d79179f5f
Existing build: /root/BORIS/software-site-build/build.sh
Existing domain deploy: /root/BORIS/software-site-deploy/deploy-domain.sh
Read the scripts before reuse: current deploy fallback produces soft-404; its CSP blocks analytics/inline scripts; its SEO and sitemap lists are separately hardcoded; finish-domain checks DNS existence but not the correct destination.
Do not blindly run finish-domain.sh or the old full deploy script.

## Inventory and baseline
35 public routes discovered from actual router, live API service config, and current BORIS sitemap.
17 service details, 6 articles, one case detail (/cases/boris-ai).
All 35 old URLs return HTTP 200.
Current sitemap contains 30 development URLs, omitting industries, prices, estimate, privacy, personal-data-consent.
Only ONE distinct case detail exists: the requested two-case acceptance cannot honestly be claimed.
Old route unknown /software-dev/this-page-does-not-exist returns 200 (soft-404).
SSR source of all old SPA routes contains the root canonical; current client code adjusts metadata.
Files: route-mapping.json, old-routes-baseline.json.

## Source/public drift
Live HTML SHA256: 0a5354b5394f6812c6290eb05b60909790c8c00a82171bb323f757cb2365077f
Live app.js SHA256: a3c20317d578ddbbf46df0591de9bf30600196dbf6d2576651a4c4ab956bae71
Canonical package app.js SHA256: e656d7f741381926c5fb1824ed0aa7ee95bac228a45b629057e3254ce4b9cb52
Normalized Babel comparison proves live JS adds an admin Metrica tab absent from JSX.
Live HTML initializes counter 112462421; JSX and the live lead goal call use 111297252.
Live HTML also has contact floating buttons and retargeting logic absent from source shell.
Reconcile these into canonical source before rebuilding; preserve old private admin on BORIS.
Existing app-icon.svg already is a blue rounded square with white S. Full versioned icon pack is NOT verified.

## Runtime baseline
SSH host boris is 8509713-oh794426.twc1.net / 193.160.209.44.
SSH became intermittent; SentinelX host_d8ca54c35cd8427c is operational and supports reads.
BORIS homepage 200; login 200; /health 200 with database ok.
boris-frontend, boris-backend and boris-backend-replica active.
Software-site health 200, storage_ok=true, smtp_configured=true, recipient_count=2, services_count=17.
These are read-only health checks, not a form-submit acceptance.
Frontend current: /root/BORIS/frontend/.boris-builds/artifacts/20260927T043144Z-2424024-14647
Backend current link: /root/BORIS/releases/backend/7fa0159a9cda5babbd295000fd90d301dcd73bd0ef6fa554b250f4f0b66daca2/backend
This is NEWER than requested d1795d516be1415839d41a09406a391db41c444536b88f3861b5256bbf615190; do not roll it back.
Current release prospect_campaigns.py SHA256: bc110d5c6ce9cff72e22e9956fc35cf1c4db37e0757548a8d06dd904b33c2be1
Worker workspace may differ; revalidate both before any scoped email change.

## Prepared, not installed
- Machine-readable old→new mapping for 35 routes.
- Read-only regression verifier for old exact 301, new 200/self-canonical, UTM redirects, private-route denial, robots and sitemap.
- New-host nginx candidate with explicit public API allowlist and 404 fallback.
- Old-host redirect candidate, only for final cutover.
Mapping uniqueness/transform 35/35 and verifier Python syntax pass.
Nginx candidates have NOT passed nginx -t and are NOT deployment-ready.
No frontend/backend source patches, banners, certificates or new releases were installed.
No forms submitted, emails sent, campaign status changed, or nginx reload performed.
No production application/config/DNS mutation by this task.

## Next actions
1. Resolve DNS discrepancy; fresh-check @ and www using external resolvers against 193.160.209.44.
2. Revalidate active releases, dirty source and ongoing writers. Resume existing outreach context sxc_FPWV267H but do not overwrite its send-window task.
3. Preserve exact nginx configs and source/release hashes, reconcile current public changes into canonical JSX; create scoped isolated build.
4. Generate all 35 route HTML heads from one inventory; versioned full S icons; 404; analytics; public API allowlist.
5. Stage noindex host, obtain separate SAN certificate, nginx -t before reload, verify renewal.
6. Prepare DEV template/6 variants/contract/banner href from current backend; retain legacy detection in BORIS exclusion guards. Update 4 JPEGs through source workflow; test without SMTP. Preserve shared cap20, pacing20min, 09–21 Moscow and evidence gates.
7. Test all new routes and mobile/tablet/desktop, one marked QA form through storage/notifications, analytics, assets and security.
8. Only then activate old 301, remove development URLs from BORIS sitemap/robots through supported isolated frontend build/deploy, remove temporary noindex, run complete regression.
9. Report PRODUCTION_PASS only after all requested gates have live evidence.

SentinelX context: sentinelx_context_sxc_VXZV71BT


## Resume checkpoint — 2026-09-27 23:56 Europe/Moscow
SYSTEMS_AI_DOMAIN_CUTOVER=BLOCKED_DNS
This section supersedes earlier "no changes" statements. Production cutover has NOT occurred.

### DNS evidence
At 20:56:55 UTC, ns1.reg.ru, ns2.reg.ru, 1.1.1.1 and 8.8.8.8 ALL returned 95.163.244.138 for BOTH systems-ai-oko.ru and www.systems-ai-oko.ru.
Required target: 193.160.209.44. Authoritative records are still wrong; this is not only a recursive-cache delay. SSL was not requested against the wrong destination.

### Changes actually installed
- Exact nginx text backups: /root/BORIS/backups/systems-domain-20260927T203621Z.
- New HTTP-only ACME/noindex/404 vhost: /etc/nginx/sites-available/systems-ai-oko.ru with sites-enabled symlink. SHA256 3166acf3f4bc7655fee3bf7991738c8ce1279342e0648c922322057fc8a80b9d.
- Internal staging vhost /etc/nginx/sites-enabled/systems-domain-staging, loopback 127.0.0.1:18793, noindex.
- nginx -t as root PASS; graceful reload completed. No BORIS vhost changes, no old redirects enabled.
- BORIS /, /login, /health and /software-dev/ all HTTP 200 after changes.

### Prepared candidate and evidence
Remote staging: /tmp/systems-domain-candidate-20260927.
- Reconciled canonical JSX with existing live admin Metrica tab and live contact/retarget shell. One counter 112462421; retain private admin on BORIS.
- Canonical build.sh ran in isolated staging. Source SHA256 740e5c3185705ca680b2136e2f435451f79d3556b663508ca9f5d032bc664b0b.
- 35 route HTML heads with self-canonical, title/description, OG and JSON-LD; 35-entry sitemap; robots; S favicon ICO/SVG/PNG/apple; root manifest; true unknown 404.
- 35/35 raw route canonical checks PASS.
- 40/40 browser checks PASS: widths 375,390,430,768,1024,1366,1440,1920, five representative pages each; no horizontal overflow, broken images or JS page errors. Analytics requests were aborted during these layout checks.
- Staging icons all 200; unknown/login/dashboard/software-dev-admin/private API 404. Internal staging manifest MIME is application/octet-stream; FINAL nginx candidate sets application/manifest+json and still requires live validation.
- Four JPEG banners generated using built-in ImageGen from originals, then JPEG format/size conversion to 1200x650. Local outputs/banners contains final files and hashes. All four copied to /var/lib/sentinelx/uploads/systems-domain-20260927, SHA256 verified; none installed into active campaign assets.
- Backend email candidate changes only app/services/prospect_campaigns.py and app/services/development_lead_radar.py against active 7fa0159a9cda5babbd295000fd90d301dcd73bd0ef6fa554b250f4f0b66daca2.
- Six DEV variants now use https://systems-ai-oko.ru/; contract rejects old URL; banner href/directory changed; BORIS historical exclusion recognizes old and new URLs.
- 24/24 offline variant/banner combinations PASS for extracted candidate functions and standard MIME roundtrip, old URL rejection, missing-banner rejection. This is NOT an installed queue-runtime/SMTP acceptance.
- No campaign DB templates, statuses, scheduler, cap20, pacing20m or 09–21 Moscow window changed; no emails sent.
- Local frontend-seo-candidate removes DEV entries from BORIS sitemap and explicit robots allow. Not built or deployed; only activate after new domain and redirects are proven.
- Local candidate-evidence.zip contains source, build script, patch, metadata and QA evidence. Local prepare_site_candidate.py / prepare_email_candidate.py remain in work.

### Remaining release gates (do not claim PASS)
1. Verify authoritative/public DNS BOTH names -> 193.160.209.44, check AAAA too; issue SAN certificate and renewal check.
2. Revalidate current active backend and source hashes. Publish source/build through supported site workflow; validate final TLS nginx candidate using actual certificate; preserve old private admin.
3. Test real HTTPS host routing/assets/35 pages, UTM, analytics and one marked owner-QA form with storage and notification evidence. No form has been submitted yet.
4. Complete legitimate scoped backend release certification; no immutable/protected bypass. Install versioned JPEG assets and update only DEV campaign 22 templates through legitimate path after new URL works. Validate actual MIME/rendering/queue. Never force SMTP outside normal policy.
5. Canonical isolated frontend build/deploy for BORIS sitemap/robots candidate; preserve unrelated changes.
6. Enable exact path/query-preserving old 301 only last, /go/software as applicable, remove temporary noindex and internal staging vhost after final acceptance.
7. Run outputs/verify-cutover.py and full live acceptance/regression. Existing content has only ONE detailed case; do not fabricate a second case or claim two-case coverage.
