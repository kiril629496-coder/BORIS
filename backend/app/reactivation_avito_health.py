# -*- coding: utf-8 -*-
"""Read-only Avito Messenger capability probe for the reactivation circuit.

No AI, no sends and no browser automation.  The probe distinguishes entitlement
from transport failures and stores only operational metadata in account-scoped
storage.  A successful probe never mutates reactivation business state.
"""
from datetime import datetime, timezone
import time
import json

from sqlalchemy import text
from app.services.exception_observability import observe_suppressed

KEY = "reactivation_avito_capability_v1"
RESTORED_KEY = "reactivation_avito_capability_restored_v1"
INBOX_KEY = "reactivation_inbox_capability_v1"
PERSISTENCE_KEY = "reactivation_avito_persistence_v1"
PERSISTENCE_ALERT_AFTER = 3
TRANSIENT_RETRY_SECONDS = 0.35
ACTIVE_STATES = {"candidate", "needs_review", "approved", "scheduled", "cooldown"}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _classify_error(message: str) -> str:
    msg = str(message or "").lower()
    if "402" in msg or "подписку с api мессенджера" in msg or "messenger subscription" in msg:
        return "avito_messenger_subscription_required"
    if "401" in msg or "авториз" in msg or "unauthorized" in msg:
        return "avito_auth_failed"
    if "429" in msg or "rate limit" in msg or "too many requests" in msg:
        return "avito_rate_limited"
    if any(f"error {code}" in msg or f"http {code}" in msg for code in range(500, 600)):
        return "avito_api_unavailable"
    if "timeout" in msg or "timed out" in msg or "connection" in msg or "reset" in msg:
        return "avito_transport_unreachable"
    return "avito_api_error"


def _save(db, account_id: str, payload: dict):
    """Persist capability and remember a verified blocked->restored edge."""
    from app.models.storage import Storage
    row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == KEY).first()
    previous = {}
    if row and row.value:
        try:
            previous = json.loads(row.value)
        except Exception:
            previous = {}
    # A restoration fact is useful to the owner/supervisor after the current
    # capability becomes green. Store only verified transitions, never guesses.
    if (previous.get("state") in {"waiting_external", "degraded"}
            and payload.get("state") == "healthy"):
        restored = {
            "restored_at": _now_iso(),
            "from_state": previous.get("state"),
            "from_classification": previous.get("classification"),
            "to_classification": payload.get("classification"),
            "verification": "fresh_avito_capability_probe",
        }
        restored_row = db.query(Storage).filter(
            Storage.account_id == account_id, Storage.key == RESTORED_KEY
        ).first()
        restored_value = json.dumps(restored, ensure_ascii=False, separators=(",", ":"))
        if restored_row:
            restored_row.value = restored_value
        else:
            db.add(Storage(account_id=account_id, key=RESTORED_KEY, value=restored_value))
        payload = {**payload, "last_restoration": restored}
    value = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if row:
        row.value = value
    else:
        db.add(Storage(account_id=account_id, key=KEY, value=value))
    db.commit()



