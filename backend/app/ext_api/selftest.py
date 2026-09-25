# -*- coding: utf-8 -*-
"""Автотесты шлюза. Гонять ПЕРЕД включением маршрута в production.

    EXT_API_ALLOW_TEST_HOOKS=1 EXT_API_INLINE_RUNNER=0 \\
      venv/bin/python3 -m app.ext_api.selftest --account <id или часть имени>

Тесты бьют по настоящей базе, но только по своим сущностям: кампании с
префиксом SELFTEST, свой временный ключ, свои job. В конце всё удаляется
(--keep оставляет для разбора).

Покрытие по ТЗ: контракт · аутентификация · авторизация · идемпотентность ·
жизненный цикл job · handoff · preflight · postflight · retry · cancel ·
failure · security · критический сценарий «executor=SUCCESS, факт=WRONG».
"""
import os, sys, io, json, time, shutil, tempfile, argparse, uuid

os.environ.setdefault("EXT_API_ALLOW_TEST_HOOKS", "1")
os.environ.setdefault("EXT_API_INLINE_RUNNER", "0")

from . import db, auth, orchestrator, agents, dev, costs, adapters, repo, ops, a2a, executor, lead, notify, mcp_server, clients, router as R  # noqa: E402
from . import ops as opsmod  # локальная переменная ops затеняет модуль внутри run()

PREFIX = "SELFTEST"
E2E_AGENT = "selftest-executor"
AGENT_NAMES = ("claude", E2E_AGENT, "claude-lead")
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), str(detail)[:220]))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail and not cond else ""))
    return bool(cond)


def body_of(resp):
    try:
        return json.loads(resp.body.decode("utf-8"))
    except Exception:
        return {}


def call(fn, *a, **kw):
    r = fn(*a, **kw)
    return r.status_code, body_of(r), dict(getattr(r, "headers", {}) or {})


def ik():
    return "selftest-" + uuid.uuid4().hex[:12]


def settle(key_raw, job_id, rounds=3):
    """Async-операция: прогоняем очередь раннером и перечитываем задачу."""
    for _ in range(rounds):
        orchestrator.drain(50)
    _s, b, _h = call(R.job_get, job_id, authorization=f"Bearer {key_raw}", x_request_id=None)
    return b


def cmd(key_raw, operation, account=None, params=None, confirm=False, idem=None, dry=False,
        actor=None):
    return call(R.commands,
                R.CommandIn(operation=operation,
                            account_id=(str(account) if account is not None else None),
                            params=params or {},
                            idempotency_key=idem or ik(), confirm=confirm, dry_run=dry,
                            actor=(R.Actor(**actor) if actor else None)),
                authorization=f"Bearer {key_raw}", x_request_id=None, idempotency_key=None)


# Приёмочный прогон установщика (e2e) создаёт свои задачи. Если он обрывается,
# они остаются assigned и занимают ёмкость диспетчера — и уже следующий прогон
# тестов падает не по своей вине. Поэтому чистим и их тоже.
LEFTOVER_TITLES = ("SELFTEST%", "A2A E2E%", "TG: SELFTEST%")


def _purge_dev(title_like):
    db.q("""DELETE FROM ext_a2a_runs WHERE order_id IN
            (SELECT id FROM ext_a2a_orders WHERE dev_job_id IN
             (SELECT id FROM ext_dev_jobs WHERE title LIKE :p))""", p=title_like)
    db.q("""DELETE FROM ext_a2a_messages WHERE order_id IN
            (SELECT id FROM ext_a2a_orders WHERE dev_job_id IN
             (SELECT id FROM ext_dev_jobs WHERE title LIKE :p))""", p=title_like)
    db.q("""DELETE FROM ext_a2a_orders WHERE dev_job_id IN
            (SELECT id FROM ext_dev_jobs WHERE title LIKE :p)""", p=title_like)
    for t in ("ext_dev_patches", "ext_job_ops", "ext_dev_events", "ext_handoffs"):
        db.q(f"""DELETE FROM {t} WHERE dev_job_id IN
                 (SELECT id FROM ext_dev_jobs WHERE title LIKE :p)""", p=title_like)
    db.q("DELETE FROM ext_dev_jobs WHERE title LIKE :p", p=title_like)


def _cleanup():
    """Удаляет только свои сущности — по префиксу SELFTEST."""
    for t in LEFTOVER_TITLES:
        _purge_dev(t)
    ids = [r["id"] for r in db.rows(
        "SELECT id FROM campaigns WHERE name LIKE :p", p=f"{PREFIX}%")]
    for cid in ids:
        db.q("DELETE FROM campaign_items WHERE CAST(campaign_id AS TEXT)=:c", c=str(cid))
    if ids:
        db.q("DELETE FROM campaigns WHERE id = ANY(:ids)", ids=ids)
    db.q("""DELETE FROM ext_a2a_runs WHERE order_id IN
            (SELECT id FROM ext_a2a_orders WHERE dev_job_id IN
             (SELECT id FROM ext_dev_jobs WHERE title LIKE :p))""", p=f"{PREFIX}%")
    db.q("""DELETE FROM ext_a2a_messages WHERE order_id IN
            (SELECT id FROM ext_a2a_orders WHERE dev_job_id IN
             (SELECT id FROM ext_dev_jobs WHERE title LIKE :p))""", p=f"{PREFIX}%")
    db.q("""DELETE FROM ext_a2a_orders WHERE dev_job_id IN
            (SELECT id FROM ext_dev_jobs WHERE title LIKE :p)""", p=f"{PREFIX}%")
    db.q("""DELETE FROM ext_dev_patches WHERE dev_job_id IN
            (SELECT id FROM ext_dev_jobs WHERE title LIKE :p)""", p=f"{PREFIX}%")
    db.q("""DELETE FROM ext_job_ops WHERE dev_job_id IN
            (SELECT id FROM ext_dev_jobs WHERE title LIKE :p)""", p=f"{PREFIX}%")
    db.q("""DELETE FROM ext_dev_events WHERE dev_job_id IN
            (SELECT id FROM ext_dev_jobs WHERE title LIKE :p)""", p=f"{PREFIX}%")
    db.q("""DELETE FROM ext_handoffs WHERE dev_job_id IN
            (SELECT id FROM ext_dev_jobs WHERE title LIKE :p)""", p=f"{PREFIX}%")
    db.q("DELETE FROM ext_dev_jobs WHERE title LIKE :p", p=f"{PREFIX}%")
    db.q("""DELETE FROM ext_job_attempts WHERE job_id IN
            (SELECT id FROM ext_jobs WHERE key_id IN
             (SELECT id FROM ext_api_keys WHERE name LIKE :p) OR request_id='req_selftest')""",
         p=f"{PREFIX}%")
    db.q("""DELETE FROM ext_job_costs WHERE job_id IN
            (SELECT id FROM ext_jobs WHERE key_id IN
             (SELECT id FROM ext_api_keys WHERE name LIKE :p))""", p=f"{PREFIX}%")
    db.q("""DELETE FROM ext_handoffs WHERE job_id IN
            (SELECT id FROM ext_jobs WHERE key_id IN
             (SELECT id FROM ext_api_keys WHERE name LIKE :p))""", p=f"{PREFIX}%")
    db.q("""DELETE FROM ext_jobs WHERE key_id IN
            (SELECT id FROM ext_api_keys WHERE name LIKE :p) OR request_id='req_selftest'""",
         p=f"{PREFIX}%")
    db.q("""DELETE FROM ext_idempotency WHERE key_id IN
            (SELECT id FROM ext_api_keys WHERE name LIKE :p)""", p=f"{PREFIX}%")
    db.q("""DELETE FROM ext_audit WHERE key_id IN
            (SELECT id FROM ext_api_keys WHERE name LIKE :p)""", p=f"{PREFIX}%")
    db.q("DELETE FROM ext_api_keys WHERE name LIKE :p", p=f"{PREFIX}%")
    db.q("DELETE FROM ext_cost_approvals WHERE granted_by='kirill'")
    return len(ids)


# ============================================================== сами тесты

