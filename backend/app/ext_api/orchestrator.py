# -*- coding: utf-8 -*-
"""Оркестратор: единственное место, где рождается и живёт Job.

Шлюз не содержит бизнес-логики. Он проверяет права, создаёт Job, отдаёт работу
штатному исполнителю BORIS, запускает Auto QA и возвращает результат.
"""
import os, json, time, threading, traceback
from datetime import datetime

from . import db, auth, registry, discovery, preflight, postflight, costs
from .errors import ApiError, classify, log_internal
from app.services.exception_observability import observe_suppressed

STATUSES = ("queued", "running", "completed", "failed", "blocked", "cancelled")
TEST_HOOKS = os.environ.get("EXT_API_ALLOW_TEST_HOOKS") == "1"


# ============================================================ хранилище Job

def job_public(j):
    if not j:
        return None
    for f in ("params_json", "result_json", "preflight_json", "postflight_json"):
        if isinstance(j.get(f), str):
            try:
                j[f] = json.loads(j[f])
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=26)
    return {
        "job_id": f"job_{j['id']}", "id": j["id"], "request_id": j.get("request_id"),
        "account_id": j.get("account_id"), "account_ref": j.get("account_ref"),
        "operation": j.get("operation"), "op_class": j.get("op_class"),
        "actor": {"type": j.get("actor_type") or "ai_agent", "name": j.get("actor")},
        "status": j.get("status"), "attempts": j.get("attempts"),
        "max_attempts": j.get("max_attempts"),
        "created_at": j.get("created_at"), "started_at": j.get("started_at"),
        "finished_at": j.get("finished_at"),
        "params": j.get("params_json"), "result": j.get("result_json"),
        "preflight": j.get("preflight_json"),
        "qa": {"status": j.get("qa_status"), **(j.get("postflight_json") or {})}
              if j.get("qa_status") else None,
        "error": ({"code": j.get("error_code"), "message": j.get("error_text"),
                   "retryable": j.get("retryable")} if j.get("error_code") else None),
        "next_action": j.get("next_action"), "handoff_id": j.get("handoff_id"),
        "fixed_by": j.get("fixed_by"), "dry_run": j.get("dry_run"),
        "idempotency_key": j.get("idempotency_key"),
        "costs": costs.report(job_id=j["id"]),
    }


def get_job(job_id):
    if isinstance(job_id, str) and job_id.startswith("job_"):
        job_id = job_id[4:]
    try:
        job_id = int(job_id)
    except (TypeError, ValueError):
        raise ApiError("JOB_NOT_FOUND", f"неверный идентификатор задачи: {job_id}")
    return db.one("SELECT * FROM ext_jobs WHERE id=:i", i=job_id)


def _set(job_id, **fields):
    if not fields:
        return
    sets = ", ".join(f"{k}=:{k}" for k in fields)
    db.q(f"UPDATE ext_jobs SET {sets}, updated_at=NOW() WHERE id=:__id",
         __id=job_id, **fields)


def _attempt_start(job_id, n):
    return db.one("""INSERT INTO ext_job_attempts (job_id, attempt_number, status)
                     VALUES (:j,:n,'running') RETURNING id""", j=job_id, n=n)["id"]


def _attempt_end(aid, status, error_code=None, error_text=None, result=None):
    db.q("""UPDATE ext_job_attempts SET finished_at=NOW(), status=:s, error_code=:c,
                error_text=:e, result_json=:r WHERE id=:i""",
         i=aid, s=status, c=error_code, e=(str(error_text)[:2000] if error_text else None),
         r=(json.dumps(result, ensure_ascii=False, default=str)[:20000] if result else None))


def attempts_of(job_id):
    return db.rows("""SELECT attempt_number, started_at, finished_at, status,
                             error_code, error_text FROM ext_job_attempts
                      WHERE job_id=:j ORDER BY attempt_number""", j=job_id)


# ============================================================== исполнители

