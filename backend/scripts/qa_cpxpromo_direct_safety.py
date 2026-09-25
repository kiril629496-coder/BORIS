#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
src = (ROOT / "app/api/cpxpromo.py").read_text(encoding="utf-8")
checks = []

def check(name, cond, detail=""):
    ok = bool(cond)
    checks.append((name, ok, detail))
    print(("PASS" if ok else "FAIL"), name, detail)

# Static ordering/contract checks: these are deliberately narrow so a later
# refactor cannot silently remove the production safety boundary.
remove_start = src.index('def remove_promotion(')
set_manual_start = src.index('def set_manual(')
remove = src[remove_start:set_manual_start]
set_manual = src[set_manual_start:]

check("REMOVE_BATCH_CAP_PRESENT", "CPXPROMO_REMOVE_BATCH_CAP_V1" in remove)
check("REMOVE_BATCH_CAP_PRETOKEN", remove.index("CPXPROMO_REMOVE_BATCH_CAP_V1") < remove.index("tok = _token"))
check("REMOVE_MASS_COUNT_GUARD", "mass_count=len(_requested_ids)" in remove)
check("REMOVE_SHARED_RUN_PREFIX", "CPXPROMO_REMOVE_RUN_SCOPE_V1" in remove and '_request_key = _remove_run_prefix + ":" +' in remove)
check("REMOVE_PER_ITEM_ACTION_LOG", "CPXPROMO_REMOVE_PER_ITEM_ACTION_LOG_V1" in remove and "_remove_log_action(" in remove)
check("REMOVE_AMBIGUOUS_5XX", '"delivery_unknown"' in remove and 'int(r.status_code) >= 500' in remove)
check("REMOVE_FINAL_THROTTLE", "CPXPROMO_REMOVE_FINAL_SHARED_THROTTLE_V1" in remove)
check("REMOVE_EXACT_POSTWRITE_VERIFY", "CPXPROMO_REMOVE_EXACT_POSTWRITE_VERIFY_V1" in remove
      and "getPromotionsByItemIds" in remove
      and '"exact_postremove_readback"' in remove)
check("REMOVE_SUCCESS_ONLY_AFTER_VERIFY",
      remove.index('"exact_postremove_readback"') < remove.index('action="Остановил продвижение"'))
check("REMOVE_VERIFY_FAIL_CLOSED",
      "postremove_verify_transport" in remove
      and "postremove_verify_429" in remove
      and "postremove_verify_http" in remove
      and "postremove_promotion_still_present_or_unproven" in remove)
check("REMOVE_VERIFY_VALID_PAYLOAD_REQUIRED",
      '_verify_items = None' in remove
      and '_verify_items is not None' in remove
      and 'not _verify_item.get("manualPromotion")' in remove
      and 'not _verify_item.get("autoPromotion")' in remove)
check("DIRECT_ITEM_LIMIT_FAIL_CLOSED", "CPXPROMO_DIRECT_ITEM_LIMIT_FAIL_CLOSED_V1" in set_manual)
check("DIRECT_SHARED_RUN_PREFIX", "CPXPROMO_DIRECT_ATOMIC_RUN_SCOPE_V1" in set_manual and '_request_key = _direct_run_prefix + ":" +' in set_manual)
check("DIRECT_FINAL_THROTTLE", "CPXPROMO_FINAL_SHARED_THROTTLE_RECHECK_V1" in set_manual)
check("DIRECT_AMBIGUOUS_5XX", "CPXPROMO_SETMANUAL_AMBIGUOUS_5XX_FAIL_CLOSED_V1" in set_manual)

# Zero-provider dynamic proof: an oversized remove request must stop before
# token/provider access. No DB/provider mutation is possible in this test.
import app.api.cpxpromo as m
orig_write = m._write_blocked
orig_token = m._token
try:
    m._write_blocked = lambda account_id: (False, None)
    def forbidden_token(*args, **kwargs):
        raise AssertionError("token/provider path reached for oversized batch")
    m._token = forbidden_token
    result = m.remove_promotion(m.ItemIDsBody(account_id="qa_no_provider", item_ids=[1,2,3,4,5,6]))
    check("REMOVE_OVERSIZED_ZERO_PROVIDER",
          result.get("status") == "blocked"
          and result.get("reason_code") == "blocked_mass_action"
          and result.get("max_actions_run") == 5
          and result.get("changed_avito") is False,
          str(result))
finally:
    m._write_blocked = orig_write
    m._token = orig_token

failed = [name for name, ok, _ in checks if not ok]
print("QA_CPXPROMO_DIRECT_SAFETY=", "PASS" if not failed else "FAIL", "checks=", len(checks), "failed=", len(failed))
if failed:
    raise SystemExit(1)
