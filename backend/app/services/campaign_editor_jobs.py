"""Durable adapters for existing Campaign editor functions; no second queue."""
import hashlib
import json
from typing import get_type_hints
from fastapi import HTTPException
from sqlalchemy.orm.exc import StaleDataError

OPERATIONS = {
    "texts": "ff_workflow_texts",
    "auto_media": "ff_workflow_auto_media",
    "banners": "ff_workflow_banners",
    "photo_stack": "ff_workflow_photo_stack",
    "validate": "campaign_feed_validate",
    "bulk_transform": "ff_workflow_bulk_transform",
    "restore": "campaign_restore_card_revision",
}

_BANNER_QA_AUTO_RETRY_LIMIT = 2
_BANNER_QA_RETRY_MARKERS = (
    "баннер не прошёл финальную проверку",
    "баннер или фотография не прошли финальную смысловую проверку",
)


def _retryable_banner_qa_failure(result) -> bool:
    """Only creative-QA rejection is autonomous; billing/input conflicts remain owner gates."""
    errors = list((result or {}).get("errors") or [])
    if not errors:
        return False
    messages = []
    for error in errors:
        if not isinstance(error, dict):
            return False
        message = str(error.get("message") or error.get("code") or "").strip().lower()
        if not message:
            return False
        messages.append(message)
    return bool(messages) and all(any(marker in message for marker in _BANNER_QA_RETRY_MARKERS) for message in messages)

def maybe_enqueue(db, user, campaign_id, operation, body, **extra):
    if not db.info.get("campaign_http_request") or db.info.get("campaign_editor_executing"):
        return None
    if operation not in OPERATIONS:
        raise HTTPException(422, "Неизвестная операция редактора")
    data = body.model_dump() if hasattr(body, "model_dump") else body.dict() if hasattr(body, "dict") else dict(body)
    if operation in {"restore", "bulk_transform"} and (data.get("preview_only", True) or not data.get("confirmed")):
        return None
    from app.api import campaigns as C
    from app.models.background_job import BackgroundJob
    from app.services.campaign_card_revisions import lock_versions
    from app.services.jobs import enqueue, job_public, HANDLERS
    # Safe source-first rollout: do not enqueue an unregistered worker kind.
    if "campaign_editor" not in HANDLERS:
        return None
    allowed = C._allowed_accounts(db, user)
    campaign = C._campaign_or_404(db, campaign_id, allowed)
    aid = str(campaign.default_account_id or "")
    if aid not in allowed:
        raise HTTPException(403, "Нет доступа к аккаунту")
    C._require_manage_ads(db, user, aid)
    if data.get("account_id") and data["account_id"] != aid:
        raise HTTPException(403, "Задача и аккаунт должны совпадать")
    C._ff_assert_card_editor_mutable(campaign)
    rows = C._ff_items(db, campaign_id, allowed)
    ids = [int(extra["item_id"])] if operation == "restore" else [int(i) for i in data.get("item_ids") or [r.id for r in rows]]
    selected = {int(r.id): r for r in rows if int(r.id) in ids and r.account_id == aid}
    if not ids or len(selected) != len(set(ids)):
        raise HTTPException(404, "Объявление не найдено")
    expected = data.get("expected_updated_at") or db.info.get("campaign_expected_versions")
    if operation == "validate" and not expected:
        expected = {str(i): selected[i].updated_at.isoformat() for i in ids}
    lock_versions(db, campaign_id, ids, expected, allowed)
    expected = dict(db.info.get("campaign_expected_versions") or {})
    basis = json.dumps([campaign_id, aid, operation, data, extra, expected], sort_keys=True, ensure_ascii=False)
    fingerprint = "campaign_editor:" + hashlib.sha256(basis.encode()).hexdigest()
    row = db.query(BackgroundJob).filter(BackgroundJob.fingerprint == fingerprint).first()
    created = False
    if row is None:
        row, created = enqueue(db, "campaign_editor", payload={
            "campaign_id": campaign_id, "account_id": aid, "operation": operation,
            "body": data, "extra": extra, "item_ids": ids, "remaining": ids,
            "expected_updated_at": expected,
        }, account_id=aid, owner_user_id=user.id, fingerprint=fingerprint, progress_total=len(ids))
    if created:
        from app.models.storage import Storage
        from app.services.job_execution_clock import transition, now
        db.add(Storage(account_id=aid, key=f"job_execution_clock:{row.id}",
                       value=json.dumps(transition({}, "waiting", now()))))
    db.commit()
    return {"status": "queued", "persistent": True, "created": created,
            "job_id": int(row.id), "job": public_job(row)}

