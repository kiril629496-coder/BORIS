# MOP runtime acceptance checkpoint — 2026-09-08

Verified on production after canonical rolling:

- public authenticated GET /api/inbox/attention?account_id=all returned HTTP 200;
- unpaid household-unit accounts 3411770_94346 and prodazha_bytovok_25677 expose zero MOP actionable human_required/send_failed/draft_ready rows;
- public authenticated GET /api/inbox/accounts returned HTTP 200;
- both household-unit accounts report mop_enabled=false and status=readonly;
- direct MOP fleet: send_failed=0, stale_analyzing=0, waiting_external=0;
- current MOP regression after concurrent messenger edits: 51/51 PASS;
- mobile /messages: 6/6 viewports PASS (320..430 px), no overflow, page errors or 5xx;
- Guardian current quick check is green; bid-cap 19/19, hard_bad=0;
- AI marketer latest natural cycle succeeded.

Global production contract may still report converging while unrelated parallel backend workstreams continue modifying runtime files after each rolling restart. No manual backend restart is authorized over an actively changing shared tree.
