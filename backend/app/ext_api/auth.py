# -*- coding: utf-8 -*-
"""Личность агента, права, лимиты, идемпотентность, аудит.

Ключ выдаётся один раз и в базе не хранится: только sha256 и префикс для
поиска. Формат: brs_<prefix8>_<secret>. У ключа есть actor (chatgpt / claude /
скрипт) — под этим именем агент виден в Job registry и в аудите.
"""
import hashlib, secrets, json, time
from . import db
from .errors import ApiError
from app.services.exception_observability import observe_suppressed

SCOPES = ("read", "write", "tasks", "publish", "admin")
SECRET_KEYS = ("password", "secret", "token", "api_key", "apikey", "authorization",
               "client_secret", "proxy", "credential", "key_hash")


# ------------------------------------------------------------------ ключи

def _hash(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_key(name, scopes="read,write", account_ids=None, owner_user_id=None,
              actor_name=None, actor_type="ai_agent", rate_limit=60, max_items=200):
    prefix = secrets.token_hex(4)
    raw = f"brs_{prefix}_{secrets.token_urlsafe(24)}"
    db.q("""INSERT INTO ext_api_keys
              (name, key_prefix, key_hash, scopes, account_ids, owner_user_id,
               actor_type, actor_name, rate_limit_per_min, max_items_per_call)
            VALUES (:n,:p,:h,:s,:a,:o,:at,:an,:r,:m)""",
         n=name, p=prefix, h=_hash(raw), s=scopes,
         a=(",".join(str(x) for x in account_ids) if account_ids else None),
         o=owner_user_id, at=actor_type, an=(actor_name or name), r=rate_limit, m=max_items)
    return raw


def revoke_key(key_id):
    db.q("UPDATE ext_api_keys SET active=FALSE WHERE id=:i", i=key_id)


def list_keys():
    return db.rows("""SELECT id, name, actor_type, actor_name, key_prefix, scopes, account_ids,
                             active, rate_limit_per_min, max_items_per_call,
                             created_at, last_used_at, requests_count
                      FROM ext_api_keys ORDER BY id""")


# ------------------------------------------------------------------ доступ

def resolve(authorization):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError("UNAUTHORIZED", "нужен заголовок Authorization: Bearer <ключ>")
    raw = authorization.split(" ", 1)[1].strip()
    parts = raw.split("_")
    if len(parts) < 3 or parts[0] != "brs":
        raise ApiError("UNAUTHORIZED", "формат ключа не тот")
    row = db.one("""SELECT * FROM ext_api_keys
                    WHERE key_prefix=:p AND key_hash=:h AND active=TRUE""",
                 p=parts[1], h=_hash(raw))
    if not row:
        raise ApiError("UNAUTHORIZED", "ключ неизвестен или отозван")

    limit = int(row.get("rate_limit_per_min") or 60)
    used = db.one("""SELECT COUNT(*) AS n FROM ext_audit
                     WHERE key_id=:i AND created_at > NOW() - INTERVAL '1 minute'""",
                  i=row["id"])["n"]
    if used >= limit:
        raise ApiError("RATE_LIMITED", f"превышен лимит {limit} запросов в минуту для этого ключа")

    db.q("""UPDATE ext_api_keys SET last_used_at=NOW(), requests_count=requests_count+1
            WHERE id=:i""", i=row["id"])
    row.pop("key_hash", None)
    row["scope_set"] = {s.strip() for s in (row.get("scopes") or "").split(",") if s.strip()}
    raw_acc = row.get("account_ids")
    row["allowed_accounts"] = ({int(x) for x in str(raw_acc).split(",") if x.strip()}
                               if raw_acc not in (None, "") else None)
    return row


def need_scope(key, scope):
    if scope not in key["scope_set"] and "admin" not in key["scope_set"]:
        raise ApiError("FORBIDDEN_SCOPE", f"ключу не выдан scope «{scope}»")


def need_account(key, account_id):
    allowed = key.get("allowed_accounts")
    if allowed is not None and int(account_id) not in allowed:
        raise ApiError("FORBIDDEN_ACCOUNT", f"ключу не разрешён аккаунт {account_id}")


def max_items(key):
    return int(key.get("max_items_per_call") or 200)


AUTONOMOUS_DEV_ROLE = "autonomous-dev"


def actor_of(key):
    return {"type": key.get("actor_type") or "ai_agent",
            "name": key.get("actor_name") or key.get("name")}


def is_autonomous_dev(key):
    """Dedicated development capability. It is mutually exclusive with admin."""
    scopes = set(key.get("scope_set") or set())
    return ((key.get("actor_type") or "") == AUTONOMOUS_DEV_ROLE
            and "admin" not in scopes)


def need_autonomous_dev(key):
    if not is_autonomous_dev(key):
        raise ApiError(
            "FORBIDDEN_SCOPE",
            "операция доступна только отдельной роли autonomous-dev без admin",
        )


# ------------------------------------------------------------ идемпотентность

def request_hash(body):
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def idem_lookup(key, idem_key, body):
    """Тот же ключ и то же тело — возвращаем прошлый ответ, ничего не выполняя.
    Тот же ключ с другим телом — 409, а не молчаливая перезапись."""
    if not idem_key:
        return None
    row = db.one("""SELECT * FROM ext_idempotency
                    WHERE key_id=:k AND idempotency_key=:i""", k=key["id"], i=idem_key)
    if not row:
        return None
    if row["request_hash"] != request_hash(body):
        raise ApiError("IDEMPOTENCY_CONFLICT")
    try:
        saved = json.loads(row["response_json"] or "{}")
    except Exception:
        saved = {}
    saved["replayed"] = True
    return saved


def idem_store(key, idem_key, body, response, status_code=200, job_id=None):
    if not idem_key:
        return
    try:
        db.q("""INSERT INTO ext_idempotency
                  (key_id, idempotency_key, request_hash, status_code, response_json, job_id)
                VALUES (:k,:i,:h,:s,:r,:j)
                ON CONFLICT (key_id, idempotency_key) DO NOTHING""",
             k=key["id"], i=idem_key, h=request_hash(body), s=status_code,
             r=json.dumps(response, ensure_ascii=False, default=str)[:60000], j=job_id)
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=154)