def _own_completed_revision(db, job, item_id):
    from app.models.storage import Storage
    rows = db.query(Storage).filter(Storage.account_id == job.account_id,
        Storage.key.like(f"campaign_card_revision:{item_id}:%")).order_by(Storage.id.desc()).limit(100).all()
    for row in rows:
        try:
            if int(json.loads(row.value).get("job_id") or 0) == int(job.id):
                return True
        except (ValueError, TypeError):
            pass
    return False

def _run_premium_banner_slice(db, job, payload, C, campaign, user, allowed, aid):
    """Premium editor lane; uses the same policy as campaign_banners."""
    from datetime import datetime, timezone
    from app.services.campaign_card_revisions import job_revision_scope
    from app.services.campaign_banner_job_policy import (
        legacy_banner_attempted,
        plan_banner_slice, finish_banner_slice,
    )
    ids = list(dict.fromkeys(int(i) for i in payload.get("item_ids") or []))
    data = dict(payload.get("body") or {})
    force = bool(data.get("force"))
    rows = {int(r.id): r for r in C._ff_items(db, campaign.id, allowed)}
    if not ids or any(i not in rows or str(rows[i].account_id or "") != aid for i in ids):
        return {"stage": "banners_blocked", "unit": "banners", "done": 0,
                "total": len(ids), "code": "BANNER_ITEM_SCOPE_INVALID"}, "partial"

    def passed_ids():
        intents = dict((job.payload_json or {}).get("banner_item_intents") or {})
        passed = []
        for row in C._ff_items(db, campaign.id, allowed):
            iid = int(row.id)
            if iid not in ids or str(row.account_id or "") != aid:
                continue
            item = C._ff_payload(row)
            expected_intent = intents.get(str(iid)) if force else None
            own_revision = _own_completed_revision(db, job, iid)
            # Preserve the committed-revision recovery as evidence. It cannot
            # override a mismatched current receipt/hash/intent. A legacy forced
            # order without its original intent map must first be reconciled.
            if force and not expected_intent:
                continue
            ready = C._ff_premium_banner_ready(db, row, payload=item,
                                               expected_intent=expected_intent)
            if ready and (not force or expected_intent or own_revision):
                passed.append(iid)
        return passed

    def persist(out):
        state = out["payload"]
        before = dict(job.payload_json or {})
        if (out["result"]["done"] > int(job.progress_done or 0)
                or dict(state.get("banner_confirmed_artifacts") or {})
                != dict(before.get("banner_confirmed_artifacts") or {})):
            state["_banner_last_progress_at"] = datetime.now(timezone.utc).isoformat()
        state["stage"] = out["result"]["stage"]
        job.payload_json = state
        job.progress_done = out["result"]["done"]
        job.progress_total = out["result"]["total"]
        db.commit()
        return out["result"]

    plan = plan_banner_slice(
        job_id=job.id, payload=payload, item_ids=ids, passed_ids=passed_ids(),
        legacy_attempted=legacy_banner_attempted(
            payload, attempts=job.attempts, has_result=bool(job.result_json)),
        intent_namespace="editor",
    )
    initial = persist(plan)
    if plan["final"] != "invoke":
        return initial, plan["final"]
    chunk = plan["chunk"]
    data.update(
        item_ids=chunk,
        idempotency_key=data.get("idempotency_key") or f"editor:{int(job.id)}",
        item_idempotency_keys=plan["item_idempotency_keys"],
        item_budget_intent_keys=plan["item_budget_intent_keys"],
        confirmed_paid_items=len(chunk),
        force=force or any(int((plan["payload"].get("quality_retry_counts") or {}).get(str(i)) or 0) for i in chunk),
    )
    fn = C.ff_workflow_banners
    annotation = get_type_hints(fn).get("body", dict)
    body = data if annotation is dict else annotation(**data)
    previous = db.info.get("campaign_editor_executing")
    db.info["campaign_editor_executing"] = True
    try:
        with job_revision_scope(db, campaign.id, chunk, payload.get("expected_updated_at"),
                                allowed, int(job.id), "Операция редактора: banners"):
            result = fn(campaign.id, body, db, user)
            versions = dict(db.info.get("campaign_written_versions") or {})
            if versions:
                result = {**result, "item_versions": versions}
        state = dict(job.payload_json or {})
        # Continue from exactly our own committed versions, never a later read
        # which might already include another editor's changes.
        if versions:
            expected = dict(state.get("expected_updated_at") or {})
            expected.update(versions)
            state["expected_updated_at"] = expected
        state["last_result"] = result
        out = finish_banner_slice(
            job_id=job.id, payload=state, item_ids=ids, called_ids=chunk,
            result=result, passed_ids=passed_ids(),
            quality_retry_limit=_BANNER_QA_AUTO_RETRY_LIMIT,
        )
        return persist(out), out["final"]
    except (HTTPException, StaleDataError) as exc:
        db.rollback()
        if isinstance(exc, HTTPException) and exc.status_code != 409:
            raise
        detail = exc.detail if isinstance(exc, HTTPException) and isinstance(exc.detail, dict) else {}
        conflict = isinstance(exc, StaleDataError) or detail.get("code") == "CARD_VERSION_CONFLICT"
        return {"code": "CARD_VERSION_CONFLICT" if conflict else "EDITOR_REVIEW_REQUIRED", "item_id": chunk[0],
                "unit": "banners", "pending_item_ids": list(plan["result"]["pending_item_ids"]),
                "message": "Это объявление уже изменил другой сотрудник." if conflict else str(detail.get("message") or "Проверьте данные карточки перед продолжением."),
                "done": plan["result"]["done"], "total": len(ids)}, "awaiting_confirmation"
    finally:
        if previous is None:
            db.info.pop("campaign_editor_executing", None)
        else:
            db.info["campaign_editor_executing"] = previous