def save_inbox_sync_capability(db, account_id: str, sync_result: dict) -> dict:
    """Persist fresh minute-poller evidence without changing business state.

    This is intentionally separate from the bounded capability probe: the full
    inbox poller sees up to 50 chats every minute and is stronger evidence that
    an older five-chat probe must not override. No message bodies are stored.
    """
    from app.models.storage import Storage
    data = sync_result if isinstance(sync_result, dict) else {}
    status = str(data.get("status") or "error")
    synced = int(data.get("synced") or 0)
    transport_errors = int(data.get("transport_errors") or 0)
    classes = []
    for item in (data.get("transport_error_classes") or [])[:5]:
        msg = str((item or {}).get("message") or "")
        classes.append({
            "classification": _classify_error(msg),
            "count": int((item or {}).get("count") or 0),
        })
    total_402 = sum(x["count"] for x in classes if x["classification"] == "avito_messenger_subscription_required")
    top_error_classification = (
        _classify_error(str(data.get("message") or ""))
        if status not in {"ok", "partial"} else None
    )
    transient_external = {
        "avito_rate_limited",
        "avito_api_unavailable",
        "avito_transport_unreachable",
    }
    if status == "ok" and transport_errors == 0:
        state, classification = "healthy", "flow_ok"
    elif total_402 and synced == 0:
        state, classification = "waiting_external", "avito_messenger_subscription_required"
    elif total_402:
        state, classification = "degraded", "avito_messenger_history_partial_402"
    elif transport_errors == 0 and top_error_classification in transient_external:
        state, classification = "waiting_external", top_error_classification
    else:
        state, classification = "degraded", "avito_transport_degraded"
    payload = {
        "checked_at": _now_iso(), "source": "minute_inbox_sync",
        "state": state, "classification": classification,
        "sync_status": status, "synced": synced,
        "top_error_classification": top_error_classification,
        "transport_errors": transport_errors, "transport_error_classes": classes,
        "transport_blocked_chat_ids": [str(x) for x in (data.get("transport_blocked_chat_ids") or []) if x][:100],
    }
    row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == INBOX_KEY).first()
    value = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if row: row.value = value
    else: db.add(Storage(account_id=account_id, key=INBOX_KEY, value=value))
    prow = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == PERSISTENCE_KEY).first()
    prior = {}
    if prow and prow.value:
        try: prior = json.loads(prow.value)
        except Exception: prior = {}
    same = str(prior.get("classification") or "") == classification
    consecutive = (int(prior.get("consecutive") or 0) + 1) if same else 1
    first_seen = prior.get("first_seen_at") if same else payload["checked_at"]
    persistent = state in {"waiting_external", "degraded"} and consecutive >= PERSISTENCE_ALERT_AFTER
    persistence = {"checked_at":payload["checked_at"],"classification":classification,"state":state,"consecutive":consecutive,"first_seen_at":first_seen,"persistent":persistent,"owner_action_required":bool(persistent and classification=="avito_messenger_subscription_required"),"auto_reprobe":state in {"waiting_external","degraded"}}
    pvalue=json.dumps(persistence, ensure_ascii=False, separators=(",", ":"))
    if prow: prow.value=pvalue
    else: db.add(Storage(account_id=account_id,key=PERSISTENCE_KEY,value=pvalue))
    db.commit()
    return {**payload,"persistence":persistence}



def chat_transport_blocked(db, account_id: str, chat_id: str, max_age_sec: int = 180) -> bool:
    """True only when the latest minute inbox sweep explicitly saw 402 on this chat."""
    from app.models.storage import Storage
    row=db.query(Storage).filter(Storage.account_id==account_id,Storage.key==INBOX_KEY).first()
    if not row or not row.value: return False
    try:
        item=json.loads(row.value)
        checked=datetime.fromisoformat(str(item.get("checked_at") or "").replace("Z","+00:00"))
        if checked.tzinfo is None: checked=checked.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc)-checked).total_seconds()>int(max_age_sec): return False
        if str(item.get("classification") or "") not in {"avito_messenger_subscription_required","avito_messenger_history_partial_402"}: return False
        return str(chat_id) in {str(x) for x in (item.get("transport_blocked_chat_ids") or [])}
    except Exception:
        return False

