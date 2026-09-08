# Unpaid MOP attention entitlement filter — 2026-09-08

This incremental scoped patch prevents historical MOP actionable states from
keeping an unpaid/inactive customer in the owner's operator queue.

Behavior:
- MOP human_required/send_failed/draft_ready are shown only when the account's
  current MOP entitlement is active;
- draft/event history is preserved;
- unread inbox evidence remains visible;
- CRM, reminders and reactivation remain visible;
- entitlement lookup fails closed for MOP actions.

Production evidence before canonical deploy:
- both household-unit accounts are inactive because their base subscription is expired;
- direct production projection after the patch shows 6 human tasks total and
  0 unpaid household-unit MOP human tasks;
- targeted regression: 24/24 PASS.

Runtime loading is intentionally left to the canonical deploy owner because the
shared backend worktree is being modified by parallel workstreams; no manual API
restart is performed over an actively changing shared tree.