# ------------------------------------------------------------------ аудит

def _redact(obj):
    if isinstance(obj, dict):
        return {k: ("***" if any(h in str(k).lower() for h in SECRET_KEYS) else _redact(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


def audit(key, method, path, status_code, request_id=None, command=None, account_id=None,
          job_id=None, status=None, affected=0, dry_run=False, idempotency_key=None,
          request=None, response=None, error=None, started=None):
    """Секреты в аудит не попадают: тела прогоняются через _redact."""
    try:
        db.q("""INSERT INTO ext_audit
                  (request_id, key_id, actor, actor_type, source, method, path, operation,
                   account_id, job_id, status, status_code, affected, dry_run,
                   idempotency_key, params_json, result_json, error_code, duration_ms)
                VALUES (:rq,:k,:ac,:at,'ext_api',:m,:p,:op,:a,:j,:st,:sc,:af,:dr,:ik,:pj,:rj,:ec,:d)""",
             rq=request_id, k=(key or {}).get("id"),
             ac=((key or {}).get("actor_name") or (key or {}).get("name")),
             at=(key or {}).get("actor_type"),
             m=method, p=str(path)[:200], op=command, a=account_id, j=job_id, st=status,
             sc=status_code, af=affected, dr=bool(dry_run), ik=idempotency_key,
             pj=(json.dumps(_redact(request), ensure_ascii=False, default=str)[:20000]
                 if request is not None else None),
             rj=(json.dumps(_redact(response), ensure_ascii=False, default=str)[:20000]
                 if response is not None else None),
             ec=(str(error)[:60] if error else None),
             d=(int((time.time() - started) * 1000) if started else None))
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=190)
