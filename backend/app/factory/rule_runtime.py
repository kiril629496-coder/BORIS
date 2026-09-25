"""BORIS Rule Runtime V1.

Single scheduler principle: this module is invoked by the existing Factory cycle.
It does not own a timer and never executes arbitrary shell or arbitrary Python.

A rule is production-capable only when it has an allowlisted executor. Daily
rules are materialized into exactly-once slots, so BORIS can distinguish
"rule exists" from "rule completed N/N times today" and safely retry failed
internal work on later Factory ticks.
"""
from __future__ import annotations

import ast
import hashlib
import json
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import text

RULE_RUNTIME_V1 = True
ROOT = Path("/root/BORIS/backend")

DDL = (
    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',
    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',
    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',
)

CRITICAL_FILES = (
    "app/mop_core.py",
    "app/api/messenger.py",
    "app/api/inbox_slots.py",
    "app/services/sales_ai_router.py",
)


def ensure_schema(db):
    for statement in DDL:
        db.execute(text(statement))


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",",":"), default=str)


def _hash(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _valid_hhmm(value: str) -> bool:
    try:
        hour, minute = [int(x) for x in str(value).split(":", 1)]
        return 0 <= hour <= 23 and 0 <= minute <= 59
    except Exception:
        return False


def _executor_core_integrity(db, rule, execution_key):
    duplicates = {}
    for rel in CRITICAL_FILES:
        path = ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        seen = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                key = (type(node).__name__, node.name)
                seen.setdefault(key, []).append(node.lineno)
        dup = {"%s:%s" % key: lines for key, lines in seen.items() if len(lines) > 1}
        if dup:
            duplicates[rel] = dup
    mop = (ROOT / "app/mop_core.py").read_text(encoding="utf-8")
    checks = {
        "duplicate_symbols": duplicates,
        "human_takeover_state_defs": mop.count("def human_takeover_state("),
        "legacy_human_takeover_active_defs": mop.count("def human_takeover_active("),
        "do_send_defs": mop.count("def do_send("),
    }
    if duplicates or checks["human_takeover_state_defs"] != 1 or checks["legacy_human_takeover_active_defs"] != 0 or checks["do_send_defs"] != 1:
        raise RuntimeError("critical_code_invariant_failed: " + _canonical(checks)[:1400])
    return checks


def _executor_mop_takeover_reconcile(db, rule, execution_key):
    """Internal-only self-heal: suppress pending MOP drafts already owned by humans."""
    from app import mop_core

    rows = db.execute(text("""
        SELECT id,account_id,avito_chat_id,status
          FROM mop_drafts
         WHERE status IN ('new','analyzing','draft_ready','in_progress','editing',
                          'custom_waiting','waiting_confirm','send_failed','waiting_external')
         ORDER BY COALESCE(updated_at,created_at) ASC,id ASC
         LIMIT 200
    """)).mappings().all()
    checked = changed = 0
    for row in rows:
        checked += 1
        takeover = mop_core.human_takeover_state(db, row["account_id"], row["avito_chat_id"])
        if not takeover or not takeover.get("active"):
            continue
        current = str(row["status"] or "")
        if not mop_core.can_go(current, "human_required"):
            continue
        updated, _ = mop_core.set_status(
            db, int(row["id"]), "human_required", (current,), "handed_to_human",
            channel="rule_runtime", actor_type="system", actor_id="rule_runtime",
            payload="Rule Runtime reconciled live employee ownership before any MOP send",
            meta={"policy_version":"MOP_HUMAN_TAKEOVER_RULE_RUNTIME_V1","external_action":False},
        )
        if updated is not None:
            changed += 1
    return {"checked": checked, "changed": changed}


EXECUTORS = {
    "core_integrity_audit": _executor_core_integrity,
    "mop_takeover_reconcile": _executor_mop_takeover_reconcile,
}


def _verify_core_integrity(db, rule, execution_key, result):
    checks = _executor_core_integrity(db, rule, execution_key)
    return {"verified": True, "checks": checks}


def _verify_mop_takeover_reconcile(db, rule, execution_key, result):
    from app import mop_core
    rows = db.execute(text("""
        SELECT id,account_id,avito_chat_id,status
          FROM mop_drafts
         WHERE status IN ('new','analyzing','draft_ready','in_progress','editing',
                          'custom_waiting','waiting_confirm','send_failed','waiting_external')
         ORDER BY COALESCE(updated_at,created_at) ASC,id ASC
         LIMIT 200
    """)).mappings().all()
    unresolved = []
    for row in rows:
        takeover = mop_core.human_takeover_state(db, row["account_id"], row["avito_chat_id"])
        if takeover and takeover.get("active"):
            unresolved.append(int(row["id"]))
    if unresolved:
        raise RuntimeError("mop_takeover_postcondition_failed:" + ",".join(map(str, unresolved[:20])))
    return {"verified": True, "checked": len(rows), "unresolved": 0}


VERIFIERS = {
    "core_integrity_audit": _verify_core_integrity,
    "mop_takeover_reconcile": _verify_mop_takeover_reconcile,
}


def _verify_result(db, rule, execution_key, result):
    verifier = VERIFIERS.get(rule["executor_key"])
    if verifier is None:
        raise RuntimeError("verifier_not_registered")
    verification = verifier(db, rule, execution_key, result) or {"verified": True}
    if isinstance(verification, dict) and verification.get("verified") is False:
        raise RuntimeError("postcondition_not_verified")
    return verification


def put_rule(db, *, rule_key, account_id, title, rule_mode="daily_count", daily_target=1,
             timezone_name="Europe/Moscow", window_start="00:00", window_end="23:59",
             executor_key=None, params=None, version=1, enabled=True):
    ensure_schema(db)
    rule_key = str(rule_key or "").strip()
    if not rule_key or len(rule_key) > 64:
        raise ValueError("invalid_rule_key")
    if rule_mode != "daily_count":
        raise ValueError("unsupported_rule_mode")
    daily_target = int(daily_target or 0)
    if not 1 <= daily_target <= 96:
        raise ValueError("daily_target_out_of_range")
    if not _valid_hhmm(window_start) or not _valid_hhmm(window_end):
        raise ValueError("invalid_window")
    try:
        ZoneInfo(timezone_name)
    except Exception as exc:
        raise ValueError("invalid_timezone") from exc
    params = params or {}
    payload = {
        "rule_key": rule_key, "account_id": account_id, "title": title,
        "rule_mode": rule_mode, "daily_target": daily_target,
        "timezone": timezone_name, "window_start": window_start, "window_end": window_end,
        "executor_key": executor_key, "params": params, "enabled": bool(enabled), "version": int(version),
    }
    content_hash = _hash(payload)
    existing = db.execute(text("SELECT * FROM factory_rule_runtime WHERE rule_key=:k FOR UPDATE"), {"k": rule_key}).mappings().first()
    executor_supported = executor_key in EXECUTORS
    verifier_supported = executor_key in VERIFIERS
    supported = executor_supported and verifier_supported
    status = "active" if enabled and supported else (
        "blocked_no_executor" if enabled and not executor_supported else ("blocked_no_verifier" if enabled else "paused")
    )
    blocked = None if supported or not enabled else (
        "executor_not_registered" if not executor_supported else "verifier_not_registered"
    )
    if existing:
        if int(version) < int(existing["version"]):
            raise ValueError("rule_version_regression")
        if int(version) == int(existing["version"]) and existing["content_hash"] != content_hash:
            raise ValueError("rule_version_collision")
        if existing["content_hash"] == content_hash:
            return dict(existing), "UNCHANGED"
        db.execute(text("""
            UPDATE factory_rule_runtime SET account_id=:a,title=:t,rule_mode=:m,daily_target=:n,
                timezone=:z,window_start=:ws,window_end=:we,executor_key=:e,params=CAST(:p AS jsonb),
                enabled=:en,status=:s,version=:v,content_hash=:h,blocked_reason=:b,updated_at=now()
             WHERE rule_key=:k
        """), {"a":account_id,"t":title,"m":rule_mode,"n":daily_target,"z":timezone_name,
                  "ws":window_start,"we":window_end,"e":executor_key,"p":_canonical(params),"en":bool(enabled),
                  "s":status,"v":int(version),"h":content_hash,"b":blocked,"k":rule_key})
        return dict(db.execute(text("SELECT * FROM factory_rule_runtime WHERE rule_key=:k"), {"k":rule_key}).mappings().first()), "UPDATED"
    row = db.execute(text("""
        INSERT INTO factory_rule_runtime(rule_key,account_id,title,rule_mode,daily_target,timezone,
            window_start,window_end,executor_key,params,enabled,status,version,content_hash,blocked_reason)
        VALUES(:k,:a,:t,:m,:n,:z,:ws,:we,:e,CAST(:p AS jsonb),:en,:s,:v,:h,:b) RETURNING *
    """), {"k":rule_key,"a":account_id,"t":title,"m":rule_mode,"n":daily_target,"z":timezone_name,
              "ws":window_start,"we":window_end,"e":executor_key,"p":_canonical(params),"en":bool(enabled),
              "s":status,"v":int(version),"h":content_hash,"b":blocked}).mappings().first()
    return dict(row), "CREATED"


def ensure_builtin_rules(db):
    results = []
    specs = (
        dict(rule_key="core_integrity_4x", account_id="__platform__",
             title="Проверить целостность критического кода BORIS", daily_target=4,
             executor_key="core_integrity_audit", version=1),
        dict(rule_key="mop_takeover_reconcile_8x", account_id="__platform__",
             title="Проверить и закрыть параллельную работу человек + МОП", daily_target=8,
             executor_key="mop_takeover_reconcile", version=1),
    )
    for spec in specs:
        results.append(put_rule(db, **spec))
    return results


def _parse_hhmm(value):
    h, m = [int(x) for x in str(value).split(":", 1)]
    return time(h, m)


def _window(rule, now_utc):
    tz = ZoneInfo(str(rule["timezone"]))
    local_now = now_utc.astimezone(tz)
    start_t = _parse_hhmm(rule["window_start"])
    end_t = _parse_hhmm(rule["window_end"])
    start_local = datetime.combine(local_now.date(), start_t, tzinfo=tz)
    end_local = datetime.combine(local_now.date(), end_t, tzinfo=tz)
    if end_local <= start_local:
        raise ValueError("cross_midnight_window_not_supported")
    return local_now, start_local, end_local


def _expected(rule, now_utc):
    local_now, start_local, end_local = _window(rule, now_utc)
    target = int(rule["daily_target"])
    if local_now < start_local:
        return local_now.date(), 0
    if local_now >= end_local:
        return local_now.date(), target
    elapsed = (local_now - start_local).total_seconds()
    total = (end_local - start_local).total_seconds()
    return local_now.date(), min(target, int(target * elapsed / total))


def _execution_key(rule_key, local_day, slot_no):
    raw = f"{rule_key}|{local_day.isoformat()}|{int(slot_no)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]


def _slot_success_count(db, rule_key, local_day):
    return int(db.execute(text("""
        SELECT count(*) FROM factory_rule_runtime_slots
         WHERE rule_key=:k AND local_day=:d AND status='SUCCESS'
    """), {"k":rule_key,"d":local_day}).scalar() or 0)


def _claim_slot(db, rule_key, local_day, slot_no, now_utc):
    key = _execution_key(rule_key, local_day, slot_no)
    row = db.execute(text("""
        INSERT INTO factory_rule_runtime_slots(rule_key,local_day,slot_no,status,attempts,
            execution_key,started_at,updated_at)
        VALUES(:k,:d,:s,'RUNNING',1,:e,:now,:now)
        ON CONFLICT(rule_key,local_day,slot_no) DO NOTHING
        RETURNING *
    """), {"k":rule_key,"d":local_day,"s":int(slot_no),"e":key,"now":now_utc}).mappings().first()
    if row:
        return dict(row)
    current = db.execute(text("""
        SELECT * FROM factory_rule_runtime_slots
         WHERE rule_key=:k AND local_day=:d AND slot_no=:s FOR UPDATE
    """), {"k":rule_key,"d":local_day,"s":int(slot_no)}).mappings().first()
    if not current or current["status"] == "SUCCESS":
        return None
    attempts = int(current["attempts"] or 0)
    if attempts >= 5:
        return None
    if current["status"] == "RUNNING" and current["started_at"]:
        age = (now_utc - current["started_at"]).total_seconds()
        if age < 900:
            return None
    if current["next_retry_at"] and current["next_retry_at"] > now_utc:
        return None
    db.execute(text("""
        UPDATE factory_rule_runtime_slots SET status='RUNNING',attempts=attempts+1,
            started_at=:now,updated_at=:now,error_text=NULL WHERE id=:id
    """), {"now":now_utc,"id":current["id"]})
    return dict(db.execute(text("SELECT * FROM factory_rule_runtime_slots WHERE id=:id"), {"id":current["id"]}).mappings().first())


def _log(db, rule, action, *, after_val="", reason="", guard_reason=None, execution_key=None):
    db.execute(text("""
        INSERT INTO action_log(account_id,actor,source,action,object_kind,object_name,after_val,
            reason,trigger,request_id,guard_reason,rule_version)
        VALUES(:a,'cron','rule_runtime',:act,'правило',:name,:after,:reason,'rule_runtime',:rid,:guard,:ver)
    """), {"a":rule["account_id"],"act":action[:64],"name":rule["title"][:300],
              "after":str(after_val)[:2000],"reason":str(reason)[:1000],
              "rid":(execution_key or rule["rule_key"])[:64],"guard":(guard_reason or None),
              "ver":int(rule["version"] or 1)})


def _refresh_executor_status(db, rule):
    executor_supported = rule["executor_key"] in EXECUTORS
    verifier_supported = rule["executor_key"] in VERIFIERS
    if not executor_supported:
        if rule["status"] != "blocked_no_executor" or rule.get("blocked_reason") != "executor_not_registered":
            db.execute(text("UPDATE factory_rule_runtime SET status='blocked_no_executor',blocked_reason='executor_not_registered',updated_at=now() WHERE id=:i"), {"i":rule["id"]})
        rule["status"] = "blocked_no_executor"
        rule["blocked_reason"] = "executor_not_registered"
        return False
    if not verifier_supported:
        if rule["status"] != "blocked_no_verifier" or rule.get("blocked_reason") != "verifier_not_registered":
            db.execute(text("UPDATE factory_rule_runtime SET status='blocked_no_verifier',blocked_reason='verifier_not_registered',updated_at=now() WHERE id=:i"), {"i":rule["id"]})
        rule["status"] = "blocked_no_verifier"
        rule["blocked_reason"] = "verifier_not_registered"
        return False
    if rule["enabled"] and rule["status"] in {"blocked_no_executor", "blocked_no_verifier"}:
        db.execute(text("UPDATE factory_rule_runtime SET status='active',blocked_reason=NULL,updated_at=now() WHERE id=:i"), {"i":rule["id"]})
        rule["status"] = "active"
        rule["blocked_reason"] = None
    return True


def run_due(db, *, now=None, max_total_runs=12):
    ensure_schema(db)
    ensure_builtin_rules(db)
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    rows = [dict(r) for r in db.execute(text("SELECT * FROM factory_rule_runtime WHERE enabled ORDER BY rule_key")).mappings().all()]
    summary = {"rules":len(rows),"attempted":0,"success":0,"failed":0,"blocked":0,"items":[]}
    for rule in rows:
        db.execute(text("UPDATE factory_rule_runtime SET last_evaluated_at=:n WHERE id=:i"), {"n":now_utc,"i":rule["id"]})
        if not _refresh_executor_status(db, rule):
            summary["blocked"] += 1
            summary["items"].append({"rule_key":rule["rule_key"],"status":"blocked_no_executor"})
            continue
        local_day, expected = _expected(rule, now_utc)
        fact = _slot_success_count(db, rule["rule_key"], local_day)
        due = max(0, expected - fact)
        item = {"rule_key":rule["rule_key"],"target":int(rule["daily_target"]),"expected":expected,"fact":fact,"due":due,"status":rule["status"]}
        summary["items"].append(item)
        if rule["status"] != "active" or due <= 0:
            continue
        for slot_no in range(fact + 1, min(expected, int(rule["daily_target"])) + 1):
            if summary["attempted"] >= int(max_total_runs):
                break
            slot = _claim_slot(db, rule["rule_key"], local_day, slot_no, now_utc)
            if not slot:
                continue
            summary["attempted"] += 1
            execution_key = slot["execution_key"]
            try:
                result = EXECUTORS[rule["executor_key"]](db, rule, execution_key) or {}
                verification = _verify_result(db, rule, execution_key, result)
                persisted = dict(result)
                persisted["_verification"] = verification
                db.execute(text("""
                    UPDATE factory_rule_runtime_slots SET status='SUCCESS',result=CAST(:r AS jsonb),
                        finished_at=:n,next_retry_at=NULL,updated_at=:n WHERE id=:i
                """), {"r":_canonical(persisted),"n":now_utc,"i":slot["id"]})
                db.execute(text("UPDATE factory_rule_runtime SET last_status='SUCCESS',last_error=NULL,updated_at=now() WHERE id=:i"), {"i":rule["id"]})
                _log(db, rule, "Правило выполнено", after_val=_canonical(persisted), reason=f"дневной слот {slot_no}/{rule['daily_target']}; post-condition verified", execution_key=execution_key)
                summary["success"] += 1
            except Exception as exc:
                err = (type(exc).__name__ + ": " + str(exc))[:1800]
                db.execute(text("""
                    UPDATE factory_rule_runtime_slots SET status='FAILED',error_text=:e,finished_at=:n,
                        next_retry_at=:n + interval '30 minutes',updated_at=:n WHERE id=:i
                """), {"e":err,"n":now_utc,"i":slot["id"]})
                db.execute(text("UPDATE factory_rule_runtime SET last_status='FAILED',last_error=:e,updated_at=now() WHERE id=:i"), {"e":err,"i":rule["id"]})
                _log(db, rule, "Ошибка правила", reason=err, guard_reason="rule_execution_failed", execution_key=execution_key)
                summary["failed"] += 1
    return summary


def daily_status(db, *, now=None):
    ensure_schema(db)
    ensure_builtin_rules(db)
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    out = []
    for raw in db.execute(text("SELECT * FROM factory_rule_runtime ORDER BY rule_key")).mappings().all():
        rule = dict(raw)
        local_day, expected = _expected(rule, now_utc)
        fact = _slot_success_count(db, rule["rule_key"], local_day)
        out.append({
            "rule_key":rule["rule_key"], "title":rule["title"], "account_id":rule["account_id"],
            "status":rule["status"], "enabled":bool(rule["enabled"]), "executor_key":rule["executor_key"],
            "target":int(rule["daily_target"]), "expected":expected, "fact":fact,
            "remaining_today":max(0,int(rule["daily_target"])-fact),
            "due_now":max(0,expected-fact), "local_day":local_day.isoformat(),
            "last_status":rule["last_status"], "last_error":rule["last_error"],
        })
    return out


def force_run(db, rule_key, *, now=None):
    """QA/internal use only: execute next unsatisfied slot even before its natural due time."""
    ensure_schema(db)
    ensure_builtin_rules(db)
    rule = db.execute(text("SELECT * FROM factory_rule_runtime WHERE rule_key=:k"), {"k":rule_key}).mappings().first()
    if not rule:
        raise ValueError("rule_not_found")
    rule = dict(rule)
    if rule["executor_key"] not in EXECUTORS:
        raise ValueError("executor_not_registered")
    if rule["executor_key"] not in VERIFIERS:
        raise ValueError("verifier_not_registered")
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    local_day, _ = _expected(rule, now_utc)
    fact = _slot_success_count(db, rule_key, local_day)
    if fact >= int(rule["daily_target"]):
        return {"status":"already_complete","fact":fact,"target":int(rule["daily_target"])}
    slot_no = fact + 1
    slot = _claim_slot(db, rule_key, local_day, slot_no, now_utc)
    if not slot:
        return {"status":"slot_busy_or_backoff","slot_no":slot_no}
    try:
        result = EXECUTORS[rule["executor_key"]](db, rule, slot["execution_key"]) or {}
        verification = _verify_result(db, rule, slot["execution_key"], result)
        persisted = dict(result)
        persisted["_verification"] = verification
        db.execute(text("UPDATE factory_rule_runtime_slots SET status='SUCCESS',result=CAST(:r AS jsonb),finished_at=:n,next_retry_at=NULL,updated_at=:n WHERE id=:i"), {"r":_canonical(persisted),"n":now_utc,"i":slot["id"]})
        db.execute(text("UPDATE factory_rule_runtime SET last_status='SUCCESS',last_error=NULL,updated_at=now() WHERE id=:i"), {"i":rule["id"]})
        _log(db, rule, "Правило выполнено", after_val=_canonical(persisted), reason=f"принудительная QA-проверка слота {slot_no}/{rule['daily_target']}; post-condition verified", execution_key=slot["execution_key"])
        return {"status":"SUCCESS","slot_no":slot_no,"result":persisted}
    except Exception as exc:
        err = (type(exc).__name__ + ": " + str(exc))[:1800]
        db.execute(text("UPDATE factory_rule_runtime_slots SET status='FAILED',error_text=:e,finished_at=:n,next_retry_at=:n + interval '30 minutes',updated_at=:n WHERE id=:i"), {"e":err,"n":now_utc,"i":slot["id"]})
        db.execute(text("UPDATE factory_rule_runtime SET last_status='FAILED',last_error=:e,updated_at=now() WHERE id=:i"), {"e":err,"i":rule["id"]})
        _log(db, rule, "Ошибка правила", reason=err, guard_reason="rule_execution_failed", execution_key=slot["execution_key"])
        return {"status":"FAILED","slot_no":slot_no,"error":err}
