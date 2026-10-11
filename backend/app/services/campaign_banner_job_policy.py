"""Pure retry policy for the existing Campaign banner jobs and adapter.

This module neither calls providers nor writes the database. Callers persist the
returned payload before invoking ff_workflow_banners. The adapter owns current
asset verification, provider reconciliation and the aggregate cost guard.
"""
from copy import deepcopy




def legacy_banner_attempted(payload, *, attempts=0, has_result=False):
    p = dict(payload or {})
    return bool(
        int(attempts or 0) > 1 or int(p.get("_execution_count") or 0) > 1
        or int(p.get("_continuation_count") or 0) > 0
        or int(p.get("_recovery_count") or 0) > 0
        or int(p.get("_failure_count") or 0) > 0
        or p.get("retry_counts") or p.get("_banner_qa_retries")
        or p.get("last_result") or p.get("checkpoint_at") or has_result
    )


def _outcome(payload, ids, passed, *, stage, final, code=None, extra=None):
    p = deepcopy(payload)
    passed = set(int(i) for i in passed)
    remaining = [i for i in ids if i not in passed]
    p.update(item_ids=list(ids), remaining=remaining)
    result = {
        "stage": stage, "unit": "banners", "done": len(ids)-len(remaining),
        "total": len(ids), "pending_item_ids": remaining,
        "retryable": final == "retry",
        "progress_evidence": dict(p.get("banner_confirmed_artifacts") or {}),
    }
    if code:
        result["code"] = code
    if extra:
        result.update(extra)
    return {"payload": p, "result": result, "final": final}


def plan_banner_slice(*, job_id, payload, item_ids, passed_ids, legacy_attempted,
                      intent_namespace="campaign_banners"):
    """Freeze per-item intents before the first possible paid call.

    Old jobs are never assigned new logical identities merely because they lack
    the new payload fields. A reconciled map may preserve their exact old keys.
    """
    p = deepcopy(dict(payload or {}))
    p["banner_worker_policy"] = "premium_worker_reliability_v1"
    ids = list(dict.fromkeys(int(i) for i in item_ids))
    passed = set(int(i) for i in passed_ids)
    if not ids or any(i <= 0 for i in ids) or not passed.issubset(ids):
        return _outcome(p, ids, passed, stage="banners_blocked", final="partial",
                        code="BANNER_ITEM_SCOPE_INVALID")
    remaining = [i for i in ids if i not in passed]
    if not remaining:
        return _outcome(p, ids, passed, stage="banners_done", final="completed")
    intents = dict(p.get("banner_item_intents") or {})
    budget_intents = dict(p.get("banner_item_budget_intents") or {})
    allowed_keys = {str(i) for i in ids}
    if (set(intents) | set(budget_intents))-allowed_keys:
        return _outcome(p, ids, passed, stage="banners_blocked", final="partial",
                        code="BANNER_INTENT_SCOPE_INVALID")
    if not intents:
        if legacy_attempted:
            return _outcome(p, ids, passed, stage="banners_reconciliation_required", final="partial",
                            code="LEGACY_BANNER_INTENT_RECONCILIATION_REQUIRED")
        prefix = f"editor:{int(job_id)}:banner" if intent_namespace == "editor" else f"campaign_banners:{int(job_id)}"
        budget_intents = {str(i): f"{prefix}:item:{i}" for i in ids}
        intents = {str(i): f"{budget_intents[str(i)]}:r0" for i in ids}
        p["quality_retry_counts"] = {}
    if any(not isinstance(intents.get(str(i)), str) or not intents[str(i)].strip()
           or not isinstance(budget_intents.get(str(i)), str) or not budget_intents[str(i)].strip()
           for i in remaining):
        return _outcome(p, ids, passed, stage="banners_reconciliation_required", final="partial",
                        code="BANNER_INTENT_MAPPING_INCOMPLETE")
    p.update(banner_item_intents=intents, banner_item_budget_intents=budget_intents,
             banner_intent_schema=1)
    out = _outcome(p, ids, passed, stage="banners", final="invoke")
    out["chunk"] = remaining[:4]
    out["item_idempotency_keys"] = {str(i): intents[str(i)] for i in out["chunk"]}
    out["item_budget_intent_keys"] = {str(i): budget_intents[str(i)] for i in out["chunk"]}
    return out


def _artifact_identity(error):
    try:
        version = int(error.get("version") or 0)
    except (ValueError, TypeError):
        return None
    banner_id = str(error.get("banner_id") or "").strip()
    sha = str(error.get("artifact_sha256") or "").strip()
    if (error.get("image_outcome") != "succeeded" or not banner_id or version <= 0
            or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha.lower())):
        return None
    return f"{banner_id}:v{version}:{sha.lower()}"


