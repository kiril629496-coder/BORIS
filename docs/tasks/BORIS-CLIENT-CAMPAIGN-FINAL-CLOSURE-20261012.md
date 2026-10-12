# BORIS Client Campaign Final Closure — Engineering Handoff

Date: 2026-10-12
Scope: Campaign 508 (Планета), 517 (Мария), 538 (Альберт)

## Purpose

This document records the production contracts established while closing the Campaign/Banner/Avito failure chain. Future changes must preserve these contracts.

## Immutable business invariants

1. Provider-confirmed publication is the only publication truth.
2. A successful/published item is never replayed merely because a sibling failed.
3. Explicit item_ids are an immutable subset boundary through banners → prepare → validation → feed → delivery.
4. Cost anomaly blocks new paid provider calls, not local/cache-only recovery.
5. A terminal partial business outcome is not a retryable infrastructure error.
6. Prepare-only owner orders must never be converted into autonomous confirmed=True publication.
7. A submitted direct-upload upload_id is immutable for exactly-once/funding watchers.
8. Machine metadata is server identity, never visual copy to be judged by Vision.
9. Legacy Premium storage aliases are equivalent only when tenant + banner identity + version + exact SHA-256 + current receipt + provenance all match.
10. Runtime source, immutable release, API generation and worker generation must converge before a change is accepted as deployed.

## Current client state

### Campaign 508 — Планета
- 120/120 published and provider-confirmed.
- Do not submit again.
- Legacy banner policy: banner_every_n=4 (about 30 banners for 120 cards).

### Campaign 517 — Мария
- 44-card ready/delivery scope.
- Original and only real submit upload: 614146079.
- Avito blocker: error_fee / message 2214 (advance funding required).
- Watcher is read-only and pinned to upload 614146079.
- No repeat feed submit is permitted while waiting for funding.

### Campaign 538 — Альберт
- Requested total: 100 active cards; superseded rows excluded.
- Current truthful ready subset: 42.
- One terminal banner block observed on item 6981: Awkward crop.
- Owner order is prepare-only: external publication is forbidden at this stage.
- publication_policy.mode=prepare_only is authoritative.
- Historical autonomous uploads 614524541 and 614528860 are incident evidence only; they are not current activation state.
- Campaign must remain partial / ready_subset_for_review until a real owner decision changes the policy.

## Key production fixes / commits

- 5510ddf — cost anomaly recovery without paid QA loops
- f5ae4db — reconcile non-banner legacy media
- e3d0ab9 — terminalize exhausted provider-free repair
- d2460dd / 68df18a — preserve exact ready subset through banners/prepare
- 051b039 / b8c064d / e657b50 — Premium QA machine-metadata isolation and legacy QA recovery
- 6544c3e — migrate old banner QA receipts and Avito funding wait
- 50bba99 / 46819aa / 089bad0 — terminal visual block does not stall siblings; blocked card readiness stays truthful
- 9bb018b — exact-byte Premium banner alias acceptance
- 54b62e0 — subset continuation skips full rematerialization
- b8b19ab — exact ready subset finalizes as partial, not as full order
- a50351d / d47c2d1 — durable chunked Premium validation and exact-scope feed validation
- f585a99 — terminal partial jobs do not hot-resume
- daf23c9 — durable prepare-only owner-order enforcement
- 20c9544 — report-only observation under prepare-only policy
- 63b97cb — funding watcher pinned to submitted upload identity

## Release discipline

For Campaign production changes:
1. Keep runtime delta narrowly scoped.
2. Run focused regression tests.
3. Commit atomically.
4. Build candidate from current immutable release, not the dirty worktree.
5. Create BORIS_SCOPED_RELEASE_SCOPE_V1 manifest.
6. Run scoped_release_certification.py.
7. Require architecture_release_gate PASS.
8. Activate immutable release.
9. Restart worker through allowed service action.
10. Converge APIs only through boris-deploy.service / rolling_restart_backend.sh.
11. Verify /health and process paths.
12. Verify DB business state and external-write audit.

Never edit /root/BORIS/releases/backend/<hash> directly.

## Retry / partial semantics

- providerfree_repair_exhausted: terminal partial.
- banners_partial: terminal partial.
- ready_subset_for_review: terminal partial.
- publication_partial: terminal provider evidence.
- awaiting_external_funding: recurring read-only observation; no resubmit.
- ALREADY_PUBLISHED_CONFIRMED: terminal exactly-once noop.

A repair after a terminal partial creates a new repair/revision job identity; it does not hot-resume the historical terminal job.

## Avito upload identity rules

Direct submit:
- Capture upload_id once after provider acceptance.
- Persist it in BackgroundJob and Feed Factory owner_activation.
- Future direct/funding observations must query that same upload_id.
- Do not replace it with /uploads/current or a newer account upload.
- If old upload falls out of bounded provider history, return pending/unknown for that same id unless the workflow explicitly permits newer-scope reconciliation.

Scheduled/cutover workflows may follow a newer upload only under their separate exact-scope contracts.

## Prepare-only owner policy

Durable publication_policy is authoritative:
- mode=prepare_only
- automatic_external_write=false
- requires_real_owner_activation=true

Legacy prompt parsing is only a recovery mechanism. It must recognize both:
- "не публиковать / не отправлять"
- "публикацию ... не выполнять"

No autonomous worker may call confirmed=True when prepare-only is active.

## Banner QA rules

- Vision result binds by server-authored slot.
- Model echoes of banner_id/version/SHA are never trusted as identity.
- Visual failures such as Awkward crop are real QA failures.
- A proven terminal visual failure may terminal-block only that item if bounded paid retry is unavailable.
- Siblings continue.
- Unknown/reconciliation outcomes remain fail-closed.
- Batch density/composition QA remains mandatory.

## Required regression coverage for future changes

At minimum preserve tests for:
- exact subset does not widen
- terminal partial does not auto-resume
- prepare-only blocks autonomous external write
- reverse-negation owner prompt parsing
- funding watcher pins original upload
- exact-byte Premium alias acceptance/rejection
- terminal banner block continues siblings
- machine metadata not treated as visible text
- Planeta 120 / banner_every_n=4 → target 30
- superseded items excluded from requested active scope
- provider-confirmed full success prevents duplicate deliver

## Incident notes

Two autonomous Avito uploads were observed for Campaign 538 before the prepare-only parser/policy fix:
- 614524541
- 614528860

They are retained as historical evidence. Their item-level scope could not be reconstructed with sufficient provider certainty and must not be represented as successful publication of Campaign 538.

Campaign 517 had one real submit:
- 614146079

A later watcher temporarily drifted to a newer account upload without a new external write. Commit 63b97cb prevents this class of identity drift.

## Definition of done

A Campaign change is not done merely because source is committed.
Done requires:
- tests PASS
- scoped certification PASS
- immutable release active
- API and worker generations match current
- health PASS
- DB state truthful
- external-write audit consistent with owner policy
- no unintended new provider write
