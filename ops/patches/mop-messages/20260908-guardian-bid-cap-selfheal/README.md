# Guardian bid-cap missing/stale runtime self-heal — 2026-09-08

This incremental scoped patch extends the existing MOP/messages Guardian branch.

Behavior:
- exact missing bid-cap tenants are identified instead of only counting them;
- real hard-cap violations stay immediately critical;
- missing/stale runtime waits when the canonical hourly marketer is already running;
- otherwise Guardian performs at most two targeted lower-only reconciliations;
- targeted reconciliation uses the canonical cpx_cap_reconciler singleton lock;
- the recovery lane can only lower/remove promotion and cannot increase spend;
- 5-minute per-account backoff prevents Guardian pressure loops.

Production proof:
- the natural 17:05 UTC marketer retry completed SUCCESS with fail=0;
- Guardian cleared the prior failed-cycle state and returned success;
- bid-cap fleet converged to 21 configured / 21 seen / 0 missing / 0 stale / 0 hard bad;
- production contract passed 21/21;
- MOP provider-outage human tasks remained 0.

The branch continues to store only scoped operational patches because the shared
production worktree contains parallel workstreams that must not be mixed.