def finish_banner_slice(*, job_id, payload, item_ids, called_ids, result,
                        passed_ids, quality_retry_limit):
    """Only an explicit, budget-authorized quality rejection changes an intent."""
    p = deepcopy(dict(payload or {}))
    p["banner_worker_policy"] = "premium_worker_reliability_v1"
    ids = list(dict.fromkeys(int(i) for i in item_ids))
    called = set(int(i) for i in called_ids)
    passed = set(int(i) for i in passed_ids)
    result = dict(result or {})
    errors = result.get("errors") or []
    status = str(result.get("status") or "")
    if not called.issubset(ids) or not passed.issubset(ids):
        return _outcome(p, ids, passed, stage="banners_blocked", final="partial",
                        code="BANNER_ITEM_SCOPE_INVALID")
    if status == "billing_pending":
        return _outcome(p, ids, passed, stage="billing_pending", final="retry",
                        code=str(result.get("code") or "BANNER_BILLING_PENDING"), extra={"adapter_result": result})
    if status in {"billing_limit", "paid_confirmation_required", "idempotency_required"}:
        return _outcome(p, ids, passed, stage=status, final="awaiting_confirmation",
                        code=str(result.get("code") or status), extra={"adapter_result": result})
    if status not in {"ok", "error", "partial", "retrying"} or (status != "ok" and not errors):
        return _outcome(p, ids, passed, stage="banners_blocked", final="partial",
                        code=str(result.get("code") or "BANNER_ADAPTER_BLOCKED"), extra={"adapter_result": result})
    if not isinstance(errors, list):
        errors = [None]
    by_id = {}
    for error in errors:
        try:
            iid = int(error.get("id")) if isinstance(error, dict) else None
        except (ValueError, TypeError):
            iid = None
        if iid not in called or iid in by_id:
            return _outcome(p, ids, passed, stage="banners_blocked", final="partial",
                            code="BANNER_ERROR_CONTRACT_INVALID", extra={"adapter_result": result})
        by_id[iid] = error
    counts = dict(p.get("quality_retry_counts") or {})
    intents = dict(p.get("banner_item_intents") or {})
    budget_intents = dict(p.get("banner_item_budget_intents") or {})
    authorizations = deepcopy(dict(p.get("banner_retry_authorizations") or {}))
    observed = dict(p.get("banner_confirmed_artifacts") or {})
    blocked = []
    actions = {}
    for iid in sorted(called-passed):
        error = by_id.get(iid)
        if not error:
            blocked.append({"id": iid, "code": "BANNER_RESULT_UNVERIFIED"})
            continue
        action = str(error.get("retry_action") or "")
        identity = _artifact_identity(error)
        if identity:
            observed[str(iid)] = identity
        if action in {"reconcile_image", "retry_qa"} and error.get("retry_allowed") is True:
            # Same key must enter the adapter's non-image-spending recovery path.
            actions[str(iid)] = action
            continue
        if action != "regenerate_quality" or error.get("retry_allowed") is not True:
            blocked.append({"id": iid, "code": str(error.get("code") or "BANNER_RETRY_NOT_AUTHORIZED")})
            continue
        if (error.get("qa_terminal") is not True or not identity
                or not str(error.get("qa_rejection_reason") or "").strip()):
            blocked.append({"id": iid, "code": "BANNER_QUALITY_REJECTION_UNPROVEN"})
            continue
        history = list(authorizations.get(str(iid)) or [])
        if any(x.get("rejection") == identity for x in history):
            blocked.append({"id": iid, "code": "BANNER_REJECTION_ALREADY_CONSUMED"})
            continue
        current = int(counts.get(str(iid)) or 0)
        if current >= int(quality_retry_limit):
            blocked.append({"id": iid, "code": "BANNER_QUALITY_RETRY_EXHAUSTED"})
            continue
        old_key = str(intents.get(str(iid)) or "")
        budget_key = str(budget_intents.get(str(iid)) or "")
        if not old_key or not budget_key:
            blocked.append({"id": iid, "code": "BANNER_INTENT_MAPPING_INCOMPLETE"})
            continue
        next_count = current+1
        next_key = f"{budget_key}:r{next_count}"
        counts[str(iid)] = next_count
        intents[str(iid)] = next_key
        history.append({"rejection": identity, "previous_intent": old_key,
                        "next_intent": next_key, "reason": error["qa_rejection_reason"]})
        authorizations[str(iid)] = history
        actions[str(iid)] = action
    p.update(banner_item_intents=intents, quality_retry_counts=counts,
             banner_retry_authorizations=authorizations,
             banner_confirmed_artifacts=observed, banner_pending_actions=actions)
    if blocked:
        return _outcome(p, ids, passed, stage="banners_blocked", final="partial",
                        code=blocked[0]["code"], extra={"blocked_items": blocked, "adapter_result": result})
    final = "completed" if len(passed) == len(ids) else "retry"
    return _outcome(p, ids, passed, stage="banners_done" if final == "completed" else "banners",
                    final=final, extra={"batch_size": len(called), "adapter_result": result})
