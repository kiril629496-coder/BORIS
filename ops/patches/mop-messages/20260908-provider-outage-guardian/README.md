# MOP provider-outage self-heal + Guardian retry policy — 2026-09-08

This scoped production patch contains only the final MOP/messages workstream changes.

Key behavior:
- temporary sales-AI provider outage no longer creates an owner/operator handoff by itself;
- deterministic emergency mode continues qualification without paid AI;
- provider fallback diagnostics preserve the provider/reason chain;
- partnership solicitations become zero-AI business handoffs;
- sales_ai_router is included in background runtime generation tracking;
- Guardian treats one failed hourly marketer execution as a canonical-timer retry warning,
  while a second distinct failed execution in a row remains critical;
- owner-facing partnership reasons are human-readable.

Production state at capture:
- focused MOP/router regression: 106/106 PASS;
- Guardian retry-policy tests: 5/5 PASS;
- production contract: 20/20 PASS;
- mobile /messages: 6/6 PASS;
- provider-outage human tasks: 0;
- Guardian current result: success.

Legacy production-state cleanup was performed separately and is documented in manifest.txt.
It is intentionally not encoded as a replayable migration because the affected draft IDs are
live production history and the cleanup must not be re-applied blindly.

The GitHub repository still does not contain the full production MOP backend baseline, therefore
this branch stores a scoped patch instead of copying whole production files and mixing parallel
workstreams.