def _campaign_create(job, p, acc):
    name = p["name"]
    exist = db.one("""SELECT id FROM campaigns
                      WHERE name=:n AND CAST(default_account_id AS TEXT)=:a
                      ORDER BY id LIMIT 1""", n=name, a=str(acc["id"]))
    if exist:
        return {"campaign_id": exist["id"], "created": False, "account_id": acc["id"],
                "executor": "existing_row"}

    fn, target, _ = discovery.resolve(registry.get("campaign_create").get("executors") or [])
    if fn is not None:
        try:
            res = fn(name=name, account_id=acc["id"])
            cid = res.get("id") if isinstance(res, dict) else getattr(res, "id", None)
            if cid:
                return {"campaign_id": int(cid), "created": True, "account_id": acc["id"],
                        "executor": target}
        except Exception as e:
            log_internal(job.get("request_id"), "campaign_service.new_campaign", e)

    cols = db.columns("campaigns")
    f, v, prm = [], [], {}

    def add(col, nm, val, raw=None):
        if col in cols:
            f.append(col); v.append(raw or f":{nm}"); prm[nm] = val

    jt = db.columns("campaigns").get("settings_json", "text")
    raw_settings = ("CAST(:st AS jsonb)" if jt in ("json", "jsonb") else ":st")
    add("name", "name", name)
    add("default_account_id", "acc", db.fit("campaigns", "default_account_id", acc["id"]))
    add("owner_user_id", "own", db.fit("campaigns", "owner_user_id", acc.get("owner_user_id")))
    add("scenario_type", "scn", p.get("scenario_type") or "ads")
    add("source_type", "src", "agent")
    add("status", "stt", "active")
    add("settings_json", "st", json.dumps(p.get("settings") or {}, ensure_ascii=False),
        raw=raw_settings)
    row = db.one(f"INSERT INTO campaigns ({', '.join(f)}) VALUES ({', '.join(v)}) RETURNING id",
                 **prm)
    return {"campaign_id": row["id"], "created": True, "account_id": acc["id"],
            "executor": "campaigns_table"}


ITEM_STATUSES = ("imported", "analyzing", "needs_data", "draft", "ready",
                 "awaiting_confirmation", "publishing", "published", "publish_failed", "paused")


def _ads_create(job, p, acc):
    campaign_id = p.get("campaign_id")
    if not campaign_id:
        camp = _campaign_create(job, {"name": p.get("campaign_name") or
                                      f"Кампания агента · аккаунт {acc['id']}"}, acc)
        campaign_id = camp["campaign_id"]
    source = p.get("source") or "agent"
    items = p.get("items")
    if not isinstance(items, list) or not items:
        raise ApiError("MISSING_INPUT", "в params.items не передано ни одного объявления")
    cols = db.columns("campaign_items")
    jt = cols.get("payload_json", "text")
    pj = "CAST(:payload AS jsonb)" if jt in ("json", "jsonb") else ":payload"
    created = updated = skipped = 0
    refs, ids = [], []

    for it in items:
        ref = it.get("source_ref")
        if not ref or not it.get("title"):
            raise ApiError("VALIDATION_ERROR", "у каждого элемента нужны source_ref и title")
        st = it.get("status") or "draft"
        if st not in ITEM_STATUSES:
            raise ApiError("VALIDATION_ERROR", f"недопустимый статус элемента «{st}»")
        refs.append(ref)
        payload = {"title": it.get("title"), "description": it.get("description"),
                   "price": it.get("price"), "category_id": it.get("category_id"),
                   "images": it.get("images") or [], "keywords": it.get("keywords") or [],
                   **(it.get("extra") or {})}
        ch = db.content_hash(payload)
        hid = db.hypothesis_id(it.get("geo"), it.get("intent"), it.get("offer"), it.get("segment"))
        exist = db.one("""SELECT id, content_hash FROM campaign_items
                          WHERE CAST(campaign_id AS TEXT)=:c AND source=:s AND source_ref=:r""",
                       c=str(campaign_id), s=source, r=ref)
        if exist:
            ids.append(exist["id"])
            if exist["content_hash"] == ch:
                skipped += 1
                continue
            db.q(f"""UPDATE campaign_items SET payload_json={pj}, content_hash=:ch, status=:st,
                        segment=:sg, intent=:itn, geo=:geo, offer=:of, hypothesis_id=:hid,
                        updated_at=NOW() WHERE id=:id""",
                 payload=json.dumps(payload, ensure_ascii=False), ch=ch, st=st,
                 sg=it.get("segment"), itn=it.get("intent"), geo=it.get("geo"),
                 of=it.get("offer"), hid=hid, id=exist["id"])
            updated += 1
            continue

        f, v, prm = [], [], {}

        def add(col, nm, val, raw=None):
            if col in cols:
                f.append(col); v.append(raw or f":{nm}"); prm[nm] = val

        add("campaign_id", "c", db.fit("campaign_items", "campaign_id", campaign_id))
        add("account_id", "a", db.fit("campaign_items", "account_id", acc["id"]))
        add("external_item_id", "ext", it.get("external_item_id"))
        add("segment", "sg", it.get("segment"))
        add("intent", "itn", it.get("intent"))
        add("geo", "geo", it.get("geo"))
        add("offer", "of", it.get("offer"))
        add("hypothesis_id", "hid", hid)
        add("status", "st", st)
        add("source", "src", source)
        add("source_ref", "ref", ref)
        add("content_hash", "ch", ch)
        add("payload_json", "payload", json.dumps(payload, ensure_ascii=False), raw=pj)
        row = db.one(f"INSERT INTO campaign_items ({', '.join(f)}) "
                     f"VALUES ({', '.join(v)}) RETURNING id", **prm)
        created += 1
        ids.append(row["id"])

    if TEST_HOOKS and p.get("__test_break_postflight"):
        # намеренная порча фактического состояния: исполнитель вернёт success,
        # а в базе элементов не будет. Проверяет, что QA ловит расхождение.
        db.q("DELETE FROM campaign_items WHERE CAST(campaign_id AS TEXT)=:c AND source=:s",
             c=str(campaign_id), s=source)

    return {"campaign_id": campaign_id, "source": source, "expected_refs": refs,
            "requested": len(items), "created": created, "updated": updated,
            "skipped": skipped, "item_ids": ids, "executor": "campaign_items_table"}


