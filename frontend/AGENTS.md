<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

# BORIS FRONTEND BUILD / DEPLOY CONTRACT — AUTHORITATIVE

This contract applies to every frontend task, agent and chat.

FORBIDDEN:
- direct production `next build` in `/root/BORIS/frontend`;
- using project-root `.next` as a production runtime release or development build destination;
- deleting/replacing the active runtime release before a complete READY artifact exists;
- waiting for or killing unrelated isolated builders merely to deploy.

REQUIRED BUILD FLOW:
- run `npm run build`;
- source is snapshotted into a unique `.boris-builds/work/<key>` workspace;
- Next builds only inside that private workspace;
- only a successful complete build becomes an immutable READY artifact under `.boris-builds/artifacts/<key>`;
- failed builds have zero production impact;
- parallel builds use independent work/artifact directories and may run concurrently.

REQUIRED PRODUCTION FLOW:
- runtime serves `.boris-builds/current`, an atomic symlink to one immutable READY artifact;
- deploy with `npm run deploy -- <READY-artifact>` or `deploy.sh <READY-artifact>`;
- deploy lock `.boris-builds/deploy.lock` serializes only the short production switch/restart/health/rollback lifecycle;
- atomic symlink rename guarantees `current/BUILD_ID` is never transiently missing;
- frontend is restarted only after the successful artifact is active;
- health failure atomically restores the previous release and restarts frontend;
- builders may continue during deploy because they never write the active release.

`next.config.ts` intentionally fails closed for direct production builds outside `.boris-builds/work/`.

Permanent acceptance invariant:
`LIVE_NEXT_BUILD_WRITES=0`, `ISOLATED_BUILDS=PASS`, `CONCURRENT_BUILD_SAFETY=PASS`, `ATOMIC_DEPLOY=PASS`, `FAILED_BUILD_PRODUCTION_IMPACT=0`, `ROLLBACK=PASS`.

Before every production frontend switch run `scripts/verify-frontend-deploy-contract.sh`; if it fails, deployment is forbidden until the deployment contract itself is restored.