def effective_capability(db, account_id: str) -> dict:
    """Newest verified read evidence wins: minute inbox sync or bounded probe."""
    from app.models.storage import Storage
    candidates=[]
    for key in (KEY, INBOX_KEY):
        row=db.query(Storage).filter(Storage.account_id==account_id, Storage.key==key).first()
        if not row or not row.value: continue
        try:
            item=json.loads(row.value); item["storage_key"]=key; candidates.append(item)
        except Exception:
            continue
    if not candidates: return {}
    def stamp(item):
        try: return datetime.fromisoformat(str(item.get("checked_at") or "").replace("Z","+00:00")).timestamp()
        except Exception: return 0
    # Full inbox sync covers up to 50 chats and is stronger evidence than the
    # bounded 5-chat probe. While that full evidence is fresh, keep it even if a
    # sample ran a few seconds later. Once it is stale, the newer probe may win.
    now_ts=datetime.now(timezone.utc).timestamp()
    inbox=next((x for x in candidates if x.get("storage_key")==INBOX_KEY),None)
    sample=next((x for x in candidates if x.get("storage_key")==KEY),None)
    inbox_fresh=bool(inbox and stamp(inbox)>0 and (now_ts-stamp(inbox)) <= 180)
    effective=inbox if inbox_fresh else max(candidates,key=stamp)
    transient_provider_classes={
        "avito_rate_limited", "avito_api_unavailable", "avito_transport_unreachable"
    }
    # Compatibility bridge for an older minute-poller process: on a top-level
    # chat-list failure it could persist only generic avito_transport_degraded,
    # while the bounded read-only probe from the same window preserved the exact
    # provider class (for example HTTP 500). Prefer that more specific fresh
    # evidence only when the full sync produced no per-chat transport failures.
    if (inbox_fresh and inbox and sample
            and str(inbox.get("classification") or "") == "avito_transport_degraded"
            and str(inbox.get("sync_status") or "") == "error"
            and int(inbox.get("transport_errors") or 0) == 0
            and str(sample.get("classification") or "") in transient_provider_classes
            and stamp(sample) > 0 and (now_ts-stamp(sample)) <= 180):
        effective=sample
    # If an older minute-poller persisted only a generic top-level failure,
    # account_slots still contains the exact error from that same sync attempt.
    # Reuse that already-paid/read evidence instead of calling Avito again.
    if (str(effective.get("classification") or "") == "avito_transport_degraded"
            and str(effective.get("sync_status") or "") == "error"
            and int(effective.get("transport_errors") or 0) == 0):
        try:
            row=db.execute(text("select last_sync_at,last_sync_error from account_slots where account_id=:a"),{"a":account_id}).first()
            if row and row[1]:
                exact_cls=_classify_error(str(row[1]))
                if exact_cls in transient_provider_classes:
                    effective={**effective,"state":"waiting_external","classification":exact_cls,
                               "classification_source":"account_slots.last_sync_error"}
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=216)
    # Historical/runtime evidence may have been persisted before transient
    # provider failures were classified as waiting_external. Normalize known
    # read-only provider outages at consumption time so the supervisor does not
    # turn Avito 5xx/rate-limit/network outages into an internal recovery P1.
    if str(effective.get("classification") or "") in transient_provider_classes and str(effective.get("state") or "") == "degraded":
        effective={**effective,"state":"waiting_external"}
    prow=db.query(Storage).filter(Storage.account_id==account_id,Storage.key==PERSISTENCE_KEY).first()
    if prow and prow.value:
        try: effective={**effective,"persistence":json.loads(prow.value)}
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=227)
    return effective

def _active_entitlement(account_id: str) -> dict:
    from app.api.messenger import get_manager_balance
    try:
        return get_manager_balance(account_id) or {}
    except Exception as exc:
        return {"active": False, "entitlement_check_error": type(exc).__name__}


def _read_with_one_transient_retry(call, *args, **kwargs):
    """One bounded self-heal retry for read-only transient Avito failures.

    Authentication refresh is already handled inside canonical messenger reads.
    402 is an entitlement/capability condition and is never retried here.
    """
    first = call(*args, **kwargs)
    if (first or {}).get("status") == "ok":
        return first, False
    cls = _classify_error((first or {}).get("message"))
    if cls not in {"avito_transport_unreachable", "avito_api_unavailable", "avito_api_error"}:
        return first, False
    time.sleep(TRANSIENT_RETRY_SECONDS)
    second = call(*args, **kwargs)
    return second, True