def _launch_client(job, p, acc):
    if p.get("publish"):
        raise ApiError("EXTERNAL_PUBLISH_DISABLED",
                       "publish=true в этой версии шлюза запрещён; запускайте подготовку с publish=false")
    steps = []
    camp = _campaign_create(job, {"name": p.get("campaign_name") or
                                  f"Запуск клиента · аккаунт {acc['id']}"}, acc)
    steps.append({"step": "campaign_create", "result": camp})
    out = {"campaign_id": camp["campaign_id"], "account_id": acc["id"], "steps": steps,
           "mode": "prepare", "published": 0, "executor": "gateway_prepare"}

    items = p.get("items")
    if items:
        ads = _ads_create(job, {"campaign_id": camp["campaign_id"], "items": items,
                                "source": p.get("source") or "agent",
                                "__test_break_postflight": p.get("__test_break_postflight")}, acc)
        steps.append({"step": "ads_create", "result": {k: ads[k] for k in
                      ("requested", "created", "updated", "skipped")}})
        out.update({k: ads[k] for k in ("expected_refs", "requested", "created",
                                        "updated", "skipped", "source")})
    elif p.get("ads"):
        raise ApiError("MISSING_INPUT",
                       "запрошено ads=%s, но тексты объявлений не переданы в params.items; "
                       "генерация текстов в этой версии шлюза не выполняется" % p["ads"])

    for k in ("images_per_ad", "banners", "feed"):
        if p.get(k):
            out.setdefault("not_executed", []).append(
                {"stage": k, "reason": "NOT_IMPLEMENTED",
                 "detail": "нет штатного исполнителя, подключённого к шлюзу"})
    return out


def client_status_of(acc):
    camps = db.rows("""SELECT id, name, status, created_at FROM campaigns
                       WHERE CAST(default_account_id AS TEXT)=:a
                       ORDER BY id DESC LIMIT 20""", a=str(acc["id"]))
    by_status = db.rows("""SELECT status, COUNT(*) AS n FROM campaign_items
                           WHERE CAST(account_id AS TEXT)=:a GROUP BY status""",
                        a=str(acc["id"]))
    jobs = db.rows("""SELECT id, operation, status, created_at FROM ext_jobs
                      WHERE account_id=:a ORDER BY id DESC LIMIT 10""", a=acc["id"])
    cols = db.columns("accounts")
    creds = {c: bool(acc.get(c)) for c in ("avito_client_id", "avito_client_secret",
                                           "avito_user_id") if c in cols}
    return {"account_id": acc["id"], "account": acc.get(db.account_name_col()),
            "campaigns": camps, "items_by_status": {r["status"]: r["n"] for r in by_status},
            "items_total": sum(r["n"] for r in by_status),
            "recent_jobs": [{"job_id": f"job_{j['id']}", "operation": j["operation"],
                             "status": j["status"], "created_at": j["created_at"]} for j in jobs],
            "credentials_present": creds}



def _client_status(job, p, acc):
    return client_status_of(acc)


