# BORIS deployment workflow

## Canonical source
Production source tree: `/root/BORIS`.

Current configured Git remote:
`https://github.com/kiril629496-coder/BORIS.git`

The repository must be private before production-source synchronization is pushed.
A second repository/account (`kirillkozerogov-coder/boris`) must be compared before any final repository migration.

## Production branch
Current production work branch:
`avito-money-core-production-20260909`

## Safety rules
Never deploy with:
- `git pull` directly in the production tree
- `git reset --hard`
- `git clean -fd`
- force push
- committing production `.env`, credentials, DB dumps, client data, recordings or runtime state

Production source changes must be backed up and committed before remote synchronization.

## Normal workflow
1. Work on a dedicated branch or reviewed production-source change.
2. Run secret scan on staged content.
3. Run backend compile/tests that are safe in an isolated test environment.
4. Run frontend lint/build.
5. Commit logical units with clear messages.
6. Push to the private canonical GitHub repository.
7. Revalidate remote SHA.
8. Deploy the exact accepted commit through the BORIS release/deploy mechanism.
9. Run live health/smoke checks.
10. Record the deployed SHA.

## Current runtime model
The live backend is launched from an immutable release path under:
`/root/BORIS/releases/backend/<release-id>/backend`

Therefore editing or committing `/root/BORIS` does not by itself deploy the working tree to the live API.

The frontend is supervised by `boris-frontend.service`.

## Pre-deploy checks
At minimum:
- `git status`
- staged secret scan
- Python compileall
- safe backend test suite
- frontend lint
- frontend build
- DB connectivity health
- API health
- frontend HTTP health
- required BORIS systemd services

Tests must not be forced to use production credentials or production DB merely to make a CI check pass.

## Production verification
After deployment record:
- repository
- branch
- full SHA
- short SHA
- commit subject
- deployment time
- smoke-test result

Target invariant:
`SERVER SOURCE HEAD == PRIVATE CANONICAL GITHUB HEAD`

The deployed runtime may use an immutable release identifier, but that release must be traceable to the accepted Git commit.

## Rollback
Code rollback and database rollback are separate operations.

For code:
1. identify the previous known-good Git SHA;
2. build/restore a release from that exact SHA using the normal release mechanism;
3. restart only the required services;
4. run health/smoke checks.

Do not use destructive working-tree reset as the rollback mechanism.

For DB changes:
- verify migration reversibility before deployment;
- use the verified PostgreSQL backup if restore is actually required;
- never overwrite newer production data merely to roll code back.

## Emergency server hotfix
If a production hotfix must be made directly on the server:
1. make the minimum change;
2. verify it;
3. immediately capture it in Git;
4. secret-scan;
5. push to the private canonical repository;
6. restore the invariant that server source and GitHub represent the same source state.

## Backup reference for 2026-09-25 sync
See:
`docs/production_git_sync_audit_20260925.md`