def probe_account(db, account_id: str, *, max_history_probe: bool = True) -> dict:
    """Probe chat-list + one chat history; never sends a message."""
    from app.api.messenger import fetch_chats, fetch_chat_messages

    balance = _active_entitlement(account_id)
    if not balance.get("active"):
        payload = {
            "checked_at": _now_iso(), "state": "stopped_expected",
            "classification": "entitlement_inactive", "entitlement": {
                "active": bool(balance.get("active")), "expired": bool(balance.get("expired")),
                "left": balance.get("left"), "days_left": balance.get("days_left"),
            },
            "chat_list": "not_checked", "history": "not_checked",
            "send": "not_checked", "incoming": "not_checked", "receipt": "not_checked",
            "self_heal": {"attempted": False, "restored": False},
        }
        _save(db, account_id, payload)
        return payload

    result = {
        "checked_at": _now_iso(), "state": "healthy", "classification": "flow_ok",
        "entitlement": {"active": True, "expired": bool(balance.get("expired")),
                         "left": balance.get("left"), "days_left": balance.get("days_left")},
        "chat_list": "unknown", "history": "unknown", "send": "unknown",
        "incoming": "unknown", "receipt": "unknown", "chat_count": 0,
        "self_heal": {"attempted": False, "restored": False},
    }

    chats_result, retried = _read_with_one_transient_retry(fetch_chats, account_id, unread_only=False)
    result["self_heal"]["attempted"] = bool(retried)
    result["self_heal"]["restored"] = bool(retried and chats_result.get("status") == "ok")
    if chats_result.get("status") != "ok":
        cls = _classify_error(chats_result.get("message"))
        waiting_external_classes = {
            "avito_messenger_subscription_required",
            "avito_rate_limited",
            "avito_api_unavailable",
            "avito_transport_unreachable",
        }
        result.update(state="waiting_external" if cls in waiting_external_classes else "degraded",
                      classification=cls, chat_list="failed", history="not_checked")
        _save(db, account_id, result)
        return result

    chats = chats_result.get("chats") or []
    result["chat_list"] = "ok"
    result["chat_count"] = len(chats)
    if not chats:
        result.update(history="no_data", incoming="no_data", history_probe_count=0, history_probe_failures=0)
    elif max_history_probe:
        # Messenger access can differ per dialog. One readable chat must not turn
        # the account green while other recent chats return subscription 402.
        sample = chats[:min(5, len(chats))]
        probed = failures = 0
        incoming_observed = False
        error_classes = []
        for chat in sample:
            chat_id = str((chat or {}).get("id") or "")
            if not chat_id:
                continue
            probed += 1
            history, history_retried = _read_with_one_transient_retry(fetch_chat_messages, account_id, chat_id)
            if history_retried:
                result["self_heal"]["attempted"] = True
                if history.get("status") == "ok":
                    result["self_heal"]["restored"] = True
            if history.get("status") == "ok":
                raw = history.get("messages") or {}
                msgs = raw.get("messages", []) if isinstance(raw, dict) else (raw or [])
                incoming_observed = incoming_observed or any(str(m.get("direction", "")).lower().startswith("in") for m in msgs)
            else:
                failures += 1
                error_classes.append(_classify_error(history.get("message")))
        result["history_probe_count"] = probed
        result["history_probe_failures"] = failures
        result["history_error_classes"] = sorted(set(error_classes))
        result["incoming"] = "observed" if incoming_observed else "no_recent_incoming"
        if "avito_messenger_subscription_required" in error_classes:
            # HISTORY_402_CHAT_SCOPE_V1: a 402 on only some sampled dialogs is
            # chat-scoped evidence, not proof that the account cannot send/read
            # every dialog. Keep the account operational/degraded and let the
            # exact-chat receive preflight fail closed before any POST. Only an
            # all-sampled-chat 402 may classify account-wide waiting_external.
            if failures < probed:
                result.update(history="partial", state="degraded", classification="avito_messenger_history_partial_402", history_error_class="avito_messenger_subscription_required")
            else:
                result.update(history="failed", state="waiting_external", classification="avito_messenger_subscription_required", history_error_class="avito_messenger_subscription_required")
        elif failures:
            cls = error_classes[0] if error_classes else "avito_api_error"
            result.update(history="partial" if failures < probed else "failed", state="degraded", classification=cls, history_error_class=cls)
        else:
            result["history"] = "ok"

    row = db.execute(text("""
        SELECT count(*) FILTER (WHERE status='sent' AND avito_message_id IS NOT NULL),
               count(*) FILTER (WHERE status='delivery_unknown'),
               count(*) FILTER (WHERE error_code='transport_unavailable')
          FROM reactivation_messages
         WHERE account_id=:a
           AND sent_at > now() - interval '30 days'
    """), {"a": account_id}).one()
    if int(row[0] or 0) > 0:
        result["send"] = "verified_by_recent_receipt"
        result["receipt"] = "verified_by_provider_id"
    elif int(row[1] or 0) > 0:
        result["send"] = "delivery_unknown_present"
        result["receipt"] = "missing"
    elif int(row[2] or 0) > 0:
        result["send"] = "transport_error_history"
        result["receipt"] = "unknown"
    else:
        result["send"] = "no_recent_proof"
        result["receipt"] = "no_recent_proof"

    _save(db, account_id, result)
    return result

def probe_accounts(db, account_ids):
    out = []
    for account_id in account_ids:
        try:
            out.append({"account_id": account_id, **probe_account(db, account_id)})
        except Exception as exc:
            db.rollback()
            payload = {"checked_at": _now_iso(), "state": "degraded",
                       "classification": "probe_exception", "error_type": type(exc).__name__}
            try:
                _save(db, account_id, payload)
            except Exception:
                db.rollback()
            out.append({"account_id": account_id, **payload})
    return out