def run(db, job, payload):
    from app.services.jobs import _campaign_job_context
    from app.services.campaign_card_revisions import job_revision_scope
    C, campaign, user, allowed, aid = _campaign_job_context(db, job, payload)
    operation = str(payload.get("operation") or "")
    if operation not in OPERATIONS:
        raise RuntimeError("Unknown durable editor operation")
    from app.services.campaign_editor_acceptance import operation_for
    substitute = operation_for(db, campaign, user, job, operation)
    if substitute is None and operation in {"texts", "auto_media", "banners", "photo_stack", "validate"} and C._ff_paid_qa_forbidden(campaign):
        return {"status": "qa_external_blocked", "code": "QA_EXTERNAL_DISABLED",
                "message": "Внешние операции для QA-кампаний отключены. Задача сохранена без обращения к провайдерам."}, "awaiting_confirmation"
    if (operation == "banners" and substitute is None
            and str((C._ff_account_campaign_ai_policy(db, aid) or {}).get("banner_engine") or "").lower() == "full_ai_premium"):
        return _run_premium_banner_slice(db, job, payload, C, campaign, user, allowed, aid)
    ids = [int(i) for i in payload.get("item_ids") or []]
    remaining = [int(i) for i in payload.get("remaining", ids)]
    if not remaining:
        return dict(payload.get("last_result") or {}, done=len(ids), total=len(ids)), "completed"
    # Campaign-level validation and explicit confirmed bulk operations keep their
    # existing atomic scope. Other operations checkpoint one item at a time.
    chunk = remaining if operation in {"validate", "bulk_transform"} else remaining[:1]
    data = dict(payload.get("body") or {})
    if operation not in {"restore", "validate"} or substitute is not None:
        data["item_ids"] = chunk
    if operation in {"texts", "banners"}:
        # Original user intent is stable across crash/retry and provider receipts.
        data["idempotency_key"] = data.get("idempotency_key") or f"editor:{job.id}"
    if operation == "banners" and chunk:
        # A banner that failed final creative QA needs a NEW visual intent; replaying
        # the old key would intentionally reuse the same cached bytes forever.
        retries = dict(payload.get("_banner_qa_retries") or {})
        retry_number = max(0, int(retries.get(str(chunk[0])) or 0))
        if retry_number:
            data["force"] = True
            data["idempotency_key"] = f"editor:{job.id}:banner-qa-retry:{int(chunk[0])}:{retry_number}"
            # The durable editor processes one banner at a time. This explicit count
            # authorizes exactly that one new paid intent; tariff settlement still
            # happens only after all banner/gallery QA passes.
            data["confirmed_paid_items"] = 1
    fn = substitute or getattr(C, OPERATIONS[operation])
    annotation = get_type_hints(fn).get("body", dict)
    body = data if annotation is dict else annotation(**data)
    previous = db.info.get("campaign_editor_executing")
    db.info["campaign_editor_executing"] = True
    try:
        already = operation not in {"validate", "bulk_transform"} and all(_own_completed_revision(db, job, i) for i in chunk)
        if already:
            result = {"status": "ok", "changed": chunk, "recovered_committed_revision": True}
        else:
            with job_revision_scope(db, campaign.id, chunk, payload.get("expected_updated_at"),
                                    allowed, int(job.id), "Операция редактора: " + operation):
                if operation == "restore":
                    extra = payload.get("extra") or {}
                    result = fn(campaign.id, int(extra["item_id"]), str(extra["revision"]), body, db, user)
                    # Restored content is checked locally; external publication
                    # still requires the existing explicit owner gate.
                else:
                    result = fn(campaign.id, body, db, user)
                versions = dict(db.info.get("campaign_written_versions") or {})
                if versions:
                    result = {**result, "item_versions": versions}
        if operation == "restore":
            row = C._ff_item_by_id(db, campaign.id, int((payload.get("extra") or {})["item_id"]), allowed)
            result = {**result, "validation": C._ff_validate_row(row, db)}
        if operation == "banners" and chunk and _retryable_banner_qa_failure(result):
            retry_map = dict((job.payload_json or {}).get("_banner_qa_retries") or {})
            retry_key = str(int(chunk[0]))
            retry_count = max(0, int(retry_map.get(retry_key) or 0))
            if retry_count < _BANNER_QA_AUTO_RETRY_LIMIT:
                next_retry = retry_count + 1
                retry_map[retry_key] = next_retry
                next_payload = dict(job.payload_json or {})
                next_payload["_banner_qa_retries"] = retry_map
                next_payload["_banner_qa_last_item"] = int(chunk[0])
                next_payload["_banner_qa_last_error"] = str((result.get("errors") or [{}])[0].get("message") or "")[:240]
                job.payload_json = next_payload
                return {
                    **result,
                    "status": "retrying",
                    "stage": "banner_qa_retry",
                    "retryable": True,
                    "pending_item_ids": [int(chunk[0])],
                    "retry_number": next_retry,
                    "retry_limit": _BANNER_QA_AUTO_RETRY_LIMIT,
                    "message": f"Баннер не прошёл QA. BORIS сам создаёт новый вариант ({next_retry}/{_BANNER_QA_AUTO_RETRY_LIMIT}).",
                    "done": len(ids)-len(remaining),
                    "total": len(ids),
                }, "retry"
            return {
                **result,
                "status": "failed",
                "code": "BANNER_QA_RETRY_EXHAUSTED",
                "stage": "banner_qa_retry_exhausted",
                "retryable": False,
                "retry_number": retry_count,
                "retry_limit": _BANNER_QA_AUTO_RETRY_LIMIT,
                "message": "BORIS исчерпал безопасные автоматические варианты баннера. Готовые данные сохранены; задача остановлена без бесконечных повторов.",
                "done": len(ids)-len(remaining),
                "total": len(ids),
            }, "failed"
        if result.get("errors") or result.get("status") in {"qa_paid_blocked", "billing_pending", "paid_confirmation_required", "idempotency_required"}:
            return {**result, "done": len(ids)-len(remaining), "total": len(ids)}, "awaiting_confirmation"
        rest = remaining[len(chunk):]
        progress = len(ids) - len(rest)
        payload = {**dict(job.payload_json or {}), "remaining": rest, "last_result": result}
        job.payload_json = payload
        job.progress_done = progress
        job.progress_total = len(ids)
        db.commit()
        return {**result, "done": progress, "total": len(ids)}, "retry" if rest else "completed"
    except (HTTPException, StaleDataError) as exc:
        db.rollback()
        if isinstance(exc, HTTPException) and exc.status_code != 409:
            raise
        detail = exc.detail if isinstance(exc, HTTPException) and isinstance(exc.detail, dict) else {}
        conflict = isinstance(exc, StaleDataError) or detail.get("code") == "CARD_VERSION_CONFLICT"
        return {"code": "CARD_VERSION_CONFLICT" if conflict else "EDITOR_REVIEW_REQUIRED", "item_id": chunk[0],
                "message": "Это объявление уже изменил другой сотрудник." if conflict else str(detail.get("message") or "Проверьте данные карточки перед продолжением."),
                "done": len(ids)-len(remaining), "total": len(ids)}, "awaiting_confirmation"
    finally:
        if previous is None:
            db.info.pop("campaign_editor_executing", None)
        else:
            db.info["campaign_editor_executing"] = previous

def public_job(job):
    from app.services.jobs import job_public
    data = job_public(job)
    data.update(started_at=job.started_at.isoformat() if job.started_at else None,
                recovery_count=int((job.payload_json or {}).get("_recovery_count") or 0),
                result=job.result_json or {})
    return data
