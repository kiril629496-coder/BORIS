# MOP speed + shared rules + training UI — verified production recovery bundle

Purpose: preserve the exact production source snapshot after the September 10 MOP hardening and runtime verification. The main live BORIS checkout is intentionally not replaced or reset from GitHub; this repository uses operational recovery bundles.

Verified production behavior:
- Active paid MOP accounts tested: Anton plastering, Maria broker, Planeta Furniture, Vasiliy auto.
- OpenAI is not usable because billing balance is empty (`UNAVAILABLE_BILLING`). MOP does not depend on OpenAI.
- DeepSeek/PlusVibe free route is supported; Gemini and local Ollama remain fallback layers according to provider readiness/fences.
- Shared MOP rules include typo tolerance and no-repeat behavior.
- Deterministic learning executes only explicit `if client says X -> reply Y` instructions and matches whole normalized token phrases. Example word `да` can no longer match inside `аренда`.
- Client output policy blocks repeated phone requests after a phone is already present, internal/invented BORIS identity, and repeated/unconfirmed specialist-handoff promises.
- MOP AI total interactive budget is bounded and local Ollama is skipped before start when production pressure proves it unsafe.
- Public training client JS uses a 25-second `sparring/turn` AbortController timeout and always clears loading in `finally`; one click sends one POST.
- Both backend replicas were restarted after the fixes and returned healthy responses; no Traceback was seen after rollout during verification.
- Focused related regression suite: `93 passed`.
- Full production training API QA: 4/4 paid MOP accounts produced valid niche-specific replies; phone handoff after a supplied phone was deterministic and did not ask for the number again.
- Stress fallback without an available AI provider responded in roughly 0.49–0.67 seconds in the tested four accounts instead of waiting through multiple provider timeouts.
- At final queue audit: `scheduled_messages` pending = 0; stale MOP locks >10 minutes = 0.

This bundle stores exact source snapshots plus SHA256 hashes so recovery can compare byte-for-byte before applying anything. Apply only through BORIS canonical backup/test/rolling-deploy flow; never overwrite live production blindly.
