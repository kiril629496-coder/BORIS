# BORIS MOP/messages scoped production patch — 2026-09-08

This branch intentionally does **not** copy full production source files.

Reason: the current GitHub repository does not yet contain the MOP/messages source
tree that exists on production, while the production working tree also contains
parallel changes from other workstreams. Copying whole files would mix unrelated
tasks.

The patch in this directory contains only the final scoped MOP/messages series
captured from production baselines. Apply it only after the corresponding MOP
backend baseline has been bootstrapped into GitHub.

Production acceptance for this series:
- 66/66 focused regression tests passed.
- Production contract: PASS, 20/20 accounts.
- API1/API2/public health: PASS.
- Mobile /messages: 6/6 viewports PASS.
- MOP fleet: no send_failed, stale analyzing, waiting_external, draft_ready,
  billing-invalid, or terminal stale send_error.
- Owner attention: 5 real human handoffs, 0 QA artifacts.
- Telegram direct transport no longer inherits the global HTTP(S) proxy.
- Background worker generation now tracks Telegram/MOP direct import roots.