def _kpi_check(job, p, acc):
    fn, target, _ = discovery.resolve(registry.get("kpi_check").get("executors") or [])
    if fn is not None:
        try:
            return {"executor": target, "result": fn(acc["id"])}
        except Exception as e:
            log_internal(job.get("request_id"), "kpi executor", e)
    published = db.one("""SELECT COUNT(*) AS n FROM campaign_items
                          WHERE CAST(account_id AS TEXT)=:a AND status='published'""",
                       a=str(acc["id"]))["n"]
    total = db.one("SELECT COUNT(*) AS n FROM campaign_items WHERE CAST(account_id AS TEXT)=:a",
                   a=str(acc["id"]))["n"]
    return {"executor": "gateway_readonly", "items_total": total, "items_published": published,
            "note": "штатный KPI-исполнитель не подключён, отдан срез по данным BORIS"}


def _verify(job, p, acc):
    target = get_job(p["job_id"])
    if not target:
        raise ApiError("JOB_NOT_FOUND", f"задача {p['job_id']} не найдена")
    res = target.get("result_json")
    if isinstance(res, str):
        try:
            res = json.loads(res)
        except Exception:
            res = {}
    qa = postflight.run(target["operation"], res or {})
    return {"verified_job": f"job_{target['id']}", "operation": target["operation"], "qa": qa}


HANDLERS = {
    "campaign_create": _campaign_create,
    "ads_create": _ads_create,
    "launch_client": _launch_client,
    "client_status": _client_status,
    "kpi_check": _kpi_check,
    "verify": _verify,
}


# ============================================================== жизненный цикл

def create_job(key, request_id, operation, account_ref, params, idempotency_key=None,
               confirm=False, dry_run=False, max_attempts=3):
    spec = registry.get(operation)
    checks, acc, err = preflight.run(key, operation, account_ref, params or {}, confirm=confirm)
    op_class = (spec or {}).get("cls", "READ_ONLY")

    row = db.one("""INSERT INTO ext_jobs
            (request_id, key_id, actor, actor_type, account_id, account_ref, operation,
             op_class, params_json, status, max_attempts, confirmed, dry_run,
             preflight_json, idempotency_key)
          VALUES (:rq,:k,:ac,:at,:a,:ar,:op,:cl,:p,:st,:ma,:cf,:dr,:pf,:ik) RETURNING id""",
        rq=request_id, k=key["id"], ac=key.get("actor_name") or key["name"],
        at=key.get("actor_type") or "ai_agent",
        a=(acc or {}).get("id") or 0, ar=str(account_ref) if account_ref is not None else None,
        op=operation, cl=op_class,
        p=json.dumps(_redact(params or {}), ensure_ascii=False)[:60000],
        st=("blocked" if err else "queued"), ma=max_attempts, cf=bool(confirm), dr=bool(dry_run),
        pf=json.dumps(checks, ensure_ascii=False)[:20000], ik=idempotency_key)
    job_id = row["id"]

    if err:
        _set(job_id, error_code=err.code, error_text=err.message, retryable=err.retryable,
             finished_at=datetime.utcnow(), next_action=_next_action("blocked", err.code))
    return get_job(job_id), err


SECRET_HINTS = ("password", "secret", "token", "api_key", "apikey", "authorization",
                "client_secret", "proxy", "credential")


