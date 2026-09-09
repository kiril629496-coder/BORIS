# Maria MOP production checkpoint — 2026-09-09

Isolated checkpoint created from the live BORIS production MOP contour without
switching, resetting, cleaning, or staging the main production working tree.

Scope: Messenger MOP, combat training, persistent learning policy, provider
fallback routing, Avito duplicate protection, local Ollama fallback, Maria
real-estate qualification fallback, UI training feedback and regression tests.

This branch must be validated independently before merge/deploy. It is a
recovery/review checkpoint and does not change the currently running production
checkout by itself.

Validation before checkpoint commit:
- isolated worktree, production checkout untouched;
- Python compile of core MOP/training/router files: PASS;
- MOP + Maria qualification + local-lock + control-plane regression suite: 119 passed;
- forbidden runtime/secret path check: none;
- hardcoded credential scan across candidate files: 0 findings;
- production runtime is deployed separately by BORIS rolling deploy; this branch is not auto-deployed.

Final production validation:
- primary API full five-turn Maria qualification E2E: PASS;
- replica API full five-turn Maria qualification E2E: PASS;
- sequence under provider fallback: budget -> district/metro -> market -> rooms -> phone;
- every training turn had avito_send=false;
- real Maria Avito data (24h): exact duplicate outbound=0, fuzzy duplicate outbound=0,
  send_failed=0, BORIS reply within 120s after Avito Assistant event=0;
- Maria MOP active, auto_send enabled; account qualification settings updated in DB;
- conflicting historical training rules preserved but marked rejected; effective rules are
  the current safe platform/Maria rules only.