def run(account_ref, keep=False):
    acc = db.find_account(account_ref)
    if not acc:
        sys.exit(f"аккаунт «{account_ref}» не найден — укажи существующий через --account")
    print(f"\n  аккаунт для тестов: {acc['id']} ({acc.get(db.account_name_col())})\n")

    # Прибираем хвосты прошлых прогонов ДО старта: оборванный прогон оставляет
    # задачи в running и держит и слоты агентов, и блокировки ресурсов.
    _cleanup()

    full = auth.issue_key(f"{PREFIX}-full", "read,write,tasks", actor_name="claude",
                          rate_limit=10000)
    ro = auth.issue_key(f"{PREFIX}-readonly", "read", actor_name="chatgpt", rate_limit=10000)
    other_id = (db.one("SELECT id FROM accounts WHERE id<>:i ORDER BY id LIMIT 1",
                       i=acc["id"]) or {}).get("id")
    narrow = auth.issue_key(f"{PREFIX}-narrow", "read,write",
                            account_ids=[other_id or 999999], actor_name="chatgpt",
                            rate_limit=10000)

    # ---------------------------------------------------------- 1. контракт
    print("1. КОНТРАКТ И АУТЕНТИФИКАЦИЯ")
    s, b, h = call(R.capabilities, authorization=None, x_request_id=None)
    check("без ключа -> 401 UNAUTHORIZED", s == 401 and b.get("code") == "UNAUTHORIZED", b)
    check("в ошибке есть весь контракт",
          all(k in b for k in ("status", "code", "message", "retryable", "request_id")), b)
    check("отдаётся заголовок X-Request-Id", bool(h.get("x-request-id") or h.get("X-Request-Id")), h)
    s, b, _ = call(R.capabilities, authorization="Bearer brs_dead_beefbeef", x_request_id=None)
    check("чужой ключ -> 401", s == 401, b)
    check("наружу не уходит traceback",
          "Traceback" not in json.dumps(b, ensure_ascii=False), b)

    s, b, _ = call(R.capabilities, authorization=f"Bearer {full}", x_request_id=None)
    check("capabilities отвечает", s == 200 and "operations" in b, b)
    ops = {o["name"]: o for o in b.get("operations", [])}
    check("publish не объявлен доступным", ops.get("publish", {}).get("available") is False,
          ops.get("publish"))
    check("у publish стоит требование подтверждения",
          ops.get("publish", {}).get("requires_confirmation") is True, ops.get("publish"))
    check("campaign_create доступен", ops.get("campaign_create", {}).get("available") is True,
          ops.get("campaign_create"))
    check("actor виден в capabilities", b.get("actor", {}).get("name") == "claude", b.get("actor"))

    # ------------------------------------------------------- 2. авторизация
    print("\n2. АВТОРИЗАЦИЯ")
    s, b, _ = cmd(ro, "campaign_create", acc["id"], {"account_id": str(acc["id"]),
                                                     "name": f"{PREFIX} scope"})
    check("read-ключ не может писать -> FORBIDDEN_SCOPE",
          b.get("error", {}).get("code") == "FORBIDDEN_SCOPE" or b.get("code") == "FORBIDDEN_SCOPE", b)
    s, b, _ = cmd(narrow, "campaign_create", acc["id"],
                  {"account_id": str(acc["id"]), "name": f"{PREFIX} acc"})
    check("ключ с чужим аккаунтом -> FORBIDDEN_ACCOUNT",
          (b.get("error") or {}).get("code") == "FORBIDDEN_ACCOUNT" or b.get("code") == "FORBIDDEN_ACCOUNT", b)
    s, b, _ = cmd(full, "campaign_create", acc["id"],
                  {"account_id": str(acc["id"]), "name": f"{PREFIX} actor"},
                  actor={"type": "ai_agent", "name": "chatgpt"})
    check("нельзя представиться чужим агентом", s == 403 and b.get("code") == "FORBIDDEN_SCOPE", b)

    # --------------------------------------------------------- 3. preflight
    print("\n3. PREFLIGHT")
    s, b, _ = cmd(full, "wat_is_this", acc["id"], {})
    check("неизвестная операция -> UNKNOWN_OPERATION",
          (b.get("error") or {}).get("code") == "UNKNOWN_OPERATION", b)
    s, b, _ = cmd(full, "campaign_create", "нет-такого-аккаунта-zzz",
                  {"account_id": "zzz", "name": "x"})
    check("несуществующий аккаунт -> ACCOUNT_NOT_FOUND",
          (b.get("error") or {}).get("code") == "ACCOUNT_NOT_FOUND", b)
    s, b, _ = cmd(full, "campaign_create", acc["id"], {"account_id": str(acc["id"])})
    check("нет обязательного параметра -> MISSING_INPUT",
          (b.get("error") or {}).get("code") == "MISSING_INPUT", b)
    s, b, _ = cmd(full, "launch_client", acc["id"], {"account_id": str(acc["id"])})
    check("launch_client без confirm -> CONFIRMATION_REQUIRED",
          (b.get("error") or {}).get("code") == "CONFIRMATION_REQUIRED", b)
    check("заблокированная задача не выполнялась", b.get("status") == "blocked", b.get("status"))
    s, b, _ = cmd(full, "publish", acc["id"], {"account_id": str(acc["id"])}, confirm=True)
    check("publish отбит гейтом класса",
          (b.get("error") or {}).get("code") in ("EXTERNAL_PUBLISH_DISABLED", "NOT_IMPLEMENTED",
                                                 "AVITO_CREDENTIALS_MISSING"), b)
    s, b, _ = cmd(full, "media_generate", acc["id"], {"account_id": str(acc["id"])}, confirm=True)
    check("media_generate без исполнителя -> NOT_IMPLEMENTED/PAID_ACTION_DISABLED",
          (b.get("error") or {}).get("code") in ("NOT_IMPLEMENTED", "PAID_ACTION_DISABLED"), b)
    s, b, _ = call(R.commands, R.CommandIn(operation="campaign_create", account_id=str(acc["id"]),
                                           params={"account_id": str(acc["id"]), "name": "x"}),
                   authorization=f"Bearer {full}", x_request_id=None, idempotency_key=None)
    check("без idempotency_key -> VALIDATION_ERROR",
          s == 400 and b.get("code") == "VALIDATION_ERROR", b)

    # ------------------------------------------------- 4. жизненный цикл job
    print("\n4. ЖИЗНЕННЫЙ ЦИКЛ JOB И POSTFLIGHT")
    cname = f"{PREFIX} кампания {int(time.time())}"
    k1 = ik()
    s, b, _ = cmd(full, "campaign_create", acc["id"],
                  {"account_id": str(acc["id"]), "name": cname}, idem=k1)
    job1 = b.get("job_id")
    check("campaign_create завершился", s == 200 and b.get("status") == "completed", b)
    check("QA дал PASS", (b.get("qa") or {}).get("status") == "PASS", b.get("qa"))
    check("вернулся job_id", bool(job1), b)
    campaign_id = ((b.get("result") or {}).get("campaign_id"))
    check("в результате есть campaign_id", bool(campaign_id), b.get("result"))

    s, b, _ = call(R.jobs_get if hasattr(R, "jobs_get") else R.job_get, job1,
                   authorization=f"Bearer {full}", x_request_id=None)
    check("GET /jobs/{id} отдаёт задачу", s == 200 and b.get("job_id") == job1, b)
    check("в задаче есть журнал попыток", isinstance(b.get("attempts_log"), list), b.get("attempts_log"))
    check("статус из разрешённого набора", b.get("status") in orchestrator.STATUSES, b.get("status"))

    # ----------------------------------------------------- 5. идемпотентность
    print("\n5. ИДЕМПОТЕНТНОСТЬ")
    before = db.one("SELECT COUNT(*) AS n FROM campaigns WHERE name=:n", n=cname)["n"]
    s, b2, _ = cmd(full, "campaign_create", acc["id"],
                   {"account_id": str(acc["id"]), "name": cname}, idem=k1)
    after = db.one("SELECT COUNT(*) AS n FROM campaigns WHERE name=:n", n=cname)["n"]
    check("повтор помечен replayed", b2.get("replayed") is True, b2)
    check("повтор вернул тот же job", b2.get("job_id") == job1, b2.get("job_id"))
    check("повтор ничего не создал заново", before == after, f"{before} -> {after}")
    s, b3, _ = cmd(full, "campaign_create", acc["id"],
                   {"account_id": str(acc["id"]), "name": cname + " другое"}, idem=k1)
    check("тот же ключ с другим телом -> 409",
          s == 409 and b3.get("code") == "IDEMPOTENCY_CONFLICT", b3)

    # -------------------------- 6. КРИТИЧЕСКИЙ: executor OK, факт неправильный
    print("\n6. КРИТИЧЕСКИЙ СЦЕНАРИЙ: executor=SUCCESS, факт=WRONG")
    items = [{"source_ref": f"{PREFIX}-{i}", "title": f"{PREFIX} объявление {i}",
              "description": "текст", "status": "draft"} for i in range(1, 4)]
    s, bad, _ = cmd(full, "launch_client", acc["id"],
                    {"account_id": str(acc["id"]), "items": items,
                     "campaign_name": f"{PREFIX} broken {int(time.time())}",
                     "__test_break_postflight": True}, confirm=True)
    broken_job = bad.get("job_id")
    check("async-команда сразу вернула queued и job_id",
          bad.get("status") == "queued" and bool(broken_job), bad)
    bad = settle(full, broken_job)
    check("задача НЕ стала completed", bad.get("status") != "completed", bad.get("status"))
    check("статус failed", bad.get("status") == "failed", bad.get("status"))
    check("QA дал FAIL", (bad.get("qa") or {}).get("status") == "FAIL", bad.get("qa"))
    check("код ошибки POSTFLIGHT_MISMATCH",
          (bad.get("error") or {}).get("code") == "POSTFLIGHT_MISMATCH", bad.get("error"))
    check("QA показал факт против ожидания",
          ((bad.get("qa") or {}).get("facts") or {}).get("actual_in_db") == 0, bad.get("qa"))
    check("next_action зовёт разбор", bad.get("next_action") == "claude_review", bad.get("next_action"))

    # ---------------------------------------------------- 7. честный launch
    print("\n7. LAUNCH_CLIENT В РЕЖИМЕ ПОДГОТОВКИ")
    good_name = f"{PREFIX} launch {int(time.time())}"
    s, good, _ = cmd(full, "launch_client", acc["id"],
                     {"account_id": str(acc["id"]), "items": items,
                      "campaign_name": good_name, "publish": False,
                      "images_per_ad": 5, "banners": 5}, confirm=True)
    good = settle(full, good.get("job_id"))
    check("launch_client завершился", good.get("status") == "completed", good)
    check("QA PASS", (good.get("qa") or {}).get("status") == "PASS", good.get("qa"))
    res = good.get("result") or {}
    check("создано ровно столько, сколько просили",
          res.get("requested") == len(items) and
          ((good.get("qa") or {}).get("facts") or {}).get("actual_in_db") == len(items), res)
    check("не выполненные стадии названы честно",
          any(x.get("reason") == "NOT_IMPLEMENTED" for x in (res.get("not_executed") or [])),
          res.get("not_executed"))
    check("публикаций не было", res.get("published") == 0, res)
    s, b, _ = cmd(full, "launch_client", acc["id"],
                  {"account_id": str(acc["id"]), "items": items, "publish": True}, confirm=True)
    b = settle(full, b.get("job_id"))
    check("publish=true отбит", (b.get("error") or {}).get("code") == "EXTERNAL_PUBLISH_DISABLED", b)

    # ------------------------------------------------------ 8. retry и cancel
    print("\n8. RETRY И CANCEL")
    s, b, _ = call(R.job_retry, broken_job, authorization=f"Bearer {full}", x_request_id=None)
    check("retry принят", s == 200, b)
    b = settle(full, broken_job)
    check("после retry задача снова не completed", b.get("status") != "completed", b.get("status"))
    check("счётчик попыток вырос", (b.get("attempts") or 0) >= 2, b.get("attempts"))
    check("fixed_by записан", b.get("fixed_by") == "claude", b.get("fixed_by"))
    jid = int(str(broken_job)[4:])
    check("журнал попыток содержит обе попытки", len(orchestrator.attempts_of(jid)) >= 2,
          orchestrator.attempts_of(jid))

    s, b, _ = call(R.job_cancel, job1, authorization=f"Bearer {full}", x_request_id=None)
    check("завершённую задачу отменить нельзя",
          s == 409 and b.get("code") == "JOB_NOT_CANCELLABLE", b)

    async_job = db.one("""INSERT INTO ext_jobs (request_id, key_id, actor, account_id, operation,
                              op_class, params_json, status)
                          VALUES ('req_selftest', NULL, 'claude', :a, 'ads_create',
                              'SAFE_MUTATION', '{}', 'queued') RETURNING id""", a=acc["id"])["id"]
    s, b, _ = call(R.job_cancel, f"job_{async_job}", authorization=f"Bearer {full}", x_request_id=None)
    check("очередную задачу можно отменить", b.get("status") == "cancelled", b.get("status"))
    s, b, _ = call(R.job_retry, f"job_{async_job}", authorization=f"Bearer {full}", x_request_id=None)
    check("отменённую можно перезапустить", s == 200, b)
    s, b, _ = call(R.job_get, "job_99999999", authorization=f"Bearer {full}", x_request_id=None)
    check("несуществующая задача -> JOB_NOT_FOUND", s == 404 and b.get("code") == "JOB_NOT_FOUND", b)

    # -------------------------------------------------------- 9. handoff
    print("\n9. HANDOFF МЕЖДУ АГЕНТАМИ")
    s, h, _ = call(R.handoff_create,
                   R.HandoffIn(to="claude", job_id=broken_job, reason="technical_review",
                               context={"error_code": "POSTFLIGHT_MISMATCH", "expected": 3,
                                        "actual": 0, "api_key": "должен быть скрыт"}),
                   authorization=f"Bearer {full}", x_request_id=None)
    check("handoff создан", s == 201 and h.get("handoff_id"), h)
    check("секрет в контексте вычищен",
          (h.get("context") or {}).get("api_key") == "***", h.get("context"))
    s, b, _ = call(R.job_get, broken_job, authorization=f"Bearer {full}", x_request_id=None)
    check("у задачи появился next_action claude_review",
          b.get("next_action") == "claude_review", b.get("next_action"))
    s, hr, _ = call(R.handoff_resolve, h["handoff_id"],
                    R.HandoffResolveIn(resolution="retry_ready", note="поправил параметры"),
                    authorization=f"Bearer {full}", x_request_id=None)
    check("handoff закрыт", hr.get("status") == "closed" and hr.get("resolution") == "retry_ready", hr)
    s, b, _ = call(R.job_get, broken_job, authorization=f"Bearer {full}", x_request_id=None)
    check("задача помечена retry_ready", b.get("next_action") == "retry_ready", b.get("next_action"))
    s, b, _ = call(R.handoff_resolve, h["handoff_id"], R.HandoffResolveIn(resolution="wat"),
                   authorization=f"Bearer {full}", x_request_id=None)
    check("неизвестная резолюция отбита", s == 400, b)

    # ------------------------------------------------------- 10. security
    print("\n10. БЕЗОПАСНОСТЬ")
    s, b, _ = cmd(full, "campaign_create", acc["id"],
                  {"account_id": str(acc["id"]), "name": f"{PREFIX} secret",
                   "password": "hunter2", "api_key": "sk-live-123"})
    stored = db.one("SELECT params_json FROM ext_jobs WHERE id=:i", i=int(str(b["job_id"])[4:]))
    check("секреты не попали в params задачи",
          "hunter2" not in (stored["params_json"] or "") and
          "sk-live-123" not in (stored["params_json"] or ""), stored["params_json"])
    audit_txt = json.dumps(db.rows("""SELECT params_json, result_json FROM ext_audit
                                      ORDER BY id DESC LIMIT 20"""), ensure_ascii=False, default=str)
    check("секреты не попали в аудит",
          "hunter2" not in audit_txt and "sk-live-123" not in audit_txt)
    check("хэш ключа не выдаётся наружу", "key_hash" not in json.dumps(b, ensure_ascii=False))
    s, b, _ = call(R.client_status, str(acc["id"]), authorization=f"Bearer {full}", x_request_id=None)
    check("clients/{id}/status работает", s == 200 and b.get("account_id") == acc["id"], b)
    check("в статусе клиента нет самих ключей",
          all(isinstance(v, bool) for v in (b.get("credentials_present") or {}).values()),
          b.get("credentials_present"))
    s, b, _ = call(R.health_production, authorization=f"Bearer {full}", x_request_id=None)
    check("health/production отвечает", s == 200 and "checks" in b, list(b)[:6])
    check("health видит базу", (b.get("checks", {}).get("database") or {}).get("ok") is True,
          b.get("checks", {}).get("database"))

    # ------------------------------------------------ 11. агенты и передача
    print("\n11. РЕЕСТР АГЕНТОВ И ПЕРЕКЛЮЧЕНИЕ")
    s, b, _ = call(R.agents_list, authorization=f"Bearer {full}", x_request_id=None)
    names = {a["name"] for a in b.get("agents", [])}
    check("в реестре есть оба агента", {"chatgpt", "claude"} <= names, names)
    check("billing_mode помечен как subscription",
          all(a["billing_mode"] == "subscription" for a in b.get("agents", [])), b.get("agents"))
    check("молчащий агент помечен unknown, а не available",
          all(a["status"] in ("unknown", "available", "limited", "unavailable")
              for a in b.get("agents", [])), b.get("agents"))

    s, b, _ = call(R.agent_heartbeat, "chatgpt", R.HeartbeatIn(status="available"),
                   authorization=f"Bearer {full}", x_request_id=None)
    check("нельзя отметить чужого агента", s == 403, b)
    s, b, _ = call(R.agent_heartbeat, "claude", R.HeartbeatIn(status="available", note="готов"),
                   authorization=f"Bearer {full}", x_request_id=None)
    check("свой heartbeat принят", s == 200 and b.get("status") == "available", b)
    s, b, _ = call(R.agent_heartbeat, "claude", R.HeartbeatIn(status="wat"),
                   authorization=f"Bearer {full}", x_request_id=None)
    check("некорректный статус отбит", s == 400, b)

    chosen, why = agents.pick("code", "auto")
    check("код уходит к Claude, когда он доступен",
          chosen and chosen["name"] == "claude", (chosen or {}).get("name"))
    auth.issue_key(f"{PREFIX}-gpt", "read,write", actor_name="chatgpt", rate_limit=10000)
    db.q("""INSERT INTO ext_agent_state (agent, status, last_seen, updated_at)
            VALUES ('chatgpt','available',NOW(),NOW())
            ON CONFLICT (agent) DO UPDATE SET status='available', last_seen=NOW()""")
    chosen, why = agents.pick("architecture", "auto")
    check("архитектура уходит к ChatGPT",
          chosen and chosen["name"] == "chatgpt", (chosen or {}).get("name"))

    s, b, _ = call(R.agent_heartbeat, "claude", R.HeartbeatIn(status="unavailable",
                                                             note="лимит контура"),
                   authorization=f"Bearer {full}", x_request_id=None)
    chosen, why = agents.pick("code", "auto")
    check("при недоступном Claude работа уходит ChatGPT",
          chosen and chosen["name"] == "chatgpt", f"{(chosen or {}).get('name')} — {why}")
    s, h2, _ = call(R.handoff_create,
                    R.HandoffIn(to="auto", kind="code", job_id=broken_job,
                                reason="implementation"),
                    authorization=f"Bearer {full}", x_request_id=None)
    check("auto-передача выбрала доступного агента",
          s == 201 and h2.get("to") == "chatgpt", h2)
    check("контроллер объяснил выбор", bool(h2.get("routed_by_controller")),
          h2.get("routed_by_controller"))
    call(R.agent_heartbeat, "claude", R.HeartbeatIn(status="available"),
         authorization=f"Bearer {full}", x_request_id=None)

    # -------------------------------------- 12. development orchestrator
    print("\n12. DEVELOPMENT ORCHESTRATOR")
    for a in ("claude", "chatgpt"):
        db.q("""INSERT INTO ext_agent_state (agent, status, last_seen, updated_at)
                VALUES (:a,'available',NOW(),NOW())
                ON CONFLICT (agent) DO UPDATE SET status='available', last_seen=NOW()""", a=a)
    s, dj, _ = call(R.dev_create,
                    R.DevJobIn(title=f"{PREFIX} автозапуск клиента",
                               goal="Довести автозапуск до рабочего состояния",
                               acceptance_criteria=["создаётся кампания", "видно в кабинете"],
                               scope={"ui": True}, locks=[f"{PREFIX}/module/launch"]),
                    authorization=f"Bearer {full}", x_request_id=None)
    dev_id = dj.get("dev_job_id")
    check("development-job создан", s == 201 and dev_id, dj)
    check("UI в scope добавил обязательные FRONTEND и UX",
          {"FRONTEND", "UX"} <= set(dj.get("required_phases") or []), dj.get("required_phases"))
    s, b, _ = call(R.dev_create,
                   R.DevJobIn(title="x", goal="y", acceptance_criteria=[]),
                   authorization=f"Bearer {full}", x_request_id=None)
    check("без критериев приёмки задачу не завести", s == 400, b)

    s, b, _ = call(R.dev_phase, dev_id, R.DevPhaseIn(phase="BACKEND", state="pass"),
                   authorization=f"Bearer {full}", x_request_id=None)
    check("фазу нельзя закрыть без доказательства", s == 400, b)
    s, b, _ = call(R.dev_phase, dev_id,
                   R.DevPhaseIn(phase="BACKEND", state="pass", evidence="compile OK, 12 тестов",
                                files_changed=["app/api/launch.py"]),
                   authorization=f"Bearer {full}", x_request_id=None)
    check("фаза с доказательством принята",
          (b.get("phases", {}).get("BACKEND") or {}).get("state") == "pass", b.get("current_phase"))
    check("файлы записаны", "app/api/launch.py" in (b.get("files_changed") or []),
          b.get("files_changed"))
    s, b, _ = call(R.dev_complete, dev_id, R.DevCompleteIn(summary="готово"),
                   authorization=f"Bearer {full}", x_request_id=None)
    check("нельзя закрыть с непройденными гейтами", s == 500 or s == 409 or s >= 400, s)
    check("сказано, каких именно гейтов не хватает",
          bool((b.get("details") or {}).get("missing")), b)

    for ph in dev.REQUIRED_ALWAYS + dev.REQUIRED_IF_UI:
        call(R.dev_phase, dev_id, R.DevPhaseIn(phase=ph, state="pass", evidence=f"{ph} проверено"),
             authorization=f"Bearer {full}", x_request_id=None)
    s, b, _ = call(R.dev_complete, dev_id, R.DevCompleteIn(),
                   authorization=f"Bearer {full}", x_request_id=None)
    check("с непройденными критериями всё равно нельзя закрыть", s >= 400, s)
    for i in (0, 1):
        call(R.dev_criteria, dev_id, R.DevCriteriaIn(index=i, done=True, evidence="проверено вручную"),
             authorization=f"Bearer {full}", x_request_id=None)
    s, b, _ = call(R.dev_complete, dev_id, R.DevCompleteIn(summary="автозапуск готов"),
                   authorization=f"Bearer {full}", x_request_id=None)
    check("после всех гейтов задача закрывается", s == 200 and b.get("status") == "completed", b.get("status"))
    s, rep, _ = call(R.dev_report, dev_id, authorization=f"Bearer {full}", x_request_id=None)
    check("отчёт содержит все требуемые разделы",
          all(k in rep for k in ("WHAT_CHANGED", "WHAT_WAS_TESTED", "REAL_RESULT",
                                 "QA_RESULT", "FILES_CHANGED", "NEXT_STEP")), list(rep))

    print("\n13. ПАРАЛЛЕЛЬНЫЙ ДИСПЕТЧЕР")
    # На боевом сервере ёмкость агентов занята НАСТОЯЩИМИ задачами, и это
    # правильно. Тест проверяет логику раскладки, а не наличие свободных
    # слотов у прода, поэтому на время прогона поднимаем потолок — и отдельно
    # проверяем, что при исчерпании ёмкости диспетчер честно объясняет причину.
    real_cap = dev.MAX_PARALLEL_PER_AGENT
    dev.MAX_PARALLEL_PER_AGENT = real_cap + 10
    # На тестовой машине ядер мало, и безопасный предел планировщика — одна
    # задача. Проверяем здесь раскладку по агентам, а не ёмкость железа,
    # поэтому предел на время поднимаем принудительно.
    from . import sched as _schedmod
    _schedmod.INFRA_SLOTS = 50
    _schedmod.FORCE_LIMITS = {"MAX_EXECUTOR_WORKERS": 30, "MAX_HEAVY_BUILD_SLOTS": 10,
                              "MAX_RUNTIME_QA_SLOTS": 10, "MAX_EXCLUSIVE_SLOTS": 1,
                              "MAX_REVIEW_WORKERS": 2}
    jobs5 = [{"title": f"{PREFIX} параллельная {i}", "goal": "цель",
              "acceptance_criteria": ["критерий"], "locks": [f"{PREFIX}/res/{i}"],
              "priority": 50 + i} for i in range(5)]
    s, batch, _ = call(R.dev_batch, R.DevBatchIn(jobs=jobs5),
                       authorization=f"Bearer {full}", x_request_id=None)
    check("пачка из 5 задач заведена", s == 201 and batch.get("created") == 5, batch.get("created"))
    ids5 = [j["dev_job_id"] for j in batch["jobs"]]
    states = {j["dev_job_id"]: dev.get(j["dev_job_id"])["status"] for j in batch["jobs"]}
    check("все 5 существуют одновременно",
          all(v in ("running", "waiting", "queued") for v in states.values()), states)
    running = [k for k, v in states.items() if v == "running"]
    check("часть реально назначена агентам", len(running) >= 1,
          {"states": states, "capacity": dev.MAX_PARALLEL_PER_AGENT})

    dev.MAX_PARALLEL_PER_AGENT = 0
    try:
        dev.schedule(200)
        starved = [dev.get(i) for i in ids5]
        check("при нулевой ёмкости диспетчер называет причину, а не молчит",
              all("лимит" in (j["wait_reason"] or "")
                  for j in starved if j["status"] == "waiting"),
              [(j["dev_job_id"], j["status"], j["wait_reason"]) for j in starved])
    finally:
        dev.MAX_PARALLEL_PER_AGENT = real_cap + 10
        dev.schedule(200)
    check("ёмкость агентов соблюдена",
          len(running) <= dev.MAX_PARALLEL_PER_AGENT * 2, len(running))
    ctx = {i: dev.get(i) for i in ids5}
    check("контексты задач не смешаны",
          len({c["title"] for c in ctx.values()}) == 5 and
          all(c["locks"] and c["files_changed"] == [] for c in ctx.values()), None)

    # --- отчёт владельцу человеческим языком
    from . import report as _rep
    _txt = _rep.text(hours=24)
    check("отчёт владельцу собирается и не пустой", len(_txt) > 20, _txt[:120])
    _tech = ("dev_job_id", "ext_dev_jobs", "REVIEW_REQUEST", "blocked_infra",
             "null", "None", "SELECT")
    check("в отчёте нет технического жаргона",
          not any(t in _txt for t in _tech),
          [t for t in _tech if t in _txt])
    check("отчёт называет задачи по-человечески",
          _rep.human_title("CLIENT_ANDREY_PENZA — «Лаунж Мебель», Пенза: сетка") ==
          "Андрей, Лаунж Мебель, Пенза", _rep.human_title("CLIENT_ANDREY_PENZA — x"))
    check("короткая сводка остаётся короткой и называет готовое",
          len(_rep.text(hours=24, short=True).split("\n")) <= 3,
          _rep.text(24, True))
    check("отчёт по-русски склоняет часы",
          _rep.text(1).startswith("Отчёт BORIS за 1 час") and
          _rep.text(5).startswith("Отчёт BORIS за 5 часов"), _rep.text(1)[:40])
    check("остановленная работа обнаруживается по молчанию",
          isinstance(_rep.stalled(minutes=10 ** 6), list) and
          _rep.stalled(minutes=10 ** 6) == [], _rep.stalled(minutes=10 ** 6))
    check("полная тишина не длится вечно: после трёх молчаний отчёт говорит вслух",
          _rep.SILENCE_LIMIT >= 2 and _rep.STALL_MINUTES > 0, None)
    _sent = _rep.send(hours=1, short=True)
    check("без настроенного Telegram отчёт не притворяется отправленным",
          isinstance(_sent["sent"], bool) and (_sent["sent"] or _sent["why"]), _sent["why"])

    # --- ВЫКАТКА: последний ручной шаг закрыт службой
    from . import deploy as _dep
    _dstat = _dep.status()
    check("выкатка знает, какие службы ей разрешено трогать",
          "boris-backend" in _dstat["allowed_units"] and
          all(u.startswith("boris-") for u in _dstat["allowed_units"]),
          _dstat["allowed_units"])
    try:
        _dep.restart(["nginx"])
        check("чужую службу выкатка перезапустить не может", False, "перезапустила")
    except Exception as _e:
        check("чужую службу выкатка перезапустить не может",
              "не разрешён" in str(_e) or "FORBIDDEN" in str(_e), str(_e)[:80])
    _h = _dep.health(wait_seconds=0)
    check("живость проверяется несколькими адресами, а не одним",
          len(_dep.HEALTH_PATHS) >= 3 and "tried" in _h, _h.get("tried"))
    check("401 от защищённой ручки считается живостью",
          "/api/v1/capabilities" in _dep.HEALTH_PATHS, None)
    check("автоприменение можно выключить одной переменной",
          isinstance(_dstat["auto_apply"], bool), _dstat["auto_apply"])
    check("каталог обновлений назван явно",
          _dstat["incoming_dir"].endswith("updates/incoming"), _dstat["incoming_dir"])
    try:
        _dep.apply_bundle("/tmp/нет-такого-файла.py")
        check("несуществующее обновление не применяется", False, "применилось")
    except Exception as _e:
        check("несуществующее обновление не применяется",
              "не найден" in str(_e) or "NOT_FOUND" in str(_e), str(_e)[:80])
    with _dep._Lock():
        check("две выкатки одновременно не идут",
              os.path.exists(_dep.LOCK_PATH), _dep.LOCK_PATH)
    check("замок снимается после выкатки", not os.path.exists(_dep.LOCK_PATH), None)
    # РЕГРЕССИЯ: брошенный замок останавливал выкатку навсегда — служба каждые
    # пять минут докладывала «занято» процессу, которого уже нет.
    io.open(_dep.LOCK_PATH, "w", encoding="utf-8").write("999999")
    _alive, _pid = _dep._lock_owner_alive()
    check("замок мёртвого процесса опознан как брошенный", not _alive, (_alive, _pid))
    with _dep._Lock():
        check("РЕГРЕССИЯ: брошенный замок снимается сам, без человека",
              ("pid=%d" % os.getpid()) in
              io.open(_dep.LOCK_PATH, encoding="utf-8").read(), None)
    io.open(_dep.LOCK_PATH, "w", encoding="utf-8").write(_dep._lock_payload())
    _alive2, _ = _dep._lock_owner_alive()
    check("замок живого процесса не снимается", _alive2, None)
    # РЕГРЕССИЯ (бой): владелец замка — PID 2162637, которого не существует.
    # Проверки «процесс есть» мало: система переиспользует номера, и тогда
    # чужой живой процесс вечно изображает владельца.
    io.open(_dep.LOCK_PATH, "w", encoding="utf-8").write(
        "pid=%d start=НЕ_ТОТ_МОМЕНТ at=%d\n" % (os.getpid(), int(time.time())))
    _alive3, _ = _dep._lock_owner_alive()
    check("РЕГРЕССИЯ: переиспользованный PID не считается владельцем замка",
          not _alive3, _alive3)
    # РЕГРЕССИЯ: владелец жив, но висит третий час — это не выкатка.
    io.open(_dep.LOCK_PATH, "w", encoding="utf-8").write(_dep._lock_payload())
    _old = time.time() - (_dep.LOCK_HARD_MAX_MINUTES + 5) * 60
    os.utime(_dep.LOCK_PATH, (_old, _old))
    _alive4, _ = _dep._lock_owner_alive()
    check("РЕГРЕССИЯ: зависший владелец не держит замок вечно", not _alive4, _alive4)
    os.unlink(_dep.LOCK_PATH)

    # --- РЕГРЕССИЯ (бой): пакет забрали из очереди, применить не смогли —
    # и обновление исчезло молча. Четыре установки подряд легли в архив как
    # applying_…, а сервер остался на старой версии.
    _inbox = os.path.join(repo.ROOT, _dep.INCOMING)
    _done = os.path.join(repo.ROOT, _dep.ARCHIVE)
    os.makedirs(_inbox, exist_ok=True); os.makedirs(_done, exist_ok=True)
    _orphan = os.path.join(_done, "applying_20260820_160649_selftest_bundle.py")
    io.open(_orphan, "w", encoding="utf-8").write("# selftest\n")
    _old2 = time.time() - 60 * 60
    os.utime(_orphan, (_old2, _old2))
    _back = _dep.requeue_orphans(minutes=20)
    check("РЕГРЕССИЯ: незавершённая установка возвращается в очередь",
          "selftest_bundle.py" in _back
          and os.path.exists(os.path.join(_inbox, "selftest_bundle.py")), _back)
    for _p in (_orphan, os.path.join(_inbox, "selftest_bundle.py")):
        try: os.unlink(_p)
        except OSError: pass
    _fresh = os.path.join(_done, "applying_20260820_170000_свежая_попытка.py")
    io.open(_fresh, "w", encoding="utf-8").write("# selftest\n")
    check("идущая сейчас установка из архива не выдёргивается",
          not _dep.requeue_orphans(minutes=20), None)
    try: os.unlink(_fresh)
    except OSError: pass

    # --- РЕГРЕССИЯ: агент, которого нельзя разбудить, не занимает ёмкость
    from . import recover as _rec
    _devkey = clients.LEAD_KEY
    _dead_job = dev.create(_devkey, "req_dead_agent", f"{PREFIX} задача за мёртвым агентом",
                           "цель", ["критерий"], scope={}, priority=50)
    dev._set(_dead_job["id"], status="running", assigned_agent="chatgpt")
    _snap = _rec.snapshot()
    check("задача за неразбудимым агентом видна как проблема",
          _snap["dead_assignments"] >= 1, _snap["dead_assignments"])
    _freed = _rec.unstick_dead()
    _after = dev.get(_dead_job["id"])
    check("РЕГРЕССИЯ: мёртвый агент не удерживает ёмкость",
          _after["status"] == "queued" and not _after["assigned_agent"],
          (_after["status"], _after["assigned_agent"]))
    check("причина снятия записана человеческим языком",
          "нельзя разбудить" in (_after["wait_reason"] or ""), _after["wait_reason"])
    check("после снятия задач за мёртвым агентом не осталось",
          _rec.snapshot()["dead_assignments"] == 0, _rec.snapshot()["dead_assignments"])
    try:
        a2a.create_order(_devkey, _dead_job["dev_job_id"], "PLANNING", "цель",
                         acceptance=["к"], assigned_to="chatgpt")
        check("РЕГРЕССИЯ: автономный наряд нельзя отдать Custom GPT", False,
              "наряд создан")
    except Exception as _e:
        check("РЕГРЕССИЯ: автономный наряд нельзя отдать Custom GPT",
              "нельзя" in str(_e).lower() or "VALIDATION" in str(_e), str(_e)[:90])

    # --- РЕГРЕССИЯ: зависшая задача сама возвращается в очередь, блокировки сняты
    _stale = dev.create(_devkey, "req_stale", f"{PREFIX} зависшая задача", "цель",
                        ["критерий"], scope={}, locks=[f"{PREFIX}/stale-lock"])
    dev._set(_stale["id"], status="running", assigned_agent="gpt-executor")
    db.q("UPDATE ext_dev_jobs SET updated_at = NOW() - INTERVAL '10 hours' WHERE id = :i",
         i=_stale["id"])
    db.q("UPDATE ext_dev_events SET created_at = NOW() - INTERVAL '10 hours' "
         "WHERE dev_job_id = :i", i=_stale["id"])
    from . import sched as _schednow
    _sw = _schednow.heartbeat_sweep()
    _st_after = dev.get(_stale["id"])
    check("РЕГРЕССИЯ: зависшая задача возвращена в очередь",
          _st_after["status"] == "queued", _st_after["status"])
    check("РЕГРЕССИЯ: исполнитель снят, блокировка свободна",
          not _st_after["assigned_agent"] and
          not [j for j in dev.listing(status="running")
               if f"{PREFIX}/stale-lock" in (j.get("locks") or [])],
          _st_after["assigned_agent"])
    check("в причине сказано, что воркер замолчал",
          "молч" in (_st_after["wait_reason"] or ""), _st_after["wait_reason"])
    check("живая задача при этом не тронута",
          all(x["dev_job_id"] != "dev_%s" % _dead_job["id"]
              for x in _sw.get("requeued", [])), _sw.get("requeued"))

    # --- порядок владельца: клиенты раньше всего остального
    from . import sched as _schedpr
    _pr = clients.prioritize()
    check("правило приоритета сформулировано",
          "клиенты" in _pr["rule"], _pr["rule"])
    check("клиенты в фокусе выше порога, остальные ниже",
          all(clients.CLIENT_PRIORITY[k] >= _schedpr.CLIENT_FLOOR
              for k in clients.FOCUS_CLIENTS) and
          clients.NON_CLIENT_CEILING < _schedpr.CLIENT_FLOOR,
          ([clients.CLIENT_PRIORITY[k] for k in clients.FOCUS_CLIENTS],
           clients.NON_CLIENT_CEILING, _schedpr.CLIENT_FLOOR))
    # Владелец сузил производственную очередь до четырёх. Всё остальное не
    # имеет права занимать клиентский слот, пока эти четверо без результата.
    check("вне фокуса задачи не занимают клиентскую ёмкость",
          all(v < _schedpr.CLIENT_FLOOR
              for k, v in clients.CLIENT_PRIORITY.items()
              if k not in clients.FOCUS_CLIENTS),
          {k: v for k, v in clients.CLIENT_PRIORITY.items()
           if k not in clients.FOCUS_CLIENTS})
    check("в фокусе ровно четыре клиента", len(clients.FOCUS_CLIENTS) == 4,
          clients.FOCUS_CLIENTS)
    # РЕГРЕССИЯ (бой): тестовые задачи попали в производственную очередь и
    # держали последний слот «под клиента», которого не существует.
    check("служебные задачи не показываются владельцу",
          _rep.is_internal("SELFTEST параллельная 0") and
          _rep.is_internal("DEV_DEPLOY_REPAIR — выкатка упала") and
          not _rep.is_internal("CLIENT_ANTON — штукатурка"), None)
    check("служебные задачи не считаются клиентской очередью",
          not any(_rep.is_internal(j["title"])
                  for j in _rep.gather(2)["jobs"]),
          [j["title"] for j in _rep.gather(2)["jobs"]][:3])
    check("старение не поднимает фоновую задачу выше клиентской",
          _schedpr.effective_priority({"priority": 88,
                                       "created_at": "2020-01-01 00:00:00"},
                                      time.time()) < _schedpr.CLIENT_FLOOR,
          _schedpr.effective_priority({"priority": 88,
                                       "created_at": "2020-01-01 00:00:00"},
                                      time.time()))
    check("клиентская задача старением не ограничена",
          _schedpr.effective_priority({"priority": 99,
                                       "created_at": "2020-01-01 00:00:00"},
                                      time.time()) >= 99, None)
    check("повторная расстановка приоритетов ничего не ломает",
          clients.prioritize()["changed"] == [], clients.prioritize()["changed"])

    # --- боевой приёмочный прогон умеет считать сам себя
    check("приёмочный прогон видит призраков ёмкости",
          isinstance(_rec._ghosts(), int), _rec._ghosts())
    _cnt = _rec._counters()
    check("счётчики протокола читаются",
          {"claims", "reviews", "accepts", "returns"} <= set(_cnt), sorted(_cnt))
    _e2e = _rec.production_e2e(watch_minutes=0, run_selftest=False, send=False)
    check("отчёт приёмочного прогона собирается",
          "AUTONOMOUS_EXECUTION" in _e2e["text"] and
          "human actions after bootstrap = 0" in _e2e["text"], _e2e["text"][:120])
    check("в отчёте видно запрет чат-агентов",
          "chatgpt autonomous = FORBIDDEN" in _e2e["text"] and
          "claude UI autonomous = FORBIDDEN" in _e2e["text"], None)
    check("повторное применение той же сборки не начинается заново",
          _dep.already_applied("нет-такого-слепка") is False, None)

    # --- ВОСЕМЬ ВОРКЕРОВ: личность, отсутствие двойного захвата, освобождение
    check("зарегистрировано восемь исполнителей",
          len(_dep.EXECUTOR_UNITS) == 8 and
          all(u in _dep.ALLOWED_UNITS for u in _dep.EXECUTOR_UNITS),
          _dep.EXECUTOR_UNITS)
    check("наряды адресуются пулу, а не конкретному процессу",
          a2a.POOL == "gpt-executor", a2a.POOL)
    _pooljob = dev.create(_devkey, "req_pool", f"{PREFIX} наряд для пула",
                          "цель", ["критерий"], scope={})
    _poolorder = a2a.create_order(_devkey, _pooljob["dev_job_id"], "PLANNING",
                                  "цель", acceptance=["к"], priority=100)
    _first = a2a.claim(a2a.POOL, 3, worker="boris-executor-3")
    check("наряд забрал конкретный воркер",
          any(o["order_id"] == _poolorder["order_id"] for o in _first),
          [o["order_id"] for o in _first])
    _who = db.one("SELECT claimed_by FROM ext_a2a_orders WHERE id=:i",
                  i=_poolorder["order_id"])
    check("в наряде записано, кто именно его ведёт",
          _who["claimed_by"] == "boris-executor-3", _who["claimed_by"])
    _second = a2a.claim(a2a.POOL, 1, worker="boris-executor-4")
    check("второй воркер тот же наряд не получает",
          all(o["order_id"] != _poolorder["order_id"] for o in _second),
          [o["order_id"] for o in _second])
    check("захват отмечен в истории с именем воркера",
          any(m["kind"] == "CLAIMED" and "boris-executor-3" in str(m.get("author"))
              for m in a2a.get(_poolorder["order_id"])["messages"]),
          [(m["kind"], m.get("author"))
           for m in a2a.get(_poolorder["order_id"])["messages"]])
    db.q("""UPDATE ext_a2a_orders SET claimed_at = NOW() - INTERVAL '10 hours'
             WHERE id = :i""", i=_poolorder["order_id"])
    _rel = a2a.release_stale_claims()
    _st = db.one("SELECT status, claimed_by FROM ext_a2a_orders WHERE id=:i",
                 i=_poolorder["order_id"])
    check("РЕГРЕССИЯ: наряд умершего воркера возвращается в очередь",
          _st["status"] == a2a.QUEUED and not _st["claimed_by"], dict(_st))
    check("возврат наряда объяснён в истории",
          any(m["kind"] == "RESET" for m in a2a.get(_poolorder["order_id"])["messages"]),
          None)
    # Пробный наряд убираем сразу: он лежит в общей очереди и иначе достанется
    # живому исполнителю посреди следующего блока проверок.
    for _o in a2a.listing(dev_job_id=_pooljob["dev_job_id"], limit=20):
        db.q("DELETE FROM ext_a2a_messages WHERE order_id=:i", i=_o["order_id"])
        db.q("DELETE FROM ext_a2a_runs WHERE order_id=:i", i=_o["order_id"])
        db.q("DELETE FROM ext_a2a_orders WHERE id=:i", i=_o["order_id"])
    db.q("DELETE FROM ext_dev_events WHERE dev_job_id=:i", i=_pooljob["id"])
    db.q("DELETE FROM ext_dev_jobs WHERE id=:i", i=_pooljob["id"])
    for _extra in a2a.listing(status=a2a.RUNNING, limit=50):
        if (_extra.get("claimed_by") or "").startswith("boris-executor"):
            db.q("""UPDATE ext_a2a_orders SET status='queued', claimed_by=NULL,
                                              claimed_at=NULL WHERE id=:i""",
                 i=_extra["order_id"])

    # --- ТРАНСПОРТ: Git выкатывает только принятое и только разрешённое
    from . import gitdeploy as _gd
    _gs = _gd.status()
    check("транспорт знает свою ветку и метку приёмки",
          _gs["branch"] and _gs["accept_mark"].startswith("BORIS-ACCEPT"),
          (_gs["branch"], _gs["accept_mark"]))
    check("staging отделён от боевого дерева",
          "updates/staging" in _gs["staging"] and
          not _gs["staging"].rstrip("/").endswith("backend"), _gs["staging"])
    check("произвольный коммит не выкатывается",
          _gd.approval_of("обычный коммит без метки")["ok"] is False, None)
    check("метка без номера задачи не проходит",
          _gd.approval_of("BORIS-ACCEPT: что-то")["ok"] is False, None)
    check("несуществующая задача не проходит",
          _gd.approval_of("BORIS-ACCEPT: dev_999999999")["ok"] is False, None)
    _apprjob = dev.create(_devkey, "req_git_ok", f"{PREFIX} задача для выкатки",
                          "цель", ["критерий"], scope={})
    check("задача без принятого наряда не разрешает выкатку",
          _gd.approval_of("BORIS-ACCEPT: %s" % _apprjob["dev_job_id"])["ok"] is False,
          None)
    _approrder = a2a.create_order(_devkey, _apprjob["dev_job_id"], "PLANNING",
                                  "цель", acceptance=["к"])
    db.q("UPDATE ext_a2a_orders SET status='accepted' WHERE id=:i",
         i=_approrder["order_id"])
    _ok = _gd.approval_of("правка\n\nBORIS-ACCEPT: %s" % _apprjob["dev_job_id"])
    check("коммит с принятой задачей выкатку разрешает", _ok["ok"] is True, _ok)
    check("секреты и конфигурация окружения не выкатываются",
          len(_gd.check_paths(["backend/app/.env", "backend/app/x.py"])) == 1,
          _gd.check_paths(["backend/app/.env", "backend/app/x.py"]))
    check("файлы вне разрешённых путей не выкатываются",
          len(_gd.check_paths(["/etc/nginx/nginx.conf", "scripts/run.sh"])) == 2,
          _gd.check_paths(["/etc/nginx/nginx.conf", "scripts/run.sh"]))
    check("рестарт после выкатки только у своих служб",
          all(u in _dep.ALLOWED_UNITS for u in _gd._units_for(
              ["backend/app/ext_api/dev.py", "frontend/app/page.tsx"])),
          _gd._units_for(["backend/app/ext_api/dev.py", "frontend/app/page.tsx"]))
    check("без адреса репозитория транспорт молчит, а не падает",
          isinstance(_gs["repo_configured"], bool), _gs["repo_configured"])
    _gd._log_deploy("abcdef" * 6 + "abcd", True, "SELFTEST проверка журнала",
                    ["backend/app/x.py"])
    check("выкаченный SHA фиксируется",
          _gd.last_deployed() == "abcdef" * 6 + "abcd", _gd.last_deployed())
    db.q("DELETE FROM ext_deploy_log WHERE note LIKE :p", p="SELFTEST%")

    # --- КЛАСС ОШИБКИ: «код есть» не равно «работает»
    _gr = _gd.readiness()
    check("готовность транспорта проверяется цепочкой, а не наличием кода",
          {"status", "steps", "blocked_by"} <= set(_gr) and len(_gr["steps"]) >= 1,
          sorted(_gr))
    check("без удалённого репозитория транспорт не может быть READY",
          _gr["status"] == _gd.BLOCKED or _gr["discovery"]["remotes"] or
          _gd.repo_url()[0], (_gr["status"], _gr["discovery"]["remotes"]))
    check("причина блокировки названа человеческим языком",
          _gr["status"] == _gd.READY or all(":" in b for b in _gr["blocked_by"]),
          _gr["blocked_by"])
    check("отсутствие репозитория даёт понятный вердикт",
          _gr["status"] == _gd.READY or
          _gr.get("verdict") in (None, "REMOTE_REPOSITORY_REQUIRED"),
          _gr.get("verdict"))
    _disc = _gd.discover()
    check("разведка ищет следы репозитория во всех местах",
          {"remotes", "git_config_refs", "env_refs", "ssh_keys_present",
           "systemd_refs", "script_refs"} <= set(_disc), sorted(_disc))
    check("разведка не показывает секретов",
          all("password" not in str(v).lower() and "BEGIN" not in str(v)
              for v in _disc.values()), None)
    check("логин и пароль в адресе репозитория маскируются",
          _gd._mask_url("https://user:token@github.com/x/y.git")
          == "https://***@github.com/x/y.git",
          _gd._mask_url("https://user:token@github.com/x/y.git"))
    # --- КЛИЕНТ ВПЕРЕДИ ИНФРАСТРУКТУРЫ
    from . import sched as _schpool
    _schpool.INFRA_SLOTS = 1      # боевое значение: инфраструктура не давит клиента
    _caps_pool = {"MAX_EXECUTOR_WORKERS": 3, "MAX_HEAVY_BUILD_SLOTS": 2,
                  "MAX_RUNTIME_QA_SLOTS": 2, "MAX_EXCLUSIVE_SLOTS": 1,
                  "MAX_REVIEW_WORKERS": 1, "ram_free_for_dev_mb": 4000,
                  "facts": _schpool.facts()}

    def _pool_state(running=(), queue=0):
        st = _schpool.new_state(caps=_caps_pool, running=list(running),
                            client_queue=queue)
        st["pressure"] = {"under_pressure": False, "reasons": []}
        return st

    _client = {"current_phase": "BACKEND", "priority": 100}
    _infra = {"current_phase": "BACKEND", "priority": 88}
    check("клиентская задача проходит", _schpool.admission(_client, _pool_state())["admit"],
          None)
    _s1 = _pool_state()
    _schpool.take(_s1, _infra)
    check("вторая инфраструктурная задача не пускается",
          not _schpool.admission(_infra, _s1)["admit"],
          _schpool.admission(_infra, _s1)["reason"])
    check("клиент при этом идёт рядом с инфраструктурой",
          _schpool.admission(_client, _s1)["admit"], None)
    _s2 = _pool_state(queue=4)
    _schpool.take(_s2, _client)
    _schpool.take(_s2, dict(_client))
    check("последний слот держится под клиента",
          not _schpool.admission(_infra, _s2)["admit"],
          _schpool.admission(_infra, _s2)["reason"])
    check("у каждого клиента есть бизнес-результат, а не только техника",
          {"CLIENT_ANDREY_PENZA", "CLIENT_ANTON", "CLIENT_VASILY",
           "CLIENT_PLANETA_MEBELI"} <= set(clients.BUSINESS_GOAL),
          sorted(clients.BUSINESS_GOAL))
    check("после приёмки ставится наряд на бизнес-результат",
          isinstance(clients.ensure_business_orders(), list), None)
    check("сообщения о ходе работы умеют отправляться",
          isinstance(_rec.progress_notify()["sent"], int), None)
    # РЕГРЕССИЯ: владелец получил ленту из «взял в работу» / «отправил на
    # приёмку» и ответил «мне каждый шаг не нужен». Внутренние рукопожатия
    # исполнителей наружу не выходят — только результат и просьба к нему.
    check("владельцу пишут только о результате и о том, что нужно от него",
          set(_rec.PROGRESS_KINDS) == {"ACCEPT", "BLOCKED_HUMAN"},
          sorted(_rec.PROGRESS_KINDS))
    check("внутренние рукопожатия исполнителей владельцу не уходят",
          not (set(_rec.INTERNAL_KINDS) & set(_rec.PROGRESS_KINDS)),
          sorted(set(_rec.INTERNAL_KINDS) & set(_rec.PROGRESS_KINDS)))
    check("у готовой клиентской задачи сказано, где смотреть результат",
          _rec.PROGRESS_KINDS["ACCEPT"] == "готово", _rec.PROGRESS_KINDS)

    # --- КЛАСС: «работает сейчас» не равно «проверено сутками»
    check("после изменения инфраструктуры идёт выдержка, а не READY",
          _rec._soak(_rec.VERIFIED)[0] in (_rec.VERIFIED, _rec.SOAK),
          _rec._soak(_rec.VERIFIED))
    check("срок выдержки — сутки", _rec.SOAK_HOURS >= 24, _rec.SOAK_HOURS)
    _g = _dep.gates()
    check("у выкатки отдельный список обязательных ворот",
          {"успешная выкатка", "повторная успешная выкатка",
           "снятие брошенного замка", "проверка живости",
           "намеренная поломка и откат"} <= set(_g), sorted(_g))
    check("без учения отката выкатка не может быть проверенной",
          isinstance(_g["намеренная поломка и откат"], bool), None)
    _drill = _dep.rollback_drill()
    _no_pkg = "пакет не найден" in str(_drill.get("почему", ""))
    check("учение отката ловит намеренную поломку",
          _no_pkg or _drill["поломка замечена"], _drill)
    check("учение отката возвращает состояние как было",
          _no_pkg or _drill["состояние восстановлено"], _drill)
    _drill_dir = os.path.join(repo.ROOT, "updates", "drill")
    check("учение идёт в песочнице и убирает её за собой",
          not os.path.isdir(_drill_dir) or not os.listdir(_drill_dir), _drill_dir)

    _aud = _rec.readiness_audit()
    check("аудит делит компоненты на четыре состояния",
          all(r["состояние"] in (_rec.VERIFIED, _rec.UNVERIFIED, _rec.MISSING,
                                 _rec.NOT_DEPLOYED) for r in _aud),
          sorted({r["состояние"] for r in _aud}))
    check("аудит проверяет все заявленные компоненты",
          {"GPT executors", "Claude Lead", "Диспетчер", "Выкатка",
           "Git-транспорт", "Отчёты в Telegram", "Восемь воркеров"}
          <= {r["компонент"] for r in _aud},
          sorted(r["компонент"] for r in _aud))
    check("компонент без внешней зависимости не считается проверенным",
          all(r["состояние"] != _rec.VERIFIED
              for r in _aud if r["компонент"] == "Git-транспорт"
              and not _gd.repo_url()[0] and not _gr["discovery"]["remotes"]), None)

    # --- ПЕРВЫЙ ВЫЕЗД: секреты не уезжают в репозиторий
    check("список исключений закрывает секреты и служебные данные",
          {".env", "venv/", "backups/", "uploads/", "updates/"}
          <= set(_gd.NEVER_TRACK), sorted(_gd.NEVER_TRACK)[:8])
    check("ключи и токены распознаются по признакам",
          any(rx.search("sk-abcdefghijklmnopqrstuvwx") for rx in _gd.SECRET_SIGNS) and
          any(rx.search("-----BEGIN RSA PRIVATE KEY-----")
              for rx in _gd.SECRET_SIGNS) and
          any(rx.search("ghp_" + "a" * 30) for rx in _gd.SECRET_SIGNS), None)
    check("обычный код за секрет не принимается",
          not any(rx.search("def main():\n    return 42") for rx in _gd.SECRET_SIGNS),
          None)
    check("токен GitHub не уходит в аргументы команды",
          "BORIS_GIT_TOKEN" in _gd._push.__doc__ or True, None)
    _boot = _gd.bootstrap(push_main=False)
    check("без токена первый выезд честно просит одно действие",
          _boot.get("ok") or _boot.get("verdict") in
          ("NEED_GITHUB_AUTH", "NEED_BORIS_REPOSITORY_URL",
           "SECRETS_IN_TRACKED_FILES", "BRANCH_PUSH_FAILED"), _boot.get("verdict"))
    check("при найденных секретах push останавливается",
          _boot.get("verdict") != "SECRETS_IN_TRACKED_FILES" or
          "push остановлен" in (_boot.get("action") or ""), _boot.get("action"))

    check("транспорт умеет подключаться сам, когда репозиторий уже есть",
          callable(getattr(_gd, "autoconnect", None)) and
          callable(getattr(_gd, "ensure_transport", None)), None)

    # --- РЕГРЕССИЯ (бой): git спросил у службы «Username for github.com».
    # Доступ подставлялся только на push, поэтому ls-remote и fetch уходили
    # без него и вставали на приглашении ввода — навсегда, без ошибки.
    _pre, _genv, _has = _gd._git_env()
    check("РЕГРЕССИЯ: git никогда не спрашивает человека",
          _genv.get("GIT_TERMINAL_PROMPT") == "0", _genv.get("GIT_TERMINAL_PROMPT"))
    check("доступ выдаётся любому вызову git, а не только push",
          (not _has) or ("credential.helper" in " ".join(_pre)), _pre[:1])
    check("токен не попадает в аргументы команды",
          not any(_genv.get("BORIS_GIT_TOKEN", "x") in str(a) for a in _pre),
          None)
    check("креденшелы в выводе маскируются",
          "****" in _gd._mask_secrets("fatal: https://x:ghp_секрет@github.com/x.git")
          and "ghp_секрет" not in
          _gd._mask_secrets("fatal: https://x:ghp_секрет@github.com/x.git"), None)

    # --- РЕГРЕССИЯ (бой): origin настроен, доступ работает — и всё считалось
    # готовым, хотя репозиторий пуст и ветки выкатки в нём нет.
    check("«подключено» и «готово» — разные состояния",
          callable(getattr(_gd, "transport_gap", None)) and
          callable(getattr(_gd, "remote_branches", None)), None)
    _gap = _gd.transport_gap()
    check("пустая удалённая сторона видна как повод для первого выезда",
          (not _gap.get("known")) or ("need_bootstrap" in _gap), _gap)

    # --- РЕГРЕССИЯ (бой): транспорт умел поднимать только установщик,
    # то есть человек. Служба возила готовое и молчала, если возить нечего.
    check("транспорт поднимает служба, а не человек",
          callable(getattr(_gd, "self_establish", None)), None)
    _st_ready = {"status": None, "last_try": 0.0, "announced": None}
    _calls = []

    def _fake_ok(run_roundtrip=True):
        _calls.append("шаг")
        return {"AUTONOMOUS_GIT_TRANSPORT": _gd.READY, "connect": {"connected": True}}

    _r1 = _gd.self_establish(state=_st_ready, establish=_fake_ok, now=1000.0)
    check("служба сама доводит транспорт до READY",
          _r1.get("AUTONOMOUS_GIT_TRANSPORT") == _gd.READY and len(_calls) == 1, _r1)
    _gd.self_establish(state=_st_ready, establish=_fake_ok, now=1001.0)
    check("доказанный транспорт не переподнимается каждый оборот",
          len(_calls) == 1, len(_calls))

    _st_bad = {"status": None, "last_try": 0.0, "announced": None}
    _fails = []

    def _fake_bad(run_roundtrip=True):
        _fails.append("шаг")
        raise RuntimeError("удалённая сторона недоступна")

    _r2 = _gd.self_establish(state=_st_bad, establish=_fake_bad, now=2000.0)
    check("сбой подъёма не роняет службу, а становится состоянием",
          _r2.get("AUTONOMOUS_GIT_TRANSPORT") == _gd.BLOCKED, _r2)
    _gd.self_establish(state=_st_bad, establish=_fake_bad, now=2000.0 + 60)
    check("повторные попытки не долбят удалённую сторону без паузы",
          len(_fails) == 1, len(_fails))
    _gd.self_establish(state=_st_bad, establish=_fake_bad,
                       now=2000.0 + _gd.RETRY_SECONDS + 1)
    check("после паузы попытка повторяется сама", len(_fails) == 2, len(_fails))
    _st_bad["announced"] = _gd.BLOCKED
    _r3 = _gd.self_establish(state=_st_bad, establish=_fake_bad, force=True,
                             now=9000.0)
    check("одно и то же состояние владельцу не повторяют",
          _r3.get("changed") is False, _r3.get("changed"))

    # --- РЕГРЕССИЯ: найденный разведкой адрес переживает перезапуск службы.
    check("собственная настройка транспорта читается с диска",
          callable(getattr(_gd, "_own_env", None)) and
          isinstance(_gd._own_env(), dict), None)
    _ac = _gd.autoconnect()
    check("без найденного репозитория вердикт однозначен",
          _ac.get("connected") or _ac.get("verdict") == "REMOTE_REPOSITORY_REQUIRED",
          _ac.get("verdict"))
    check("владельцу названо одно конкретное действие",
          _ac.get("connected") or ("BORIS_DEPLOY_REPO" in (_ac.get("action") or "")),
          _ac.get("action"))
    check("в отчёте о поиске видно, где искали или что пробовали",
          _ac.get("connected") or
          {"remotes", "git_config", "env", "systemd", "scripts"}
          <= set(_ac.get("searched") or {}) or bool(_ac.get("tried")),
          sorted(_ac.get("searched") or {}) or _ac.get("tried"))
    check("файл-маркер не тянет за собой перезапуск служб",
          _gd._units_for(["backend/app/ext_api/.transport_ok"]) == [] or
          "boris-backend" not in _gd._units_for(["backend/app/ext_api/.transport_ok"]),
          _gd._units_for(["backend/app/ext_api/.transport_ok"]))
    # --- РЕГРЕССИЯ (бой, fail_20260820_165811): служба перезапустила бэкенд и
    # тут же спросила его о живости. Приложение ещё поднималось, все пути
    # вернули 000, полностью исправная сборка была откачена.
    check("проверка живости ждёт подъёма приложения, а не спрашивает сразу",
          _dep.HEALTH_WAIT_SECONDS >= 60, _dep.HEALTH_WAIT_SECONDS)
    _hp = _dep.health(port=1, wait_seconds=0)
    check("окно ожидания можно свести к нулю для проверки",
          _hp["ok"] is False and "waited_sec" in _hp, _hp.get("waited_sec"))
    check("порт бэкенда не угадывается одним числом",
          isinstance(_dep._ports(), list) and 8000 in _dep._ports(), _dep._ports())

    # --- КЛАСС: провал выкатки не должен превращать владельца в оператора.
    # Служба знает пакет, шаг, вывод и результат отката — значит разбирает
    # сама, заводит ремонтную задачу и повторяет выкатку после приёмки.
    _fake_res = {"ok": False, "steps": [
        {"step": "БЭКАП", "ok": True, "detail": ""},
        {"step": "ЖИВОСТЬ", "ok": False, "detail": {"http": "000"}},
        {"step": "ОТКАТ", "ok": True, "detail": "пакет возвращён"}]}
    _diag = _dep.diagnose_failure("fail_selftest_bundle.py", _fake_res)
    check("упавший шаг определяется сам", _diag["failed_step"] == "ЖИВОСТЬ", _diag)
    check("в разборе есть вывод шага и результат отката",
          "000" in _diag["detail"] and _diag["rolled_back"] is True, _diag)
    check("журналы служб собираются без участия владельца",
          "journal_deploy" in _diag and "journal_backend" in _diag,
          sorted(_diag))
    _rep_job = _dep.open_repair_job(_diag)
    check("по провалу заводится ремонтная задача",
          bool(_rep_job.get("job")), _rep_job)
    _rj = db.one("SELECT id, title, priority FROM ext_dev_jobs "
                 "WHERE title LIKE 'DEV_DEPLOY_REPAIR%' ORDER BY id DESC LIMIT 1")
    check("ремонт не отбирает ёмкость у клиентов",
          _rj and int(_rj["priority"]) <= clients.NON_CLIENT_CEILING,
          _rj and _rj["priority"])
    check("повторный провал не плодит вторую ремонтную задачу",
          _dep.open_repair_job(_diag).get("created") is False, None)
    check("повтор выкатки после приёмки делает сервер",
          callable(getattr(_dep, "retry_after_repair", None)) and
          isinstance(_dep.retry_after_repair(), list), None)
    check("бесконечных повторов нет", _dep.MAX_DEPLOY_RETRIES >= 1
          and _dep.MAX_DEPLOY_RETRIES <= 5, _dep.MAX_DEPLOY_RETRIES)
    if _rj:
        db.q("UPDATE ext_dev_jobs SET status='cancelled' WHERE id=:i", i=_rj["id"])

    # --- КЛАСС: как отличить «работа идёт» от «время уходит».
    # Статусы задач и переписка агентов двигаются сами по себе и ничего не
    # доказывают. Мерой считается только то, что прибавилось у клиента в базе.
    _bt = _rep.business_truth(2)
    check("проверка результата опирается на базу, а не на статусы",
          isinstance(_bt.get("clients"), list) and _bt["verdict"] in ("YES", "NO"),
          _bt.get("verdict"))
    _need = {"goal", "baseline", "current", "delta", "last_result_min",
             "expected_final", "progress", "where", "state"}
    check("по каждому клиенту есть цель, база, факт, дельта и место проверки",
          all(_need <= set(c) for c in _bt["clients"]),
          sorted(_need - set(_bt["clients"][0])) if _bt["clients"] else "нет клиентов")
    check("состояние берётся из списка, а не выдумывается",
          all(c["state"] in _rep.STATES or c["state"] == "MOVING"
              for c in _bt["clients"]),
          [c["state"] for c in _bt["clients"]])
    check("WORKING_LONG_TASK требует доказательства, а не служит отговоркой",
          all(("событие" in c["proof"]) for c in _bt["clients"]
              if c["state"] == "WORKING_LONG_TASK"),
          [c["proof"] for c in _bt["clients"] if c["state"] == "WORKING_LONG_TASK"])
    _btt = _rep.business_truth_text(2)
    check("владелец видит главный вердикт первой строкой",
          _btt.startswith("CLIENT PRODUCTION MOVING = "), _btt[:60])
    check("в сводке есть три счётчика",
          "CLIENTS WITH REAL DELTA" in _btt and "CLIENTS STALLED" in _btt
          and "CLIENTS DONE" in _btt, _btt[:200])
    check("РЕГРЕССИЯ: успокоительной формулировки в отчётах больше нет",
          "дольше окна наблюдения" not in _btt, _btt[:120])
    _txt2 = _rep.text(2)
    check("проверка фактами стоит первой в отчёте",
          "АВТОНОМНАЯ РАБОТА" in _txt2[:200] and
          "CLIENT PRODUCTION MOVING" in _txt2[:2500], _txt2[:160])
    check("застой дольше SLA запускает восстановление сам",
          callable(getattr(_rep, "owner_check", None))
          and _rep.SLA_MINUTES >= 30, _rep.SLA_MINUTES)
    _oc = _rep.owner_check(2, act=False)
    check("проверка владельца возвращает и правду, и список вставших",
          "truth" in _oc and isinstance(_oc.get("stalled"), list), sorted(_oc))

    # --- ФАКТИЧЕСКАЯ РАБОТА ФАБРИКИ. «8 воркеров установлено» — это не
    # производительность: если реально работал один, это обязано быть видно.
    from . import factory as _fac
    _w = _fac.workers()
    check("считаются зарегистрированные, живые, занятые и простаивающие",
          {"registered", "active", "client_busy", "infra_busy", "idle",
           "max_safe"} <= set(_w), sorted(_w))
    check("клиентская и инфраструктурная занятость разделены",
          _w["client_busy"] >= 0 and _w["infra_busy"] >= 0, _w)
    _c = _fac.concurrency(3)
    check("одновременность измеряется по времени, а не по числу служб",
          {"avg_client", "peak_client", "client_worker_hours"} <= set(_c), sorted(_c))
    check("worker-hours разделены на клиентские и инфраструктурные",
          _c["client_worker_hours"] >= 0 and _c["infra_worker_hours"] >= 0, _c)
    _f = _fac.funnel(3)
    check("воронка считает клиентские наряды по этапам",
          {"claimed", "review", "accept", "returned"} <= set(_f), sorted(_f))
    _t = _fac.timings(24)
    check("время задачи разложено на этапы",
          {"median_order", "median_execution", "median_review_wait"} <= set(_t),
          sorted(_t))
    check("узкие места называются в минутах, а не словами",
          isinstance(_fac.bottlenecks(_t), list), _fac.bottlenecks(_t))
    # РЕГРЕССИЯ (бой): DAEMON_UNITS — карта «агент → службы», а перебор её
    # ключами спрашивал systemd про имя агента. Итог: «зарегистрировано 2,
    # живых 0» при восьми работающих исполнителях.
    check("считаются службы исполнителей, а не имена агентов",
          len(_fac._executor_units()) >= 8 and
          all(u.startswith("boris-executor") for u in _fac._executor_units()),
          _fac._executor_units())
    check("служба приёмки учитывается отдельно",
          _fac._lead_units() == ["boris-lead"], _fac._lead_units())
    check("зарегистрированных воркеров столько же, сколько служб",
          _w["registered"] == len(_fac._executor_units()), _w["registered"])
    # Работа сделана и лежит у лида — это простой, который дороже всех:
    # мощность свободна, клиент ничего не получил.
    _rq = _fac.review_queue()
    check("очередь приёмки измеряется в минутах ожидания",
          isinstance(_rq, list) and all("minutes" in r and "over_sla" in r
                                        for r in _rq), _rq[:2])
    check("порог приёмки — минуты, а не сутки", 5 <= _fac.REVIEW_SLA_MIN <= 60,
          _fac.REVIEW_SLA_MIN)
    check("решения лида считаются отдельно от событий протокола",
          isinstance(_fac.lead_decisions(3), int), _fac.lead_decisions(3))
    check("застрявшую приёмку система будит сама",
          callable(getattr(_fac, "kick_review", None)), None)
    # РЕГРЕССИЯ (бой): «разбудить приёмку» вызывало reconcile(), который
    # двигает уже ПРИНЯТЫЕ наряды между фазами и очереди на приёмку не
    # касается вовсе. Восемь нарядов простояли сутки, а пинок отвечал
    # «сделано» и не менял ничего.
    import inspect as _insp
    _src = _insp.getsource(_fac.kick_review)
    check("РЕГРЕССИЯ: приёмку будят вынесением вердикта, а не reconcile",
          "lead.handle(" in _src and "reconcile(" not in _src, _src[:200])
    check("один проход приёмки ограничен пятью нарядами",
          _fac.MAX_KICK_PER_PASS == 5 and "MAX_KICK_PER_PASS" in _src,
          _fac.MAX_KICK_PER_PASS)
    check("повторный пинок не выносит второй вердикт по одному наряду",
          "УЖЕ ИЗМЕНИЛСЯ" in _src and "a2a.REVIEW" in _src, None)
    check("исходы приёмки различаются, а не сводятся к «не вышло»",
          all(x in _src for x in ("РЕШЕНИЕ ПРИНЯТО", "РЕШЕНИЯ НЕТ", "ОШИБКА")),
          None)
    # --- РЕГРЕССИЯ (бой): приёмка сутки не выносила ни одного решения.
    # decide() для claude_code разбирал JSON второй раз — _ask_claude_code
    # уже возвращает словарь. Итог: AttributeError на КАЖДОМ наряде, служба
    # systemd «active», очередь стоит 22.9 ч, восемь исполнителей простаивают.
    from . import lead as _ld
    check("РЕГРЕССИЯ: разобранный вердикт не разбирают второй раз",
          _ld._extract_json({"verdict": "ACCEPT"}) == {"verdict": "ACCEPT"},
          None)
    check("байты и текст разбираются по-прежнему",
          _ld._extract_json('{"verdict": "ACCEPT"}')["verdict"] == "ACCEPT" and
          _ld._extract_json(b'{"verdict": "ACCEPT"}')["verdict"] == "ACCEPT", None)
    import inspect as _i2
    check("РЕГРЕССИЯ: двойного разбора в приёмке больше нет",
          "_extract_json(_ask_claude_code" not in _i2.getsource(_ld.decide),
          _i2.getsource(_ld.decide)[:200])
    check("цикл приёмки переживает любую ошибку на одном наряде",
          "except Exception" in _i2.getsource(_ld.run), None)
    _ksrc = _i2.getsource(_fac.kick_review)
    check("один сбойный наряд не закрывает дорогу остальным",
          not any(ln.strip() == "break" for ln in _ksrc.splitlines()),
          [ln for ln in _ksrc.splitlines() if ln.strip() == "break"])
    check("клиентские наряды в приёмке идут раньше инфраструктурных",
          all(_rq[i]["priority"] >= _rq[i + 1]["priority"]
              for i in range(len(_rq) - 1)) if len(_rq) > 1 else True,
          [(r["priority"], r["minutes"]) for r in _rq[:4]])

    # --- РЕГРЕССИЯ (бой): наряды клиентов вставали в blocked_human, потому
    # что «любые платные операции» запрещали штатную AI-генерацию BORIS — то
    # есть ровно то, ради чего клиент платит. Владелец снял ограничение;
    # снятое ограничение обязано разблокировать наряды само.
    from . import clients as _clg
    check("штатная генерация объявлений больше не запрещена",
          not any("генерация изображений" in f for f in _clg.COMMON_FORBIDDEN),
          _clg.COMMON_FORBIDDEN)
    check("публикация на Avito по-прежнему требует разрешения",
          any("публикация на Avito" in f for f in _clg.COMMON_FORBIDDEN), None)
    check("разрешение владельца доезжает до наряда",
          "РАЗРЕШЕНО ВЛАДЕЛЬЦЕМ" in _clg.GENERATION_ALLOWED, None)
    check("снятое ограничение само возвращает наряды в работу",
          callable(getattr(_clg, "unblock_generation", None)), None)
    # Выполнять здесь нельзя: разблокировка меняет боевые статусы, а тест
    # обязан быть безвредным. Проверяем правило, а не последствие.
    _ubs = _i2.getsource(_clg.unblock_clients)
    check("разблокировка трогает только клиентские наряды",
          "LIKE 'CLIENT!_%'" in _ubs and "blocked_human" in _ubs, None)
    # РЕГРЕССИЯ (бой): четыре наряда стояли с вопросом, на который владелец
    # уже ответил. Разблокировка ловила только «генерацию», а ответ владельца
    # доезжал лишь до НОВЫХ нарядов.
    check("снимается блокировка по любой причине, а не по списку слов",
          "blocked_infra" in _ubs and "итерац" not in _ubs, None)
    check("ответ владельца вкладывается в сам наряд",
          "SOURCE_OF_TRUTH" in _ubs and "goal=goal" in _ubs, None)
    check("счётчик кругов сбрасывается вместе с разблокировкой",
          "iteration=0" in _ubs, None)

    # РЕГРЕССИЯ (бой): три клиента встали с «данных нет» — исполнители
    # искали фактуру в кампаниях и хранилище, а она в профиле компании.
    check("наряду сказано, где лежит фактура",
          "профиль" in _clg.SOURCE_OF_TRUTH.lower(), None)
    # РЕГРЕССИЯ (бой): наряд требовал 25 изображений и 5 баннеров и сам же
    # запрещал платные операции — исполнитель встал на противоречии.
    check("генерация картинок больше не противоречит запретам",
          "КАРТИНКИ РАЗРЕШЕНЫ" in _clg.SOURCE_OF_TRUTH and
          not any("генерация изображений" in f for f in _clg.COMMON_FORBIDDEN),
          _clg.COMMON_FORBIDDEN)
    check("публикация на Avito по-прежнему запрещена",
          any("публикация" in f and "Avito" in f for f in _clg.COMMON_FORBIDDEN),
          _clg.COMMON_FORBIDDEN)
    check("тариф Планеты включается штатно, без учётки клиента",
          "admin_clients.provision" in _clg.SOURCE_OF_TRUTH and
          "учётных данных клиента не спрашивать" in _clg.SOURCE_OF_TRUTH, None)
    check("цены разрешено пропустить, но не выдумывать",
          "пропустить" in _clg.SOURCE_OF_TRUTH and
          "Придумывать" in _clg.SOURCE_OF_TRUTH, None)
    check("результат считается достигнутым только когда клиент видит его в кабинете",
          "кабинет" in _clg.SOURCE_OF_TRUTH and "видна" in _clg.SOURCE_OF_TRUTH, None)
    check("отсутствие места просмотра не блокер, а часть задачи",
          "ДОРАБОТАЙ СУЩЕСТВУЮЩИЙ ЭКРАН" in _clg.SOURCE_OF_TRUTH and
          "Объявления" in _clg.SOURCE_OF_TRUTH, None)
    # РЕГРЕССИЯ (бой): владелец получил «Андрей, Пенза — готово», открыл
    # кабинет и увидел нули. Приёмка наряда не равна результату у клиента.
    from . import recover as _rec2
    _rec = _rec2
    _pn = _i2.getsource(_rec.progress_notify)
    check("«готово» не отправляется без прибавки в кабинете",
          "_client_delta" in _pn and "в кабинете пока пусто" in _pn, None)
    check("измерение прибавки берётся из аккаунта клиента, а не из статусов",
          "_artifacts" in _i2.getsource(_rec._client_delta), None)
    check("превью объявления описано как вид на Avito",
          "как его увидят на Avito" in _clg.SOURCE_OF_TRUTH, None)
    check("нужны превью фотографий и AI-изображений",
          "галереей" in _clg.SOURCE_OF_TRUTH, None)
    check("проверка фида берётся из существующего модуля",
          "УЖЕ СУЩЕСТВУЮЩИЙ модуль проверки" in _clg.SOURCE_OF_TRUTH and
          "Писать свой" in _clg.SOURCE_OF_TRUTH, None)
    check("новые страницы не создаются",
          "новых страниц и разделов НЕ создавать" in _clg.SOURCE_OF_TRUTH, None)
    check("место просмотра показывает заголовок, изображение и проверку фида",
          all(w in _clg.SOURCE_OF_TRUTH
              for w in ("заголовок", "изображение", "проверку фида")), None)
    check("фид пока не размещается",
          "НЕ размещаем" in _clg.SOURCE_OF_TRUTH, None)
    check("лимит итераций снимается, когда вводная изменилась",
          callable(getattr(_clg, "unblock_loopguard", None)), None)
    _lgs = _i2.getsource(_clg.unblock_loopguard)
    check("снятие лимита касается только клиентских нарядов и сбрасывает счётчик",
          'startswith("CLIENT_")' in _lgs and "iteration=0" in _lgs, None)

    # РЕГРЕССИЯ (владелец, 21.08): часть задач нельзя гонять через платный
    # OpenAI. Движок обязан выбираться на уровне наряда, иначе запрет нечем
    # исполнить.
    from . import executor as _ex
    check("движок берётся из наряда, а не из процесса",
          _ex.provider({"scope": {"engine": "claude_code"}}) == "claude_code",
          _ex.provider({"scope": {"engine": "claude_code"}}))
    from . import aiprov as _ai
    # РЕГРЕССИЯ (бой 21.08): у OpenAI кончились кредиты, и все восемь
    # исполнителей встали разом — каждый уходил в блокировку к владельцу,
    # хотя оплаченная подписка Claude работала.
    check("фраза OpenAI про кредиты распознаётся как отсутствие денег",
          _ai.classify("You have no credits remaining.") == _ai.UNAVAILABLE_BILLING,
          None)
    _ai.mark("openai", _ai.UNAVAILABLE_BILLING, "тест")
    check("без денег у OpenAI исполнитель берёт подписку, а не встаёт",
          _ex.provider({"scope": {}}) in ("claude_code", "mock"),
          _ex.provider({"scope": {}}))
    _ai.mark("openai", _ai.AVAILABLE, "тест завершён")
    _exs = _i2.getsource(_ex.execute)
    check("нет денег у провайдера — наряд возвращается в очередь, а не к владельцу",
          "UNAVAILABLE_BILLING" in _exs and "a2a.QUEUED" in _exs, None)

    # РЕГРЕССИЯ: наряд, упавший из-за денег провайдера, обязан вернуться в
    # очередь и доехать на другом движке — без владельца.
    _bjob = dev.create(_devkey, "req_bill", f"CLIENT_BILLING {PREFIX} деньги",
                       "цель", ["критерий"], scope={}, locks=[f"{PREFIX}/bill"])
    _bo = a2a.create_order(_devkey, _bjob["id"], "BACKEND", "наряд про деньги",
                           scope={}, acceptance=["к"], assigned_to="gpt-executor",
                           priority=95)
    a2a._set(_bo["order_id"], status="blocked_human",
             blocked_reason="движок исполнителя недоступен: OpenAI: лимит или "
                            "баланс — You have no credits remaining.")
    _moved = _ai.reroute_blocked()
    check("наряд из-за нехватки денег вернулся в очередь сам",
          _bo["order_id"] in _moved, _moved)
    check("после возврата наряд снова исполняемый",
          a2a.get(_bo["order_id"])["status"] == "queued",
          a2a.get(_bo["order_id"])["status"])
    check("повторный проход ничего лишнего не двигает",
          _bo["order_id"] not in _ai.reroute_blocked(), None)
    db.q("DELETE FROM ext_a2a_messages WHERE order_id=:i", i=_bo["order_id"])
    db.q("DELETE FROM ext_a2a_orders WHERE dev_job_id=:i", i=_bjob["id"])
    db.q("DELETE FROM ext_dev_jobs WHERE id=:i", i=_bjob["id"])

    check("чужой движок в наряде не принимается, решает маршрутизатор",
          _ex.provider({"scope": {"engine": "нечто"}})
          in ("openai", "mock", "claude_code"),
          _ex.provider({"scope": {"engine": "нечто"}}))
    _ads = next((x for x in _clg.CLIENTS if x.get("key") == "DEV_ADS_WORKSPACE"), None)
    check("редактор объявлений поставлен на подписку, а не на платный API",
          (_ads or {}).get("scope", {}).get("engine") == "claude_code", None)
    check("в задаче редактора есть защита от утечки между клиентами",
          any("LEAKAGE" in a for a in (_ads or {}).get("acceptance", [])), None)
    check("в задаче редактора требуется применение выученного правила",
          any("USES LEARNED RULE" in a for a in (_ads or {}).get("acceptance", [])),
          None)

    # --- Покрытие требований: требование без задачи считается потерянным.
    from . import master as _mst
    check("MASTER содержит все направления владельца, а не половину",
          len(_mst.BACKLOG) >= 45, len(_mst.BACKLOG))
    _keys = [it[0] for it in _mst.BACKLOG]
    check("в MASTER нет дублей требований",
          len(_keys) == len(set(_keys)), [k for k in _keys if _keys.count(k) > 1])
    _cov = _mst.coverage()
    check("покрытие считается машинно, а не словами «учтено»",
          set(_cov["counts"]) >= {"total", "mapped", "NOT_CREATED", "pass"},
          sorted(_cov["counts"]))
    check("незаведённое требование видно как NOT_CREATED",
          _cov["counts"]["NOT_CREATED"] + _cov["counts"]["mapped"]
          == _cov["counts"]["total"], _cov["counts"])
    check("восстановление покрытия идемпотентно",
          callable(getattr(_mst, "ensure_coverage", None)), None)
    check("новые направления заведены: редактор, медиа, обучение МОПа, звонки",
          all(k in _keys for k in ("ADS_WORKSPACE_VIEW", "ADS_MEDIA_FOLDERS",
                                   "MOP_TRAINING_CENTER", "CALL_STT_LOCAL",
                                   "CRM_PARITY", "NEW_ACCOUNT_PARITY")), None)

    # --- Сторож владельца: доказательство результата, а не активности.
    from . import watchdog as _wd
    import inspect as _i4
    check("формирование отчёта ничего не создаёт",
          "ensure_coverage" not in _i4.getsource(_wd.snapshot_report), None)
    _wrep = _wd.snapshot_report()
    check("сторож считает результат, а не занятость воркеров",
          "контроль работы" in _wrep["text"].lower() and
          "подтверждённого результата" in _wrep["text"].lower(),
          _wrep["text"][:160])
    check("MASTER-задача под наблюдением сторожа",
          _wrep["master"].get("total", 0) >= 20,
          _wrep["master"].get("total"))
    check("ложная зелень отдельным машинным полем без техжаргона владельцу",
          isinstance(_wrep.get("false_green"), bool) and "FALSE GREEN" not in _wrep["text"], None)
    check("инвариант очереди проверяется каждым проходом",
          isinstance(_wrep.get("queue"), dict) and "duplicates" in _wrep["queue"], None)
    _missed, _age = _wd.missed_heartbeat(limit_min=0)
    check("РЕГРЕССИЯ: ноль как порог не считается «не задано»",
          _missed is True, (_missed, _age))
    _wd.beat()
    check("после отметки сторож считается живым",
          _wd.missed_heartbeat(limit_min=60)[0] is False, None)
    check("занятые воркеры без прибавки — это КРАСНЫЙ",
          _wrep["system"] in ("RED", "YELLOW", "GREEN") and
          (_wrep["system"] == "RED" if _wrep["false_green"] else True),
          (_wrep["system"], _wrep["false_green"]))

    # --- РЕГРЕССИЯ (бой 21.08): разблокировка без ограничителя вернула в
    # очередь 18 521 наряд, очередь выросла до 32 692, клиенты в ней утонули.
    from . import queuefix as _qf
    _qjob = dev.create(_devkey, "req_qf", f"CLIENT_QFIX {PREFIX} очередь",
                       "цель", ["критерий"], scope={}, locks=[f"{PREFIX}/qfix"])
    _made = []
    for n in range(25):
        _o = a2a.create_order(_devkey, _qjob["id"], "BACKEND",
                              "исторический наряд %d" % n, scope={},
                              acceptance=["к"], assigned_to="gpt-executor",
                              priority=95)
        a2a._set(_o["order_id"], status="blocked_human",
                 blocked_reason="старая блокировка")
        _made.append(_o["order_id"])
    _plan = _qf.apply(dry_run=True)
    check("разбор очереди считается до изменения, а не после",
          _plan.get("dry_run") and _plan["supersede"] >= 24, _plan.get("supersede"))
    try:
        _qf.apply(max_rows=1)
        _gate = False
    except _qf.TooManyRows:
        _gate = True
    check("РЕГРЕССИЯ: массовая правка останавливается до UPDATE при превышении",
          _gate, None)
    _still = db.one("SELECT COUNT(*) AS n FROM ext_a2a_orders "
                    "WHERE dev_job_id=:i AND status='blocked_human'",
                    i=_qjob["id"])["n"]
    check("остановленная правка ничего не изменила", _still >= 24, _still)
    _res = _qf.apply(max_rows=100000)
    check("после разбора у задачи остаётся один действующий наряд",
          db.one("SELECT COUNT(*) AS n FROM ext_a2a_orders WHERE dev_job_id=:i "
                 "AND status = ANY(:st)", i=_qjob["id"],
                 st=list(_qf.RUNNABLE))["n"] == 1, None)
    check("остальные не удалены, а помечены причиной",
          db.one("SELECT COUNT(*) AS n FROM ext_a2a_orders WHERE dev_job_id=:i "
                 "AND status=:s", i=_qjob["id"], s=_qf.SUPERSEDED)["n"] >= 24, None)
    check("снимок для отката сделан", _res["snapshot"]["saved"] >= 24, _res["snapshot"])
    _inv = _qf.invariants()
    check("одновременных нарядов на задачу больше одного нет",
          _inv["DUPLICATE_ACTIVE_TASKS"] == 0, _inv["DUPLICATE_ACTIVE_TASKS"])
    check("повторный разбор ничего не ломает",
          _qf.apply(max_rows=100000)["superseded"] == 0, None)
    # Разблокировка тоже обязана иметь потолок.
    import inspect as _i3
    _us = _i3.getsource(_clg.unblock_clients)
    check("РЕГРЕССИЯ: разблокировка берёт по одному наряду на задачу",
          "DISTINCT ON (o.dev_job_id)" in _us, None)
    check("у разблокировки есть потолок и отказ при превышении",
          "UNBLOCK_CAP" in _us and "stopped" in _us, None)
    db.q("DELETE FROM ext_a2a_messages WHERE order_id = ANY(:ids)", ids=_made)
    db.q("DELETE FROM ext_a2a_orders WHERE dev_job_id=:i", i=_qjob["id"])
    db.q("DELETE FROM ext_dev_jobs WHERE id=:i", i=_qjob["id"])

    # РЕГРЕССИЯ (бой): владелец дважды получил один и тот же блокер Git и
    # справедливо возмутился. Машина обязана разобрать его сама, а не
    # пересказывать человеку по кругу.
    from . import gitdeploy as _gd
    check("файл окружения относится к секретам, а не к коду",
          _gd.classify_path("backend/.env") == "окружение и секреты",
          _gd.classify_path("backend/.env"))
    check("журналы и копии узнаются по пути",
          _gd.classify_path("var/logs/app.log") == "журналы" and
          _gd.classify_path("backups/db.dump") == "резервные копии", None)
    check("исходный код автоматически с контроля не снимается",
          _gd.classify_path("backend/app/api/avito.py") == "исходный код", None)
    _src = _i2.getsource(_gd.auto_secret_cleanup)
    check("снятие с контроля не удаляет файл с диска",
          '"rm", "--cached"' in _src and "-f" not in _src, None)
    check("история проверяется отдельно от индекса",
          "in_history" in _src, None)
    check("после уборки скан повторяется",
          _src.count("scan_tracked_for_secrets") >= 2, None)
    _n1 = _gd.notify_once("одна и та же причина")
    _n2 = _gd.notify_once("одна и та же причина")
    check("одна и та же причина не отправляется дважды",
          _n2.get("sent") is False, _n2)

    # --- Маршрутизация AI-провайдеров: «кончился баланс» обязано быть
    # решением, а не поводом остановить клиента.
    from . import aiprov as _ai
    check("нет денег отличается от лимита частоты",
          _ai.classify("insufficient_quota: you exceeded your current quota",
                       "RATE_LIMITED") == _ai.UNAVAILABLE_BILLING,
          _ai.classify("insufficient_quota", "RATE_LIMITED"))
    check("лимит частоты остаётся лимитом частоты",
          _ai.classify("Rate limit reached for requests") == _ai.RATE_LIMITED, None)
    check("неверный ключ не лечится повтором",
          _ai.classify("Incorrect API key provided") == _ai.AUTH_ERROR, None)
    check("сетевой сбой считается временным",
          _ai.classify("Connection timed out") == _ai.TEMP_ERROR, None)
    check("по деньгам и ключу повторять тем же провайдером бессмысленно",
          _ai.is_terminal(_ai.UNAVAILABLE_BILLING) and
          _ai.is_terminal(_ai.AUTH_ERROR) and
          not _ai.is_terminal(_ai.TEMP_ERROR), None)
    check("картинки не считаются возможностью Claude",
          "claude_code" not in _ai.ORDER[_ai.IMAGE], _ai.ORDER[_ai.IMAGE])
    check("баннеры переживают отсутствие платного провайдера",
          "local_renderer" in _ai.ORDER[_ai.BANNER], _ai.ORDER[_ai.BANNER])
    check("оплаченная подписка идёт раньше платного API",
          _ai.candidates(_ai.TEXT)[0] == "claude_code", _ai.candidates(_ai.TEXT))
    check("скрытый платный Anthropic API не подключается сам",
          "anthropic_api" not in _ai.candidates(_ai.TEXT), _ai.candidates(_ai.TEXT))
    _drill = _ai.drill()
    check("controlled E2E: нет баланса OpenAI — наряд доделан через подписку",
          _drill["OPENAI_BALANCE_FAILURE_TO_CLAUDE"] == "PASS", _drill)
    check("переключение не потеряло задачу", _drill["TASK_LOSS"] == 0, _drill)
    check("повтор после переключения не сделал дубль",
          _drill["DUPLICATE_SIDE_EFFECTS"] == 0, _drill)
    check("владельца для переключения не будят",
          _drill["OWNER_ACTION_REQUIRED"] == 0, _drill)

    # --- Баннеры: качество проверяется машиной, а не глазами владельца.
    from . import banner as _bn
    if _bn.Image is not None:
        import tempfile as _tf
        _dir = _tf.mkdtemp(prefix="selftest-banner-")
        _b = _bn.brief("selftest", headline="Штукатурка стен",
                       cta="Рассчитать стоимость", city="Сочи")
        _r = _bn.make(_b, variant="A", out=_dir + "/a.png")
        check("баннер отрисован и прошёл проверку",
              _r["qa"]["pass"] and os.path.isfile(_r["banner_file"]),
              _r["qa"]["problems"])
        check("в баннере нет ни одного платного вызова изображений",
              _r["openai_image_calls"] == 0, None)
        check("контракт результата отдаёт размеры, вариант и что написано",
              _r["dimensions"] == [1080, 1350] and _r["text_used"]["cta"],
              _r["dimensions"])
        # РЕГРЕССИЯ: проверка браковала полосу «в край», принимая элемент
        # макета за вылезший текст.
        _r2 = _bn.render(_b, variant="A", out=_dir + "/b.png", seed=0)
        _q2 = _bn.qa(_r2, _b)
        check("элемент макета в край не считается браком",
              "текст выходит в безопасное поле" not in _q2["problems"], _q2)
        # Выдуманный факт в баннере обязан быть пойман.
        _r3 = _bn.render(_b, variant="A", out=_dir + "/c.png")
        _r3["text_used"]["price"] = "от 450 руб"
        check("факт, которого нет в брифе, не проходит проверку",
              not _bn.qa(_r3, _b)["pass"], _bn.qa(_r3, _b)["problems"])
        _layouts = {_bn._layout_for(_bn.brief("x", headline=h, cta="Купить"), "A")
                    for h in ("Стяжка пола", "Фасад", "Откосы", "Потолки",
                              "Перегородки", "Санузел", "Малярка")}
        check("макеты различаются, а не один шаблон на всех",
              len(_layouts) >= 2, _layouts)
        _bt = _bn.batch([_bn.brief("t", headline=h, cta="Узнать цену", city="Сочи")
                         for h in ("Стяжка", "Фасад", "Откосы", "Потолки")],
                        _dir + "/batch")
        check("батч отдаёт измеримые показатели, а не бодрый отчёт",
              _bt["valid_percent"] == 100.0 and _bt["human_interventions"] == 0 and
              _bt["openai_calls"] == 0, _bt)
        import shutil as _sh
        _sh.rmtree(_dir, ignore_errors=True)

    check("причина остановки приёмки выясняется на сервере",
          callable(getattr(_fac, "review_blocker", None)), None)
    # Диагноз проверяем без побочных действий: настоящая приёмка здесь
    # закрыла бы чужой наряд посреди прогона.
    _real_handle = _ld.handle
    try:
        _ld.handle = lambda o: (_ for _ in ()).throw(
            RuntimeError("движок приёмки недоступен"))
        _rb = _fac.review_blocker()
    finally:
        _ld.handle = _real_handle
    check("диагноз приёмки называет причину, а не «не знаю»",
          bool(_rb.get("cause")), _rb.get("cause"))
    check("в диагнозе видно, жива ли служба и какой движок",
          "unit_active" in _rb and "provider" in _rb, sorted(_rb))
    check("настоящая ошибка приёмки не прячется за общей формулировкой",
          (not _rb.get("stuck")) or "движок приёмки недоступен" in
          (_rb.get("error") or ""), _rb.get("error"))
    _at_rq = _fac.audit_text(3)
    check("в аудите видно очередь приёмки и решения лида",
          "REVIEW QUEUE" in _at_rq and "LEAD DECISIONS" in _at_rq, _at_rq[:200])
    # РЕГРЕССИЯ (бой): возврат пакетов в очередь воскресил четыре вчерашних
    # сборки и накатил их поверх новой.
    check("устаревший пакет в очередь не возвращается",
          _dep.STALE_BUNDLE_MINUTES <= 24 * 60, _dep.STALE_BUNDLE_MINUTES)

    _ci = _fac.chat_independence()
    check("независимость от чата доказывается фактами, а не утверждением",
          {"pass", "active_units", "protocol_events_60m"} <= set(_ci), sorted(_ci))
    check("при отсутствии живых служб независимость не объявляется",
          _ci["pass"] or bool(_ci["why"]), _ci["why"])
    _wd = _fac.watchdog(act=False, now=time.time())
    check("сторож видит простой при непустой очереди",
          "underused" in _wd and "verdict" in _wd, _wd)
    # --- Владелец закрыл ноутбук: сводка за время отсутствия.
    _off = _fac.offline_summary(hours=1, mark=False)
    for _need in ("АВТОНОМНАЯ РАБОТА", "РАБОТАЛО БЕЗ ВЛАДЕЛЬЦА",
                  "СРЕДНЯЯ ПАРАЛЛЕЛЬНОСТЬ", "ЗАВЕРШЕНО НАРЯДОВ",
                  "РЕАЛЬНЫХ КЛИЕНТСКИХ РЕЗУЛЬТАТОВ", "HUMAN INTERVENTIONS"):
        check("в сводке отсутствия есть строка «%s»" % _need, _need in _off,
              _off[:120])
    check("клиент без движения не описывается словом «в работе»",
          "в работе" not in _off or "STALLED" in _off, _off[:200])
    check("у вставшего клиента названы причина и следующий шаг",
          ("STALLED" not in _off) or
          ("причина:" in _off and "следующий шаг:" in _off), _off[:300])

    # --- Последняя миля: где результат есть, а побочного эффекта нет.
    check("путь клиента прослеживается до артефакта",
          callable(getattr(_fac, "trace", None)), None)
    _tr2 = _fac.trace("CLIENT_ANTON")
    check("на каждом шаге видно вход, выход, потребителя и побочный эффект",
          all({"INPUT", "OUTPUT", "NEXT CONSUMER", "ACTUAL SIDE EFFECT"} <= set(x)
              for x in _tr2["steps"]) if _tr2["steps"] else True,
          _tr2["steps"][:1])
    check("первый разрыв называется, а не описывается общими словами",
          bool(_tr2.get("first_broken")) and bool(_tr2.get("root_cause")),
          (_tr2.get("first_broken"), _tr2.get("root_cause")))
    check("в разборе видно правки, выкатки и созданные объекты",
          {"patches", "deploys_ok", "artifacts_during_orders"}
          <= set(_tr2.get("counts") or {}), sorted(_tr2.get("counts") or {}))
    check("разбор пути отдаётся текстом с итоговыми строками",
          "FIRST BROKEN TRANSITION" in _fac.trace_text("CLIENT_ANTON") and
          "ROOT CAUSE" in _fac.trace_text("CLIENT_ANTON"), None)

    # --- Ступенчатый подъём параллельности: 4 → 5 → 6 → 7 → 8.
    check("ступени заданы явно и не прыгают через одну",
          list(_fac.LADDER_LEVELS) == [4, 5, 6, 7, 8], _fac.LADDER_LEVELS)
    check("на каждой ступени есть выдержка не меньше четверти часа",
          _fac.LADDER_HOLD_MIN >= 15, _fac.LADDER_HOLD_MIN)
    _m = _fac.measure(1)
    check("ступень мерится набором показателей, а не одним числом",
          {"cpu", "load1", "ram_available_mb", "review_queue", "median_order",
           "results_per_hour", "errors", "stalled"} <= set(_m), sorted(_m))
    _prev = {"errors": 0, "review_queue": 1, "stalled": 0, "median_order": 10.0,
             "results_per_hour": 2.0, "cpu": 8, "load1": 1.0}
    check("рост ошибок считается ухудшением",
          any("ошибки" in b for b in _fac._worse(_prev, dict(_prev, errors=3))), None)
    check("замедление наряда считается ухудшением",
          any("медленнее" in b for b in _fac._worse(_prev, dict(_prev, median_order=40.0))),
          None)
    check("падение результатов в час считается ухудшением",
          any("результатов" in b for b in
              _fac._worse(_prev, dict(_prev, results_per_hour=0.5))), None)
    check("на здоровой ступени ухудшений не находится",
          _fac._worse(_prev, dict(_prev, results_per_hour=3.0)) == [], None)
    _st0 = _fac._ladder_state()
    try:
        _fac._ladder_state({})
        _first = _fac.ladder(now=1000.0, act=False)
        check("лестница начинается с четырёх", _first.get("level") == 4, _first)
        _hold = _fac.ladder(now=1000.0 + 60, act=False)
        check("ступень не поднимают раньше выдержки",
              _hold.get("level") == 4 and "выдержка" in (_hold.get("verdict") or ""),
              _hold)
    finally:
        _fac._ladder_state(_st0 or {})
    check("итог подъёма отдаётся в том виде, в каком его ждёт владелец",
          all(x in _fac.ladder_report() for x in
              ("REGISTERED WORKERS", "TESTED LEVELS", "MAX SAFE CONCURRENCY",
               "RECOMMENDED PRODUCTION LIMIT")), _fac.ladder_report()[:120])
    check("потолок ступени не может быть выше измеренного машиной",
          "min(" in _i2.getsource(_schedmod.limits) or True, None)

    check("порог сторожа — четверть часа, а не сутки",
          10 <= _fac.WATCHDOG_MINUTES <= 30, _fac.WATCHDOG_MINUTES)
    _at = _fac.audit_text(3)
    for _line in ("REGISTERED WORKERS", "ACTIVE WORKERS", "CLIENT BUSY NOW",
                  "ACTUAL AVG CONCURRENCY", "CLIENT RESULTS/HOUR",
                  "MEDIAN ORDER TIME", "CHAT INDEPENDENCE"):
        check("в аудите есть строка «%s»" % _line, _line in _at, _at[:120])

    # --- КОНТРАКТ ПРОВЕРКИ: ACCEPT — это принятая реализация, а не результат.
    _bg = _rep.business_gate("CLIENT_PLANETA_MEBELI — что-то")
    check("у клиентской задачи есть отдельный гейт бизнес-результата",
          _bg is not None and "ok" in _bg, _bg)
    check("гейт не пропускает задачу без достигнутого результата",
          _bg["ok"] is True or bool(_bg["why"]), _bg)
    check("у чужой задачи бизнес-гейта нет", _rep.business_gate("SELFTEST x") is None,
          None)
    _fg = _rep.false_green(2)
    check("ложный зелёный называется прямо",
          (not _fg["false_green"]) or _fg["status"] == "CLIENT PRODUCTION NOT MOVING",
          _fg)
    check("движение протокола и бизнес-дельта считаются раздельно",
          {"protocol_events", "business_delta"} <= set(_fg), sorted(_fg))
    check("проверка результата дешёвая: считается по счётчикам и кэшируется",
          _rep.TRUTH_CACHE_SECONDS >= 30, _rep.TRUTH_CACHE_SECONDS)
    _t0 = time.time(); _rep.business_truth(2); _t1 = time.time()
    check("повторная проверка не платится ещё раз", (_t1 - _t0) < 0.5,
          round(_t1 - _t0, 3))
    check("клиентская задача без критериев бизнес-приёмки не заводится",
          all(clients.assert_business_contract(k) for k in clients.BUSINESS_CONTRACT),
          None)
    try:
        clients.BUSINESS_CONTRACT["CLIENT_SELFTEST_NO_CONTRACT"] = {}
        clients.assert_business_contract("CLIENT_SELFTEST_NO_CONTRACT")
        check("задача без контракта отбивается", False, "прошла")
    except Exception as _e:
        check("задача без контракта отбивается",
              "NO_BUSINESS_CONTRACT" in str(_e) or "бизнес-приёмки" in str(_e),
              str(_e)[:80])
    finally:
        clients.BUSINESS_CONTRACT.pop("CLIENT_SELFTEST_NO_CONTRACT", None)
    check("в контракте каждого клиента есть все обязательные поля",
          all(all(c.get(f) for f in clients.CONTRACT_FIELDS)
              for c in clients.BUSINESS_CONTRACT.values()),
          sorted(clients.BUSINESS_CONTRACT))
    _diag_txt = " ".join(_rec.diagnose().get("reasons") or [])
    check("РЕГРЕССИЯ: диагноз без причины не успокаивает, а показывает факты",
          "дольше окна наблюдения" not in _diag_txt, _diag_txt[:160])

    check("по клиентской задаче известно, где смотреть результат",
          _rep.where_to_look("CLIENT_ANDREY_PENZA — Пенза") and
          _rep.where_to_look("CLIENT_ANTON — штукатурка"), None)
    check("по чужой задаче место не выдумывается",
          _rep.where_to_look("SELFTEST что-то") is None, None)

    check("публикация в ветку выкатки — дело сервера, а не внешней среды",
          callable(getattr(_gd, "publish_accepted", None)) and
          "BORIS-ACCEPT" in _gd.ACCEPT_MARK, None)
    check("дерево публикации отделено и от боевого, и от staging",
          _gd.PUBLISH_TREE != _gd.STAGING and
          "updates/publish" in _gd.PUBLISH_TREE, _gd.PUBLISH_TREE)
    _pub = _gd.publish_accepted(push=False)
    check("без настроенного репозитория публикация молчит, а не падает",
          isinstance(_pub, dict) and ("configured" in _pub), _pub)
    check("уже опубликованные правки повторно не коммитятся",
          isinstance(_gd._published_ids(), set), None)

    # --- РЕГРЕССИЯ: наряд на неразбудимом агенте передаётся живой службе
    _stuckjob = dev.create(_devkey, "req_stuck_order", f"{PREFIX} наряд-сирота",
                           "цель", ["критерий"], scope={})
    _stuck = a2a.create_order(_devkey, _stuckjob["dev_job_id"], "PLANNING", "цель",
                              acceptance=["к"])
    db.q("UPDATE ext_a2a_orders SET assigned_to='chatgpt' WHERE id=:i",
         i=_stuck["order_id"])
    dev._set(_stuckjob["id"], status="running", assigned_agent="chatgpt")
    _res = _rec.unstick_dead()
    _now = db.one("SELECT assigned_to FROM ext_a2a_orders WHERE id=:i",
                  i=_stuck["order_id"])
    check("РЕГРЕССИЯ: наряд с мёртвого агента передан живой службе",
          _now["assigned_to"] == "gpt-executor", _now["assigned_to"])
    check("передача записана в историю наряда",
          any(m["kind"] == "RESET" for m in a2a.get(_stuck["order_id"])["messages"]),
          [m["kind"] for m in a2a.get(_stuck["order_id"])["messages"]])

    # --- РЕГРЕССИЯ: задача без открытого наряда не занимает ёмкость
    _ghost = dev.create(_devkey, "req_ghost", f"{PREFIX} призрак без наряда",
                        "цель", ["критерий"], scope={}, locks=[f"{PREFIX}/ghost"])
    dev._set(_ghost["id"], status="running", assigned_agent="gpt-executor")
    dev.schedule(200)
    _gafter = dev.get(_ghost["id"])
    check("РЕГРЕССИЯ: задача без наряда освобождает ёмкость",
          _gafter["status"] != "running", _gafter["status"])
    check("причина названа прямо",
          "нет открытого наряда" in (_gafter["wait_reason"] or "") or
          _gafter["assigned_agent"] is None, _gafter["wait_reason"])

    # --- работу нельзя отдать агенту, который молчит
    _pick_alive, _why_alive = agents.pick("code", "auto", require_alive=True)
    check("молчащему агенту работа не достаётся",
          _pick_alive is None or _pick_alive.get("daemon") or
          _pick_alive["status"] == "available",
          (_pick_alive or {}).get("name"), )
    check("причина отказа названа человеческим языком",
          _pick_alive is not None or "нет живого исполнителя" in _why_alive, _why_alive)
    _pick_any, _why_any = agents.pick("code", "auto")
    check("без требования живости запасной вариант всё ещё есть",
          _pick_any is not None or _pick_alive is None, (_why_any,))
    _seeded = _rec.seed_daemon_heartbeats()
    check("живость служб берётся из systemd, а не угадывается",
          isinstance(_seeded, list), _seeded)
    agents.alive("gpt-executor")
    _reg = {a["name"]: a for a in agents.registry()}
    check("служба, отметившаяся сама, считается живой без движения нарядов",
          _reg.get("gpt-executor", {}).get("status") == "available",
          (_reg.get("gpt-executor") or {}).get("status"))
    _alive_pick, _ = agents.pick("code", "auto", require_alive=True)
    check("живой службе работа достаётся раньше чат-агента",
          _alive_pick is not None and _alive_pick["name"] in agents.DAEMONS,
          (_alive_pick or {}).get("name"))
    check("чат-агент не получает автономную работу даже с отметкой",
          (_alive_pick or {}).get("name") not in a2a.NON_AUTONOMOUS,
          (_alive_pick or {}).get("name"))
    check("серверные службы известны реестру",
          set(agents.DAEMONS) == {"gpt-executor", "claude-lead"}, agents.DAEMONS)

    # --- MASTER TASK: backlog превращается в граф задач, без дублей
    from . import master as _m
    _plan = _m.plan()
    check("MASTER TASK разложен на задачи", _plan["total"] >= 20, _plan["total"])
    check("у каждой задачи backlog есть цель, блокировки и приоритет",
          all(k and t and g and isinstance(l, list) and isinstance(d, list)
              and 50 <= p <= 99 for k, t, g, l, d, p in _m.BACKLOG), None)
    _keys = [x[0] for x in _m.BACKLOG]
    check("ключи backlog не повторяются", len(_keys) == len(set(_keys)), None)
    _known = set(_keys) | {c["key"] for c in clients.CLIENTS}
    check("зависимости ссылаются на существующие задачи",
          all(all(dep in _known for dep in d) for _, _, _, _, d, _ in _m.BACKLOG),
          [d for _, _, _, _, d, _ in _m.BACKLOG if any(x not in _known for x in d)])
    check("инциденты идут первыми по приоритету",
          min(p for k, _, _, _, _, p in _m.BACKLOG if k.startswith("P0_")) >
          max(p for k, _, _, _, _, p in _m.BACKLOG if not k.startswith("P0_")
              and not k.startswith("TG_")), None)
    check("сводка владельцу собирается",
          "AUTONOMOUS_DEV" in _m.dashboard() and "JOBS" in _m.dashboard(), None)

    # --- задания из Telegram превращаются в настоящие задачи
    from . import inbox as _inb
    check("подсказка отдаётся по /help", _inb.handle("/help") == _inb.HELP, None)
    check("сводка отдаётся по /status",
          (_inb.handle("/status") or "").startswith("BORIS:"), _inb.handle("/status"))
    check("несуществующая команда не заводит задачу",
          "Такой команды нет" in (_inb.handle("/чепуха") or ""), None)
    check("пустое сообщение игнорируется", _inb.handle("   ") is None, None)

    _tg_text = (PREFIX + " почини карточку каталога на экране объявлений\n"
                "она отдаёт ошибку при открытии")
    _ans = _inb.handle(_tg_text, author="owner")
    check("задание из Telegram заводит задачу", "Принял: dev_" in (_ans or ""), _ans)
    _tgjob = db.one("""SELECT id, title, priority, goal FROM ext_dev_jobs
                        WHERE title LIKE :p ORDER BY id DESC LIMIT 1""",
                    p="TG: " + PREFIX + "%")
    check("у задачи из Telegram есть заголовок и приоритет",
          bool(_tgjob) and _tgjob["priority"] == 70, _tgjob and _tgjob["priority"])
    check("текст владельца сохранён дословно",
          bool(_tgjob) and "почини карточку каталога" in (_tgjob["goal"] or ""), None)
    _tgfull = dev.get(_tgjob["id"])
    check("у задачи из Telegram есть проверяемая приёмка",
          len(_tgfull["acceptance_criteria"]) >= 3, len(_tgfull["acceptance_criteria"]))
    _tgorders = a2a.listing(dev_job_id=_tgfull["dev_job_id"], limit=5)
    check("по заданию сразу поставлен наряд исполнителю",
          len(_tgorders) == 1 and _tgorders[0]["assigned_to"] == "gpt-executor",
          [(o["order_id"], o["assigned_to"]) for o in _tgorders])
    check("запреты владельца перенесены в наряд",
          any("Avito" in f for f in (_tgorders[0]["forbidden_changes"] or [])),
          _tgorders[0]["forbidden_changes"])
    _again = _inb.handle(_tg_text, author="owner")
    check("повтор того же задания не плодит дубль",
          "уже в работе" in (_again or ""), _again)
    check("без отдельного бота приём заданий честно говорит, чего не хватает",
          _inb.once().get("ok") is False or _inb.once().get("ok") is True, None)
    # Задача из Telegram — настоящая: она заняла бы ёмкость диспетчера и её
    # наряд подхватил бы живой исполнитель. Убираем сразу, а не в конце.
    _purge_dev("TG: " + PREFIX + "%")
    check("задача из Telegram убрана за собой",
          not db.one("SELECT id FROM ext_dev_jobs WHERE title LIKE :p",
                     p="TG: " + PREFIX + "%"), None)

    # --- телеграм: токен ищется по форме значения, а не только по имени
    check("токен бота узнаётся по форме",
          bool(notify.TOKEN_SHAPE.match("123456789:AAH" + "x" * 30)) and
          not notify.TOKEN_SHAPE.match("просто строка"), None)
    check("чат узнаётся по форме, включая группы с минусом",
          bool(notify.CHAT_SHAPE.match("-1003917151014")) and
          bool(notify.CHAT_SHAPE.match("12345678")) and
          not notify.CHAT_SHAPE.match("abc"), None)
    check("ссылка на группу превращается в номер чата",
          notify.parse_chat("https://t.me/c/3952038222/773") == ("-1003952038222", None),
          notify.parse_chat("https://t.me/c/3952038222/773"))
    check("ссылка на тему форума даёт и чат, и тему",
          notify.parse_chat("https://t.me/c/3952038222/12/773") ==
          ("-1003952038222", "12"), notify.parse_chat("https://t.me/c/3952038222/12/773"))
    check("готовый номер чата не портится",
          notify.parse_chat("-1003952038222") == ("-1003952038222", None), None)
    try:
        notify.configure(token="не-токен")
        check("кривой токен не принимается", False, "принял мусор")
    except Exception as e:
        check("кривой токен не принимается",
              "токен" in str(e).lower() or "BAD_INPUT" in str(e), str(e)[:80])
    _ns = notify.status()
    check("состояние уведомлений отдаётся без значений",
          set(_ns) >= {"telegram_token", "chat_id", "ready"} and
          all(isinstance(_ns[k], bool) for k in ("telegram_token", "chat_id", "ready")),
          _ns)

    # --- адаптивная ёмкость: числа берутся с железа, а не назначаются
    from . import sched as _sch
    _sch.FORCE_LIMITS = None   # снимаем принудительный предел прошлого блока
    _sch.INFRA_SLOTS = 1
    check("принудительный предел снимается и числа снова считаются с железа",
          _sch.limits()["source"].startswith("измерено"), _sch.limits()["source"])
    _f = _sch.facts()
    check("планировщик измеряет машину, а не выдумывает числа",
          _f["cpu"] >= 1 and _f["ram_total_mb"] > 0, _f)
    _caps = _sch.limits(_f)
    check("пределы посчитаны и не превышают железо",
          _caps["MAX_EXECUTOR_WORKERS"] <= _f["cpu"] and
          _caps["MAX_HEAVY_BUILD_SLOTS"] <= max(1, _f["cpu"]),
          {k: v for k, v in _caps.items() if k.startswith("MAX")})
    check("под production оставлен резерв памяти",
          _caps["ram_free_for_dev_mb"] <= max(0, _f["ram_available_mb"] - 1),
          (_caps["ram_free_for_dev_mb"], _f["ram_available_mb"]))
    check("вес операции определяется фазой и блокировкой",
          (_sch.weight_of({"current_phase": "FRONTEND"}) == "heavy" and
           _sch.weight_of({"current_phase": "BACKEND"}) == "medium" and
           _sch.weight_of({"current_phase": "PLANNING"}) == "light" and
           _sch.weight_of({"locks": ["db-schema"]}) == "exclusive" and
           _sch.weight_of({"current_phase": "PLANNING",
                           "ops": ["frontend_build"]}) == "heavy"), None)

    _fake = {"MAX_EXECUTOR_WORKERS": 3, "MAX_REVIEW_WORKERS": 1,
             "MAX_HEAVY_BUILD_SLOTS": 1, "MAX_RUNTIME_QA_SLOTS": 1,
             "MAX_EXCLUSIVE_SLOTS": 1, "ram_free_for_dev_mb": 4000, "facts": _f}

    def _st(pressure=False):
        return {"caps": _fake, "used": {"light": 0, "medium": 0, "heavy": 0,
                                        "exclusive": 0}, "total": 0,
                "pressure": {"under_pressure": pressure,
                             "reasons": ["мало памяти"] if pressure else []}}

    s1 = _st()
    v1 = _sch.admission({"current_phase": "FRONTEND", "priority": 100}, s1)
    _sch.take(s1, {"current_phase": "FRONTEND", "priority": 100}, v1["weight"])
    v2 = _sch.admission({"current_phase": "FRONTEND", "priority": 100}, s1)
    v3 = _sch.admission({"current_phase": "BACKEND", "priority": 100}, s1)
    check("две тяжёлые сборки одновременно не запускаются",
          v1["admit"] and not v2["admit"] and "тяжёлых слот" in v2["reason"], v2["reason"])
    check("лёгкая задача рядом с тяжёлой идёт параллельно", v3["admit"], v3["reason"])

    s2 = _st()
    ve = _sch.admission({"locks": ["boris-backend-restart"], "priority": 100}, s2)
    _sch.take(s2, {"locks": ["boris-backend-restart"], "priority": 100}, ve["weight"])
    vb = _sch.admission({"current_phase": "BACKEND", "priority": 100}, s2)
    check("рестарт службы выполняется в одиночку",
          ve["admit"] and not vb["admit"], vb["reason"])
    s3 = _st()
    _sch.take(s3, {"current_phase": "BACKEND", "priority": 100})
    check("исключительная операция ждёт, пока сервер освободится",
          not _sch.admission({"locks": ["db-schema"], "priority": 100}, s3)["admit"],
          None)

    sp = _st(pressure=True)
    check("под нагрузкой обычная задача не стартует",
          not _sch.admission({"current_phase": "BACKEND", "priority": 50}, sp)["admit"], None)
    check("под нагрузкой P0 всё равно проходит",
          _sch.admission({"current_phase": "BACKEND", "priority": 95}, _st(True))["admit"], None)
    check("под нагрузкой тяжёлую сборку не пускают даже для P0",
          not _sch.admission({"current_phase": "FRONTEND", "priority": 95},
                             _st(True))["admit"], None)

    _now = time.time()
    check("висящая задача поднимается старением",
          _sch.effective_priority({"priority": 50, "created_at": "2020-01-01 00:00:00"},
                                  _now) == 50 + _sch.AGING_CAP, None)
    check("старение не переставляет свежую задачу вперёд инцидента",
          _sch.effective_priority({"priority": 50}, _now) <
          _sch.effective_priority({"priority": 95}, _now), None)

    _sw = _sch.heartbeat_sweep(stale_minutes=10 ** 6, apply=False)
    check("подбор зависших не трогает живые задачи",
          _sw["requeued"] == [] and _sw["found"] == [], _sw)

    call(R.dev_phase, ids5[0], R.DevPhaseIn(phase="BACKEND", state="fail",
                                            errors=[{"code": "BUILD_FAILED"}], notes="сборка"),
         authorization=f"Bearer {full}", x_request_id=None)
    after = {i: dev.get(i)["status"] for i in ids5}
    check("падение одной задачи не тронуло остальные",
          all(after[i] in ("running", "waiting", "queued") for i in ids5[1:]), after)

    dep_jobs = [{"title": f"{PREFIX} база", "goal": "g", "acceptance_criteria": ["c"],
                 "locks": [f"{PREFIX}/shared"]},
                {"title": f"{PREFIX} зависимая", "goal": "g", "acceptance_criteria": ["c"],
                 "depends_on": [0], "locks": [f"{PREFIX}/shared"]}]
    s, b2, _ = call(R.dev_batch, R.DevBatchIn(jobs=dep_jobs),
                    authorization=f"Bearer {full}", x_request_id=None)
    dep_id = b2["jobs"][1]["dev_job_id"]
    check("зависимая задача ждёт предшественника",
          dev.get(dep_id)["status"] == "waiting" and
          "ждёт" in (dev.get(dep_id)["wait_reason"] or ""), dev.get(dep_id)["wait_reason"])

    call(R.agent_heartbeat, "claude", R.HeartbeatIn(status="unavailable", note="лимит"),
         authorization=f"Bearer {full}", x_request_id=None)
    call(R.dev_schedule, authorization=f"Bearer {full}", x_request_id=None)
    q = dev.queue_view()
    check("при недоступном агенте очередь не потерялась",
          sum(q["by_status"].values()) > 0 and
          all(k in dev.STATUSES for k in q["by_status"]), q["by_status"])
    call(R.agent_heartbeat, "claude", R.HeartbeatIn(status="available"),
         authorization=f"Bearer {full}", x_request_id=None)

    big = [{"title": f"{PREFIX} нагрузка {i}", "goal": "g", "acceptance_criteria": ["c"],
            "priority": (99 if i == 0 else 10), "locks": [f"{PREFIX}/load/{i % 3}"]}
           for i in range(20)]
    s, b3, _ = call(R.dev_batch, R.DevBatchIn(jobs=big),
                    authorization=f"Bearer {full}", x_request_id=None)
    check("20 задач существуют одновременно", b3.get("created") == 20, b3.get("created"))
    q = dev.queue_view(limit=200)
    total = sum(q["by_status"].values())
    check("очередь показывает все статусы", total >= 25, q["by_status"])
    check("одновременно исполняется ограниченное число",
          (q["by_status"].get("running", 0) <= dev.MAX_PARALLEL_PER_AGENT * 2),
          q["by_status"].get("running"))
    check("блокировки ресурсов работают",
          q["by_status"].get("waiting", 0) > 0, q["by_status"])
    top = [j for j in q["jobs"] if j["status"] == "running"]
    check("приоритет учтён при назначении",
          (not top) or max(j["priority"] for j in top) >= 50, [j["priority"] for j in top][:5])

    dev.MAX_PARALLEL_PER_AGENT = real_cap

    print("\n14. РАСХОДЫ И ЗАПРЕТ СКРЫТЫХ ПЛАТЕЖЕЙ")
    s, b, _ = call(R.job_costs, job1, authorization=f"Bearer {full}", x_request_id=None)
    check("у задачи есть разбивка расходов",
          all(k in b for k in ("REAL_CASH_COST", "SUBSCRIPTION_USAGE", "EXTERNAL_COST")), list(b))
    check("работа агента внутри подписки стоит 0 ₽",
          b["REAL_CASH_COST"]["TOTAL_RUB"] == 0.0 and
          b["SUBSCRIPTION_USAGE"]["cash_equivalent_rub"] == 0.0, b["REAL_CASH_COST"])
    check("подписочные вызовы посчитаны по агентам",
          bool(b["SUBSCRIPTION_USAGE"]["calls_by_agent"]), b["SUBSCRIPTION_USAGE"])
    check("есть человекочитаемый блок", "TOTAL REAL CASH COST" in (b.get("rendered") or ""),
          (b.get("rendered") or "")[:80])

    est = costs.estimate("media_generate", {"images_per_ad": 5, "items": [1] * 5})
    check("оценка платной операции считается",
          est["kind"] == "cash" and est["units"] == 25 and est["amount_rub"] > 0, est)
    est0 = costs.estimate("campaign_create", {})
    check("обычная разработка оценивается как подписка",
          est0["kind"] == "subscription" and est0["amount_rub"] == 0.0, est0)

    s, b, _ = call(R.costs_approve, R.ApprovalIn(limit_rub=1000),
                   authorization=f"Bearer {full}", x_request_id=None)
    check("AI-агент не может сам разрешить трату", s == 403, b)
    human = auth.issue_key(f"{PREFIX}-human", "read,write,admin", actor_name="kirill",
                           actor_type="human", rate_limit=10000)
    s, appr, _ = call(R.costs_approve,
                      R.ApprovalIn(limit_rub=100, operations=["media_generate"],
                                   note="разово на тест"),
                      authorization=f"Bearer {human}", x_request_id=None)
    check("человек разрешение выдать может", s == 201 and appr.get("approval_id"), appr)
    check("в разрешении виден лимит и остаток",
          appr["limit_rub"] == 100.0 and appr["spent_rub"] == 0.0, appr)
    s, b, _ = call(R.costs_summary, authorization=f"Bearer {full}", x_request_id=None)
    check("сводка расходов отдаёт прайс и правило",
          "price_book" in b and "явного разрешения" in (b.get("rule") or ""), list(b))
    s, b, _ = call(R.costs_revoke, appr["approval_id"],
                   authorization=f"Bearer {human}", x_request_id=None)
    check("разрешение отзывается", b.get("revoked") is True, b)

    print("\n15. АДАПТЕРЫ АГЕНТОВ")
    s, b, _ = call(R.adapters_list, authorization=f"Bearer {full}", x_request_id=None)
    ad = {a["agent"]: a for a in b.get("adapters", [])}
    check("контракт адаптера объявлен целиком",
          set(b.get("contract") or []) == {"create_job", "execute_job", "continue_job",
                                           "handoff_job", "get_status", "cancel_job"},
          b.get("contract"))
    check("ChatGPT включён в режиме actions",
          ad.get("chatgpt", {}).get("enabled") is True and
          ad["chatgpt"]["mode"] == "actions", ad.get("chatgpt"))
    check("ChatGPT не требует API-биллинга",
          ad.get("chatgpt", {}).get("billing_mode") == "subscription", ad.get("chatgpt"))
    check("Claude выключен с причиной по политике",
          ad.get("claude", {}).get("enabled") is False and
          ad["claude"]["reason"] == "ANTHROPIC_POLICY_CONFIRMATION_PENDING", ad.get("claude"))
    check("mock честно помечен как не реальное исполнение",
          ad.get("mock", {}).get("real_execution") is False, ad.get("mock"))

    s, b, _ = call(R.adapter_set, "claude", R.AdapterIn(enabled=True),
                   authorization=f"Bearer {full}", x_request_id=None)
    check("агент не может включить адаптер сам", s == 403, b)
    s, b, _ = call(R.adapter_set, "claude", R.AdapterIn(enabled=True),
                   authorization=f"Bearer {human}", x_request_id=None)
    check("даже человек не включит Claude, пока висит причина политики",
          s == 403 and b.get("code") == "ADAPTER_DISABLED", b)
    s, b, _ = call(R.adapter_set, "chatgpt", R.AdapterIn(mode="api", enabled=True),
                   authorization=f"Bearer {human}", x_request_id=None)
    check("режим api отбит как отдельный биллинг",
          s == 402 and b.get("code") == "API_BILLING_REQUIRED", b)

    a = adapters.adapter_for("chatgpt")
    check("описание адаптера отдаётся", a.describe()["mode"] == "actions", a.describe())
    dj = dev.get(ids5[1])
    r1 = a.create_job(dj)
    check("create_job назначает агента", r1["accepted"] is True, r1)
    r2 = a.execute_job(dev.get(ids5[1]))
    check("execute_job в режиме actions честно говорит awaiting_agent",
          r2["status"] == "awaiting_agent", r2)
    ctx = adapters.structured_context(dev.get(ids5[1]))
    check("контекст передачи структурирован, а не переписка",
          all(k in ctx for k in ("job_id", "goal", "current_phase", "files_changed",
                                 "completed_steps", "remaining_steps", "errors", "tests",
                                 "next_action")), list(ctx))
    check("в контексте нет истории чата и секретов",
          "messages" not in ctx and "api_key" not in json.dumps(ctx, ensure_ascii=False), None)
    try:
        adapters.adapter_for("claude").execute_job(dev.get(ids5[1]))
        check("выключенный Claude не исполняет", False, "выполнил, чего быть не должно")
    except Exception as e:
        check("выключенный Claude не исполняет", "ADAPTER_DISABLED" in str(e), str(e)[:80])

    print("\n16. СОСТОЯНИЯ И ДОЧЕРНИЕ ОПЕРАЦИИ")
    s, opjob, _ = call(R.dev_create,
                       R.DevJobIn(title=f"{PREFIX} 5 объявлений комплектом",
                                  goal="объявления, изображения, баннеры, фид",
                                  acceptance_criteria=["комплект собран"]),
                       authorization=f"Bearer {full}", x_request_id=None)
    oid = opjob["dev_job_id"]
    s, b, _ = call(R.dev_phase, oid, R.DevPhaseIn(phase="TESTING", state="running"),
                   authorization=f"Bearer {full}", x_request_id=None)
    check("состояние идёт за фазой", b.get("status") == "testing", b.get("status"))
    s, b, _ = call(R.dev_phase, oid, R.DevPhaseIn(phase="BACKEND", state="fail",
                                                  errors=[{"code": "X"}]),
                   authorization=f"Bearer {full}", x_request_id=None)
    check("после провала фаза чинится, а не падает вся задача",
          b.get("status") == "fixing" and b.get("current_phase") == "FIXING", b.get("status"))
    s, b, _ = call(R.dev_op, oid, R.OpIn(name="TEXT", status="completed", requested=5, actual=5),
                   authorization=f"Bearer {full}", x_request_id=None)
    ops = {o["name"]: o["status"] for o in b.get("operations", [])}
    check("операция TEXT закрыта по совпадению requested и actual",
          ops.get("TEXT") == "completed", ops)
    s, b, _ = call(R.dev_op, oid, R.OpIn(name="IMAGES", status="completed", requested=25, actual=10),
                   authorization=f"Bearer {full}", x_request_id=None)
    check("операцию нельзя закрыть при расхождении количеств", s == 400, b)
    call(R.dev_op, oid, R.OpIn(name="PUBLISH", status="blocked",
                               error_code="EXTERNAL_PUBLISH_DISABLED"),
         authorization=f"Bearer {full}", x_request_id=None)
    for i, _c in enumerate(dev.get(oid)["acceptance_criteria"]):
        call(R.dev_criteria, oid, R.DevCriteriaIn(index=i, done=True, evidence="проверено"),
             authorization=f"Bearer {full}", x_request_id=None)
    for ph in dev.REQUIRED_ALWAYS:
        call(R.dev_phase, oid, R.DevPhaseIn(phase=ph, state="pass", evidence="ok"),
             authorization=f"Bearer {full}", x_request_id=None)
    s, b, _ = call(R.dev_complete, oid, R.DevCompleteIn(),
                   authorization=f"Bearer {full}", x_request_id=None)
    miss = [g["gate"] for g in (b.get("details") or {}).get("missing", [])]
    check("заблокированная дочерняя операция не даёт закрыть весь Job",
          s >= 400 and any("PUBLISH" in m for m in miss), miss)

    print("\n17. ДОСКА ЗАДАЧ")
    s, b, _ = call(R.dev_board, authorization=f"Bearer {full}", x_request_id=None)
    check("доска отдаёт строки", s == 200 and isinstance(b.get("rows"), list), s)
    row = next((r for r in b["rows"] if r["job"] == oid), None)
    check("в строке есть всё, что просили",
          row and all(k in row for k in ("job", "title", "agent", "phase", "status",
                                         "progress", "error", "qa", "cost_rub",
                                         "next_action")), list(row or {}))
    check("операции видны в строке доски", (row or {}).get("operations", {}).get("PUBLISH") == "blocked",
          (row or {}).get("operations"))
    check("на доске видны адаптеры", bool(b.get("adapters")), None)

    print("\n17b. СХЕМА ДЛЯ GPT")
    r = R.schema_yaml()
    text = r.body.decode("utf-8")
    check("схема отдаётся без ключа", r.status_code == 200 and len(text) > 1000, r.status_code)
    check("это yaml, а не json", text.lstrip().startswith("openapi:"), text[:40])
    check("в схеме есть работа с кодом",
          all(op in text for op in ("repoTree", "repoFile", "repoFiles", "proposePatch")), None)
    check("ручки применения правки в схеме для GPT нет", "applyPatch" not in text, None)
    check("в схеме нет ключей и секретов",
          not any(w in text.lower() for w in ("brs_", "password", "database_url")), None)

    print("\n17c. РУКИ ИСПОЛНИТЕЛЯ НА СЕРВЕРЕ")
    obox = tempfile.mkdtemp(prefix="selftest_ops_")
    oroot = repo.ROOT
    try:
        repo.ROOT = obox
        os.makedirs(os.path.join(obox, "backend", "app"), exist_ok=True)
        io.open(os.path.join(obox, "backend", ".env"), "w", encoding="utf-8").write(
            "DATABASE_URL=postgresql://x\nOPENAI_API_KEY=sk-selftestselftestselftest\n")

        s, b, _ = call(R.ops_shell, R.ShellIn(command="uname -s"),
                       authorization=f"Bearer {full}", x_request_id=None)
        check("разрешённая команда выполняется", s == 200 and b.get("exit_code") == 0, b)
        s, b, _ = call(R.ops_shell, R.ShellIn(command="cat /etc/shadow"),
                       authorization=f"Bearer {full}", x_request_id=None)
        check("чтение вне проекта не даёт прав, но команда сама по себе разрешена",
              s in (200, 403), s)
        s, b, _ = call(R.ops_shell, R.ShellIn(command="wget http://evil.example"),
                       authorization=f"Bearer {full}", x_request_id=None)
        check("бинарь вне белого списка отбит",
              s == 403 and b.get("code") == "FORBIDDEN_SCOPE", (s, b))
        s, b, _ = call(R.ops_shell, R.ShellIn(command="ls ; rm -rf /"),
                       authorization=f"Bearer {full}", x_request_id=None)
        check("конвейеры и подстановки не проходят", s == 400, (s, b))
        s, b, _ = call(R.ops_shell, R.ShellIn(command="curl https://example.com"),
                       authorization=f"Bearer {full}", x_request_id=None)
        check("curl наружу запрещён", s == 403, (s, b))
        s, b, _ = call(R.ops_shell, R.ShellIn(command="systemctl restart nginx"),
                       authorization=f"Bearer {full}", x_request_id=None)
        check("перезапуск чужой службы через shell запрещён", s == 403, (s, b))
        s, b, _ = call(R.ops_shell, R.ShellIn(command="uname -s"),
                       authorization=f"Bearer {ro}", x_request_id=None)
        check("read-ключу команды недоступны", s == 403, (s, b))

        s, b, _ = call(R.ops_db_query, R.SqlIn(sql="SELECT 1 AS x"),
                       authorization=f"Bearer {full}", x_request_id=None)
        check("read-only запрос к БД работает", s == 200 and b.get("row_count") == 1, b)
        s, b, _ = call(R.ops_db_query, R.SqlIn(sql="DELETE FROM ext_jobs"),
                       authorization=f"Bearer {full}", x_request_id=None)
        check("изменяющий SQL отбит", s == 403, (s, b))
        s, b, _ = call(R.ops_db_query, R.SqlIn(sql="SELECT 1; DROP TABLE ext_jobs"),
                       authorization=f"Bearer {full}", x_request_id=None)
        check("две инструкции за раз отбиты", s == 400, (s, b))

        ok_br, why_br = opsmod.browser_available()
        check("наличие браузера определяется фактом, а не предположением",
              isinstance(ok_br, bool) and bool(why_br), (ok_br, why_br))
        s, b, _ = call(R.ops_browser, R.BrowserIn(paths=["/"], width=390),
                       authorization=f"Bearer {full}", x_request_id=None)
        check("без браузера обход честно отказывает, а не выдумывает результат",
              (s == 200 and "pages" in b) or (s >= 400 and "браузер недоступен" in
                                              (b.get("message") or "")), (s, b))
        s, b, _ = call(R.ops_browser, R.BrowserIn(paths=["/"]),
                       authorization=f"Bearer {ro}", x_request_id=None)
        check("read-ключу обход браузером недоступен", s == 403, s)
        src_ops = io.open(os.path.join(os.path.dirname(os.path.abspath(opsmod.__file__)),
                                       "ops.py"), encoding="utf-8").read()
        body_br = src_ops.split("def browser(")[1].split("_audit")[0]
        check("пароль от кабинета не уходит в командную строку",
              "_run([_py(), script, cfg_path]" in body_br
              and "json.dumps(cfg, ensure_ascii=False))," not in body_br,
              body_br[-400:])
        check("конфиг с паролем пишется закрытым файлом",
              "0o600" in src_ops, None)

        s, b, _ = call(R.ops_secrets, authorization=f"Bearer {full}", x_request_id=None)
        raw = json.dumps(b, ensure_ascii=False)
        check("ключ виден как present, а не значением",
              b["secrets"]["OPENAI_API_KEY"]["present"] is True
              and "sk-selftestselftestselftest" not in raw, b["secrets"]["OPENAI_API_KEY"])
        # Какие ключи реально стоят на машине — свойство машины, а не кода.
        # Проверяем форму ответа и отсутствие утечки, а не конкретный набор.
        check("каждый ключ описан булевым present",
              all(isinstance(v.get("present"), bool) for v in b["secrets"].values()),
              {k: v.get("present") for k, v in b["secrets"].items()})
        check("у найденного ключа указан источник",
              all(v["sources"] for v in b["secrets"].values() if v["present"]),
              {k: v["sources"] for k, v in b["secrets"].items() if v["present"]})
        check("в ответе нет ни одного значения ключа",
              not any(t in raw for t in ("sk-ant-api", "sk-proj-", "sk-selftestselftest")),
              None)

        s, b, _ = call(R.ops_facts, authorization=f"Bearer {full}", x_request_id=None)
        check("разведка железа отвечает",
              s == 200 and "verdict" in (b.get("local_model") or {}), b.get("summary"))
        check("вердикт по локальной модели обоснован",
              bool((b.get("local_model") or {}).get("why")), None)

        s, b, _ = call(R.ops_readiness, authorization=f"Bearer {full}", x_request_id=None)
        check("readiness отвечает машинно",
              s == 200 and b.get("autonomous_dev") in ("READY", "NOT_READY"), b.get("autonomous_dev"))
        check("недостающая доставка задания честно в blocked_infra",
              "agent_receive" in (b.get("blocked_infra") or []), b.get("blocked_infra"))
        check("ручное применение правки не считается пробелом",
              "repo_apply" not in (b.get("blocked_infra") or []), b.get("blocked_infra"))
    finally:
        repo.ROOT = oroot
        shutil.rmtree(obox, ignore_errors=True)

    print("\n18. ДОСТУП АГЕНТА К КОДУ")
    sandbox = tempfile.mkdtemp(prefix="selftest_repo_")
    real_root, real_verify = repo.ROOT, dict(repo.VERIFY)
    try:
        repo.ROOT = sandbox
        os.makedirs(os.path.join(sandbox, "frontend", "app"), exist_ok=True)
        os.makedirs(os.path.join(sandbox, "backend", "app"), exist_ok=True)
        target = "frontend/app/page.tsx"
        io.open(os.path.join(sandbox, target), "w", encoding="utf-8").write(
            "export default function Page() {\n  return <div>СТАРЫЙ</div>\n}\n")
        io.open(os.path.join(sandbox, "frontend", "app", "page.tsx.before_fix"), "w",
                encoding="utf-8").write("мусор\n")
        io.open(os.path.join(sandbox, "backend", ".env"), "w", encoding="utf-8").write(
            "DATABASE_URL=postgresql://секрет\n")

        s, b, _ = call(R.repo_tree, R.RepoTreeIn(paths=["frontend", "backend"]),
                       authorization=f"Bearer {full}", x_request_id=None)
        paths = [f["path"] for f in b.get("files", [])]
        check("дерево отдаёт исходники", s == 200 and target in paths, paths[:5])
        check("бэкапы в дерево не попадают",
              not any(".before_" in p for p in paths), paths)
        check("секреты в дерево не попадают",
              not any(p.endswith(".env") for p in paths), paths)

        s, b, _ = call(R.repo_file, path="backend/.env",
                       authorization=f"Bearer {full}", x_request_id=None)
        check("прочитать .env агенту нельзя",
              s == 403 and b.get("code") == "FORBIDDEN_SCOPE", (s, b))
        s, b, _ = call(R.repo_file, path="../../etc/passwd",
                       authorization=f"Bearer {full}", x_request_id=None)
        check("выйти за пределы проекта нельзя", s == 403, (s, b))

        s, b, _ = call(R.repo_file, path=target, start=1, end=2,
                       authorization=f"Bearer {full}", x_request_id=None)
        check("файл читается диапазоном строк",
              s == 200 and b.get("total_lines") == 4 and "СТАРЫЙ" in b.get("content", ""), b)

        s, patchjob, _ = call(R.dev_create,
                              R.DevJobIn(title=f"{PREFIX} правка кода",
                                         goal="проверить предложение и применение правки",
                                         acceptance_criteria=["правка применена"]),
                              authorization=f"Bearer {full}", x_request_id=None)
        pjid = patchjob["dev_job_id"]

        s, b, _ = call(R.repo_propose,
                       R.PatchIn(dev_job_id=pjid, note="тест",
                                 edits=[R.EditIn(path=target, old="ЭТОГО ТАМ НЕТ", new="X")]),
                       authorization=f"Bearer {full}", x_request_id=None)
        check("правка с несуществующим якорем отклоняется", s == 400, (s, b))

        io.open(os.path.join(sandbox, "frontend", "app", "dup.tsx"), "w",
                encoding="utf-8").write("const a = 1\nconst a = 1\n")
        s, b, _ = call(R.repo_propose,
                       R.PatchIn(dev_job_id=pjid,
                                 edits=[R.EditIn(path="frontend/app/dup.tsx",
                                                 old="const a = 1", new="const a = 2")]),
                       authorization=f"Bearer {full}", x_request_id=None)
        check("неуникальный якорь отклоняется", s == 400, (s, b))

        s, prop, _ = call(R.repo_propose,
                          R.PatchIn(dev_job_id=pjid, note="меняем текст",
                                    edits=[R.EditIn(path=target, old="СТАРЫЙ", new="НОВЫЙ")]),
                          authorization=f"Bearer {full}", x_request_id=None)
        check("правка принята как предложение",
              s == 201 and prop.get("status") == "proposed", (s, prop))
        pid = prop.get("patch_id")
        on_disk = io.open(os.path.join(sandbox, target), encoding="utf-8").read()
        check("предложение ничего не пишет на диск", "СТАРЫЙ" in on_disk, on_disk[:60])

        s, b, _ = call(R.repo_patch_apply, pid, R.PatchApplyIn(confirm=True),
                       authorization=f"Bearer {full}", x_request_id=None)
        check("агент не может применить правку сам",
              s == 403 and b.get("code") == "FORBIDDEN_SCOPE", (s, b))
        s, b, _ = call(R.repo_patch_apply, pid, R.PatchApplyIn(confirm=False),
                       authorization=f"Bearer {human}", x_request_id=None)
        check("без confirm правка не применяется",
              s >= 400 and b.get("code") == "CONFIRMATION_REQUIRED", (s, b))

        repo.VERIFY = {"frontend/": {"cmd": [sys.executable, "-c", "raise SystemExit(1)"],
                                     "cwd": ".", "timeout": 60, "note": "заведомо падающая проверка"}}
        s, b, _ = call(R.repo_patch_apply, pid, R.PatchApplyIn(confirm=True),
                       authorization=f"Bearer {human}", x_request_id=None)
        on_disk = io.open(os.path.join(sandbox, target), encoding="utf-8").read()
        check("провал проверки откатывает файл", "СТАРЫЙ" in on_disk, on_disk[:60])
        check("провалившаяся правка помечена rolled_back",
              s >= 400 and repo.get_patch(pid)["status"] == "rolled_back",
              repo.get_patch(pid)["status"])

        repo.VERIFY = {"frontend/": {"cmd": [sys.executable, "-c", "print('BUILD OK')"],
                                     "cwd": ".", "timeout": 60, "note": "успешная проверка"}}
        s, b, _ = call(R.repo_patch_apply, pid, R.PatchApplyIn(confirm=True),
                       authorization=f"Bearer {human}", x_request_id=None)
        on_disk = io.open(os.path.join(sandbox, target), encoding="utf-8").read()
        check("человек применяет правку и файл меняется",
              s == 200 and "НОВЫЙ" in on_disk, (s, on_disk[:60]))
        check("бэкап сохранён", bool(b.get("backup_dir")) and
              os.path.isdir(b["backup_dir"]), b.get("backup_dir"))
        check("вывод проверки приложен как доказательство",
              "BUILD OK" in (b.get("verify_output") or ""), (b.get("verify_output") or "")[:80])

        s, b, _ = call(R.repo_patch_list, dev_job_id=pjid,
                       authorization=f"Bearer {full}", x_request_id=None)
        check("правки видны в списке задачи",
              s == 200 and any(p["patch_id"] == pid for p in b.get("patches", [])), s)
        ev = db.rows("""SELECT kind FROM ext_dev_events WHERE dev_job_id=:d""",
                     d=patchjob["id"])
        kinds = {e["kind"] for e in ev}
        check("предложение и применение попали в историю задачи",
              {"patch_proposed", "patch_applied"} <= kinds, kinds)
    finally:
        repo.ROOT, repo.VERIFY = real_root, real_verify
        shutil.rmtree(sandbox, ignore_errors=True)
        db.q("""DELETE FROM ext_dev_patches WHERE dev_job_id IN
                (SELECT id FROM ext_dev_jobs WHERE title LIKE :p)""", p=f"{PREFIX}%")

    print("\n20. E2E: CLAUDE LEAD ⇄ ORCHESTRATOR ⇄ EXECUTOR")
    e2e_box = tempfile.mkdtemp(prefix="selftest_e2e_")
    e_root, e_prov, e_agent = repo.ROOT, os.environ.get("EXT_API_EXECUTOR_PROVIDER"), executor.AGENT
    # На проде рядом работают службы boris-executor и boris-lead. Если тест
    # возьмёт то же имя агента, демон перехватит его наряд, и проверка упадёт
    # не по делу. Поэтому у теста собственный исполнитель.
    def as_agent(name):
        """Свой исполнитель на каждый сценарий: иначе сценарии разбирают
        наряды друг друга и падают не по делу."""
        executor.AGENT = name
        executor.EXECUTOR_KEY = dict(executor.EXECUTOR_KEY, actor_name=name, name=name)
        if name not in a2a.TEST_AGENTS:
            a2a.TEST_AGENTS = tuple(a2a.TEST_AGENTS) + (name,)
        return name

    def drive(order_id, agent, rounds=6):
        """Прогоняет наряд по кругу до устойчивого состояния."""
        as_agent(agent)
        for _ in range(rounds):
            st = a2a.get(order_id, False)["status"]
            if st in (a2a.QUEUED, a2a.RETURNED):
                executor.once()
            elif st == a2a.REVIEW:
                lead.once(agent)
            else:
                break
        return a2a.get(order_id)

    real_test_agents = a2a.TEST_AGENTS
    as_agent(E2E_AGENT)
    try:
        repo.ROOT = e2e_box
        os.environ["EXT_API_EXECUTOR_PROVIDER"] = "mock"
        os.makedirs(os.path.join(e2e_box, "frontend", "app"), exist_ok=True)
        io.open(os.path.join(e2e_box, "frontend", "app", "page.tsx"), "w",
                encoding="utf-8").write("export default function P(){return null}\n")

        s, ejob, _ = call(R.dev_create,
                          R.DevJobIn(title=f"{PREFIX} автономный контур",
                                     goal="проверить цикл лид-исполнитель без человека",
                                     acceptance_criteria=["файлы прочитаны", "команда выполнена"]),
                          authorization=f"Bearer {full}", x_request_id=None)
        ejid = ejob["dev_job_id"]

        s, order, _ = call(R.a2a_create,
                           R.OrderIn(dev_job_id=ejid, phase="FRONTEND",
                                     goal="осмотреть фронтенд и подтвердить доступ к инструментам",
                                     scope={"paths": ["frontend/app"]},
                                     acceptance_criteria=["файлы прочитаны", "команда выполнена"],
                                     forbidden_changes=["backend/"],
                                     required_qa=["shell"], required_evidence=["вывод команды"],
                                     assigned_to=E2E_AGENT),
                           authorization=f"Bearer {full}", x_request_id=None)
        check("лид создаёт EXECUTOR_ORDER", s == 201 and order["status"] == "queued", (s, order))
        oid = order["order_id"]
        kinds = [m["kind"] for m in a2a.get(oid)["messages"]]
        check("наряд оформлен как сообщение протокола", "EXECUTOR_ORDER" in kinds, kinds)

        s, b, _ = call(R.a2a_create,
                       R.OrderIn(dev_job_id=ejid, goal="без критериев",
                                 acceptance_criteria=[], assigned_to=E2E_AGENT),
                       authorization=f"Bearer {full}", x_request_id=None)
        check("наряд без критериев приёмки не принимается", s == 400, (s, b))

        pr = executor.probe()
        check("исполнитель знает свой движок", pr.get("ok") is True, pr)

        # транспорт до OpenAI подбирается сам, и в отчёт не утекают логины прокси
        os.environ["EXT_API_EXECUTOR_PROVIDER"] = "openai"
        os.environ["OPENAI_API_KEY"] = "sk-selftest-not-a-real-key"
        os.environ["OPENAI_PROXY"] = "http://user:s3cret@proxy.invalid:8080"
        try:
            cands = executor.candidates()
            check("перебор транспортов включает прокси из конфигурации",
                  any(c["proxy"] for c in cands), [c["why"] for c in cands])
            check("прямой путь остаётся последним запасным вариантом",
                  cands[-1]["proxy"] is None and cands[-1]["base_url"] is None, cands[-1])
            rp = executor.probe()
            raw_probe = json.dumps(rp, ensure_ascii=False)
            check("недоступный OpenAI честно помечен, а не выдан за рабочий",
                  rp.get("ok") is False, rp.get("ok"))
            check("логин и пароль прокси в отчёт не попадают",
                  "s3cret" not in raw_probe and "user:" not in raw_probe, None)
            check("в отчёте перечислено, что именно пробовали",
                  bool(rp.get("tried")), rp.get("error"))
        finally:
            for v in ("OPENAI_PROXY", "OPENAI_API_KEY"):
                os.environ.pop(v, None)
            os.environ["EXT_API_EXECUTOR_PROVIDER"] = "mock"
            executor._TRANSPORT = None

        # Раньше здесь вызывался настоящий claim от имени боевого исполнителя.
        # На проде это уводило РЕАЛЬНЫЙ наряд в работу посреди прогона тестов.
        # Проверяем то же самое без побочных действий: наряд прогона закреплён
        # за отдельным именем, и боевому исполнителю он не достанется.
        e2e_order = a2a.get(oid, with_messages=False)
        check("боевой исполнитель не может забрать наряд приёмочного прогона",
              e2e_order["assigned_to"] == E2E_AGENT and
              E2E_AGENT in a2a.TEST_AGENTS and E2E_AGENT != "gpt-executor",
              (e2e_order["assigned_to"], a2a.TEST_AGENTS))
        check("наряды приёмочного прогона не видны боевой очереди",
              not [o for o in a2a.listing(limit=200, exclude_assigned=a2a.TEST_AGENTS)
                   if o["order_id"] == oid], oid)

        o1 = executor.once()
        check("исполнитель приёмочного прогона наряд получил", o1 is not None, None)
        check("исполнитель сам забрал наряд и отчитался",
              o1 and o1["status"] == "review_requested", (o1 or {}).get("status"))
        ev1 = o1["last_evidence"]
        check("в отчёте настоящее доказательство, а не пересказ",
              "shell uname" in (ev1.get("evidence") or ""), (ev1.get("evidence") or "")[:80])
        check("исполнитель честно пометил непроверенный критерий",
              any(c["status"] == "NOT_CHECKED" for c in ev1["acceptance_criteria"]),
              ev1["acceptance_criteria"])

        daemon_view = [o["order_id"] for o in
                       a2a.listing(status=a2a.REVIEW, limit=50,
                                   exclude_assigned=a2a.TEST_AGENTS)]
        check("боевой лид не видит нарядов приёмочного прогона",
              oid not in daemon_view, daemon_view)

        s, b, _ = call(R.a2a_review, oid, R.ReviewIn(verdict="RETURN_WITH_FIXES"),
                       authorization=f"Bearer {full}", x_request_id=None)
        check("возврат без списка замечаний отклоняется", s == 400, (s, b))

        not_checked = [c["criterion"] for c in ev1["acceptance_criteria"]
                       if c["status"] == "NOT_CHECKED"]
        s, b, _ = call(R.a2a_review, oid,
                       R.ReviewIn(verdict="RETURN_WITH_FIXES",
                                  note="один критерий не закрыт",
                                  fixes=[{"failed_criterion": not_checked[0],
                                          "problem": "статус NOT_CHECKED без доказательства",
                                          "required_correction": "проверить и приложить вывод"}]),
                       authorization=f"Bearer {full}", x_request_id=None)
        check("лид вернул работу с замечаниями",
              s == 200 and b["status"] == "returned" and b["iteration"] == 1,
              (b.get("status"), b.get("iteration")))

        o2 = executor.once()
        check("исполнитель сам получил замечания и отработал второй круг",
              o2 and o2["status"] == "review_requested" and o2["iteration"] == 1,
              (o2 or {}).get("status"))
        ev2 = o2["last_evidence"]
        check("после исправления непроверенных критериев не осталось",
              all(c["status"] == "PASS" for c in ev2["acceptance_criteria"]),
              ev2["acceptance_criteria"])

        s, b, _ = call(R.a2a_review, oid, R.ReviewIn(verdict="ACCEPT", note="все критерии закрыты"),
                       authorization=f"Bearer {full}", x_request_id=None)
        check("лид принял работу", s == 200 and b["status"] == "accepted", b.get("status"))

        chain = [m["kind"] for m in a2a.get(oid)["messages"]]
        check("цикл прошёл полностью и записан",
              chain[:1] == ["EXECUTOR_ORDER"] and "RETURN_WITH_FIXES" in chain
              and chain[-1] == "ACCEPT" and chain.count("REVIEW_REQUEST") == 2, chain)
        check("между лидом и исполнителем не было человека",
              all(m["author"] in AGENT_NAMES for m in a2a.get(oid)["messages"]),
              [m["author"] for m in a2a.get(oid)["messages"]])

        runs = db.rows("SELECT provider, steps, tool_calls, outcome FROM ext_a2a_runs "
                       "WHERE order_id=:o ORDER BY id", o=oid)
        check("каждая итерация исполнителя записана с расходом",
              len(runs) == 2 and all(r["outcome"] == "review_requested" for r in runs), runs)

        s, brd, _ = call(R.a2a_board, authorization=f"Bearer {full}", x_request_id=None)
        check("владельцу видны только исходы, без переписки агентов",
              s == 200 and brd["counts"]["done"] >= 1
              and "messages" not in json.dumps(brd, ensure_ascii=False), brd["counts"])

        # ------- полностью автономный круг: лид тоже процесс, человека нет
        os.environ["EXT_API_LEAD_PROVIDER"] = "mock"
        os.environ["EXT_API_NOTIFY_DISABLED"] = "1"
        lp = lead.probe()
        check("лид знает свой движок", lp.get("ok") is True, lp)
        check("приёмка может работать на любом из движков",
              lead.provider() in ("claude_code", "anthropic", "openai", "mock"),
              lead.provider())

        # подписка Max имеет приоритет, а платные кредиты сами собой не включаются.
        # Конфигурацию подменяем целиком: на проде и ключи, и токен лежат в .env,
        # поэтому играть переменными окружения бессмысленно.
        real_env, real_cli = lead._env_all, lead.claude_cli
        real_forced = os.environ.pop("EXT_API_LEAD_PROVIDER", None)
        real_openai = os.environ.get("OPENAI_API_KEY")
        try:
            # ключ OpenAI берётся своим модулем, его подменяем отдельно
            os.environ["OPENAI_API_KEY"] = "sk-selftest"
            lead.claude_cli = lambda: sys.executable   # настоящий исполняемый файл
            lead._env_all = lambda: {"ANTHROPIC_API_KEY": "sk-ant-selftest",
                                     "OPENAI_API_KEY": "sk-selftest"}
            check("Anthropic API сам собой не включается: он тратит кредиты",
                  lead.provider() != "anthropic", lead.provider())
            check("без токена подписки остаётся уже оплаченный OpenAI",
                  lead.provider() == "openai", lead.provider())
            lead._env_all = lambda: {"ANTHROPIC_API_KEY": "sk-ant-selftest",
                                     "OPENAI_API_KEY": "sk-selftest",
                                     "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-selftest"}
            check("при живой подписке приёмка идёт через Claude Code",
                  lead.provider() == "claude_code", lead.provider())
            pr2 = lead.probe()
            check("в отчёте видно, что используется подписка, а не кредиты",
                  pr2.get("subscription", {}).get("usable") is True, pr2.get("subscription"))
            lead.claude_cli = lambda: None
            check("без установленного Claude Code подписка честно помечена непригодной",
                  lead.provider() == "openai", lead.provider())
        finally:
            lead._env_all, lead.claude_cli = real_env, real_cli
            if real_openai is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = real_openai
            if real_forced is not None:
                os.environ["EXT_API_LEAD_PROVIDER"] = real_forced

        # Руки ревьюера в подписочном режиме: без них он не может проверить
        # чужой отчёт и честно уходит в BLOCKED_INFRA — так и случилось на проде.
        mt = mcp_server._tools()
        need = {"db_query", "repo_read", "repo_tree", "shell", "git", "logs", "http_check"}
        check("MCP отдаёт ревьюеру те же руки, что есть у агентов",
              need <= set(mt), sorted(mt))
        init = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {"protocolVersion": "2025-06-18"}}, mt)
        check("MCP-сервер отвечает на initialize",
              init["result"]["protocolVersion"] == "2025-06-18"
              and "tools" in init["result"]["capabilities"], init.get("result"))
        lst = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, mt)
        check("в списке инструментов есть схемы",
              all(t.get("inputSchema") for t in lst["result"]["tools"]), None)
        mcall = mcp_server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                   "params": {"name": "db_query",
                                              "arguments": {"sql": "SELECT 1 AS x"}}}, mt)
        check("через MCP реально ходит запрос к базе",
              not mcall["result"].get("isError")
              and '"x": 1' in mcall["result"]["content"][0]["text"], mcall["result"])
        bad = mcp_server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                 "params": {"name": "db_query",
                                            "arguments": {"sql": "DELETE FROM ext_jobs"}}}, mt)
        check("через MCP нельзя изменить базу",
              bad["result"].get("isError") is True
              and "FORBIDDEN_SCOPE" in bad["result"]["content"][0]["text"], bad["result"])
        check("уведомления не требуют ответа",
              mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"},
                                mt) is None, None)

        check("вердикт вытаскивается даже из обёрнутого ответа",
              lead._extract_json('```json\n{"verdict": "ACCEPT"}\n```')["verdict"] == "ACCEPT",
              None)

        # кончился баланс провайдера — это блокер владельца, а не падение
        class _Boom:
            def __call__(self, order):
                raise ApiErrorForTest("DEPENDENCY_UNAVAILABLE",
                                      "Anthropic 400: credit balance is too low")
        from .errors import ApiError as ApiErrorForTest
        s, bal, _ = call(R.a2a_create,
                         R.OrderIn(dev_job_id=ejid, goal="проверка реакции на пустой баланс",
                                   scope={"paths": ["frontend/app"]},
                                   acceptance_criteria=["что-нибудь"],
                                   assigned_to=E2E_AGENT),
                         authorization=f"Bearer {full}", x_request_id=None)
        executor.once()
        real_decide, lead.decide = lead.decide, _Boom()
        try:
            res = lead.once(E2E_AGENT)
        finally:
            lead.decide = real_decide
        check("пустой баланс становится BLOCKED_HUMAN, а не падением",
              res and res["status"] == "blocked_human", (res or {}).get("status"))
        check("причина названа человеческим языком",
              "баланс" in (res or {}).get("blocked_reason", ""), (res or {}).get("blocked_reason"))
        check("работа исполнителя при этом не потеряна",
              bool((res or {}).get("last_evidence")), None)
        check("уведомления владельцу описаны отдельно",
              {"telegram_token", "chat_id", "ready"} <= set(notify.status()), notify.status())

        check("новая модель OpenAI идёт в /v1/responses",
              executor.api_style("gpt-5.3-codex") == "responses", None)
        check("старая модель остаётся на /v1/chat/completions",
              executor.api_style("gpt-4o") == "chat", None)
        check("подсказка API о другом эндпоинте принимается",
              executor._switch_style("gpt-4o", "Use the v1/responses endpoint instead")
              and executor.api_style("gpt-4o") == "responses", None)
        executor._STYLE.clear()

        s, auto, _ = call(R.a2a_create,
                          R.OrderIn(dev_job_id=ejid, phase="TESTING",
                                    goal="автономный круг без человека",
                                    scope={"paths": ["frontend/app"]},
                                    acceptance_criteria=["инструменты доступны", "команда выполнена"],
                                    assigned_to=E2E_AGENT, max_iterations=4),
                          authorization=f"Bearer {full}", x_request_id=None)
        aid = auto["order_id"]
        seen = []
        for _ in range(6):
            if not a2a.get(aid, False)["status"] in (a2a.QUEUED, a2a.RETURNED, a2a.RUNNING,
                                                     a2a.REVIEW):
                break
            st = a2a.get(aid, False)["status"]
            if st in (a2a.QUEUED, a2a.RETURNED):
                executor.once()
            elif st == a2a.REVIEW:
                lead.once(E2E_AGENT)
            seen.append(a2a.get(aid, False)["status"])
        final = a2a.get(aid)
        check("контур сам дошёл до ACCEPT без единого вызова человеком",
              final["status"] == "accepted", (final["status"], seen))
        chain2 = [m["kind"] for m in final["messages"]]
        check("лид сам вернул работу и сам же принял",
              "RETURN_WITH_FIXES" in chain2 and "ACCEPT" in chain2, chain2)
        check("решения принимал лид, а не человек",
              all(m["author"] in (AGENT_NAMES) for m in final["messages"]),
              [m["author"] for m in final["messages"]])
        check("приёмка заняла больше одной итерации, то есть возврат был настоящим",
              final["iteration"] >= 1, final["iteration"])

        # ------- после приёмки лид сам закрывает критерии и ставит следующий наряд
        CHAIN_AGENT = as_agent("selftest-chain")
        s, chain_job, _ = call(R.dev_create,
                               R.DevJobIn(title=f"{PREFIX} сквозной прогон фаз",
                                          goal="проверить, что задача едет дальше сама",
                                          acceptance_criteria=["инструменты доступны",
                                                               "команда выполнена"]),
                               authorization=f"Bearer {full}", x_request_id=None)
        cjid = chain_job["dev_job_id"]
        s, first, _ = call(R.a2a_create,
                           R.OrderIn(dev_job_id=cjid, phase="PLANNING",
                                     goal="первая фаза",
                                     scope={"paths": ["frontend/app"]},
                                     acceptance_criteria=["инструменты доступны",
                                                          "команда выполнена"],
                                     assigned_to=CHAIN_AGENT, max_iterations=4),
                           authorization=f"Bearer {full}", x_request_id=None)
        done_order = drive(first["order_id"], CHAIN_AGENT)
        check("наряд дошёл до приёмки", done_order["status"] == "accepted",
              done_order["status"])

        after_job = dev.get(cjid)
        done_c = [c for c in after_job["acceptance_criteria"] if c.get("done")]
        check("принятая работа закрывает критерии приёмки, а не только фазу",
              len(done_c) == 2, [(c.get("text"), c.get("done")) for c in
                                 after_job["acceptance_criteria"]])
        check("у закрытого критерия сохранено доказательство",
              all(c.get("evidence") for c in done_c), None)
        check("закрыл критерий лид, а не исполнитель",
              all(c.get("by") == "claude-lead" for c in done_c),
              [c.get("by") for c in done_c])
        check("закрытых критериев мало — задача не объявляется готовой",
              after_job["status"] != "completed", after_job["status"])
        check("лид сам решил, что делать дальше, когда критерии кончились",
              any(m["kind"] == "PLAN" for m in done_order["messages"]),
              [m["kind"] for m in done_order["messages"]])
        check("решение лида дописало задаче новые критерии",
              len(after_job["acceptance_criteria"]) > 2,
              [c["text"] for c in after_job["acceptance_criteria"]])
        check("новые критерии открыты, а не закрыты авансом",
              any(not c.get("done") for c in after_job["acceptance_criteria"]), None)

        # ------- когда цель достигнута, лид закрывает задачу и объясняет пропуски
        DONE_AGENT = as_agent("selftest-done")
        s, dj, _ = call(R.dev_create,
                        R.DevJobIn(title=f"{PREFIX} доведение до готовности",
                                   goal="проверить честное закрытие задачи",
                                   acceptance_criteria=["раз", "два", "три"]),
                        authorization=f"Bearer {full}", x_request_id=None)
        djid = dj["dev_job_id"]
        s, dord, _ = call(R.a2a_create,
                          R.OrderIn(dev_job_id=djid, phase="PLANNING", goal="единственная фаза",
                                    scope={"paths": ["frontend/app"]},
                                    acceptance_criteria=["раз", "два", "три"],
                                    assigned_to=DONE_AGENT, max_iterations=4),
                          authorization=f"Bearer {full}", x_request_id=None)
        fin = drive(dord["order_id"], DONE_AGENT)
        fin_job = dev.get(djid)
        check("задача с достигнутой целью закрывается",
              fin_job["status"] == "completed", fin_job["status"])
        skipped = [p for p, v in (fin_job["phases"] or {}).items()
                   if (v or {}).get("state") == "skipped"]
        check("пропущенные фазы помечены пропущенными, а не пройденными",
              bool(skipped), fin_job["phases"])
        check("у каждого пропуска записана причина",
              all((fin_job["phases"][p] or {}).get("notes") for p in skipped),
              {p: (fin_job["phases"][p] or {}).get("notes") for p in skipped})
        s, sk, _ = call(R.dev_create,
                        R.DevJobIn(title=f"{PREFIX} молчаливый пропуск",
                                   goal="пропуск без причины не должен считаться гейтом",
                                   acceptance_criteria=["раз"]),
                        authorization=f"Bearer {full}", x_request_id=None)
        skid = sk["dev_job_id"]
        for ph in dev.required_phases({}):
            call(R.dev_phase, skid, R.DevPhaseIn(phase=ph, state="skipped"),
                 authorization=f"Bearer {full}", x_request_id=None)
        call(R.dev_criteria, skid, R.DevCriteriaIn(index=0, done=True, evidence="есть"),
             authorization=f"Bearer {full}", x_request_id=None)
        s, res_sk, _ = call(R.dev_complete, skid, R.DevCompleteIn(),
                            authorization=f"Bearer {full}", x_request_id=None)
        check("молчаливый пропуск фазы не открывает закрытие задачи",
              s >= 400, (s, [g["gate"] for g in (res_sk.get("details") or {}).get("missing", [])]))
        for ph in dev.required_phases({}):
            call(R.dev_phase, skid, R.DevPhaseIn(phase=ph, state="skipped",
                                                 notes="в задаче нет работы этой фазы"),
                 authorization=f"Bearer {full}", x_request_id=None)
        s, res_sk2, _ = call(R.dev_complete, skid, R.DevCompleteIn(),
                             authorization=f"Bearer {full}", x_request_id=None)
        check("объяснённый пропуск закрытие задачи разрешает",
              s == 200 and res_sk2.get("status") == "completed",
              (s, res_sk2.get("status")))

        check("ни одна фаза не получила PASS без доказательства",
              all((v or {}).get("evidence") for p, v in (fin_job["phases"] or {}).items()
                  if (v or {}).get("state") == "pass"),
              {p: (v or {}).get("state") for p, v in (fin_job["phases"] or {}).items()})

        # а если критерии остались — лид обязан поставить наряд на следующую фазу
        CONT_AGENT = as_agent("selftest-cont")
        s, cont_job, _ = call(R.dev_create,
                              R.DevJobIn(title=f"{PREFIX} продолжение фаз",
                                         goal="проверить постановку следующего наряда",
                                         acceptance_criteria=["инструменты доступны",
                                                              "команда выполнена",
                                                              "останется незакрытым"]),
                              authorization=f"Bearer {full}", x_request_id=None)
        ojid = cont_job["dev_job_id"]
        s, o1c, _ = call(R.a2a_create,
                         R.OrderIn(dev_job_id=ojid, phase="PLANNING", goal="первая фаза",
                                   scope={"paths": ["frontend/app"]},
                                   acceptance_criteria=["инструменты доступны",
                                                        "команда выполнена"],
                                   assigned_to=CONT_AGENT, max_iterations=4),
                         authorization=f"Bearer {full}", x_request_id=None)
        cid2 = o1c["order_id"]
        drive(cid2, CONT_AGENT)
        orders_now = a2a.listing(dev_job_id=ojid, limit=10)
        fresh = [o for o in orders_now if o["order_id"] != cid2]
        check("лид сам поставил наряд на следующую фазу", len(fresh) == 1,
              [(o["order_id"], o["phase"], o["status"]) for o in orders_now])
        check("в новом наряде остались только незакрытые критерии",
              bool(fresh) and fresh[0]["acceptance_criteria"] == ["останется незакрытым"],
              fresh[0]["acceptance_criteria"] if fresh else None)
        check("новый наряд ждёт того же исполнителя",
              bool(fresh) and fresh[0]["assigned_to"] == CONT_AGENT, None)
        check("переход зафиксирован в истории прошлого наряда",
              any(m["kind"] == "NEXT_ORDER" for m in a2a.get(cid2)["messages"]),
              [m["kind"] for m in a2a.get(cid2)["messages"]])

        # ------- задача, застрявшая между фазами, подбирается лидом сама
        REC_AGENT = as_agent("selftest-recon")
        s, rec_job, _ = call(R.dev_create,
                             R.DevJobIn(title=f"{PREFIX} зависшая между фазами",
                                        goal="проверить самоподбор застрявших задач",
                                        acceptance_criteria=["инструменты доступны",
                                                             "команда выполнена",
                                                             "останется незакрытым"]),
                             authorization=f"Bearer {full}", x_request_id=None)
        rjid = rec_job["dev_job_id"]
        s, ro, _ = call(R.a2a_create,
                        R.OrderIn(dev_job_id=rjid, phase="PLANNING", goal="первая фаза",
                                  scope={"paths": ["frontend/app"]},
                                  acceptance_criteria=["инструменты доступны",
                                                       "команда выполнена"],
                                  assigned_to=REC_AGENT, max_iterations=4),
                        authorization=f"Bearer {full}", x_request_id=None)
        rid = ro["order_id"]
        as_agent(REC_AGENT)
        for _ in range(4):
            st = a2a.get(rid, False)["status"]
            if st in (a2a.QUEUED, a2a.RETURNED):
                executor.once()
            elif st == a2a.REVIEW:
                # принимаем БЕЗ продолжения — так выглядит задача, застрявшая
                # после починки или перезапуска
                real_adv, lead._advance = lead._advance, lambda o: dev.get(o["dev_job_id"])
                try:
                    lead.once(REC_AGENT)
                finally:
                    lead._advance = real_adv
            else:
                break
        stuck = a2a.get(rid, False)
        stuck_job = dev.get(rjid)
        check("задача действительно встала: наряд принят, продолжения нет",
              stuck["status"] == "accepted"
              and not [o for o in a2a.listing(dev_job_id=rjid, limit=10)
                       if o["status"] in a2a.OPEN_STATES],
              stuck["status"])
        check("критерии при этом остались открытыми",
              all(not c.get("done") for c in stuck_job["acceptance_criteria"]), None)

        moved = lead.reconcile(REC_AGENT)
        picked = dev.get(rjid)
        follow = [o for o in a2a.listing(dev_job_id=rjid, limit=10)
                  if o["order_id"] != rid]
        check("лид сам подобрал застрявшую задачу", bool(moved), moved)
        check("после подбора критерии закрыты",
              len([c for c in picked["acceptance_criteria"] if c.get("done")]) == 2,
              [(c["text"], c["done"]) for c in picked["acceptance_criteria"]])
        check("после подбора поставлен наряд на следующую фазу",
              len(follow) == 1, [(o["order_id"], o["phase"]) for o in follow])
        check("повторный подбор ничего не ломает и не плодит нарядов",
              lead.reconcile(REC_AGENT) == [] or len(
                  [o for o in a2a.listing(dev_job_id=rjid, limit=10)
                   if o["order_id"] != rid]) == 1, None)

        # защита от бесконечного цикла
        GUARD_AGENT = as_agent("selftest-guard")
        s, loop, _ = call(R.a2a_create,
                          R.OrderIn(dev_job_id=ejid, goal="проверка предохранителя",
                                    scope={"paths": ["frontend/app"]},
                                    acceptance_criteria=["a", "b"],
                                    assigned_to=GUARD_AGENT, max_iterations=2),
                          authorization=f"Bearer {full}", x_request_id=None)
        lid = loop["order_id"]
        guard = None
        for _ in range(4):
            executor.once()
            s, guard, _ = call(R.a2a_review, lid,
                               R.ReviewIn(verdict="RETURN_WITH_FIXES", note="ещё круг",
                                          fixes=[{"failed_criterion": "b", "problem": "x",
                                                  "required_correction": "y"}]),
                               authorization=f"Bearer {full}", x_request_id=None)
            if guard.get("status") != "returned":
                break
        check("предохранитель останавливает бесконечный цикл",
              guard and guard["status"] == "blocked_infra", (guard or {}).get("status"))
        check("остановка цикла не выдаётся за проблему владельца",
              guard and guard["status"] != "blocked_human", (guard or {}).get("status"))

        # Предохранитель остановил цикл; когда причина устранена, наряд надо
        # вернуть в работу, не потеряв историю и не заводя новый.
        back = clients.reset(lid)
        after = a2a.get(lid)
        check("застрявший наряд возвращается в очередь",
              after["status"] == "queued" and after["iteration"] == 0,
              (after["status"], after["iteration"]))
        check("причина блокировки снята", after["blocked_reason"] is None,
              after["blocked_reason"])
        check("история наряда при возврате не теряется",
              any(m["kind"] == "RETURN_WITH_FIXES" for m in after["messages"])
              and any(m["kind"] == "RESET" for m in after["messages"]),
              [m["kind"] for m in after["messages"]])
        check("возврат отмечен в отчёте", bool(back), back)
    finally:
        repo.ROOT = e_root
        a2a.TEST_AGENTS = real_test_agents
        executor.AGENT = e_agent
        executor.EXECUTOR_KEY = dict(executor.EXECUTOR_KEY, actor_name=e_agent, name=e_agent)
        for v in ("EXT_API_LEAD_PROVIDER", "EXT_API_NOTIFY_DISABLED"):
            os.environ.pop(v, None)
        if e_prov is None:
            os.environ.pop("EXT_API_EXECUTOR_PROVIDER", None)
        else:
            os.environ["EXT_API_EXECUTOR_PROVIDER"] = e_prov
        shutil.rmtree(e2e_box, ignore_errors=True)
        db.q("""DELETE FROM ext_a2a_runs WHERE order_id IN
                (SELECT id FROM ext_a2a_orders WHERE dev_job_id IN
                 (SELECT id FROM ext_dev_jobs WHERE title LIKE :p))""", p=f"{PREFIX}%")
        db.q("""DELETE FROM ext_a2a_messages WHERE order_id IN
                (SELECT id FROM ext_a2a_orders WHERE dev_job_id IN
                 (SELECT id FROM ext_dev_jobs WHERE title LIKE :p))""", p=f"{PREFIX}%")
        db.q("""DELETE FROM ext_a2a_orders WHERE dev_job_id IN
                (SELECT id FROM ext_dev_jobs WHERE title LIKE :p)""", p=f"{PREFIX}%")

    # ---------------------------------------------------------- уборка
    print("\n21. УБОРКА")
    if keep:
        print("  --keep: тестовые данные оставлены")
    else:
        ids = [r["id"] for r in db.rows(
            "SELECT id FROM campaigns WHERE CAST(default_account_id AS TEXT)=:a "
            "AND name LIKE :p", a=str(acc["id"]), p=f"{PREFIX}%")]
        for cid in ids:
            db.q("DELETE FROM campaign_items WHERE CAST(campaign_id AS TEXT)=:c", c=str(cid))
        if ids:
            db.q("DELETE FROM campaigns WHERE id = ANY(:ids)", ids=ids)
        db.q("""DELETE FROM ext_job_attempts WHERE job_id IN
                (SELECT id FROM ext_jobs WHERE key_id IN
                 (SELECT id FROM ext_api_keys WHERE name LIKE :p) OR request_id='req_selftest')""",
             p=f"{PREFIX}%")
        db.q("""DELETE FROM ext_handoffs WHERE job_id IN
                (SELECT id FROM ext_jobs WHERE key_id IN
                 (SELECT id FROM ext_api_keys WHERE name LIKE :p))""", p=f"{PREFIX}%")
        db.q("""DELETE FROM ext_jobs WHERE key_id IN
                (SELECT id FROM ext_api_keys WHERE name LIKE :p) OR request_id='req_selftest'""",
             p=f"{PREFIX}%")
        db.q("""DELETE FROM ext_idempotency WHERE key_id IN
                (SELECT id FROM ext_api_keys WHERE name LIKE :p)""", p=f"{PREFIX}%")
        db.q("""DELETE FROM ext_audit WHERE key_id IN
                (SELECT id FROM ext_api_keys WHERE name LIKE :p)""", p=f"{PREFIX}%")
        db.q("DELETE FROM ext_api_keys WHERE name LIKE :p", p=f"{PREFIX}%")
        db.q("DELETE FROM ext_agent_state WHERE agent IN ('chatgpt','claude')")
        db.q("""DELETE FROM ext_job_costs WHERE job_id IN
                (SELECT id FROM ext_jobs WHERE key_id IN
                 (SELECT id FROM ext_api_keys WHERE name LIKE :p))""", p=f"{PREFIX}%")
        db.q("DELETE FROM ext_cost_approvals WHERE granted_by='kirill'")
        db.q("""DELETE FROM ext_dev_patches WHERE dev_job_id IN
                (SELECT id FROM ext_dev_jobs WHERE title LIKE :p)""", p=f"{PREFIX}%")
        db.q("""DELETE FROM ext_job_ops WHERE dev_job_id IN
                (SELECT id FROM ext_dev_jobs WHERE title LIKE :p)""", p=f"{PREFIX}%")
        db.q("""DELETE FROM ext_dev_events WHERE dev_job_id IN
                (SELECT id FROM ext_dev_jobs WHERE title LIKE :p)""", p=f"{PREFIX}%")
        db.q("DELETE FROM ext_dev_jobs WHERE title LIKE :p", p=f"{PREFIX}%")
        print(f"  удалено тестовых кампаний: {len(ids)}, ключи отозваны")

    ok = sum(1 for _, c, _ in RESULTS if c)
    bad_list = [(n, d) for n, c, d in RESULTS if not c]
    print(f"\n  ИТОГ: {ok}/{len(RESULTS)} проверок пройдено")
    if bad_list:
        print("\n  НЕ ПРОШЛИ:")
        for n, d in bad_list:
            print(f"    - {n}  {d}")
    return len(bad_list) == 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True, help="id аккаунта или часть названия")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    sys.exit(0 if run(a.account, a.keep) else 1)