def _redact(obj):
    if isinstance(obj, dict):
        return {k: ("***" if any(h in str(k).lower() for h in SECRET_HINTS) else _redact(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


def _next_action(status, code=None):
    if status == "completed":
        return None
    if status == "blocked":
        return "user_decision" if code in ("CONFIRMATION_REQUIRED", "AVITO_CREDENTIALS_MISSING",
                                           "EXTERNAL_PUBLISH_DISABLED", "PAID_ACTION_DISABLED") \
            else "claude_review"
    if status == "failed":
        return "retry_ready"
    return None


def run_job(job_id):
    """Выполняет одну попытку. Возвращает свежий job."""
    job = get_job(job_id)
    if not job:
        raise ApiError("JOB_NOT_FOUND")
    if job["status"] not in ("queued",):
        return job
    if job.get("cancel_requested"):
        _set(job_id, status="cancelled", finished_at=datetime.utcnow())
        return get_job(job_id)

    costs.record_subscription(job_id, job.get("actor"),
                              note=f"попытка {int(job['attempts']) + 1}, операция {job['operation']}")
    attempt_n = int(job["attempts"]) + 1
    _set(job_id, status="running", attempts=attempt_n, started_at=datetime.utcnow())
    aid = _attempt_start(job_id, attempt_n)

    params = job.get("params_json")
    if isinstance(params, str):
        params = json.loads(params or "{}")
    acc = db.one("SELECT * FROM accounts WHERE id=:i", i=job["account_id"]) or {}
    handler = HANDLERS.get(job["operation"])

    try:
        if handler is None:
            raise ApiError("NOT_IMPLEMENTED",
                           f"для «{job['operation']}» нет подключённого штатного исполнителя")
        if job.get("dry_run"):
            result = {"dry_run": True, "note": "проверки пройдены, действие не выполнялось",
                      "campaign_id": None}
            qa = {"status": "PASS", "note": "dry-run: менять было нечего", "facts": {}}
        else:
            result = handler(job, params, acc) or {}
            qa = postflight.run(job["operation"], result)
    except ApiError as e:
        _attempt_end(aid, "blocked", e.code, e.message)
        status = "failed" if e.retryable and attempt_n < int(job["max_attempts"]) else \
                 ("blocked" if not e.retryable else "failed")
        if e.retryable and attempt_n < int(job["max_attempts"]):
            _set(job_id, status="queued", error_code=e.code, error_text=e.message, retryable=True,
                 next_action="auto_retry")
            return get_job(job_id)
        _set(job_id, status=status, error_code=e.code, error_text=e.message,
             retryable=e.retryable, finished_at=datetime.utcnow(),
             next_action=_next_action(status, e.code))
        return get_job(job_id)
    except Exception as e:
        log_internal(job.get("request_id"), f"handler {job['operation']}", e)
        code, retryable = classify(e)
        _attempt_end(aid, "failed", code, f"{type(e).__name__}: {e}")
        if retryable and attempt_n < int(job["max_attempts"]):
            _set(job_id, status="queued", error_code=code, error_text=str(e)[:500],
                 retryable=True, next_action="auto_retry")
            return get_job(job_id)
        final = "failed" if retryable else "blocked"
        _set(job_id, status=final, error_code=code,
             error_text="внутренняя ошибка, подробности во внутреннем логе",
             retryable=retryable, finished_at=datetime.utcnow(),
             next_action=_next_action(final, code))
        return get_job(job_id)

    _attempt_end(aid, "completed" if qa["status"] == "PASS" else "qa_failed",
                 None if qa["status"] == "PASS" else "POSTFLIGHT_MISMATCH",
                 None if qa["status"] == "PASS" else qa.get("note"), result)

    if qa["status"] == "PASS":
        _set(job_id, status="completed", qa_status="PASS",
             result_json=json.dumps(result, ensure_ascii=False, default=str)[:60000],
             postflight_json=json.dumps(qa, ensure_ascii=False, default=str)[:20000],
             finished_at=datetime.utcnow(), error_code=None, error_text=None, next_action=None)
    else:
        code = "POSTFLIGHT_MISMATCH" if qa["status"] == "FAIL" else "POSTFLIGHT_UNVERIFIED"
        _set(job_id, status="failed", qa_status=qa["status"],
             result_json=json.dumps(result, ensure_ascii=False, default=str)[:60000],
             postflight_json=json.dumps(qa, ensure_ascii=False, default=str)[:20000],
             finished_at=datetime.utcnow(), error_code=code, error_text=qa.get("note"),
             retryable=False, next_action="claude_review")
    return get_job(job_id)


def retry_job(key, job_id, request_id):
    job = get_job(job_id)
    if not job:
        raise ApiError("JOB_NOT_FOUND")
    auth.need_account(key, job["account_id"])
    if job["status"] not in ("failed", "blocked", "cancelled"):
        raise ApiError("JOB_NOT_RETRYABLE",
                       f"задача в статусе «{job['status']}», перезапуск разрешён из failed, blocked и cancelled")
    _set(job["id"], status="queued", cancel_requested=False, error_code=None, error_text=None,
         qa_status=None, finished_at=None, request_id=request_id,
         max_attempts=int(job["max_attempts"]) + 1,
         fixed_by=(key.get("actor_name") or key["name"]), next_action=None)
    return get_job(job["id"])


def cancel_job(key, job_id):
    job = get_job(job_id)
    if not job:
        raise ApiError("JOB_NOT_FOUND")
    auth.need_account(key, job["account_id"])
    if job["status"] in ("completed", "cancelled"):
        raise ApiError("JOB_NOT_CANCELLABLE", f"задача уже в статусе «{job['status']}»")
    if job["status"] == "running":
        _set(job["id"], cancel_requested=True, next_action="cancel_requested")
    else:
        _set(job["id"], status="cancelled", cancel_requested=True,
             finished_at=datetime.utcnow(), next_action=None)
    return get_job(job["id"])


# ================================================================== раннер

def claim_next():
    """Берёт следующую задачу безопасного класса. PAID/EXTERNAL сюда не попадают."""
    row = db.one("""UPDATE ext_jobs SET status='running', started_at=NOW(), updated_at=NOW()
                    WHERE id = (
                        SELECT id FROM ext_jobs
                        WHERE status='queued' AND cancel_requested=FALSE
                          AND op_class IN ('READ_ONLY','SAFE_MUTATION')
                        ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)
                    RETURNING id""")
    return row["id"] if row else None


def drain(limit=25):
    done = 0
    for _ in range(limit):
        jid = claim_next()
        if not jid:
            break
        _set(jid, status="queued")   # run_job сам переведёт в running и посчитает попытку
        run_job(jid)
        done += 1
    return done


_RUNNER = {"thread": None, "stop": False}


def start_inline_runner(interval=3.0):
    if os.environ.get("EXT_API_INLINE_RUNNER", "1") != "1":
        return None
    if _RUNNER["thread"] and _RUNNER["thread"].is_alive():
        return _RUNNER["thread"]

    def loop():
        while not _RUNNER["stop"]:
            try:
                drain()
            except Exception as e:
                log_internal("runner", "drain", e)
            time.sleep(interval)

    t = threading.Thread(target=loop, name="ext_api_runner", daemon=True)
    t.start()
    _RUNNER["thread"] = t
    return t


# ================================================================= handoff

RESOLUTIONS = ("resolved", "retry_ready", "blocked", "needs_user_decision")


def create_handoff(key, request_id, job_ref, to_actor, reason=None, context=None):
    job = get_job(job_ref) if job_ref else None
    if job_ref and not job:
        raise ApiError("JOB_NOT_FOUND")
    if job:
        auth.need_account(key, job["account_id"])
    frm = key.get("actor_name") or key["name"]
    row = db.one("""INSERT INTO ext_handoffs (request_id, job_id, from_actor, to_actor,
                        reason, context_json)
                    VALUES (:rq,:j,:f,:t,:r,:c) RETURNING id""",
                 rq=request_id, j=(job or {}).get("id"), f=frm, t=to_actor, r=reason,
                 c=json.dumps(_redact(context or {}), ensure_ascii=False)[:20000])
    if job:
        _set(job["id"], handoff_id=row["id"], next_action=f"{to_actor}_review")
    return get_handoff(row["id"])


def resolve_handoff(key, handoff_id, resolution, note=None):
    if resolution not in RESOLUTIONS:
        raise ApiError("VALIDATION_ERROR",
                       "resolution должен быть одним из: " + ", ".join(RESOLUTIONS))
    h = db.one("SELECT * FROM ext_handoffs WHERE id=:i", i=int(handoff_id))
    if not h:
        raise ApiError("JOB_NOT_FOUND", "передача не найдена")
    who = key.get("actor_name") or key["name"]
    db.q("""UPDATE ext_handoffs SET status='closed', resolution=:r, resolution_note=:n,
                resolved_by=:w, updated_at=NOW() WHERE id=:i""",
         i=h["id"], r=resolution, n=(note or "")[:4000], w=who)
    if h.get("job_id"):
        _set(h["job_id"], fixed_by=who,
             next_action={"resolved": None, "retry_ready": "retry_ready",
                          "blocked": "user_decision",
                          "needs_user_decision": "user_decision"}[resolution])
    return get_handoff(h["id"])


def get_handoff(hid):
    h = db.one("SELECT * FROM ext_handoffs WHERE id=:i", i=int(hid))
    if not h:
        raise ApiError("JOB_NOT_FOUND", "передача не найдена")
    if isinstance(h.get("context_json"), str):
        try:
            h["context_json"] = json.loads(h["context_json"])
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=575)
    return {"handoff_id": h["id"], "job_id": (f"job_{h['job_id']}" if h.get("job_id") else None),
            "from": h["from_actor"], "to": h["to_actor"], "reason": h.get("reason"),
            "context": h.get("context_json"), "status": h["status"],
            "resolution": h.get("resolution"), "resolution_note": h.get("resolution_note"),
            "resolved_by": h.get("resolved_by"), "created_at": h.get("created_at"),
            "updated_at": h.get("updated_at")}


# обратная совместимость имён
_job_public = job_public
