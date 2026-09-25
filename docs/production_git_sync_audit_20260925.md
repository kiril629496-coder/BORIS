# BORIS production Git sync audit — 2026-09-25

## Source of truth
- Production tree: `/root/BORIS`
- Branch: `avito-money-core-production-20260909`
- Production HEAD at audit start: `0c62976881d27087097c26ab7fd956f894ab6aa1`
- Origin: `https://github.com/kiril629496-coder/BORIS.git`
- Remote branch SHA at audit start: `d9e9f30873ea115fd262b6e948ef88cab30fda8a`
- Production was ahead by 4 commits, behind by 0.

## Backup gate
Backup completed before Git mutation.
- Full tree archive: `/root/BORIS_backups/git_sync_20260925T103214Z/BORIS_pre_git_sync_20260925_0c62976881d2.tar`
- Full tar size: 29,128,550,400 bytes
- Full tar SHA256: `eafc2239a1c2898bc571473e77fb9834ec242c7f63b2da5e9dfec9dbc75f3109`
- PostgreSQL dump: `/root/BORIS_backups/git_sync_20260925T103214Z/boris_db_20260925T103214Z.dump`
- DB dump SHA256: `960abb68e006a753aab8a64ea544138ed0875e3a7631e0ef9e7d0d6c33716e39`
- Git all-refs bundle SHA256: `d33f9d8ecd1e7b8f0ced47f364b97e62eb7445e0dd3de188cb3f8eddef6f9fe7`
- Source snapshot SHA256: `9ad8c0e021fe012ca6088038207a1ece0c9d8aeddf6478e6aaa3f9afb6a8011c`
- Full tar listing verification: PASS (`TAR_OK`).
- PostgreSQL dump structure check via `pg_restore -l`: PASS.

## Initial working-tree state
At the start:
- 194 tracked changes
- 1,587 untracked paths
- 1,781 total local differences
- 26 tracked deletions
- ~11,961 tracked files before staging new production source.

## Recovery-critical code identified
Systemd and cron references were traced back to source files under `/root/BORIS`.
Previously untracked live runner/runtime files were secret-scanned and staged when no high-confidence secret signatures were found.

Examples include:
- Asterisk bridge/runtime
- BORIS background worker/guardian
- AI marketer/control-plane runners
- Telegram sales/reactivation/watchdog runners
- Telephony runtime/guardian runners
- backup/restore verification scripts
- cron-driven ROP, MOP, reporting, monitoring, ingestion and factory runners

A local Python import graph was also walked from live entrypoints; missing local dependencies were identified and staged.

## Files intentionally excluded from Git
No files were deleted from the server. Verified runtime/client artifacts were only removed from the Git staging set and covered with narrow `.gitignore` rules.

Excluded categories include:
- client PDF reports under `backend/reports/`
- QA Asterisk state/database under `media-gateway/.qa-asterisk/`
- temporary `.tmp`, `.tmp_sx` files
- backup/snapshot suffixes such as `.bk_*`, `.ownerroot.*`
- local workspace/staging directories
- content-cert/release scratch directories
- known backend runtime/cache/client-data directories

## Secret scan
- `.env.example` contains placeholders only.
- High-confidence staged scan checked common OpenAI, GitHub, Google, AWS, Telegram, PEM private-key and bearer-token formats.
- Remaining hits were limited to self-test fixtures/signature-detection examples, not production credentials.
- Production secret directories and `.env` remain excluded from Git.
- No secret values are recorded in this audit.

## Validation
### Production smoke
- `boris-backend.service`: active/running
- `boris-frontend.service`: active/running
- local backend `/health`: `status=ok`, database=`ok`
- public `https://boris-ai.pro/`: HTTP 200

### Backend
- Python compileall over app/scripts/tests: PASS.
- Broad pytest collection from backend root is invalid because legacy `backend/test_niches.py` executes `sys.exit()` during import.
- `pytest tests` is additionally blocked under the non-root audit user by protected production `backend/.env` and test DB environment requirements.
- Tests must not be rerun as root against production secrets/DB merely to bypass this safety boundary.

### Frontend
- lint/build job launched separately; final result to be recorded when complete.

## Remaining untracked local workspace
After recovering live systemd/cron code, remaining untracked paths are primarily historical/ad-hoc workspace material and are not being deleted.

Current classification snapshot:
- backup/snapshot: ~42
- patch/fix one-off scripts: ~155
- QA/probe/diagnostic: ~46
- other Python source not proven live: ~140
- data/export files: ~15
- runtime/data directories: ~7
- shell scripts not proven live: ~4
- other local artifacts: ~124

These remain on disk until individually proven disposable or worth preserving.

## Critical GitHub blocker
The currently connected repository `kiril629496-coder/BORIS` reports visibility=`public`.
No push of the recovered production delta is allowed while that repository remains public.

The second repository/account `kirillkozerogov-coder/boris` is not currently visible through the active GitHub connector, so ancestry and uniqueness cannot yet be proven from this session.

## Next safe steps
1. Finish frontend lint/build result.
2. Re-run final staged high-confidence secret scan after all live dependencies are staged.
3. Split staged production source into logical local commits.
4. Do not push until the canonical GitHub repository is private and selected.
5. Push without force only after remote SHA is revalidated.
6. Verify final `SERVER HEAD == GITHUB HEAD`.
7. Add/update `docs/DEPLOYMENT.md` with canonical repo, deploy and rollback procedure.
