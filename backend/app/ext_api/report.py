
# -*- coding: utf-8 -*-
"""Отчёт владельцу человеческим языком.

Ни статусов, ни кодов, ни имён таблиц: что сделано, что делается, что встало
и что нужно лично от вас. Один и тот же текст идёт в терминал и в Telegram.

    venv/bin/python3 -m app.ext_api.report            # за сутки, в терминал
    venv/bin/python3 -m app.ext_api.report --send     # то же самое в Telegram
    venv/bin/python3 -m app.ext_api.report --hours 6
    venv/bin/python3 -m app.ext_api.report --short    # короткая сводка
"""
import io, json, os, re, time
from pathlib import Path

from . import db
from app.services.exception_observability import observe_suppressed

# Заголовки задач техничные — владельцу показываем понятное имя.
HUMAN = [
    (r"CLIENT_ANTON", "Антон, механизированная штукатурка"),
    (r"CLIENT_VASILY", "Василий, аренда авто с выкупом"),
    (r"CLIENT_PLANETA_MEBELI", "Планета мебели, Уфа"),
    (r"CLIENT_ANDREY_PENZA", "Андрей, Лаунж Мебель, Пенза"),
    (r"CLIENT_CABINET", "Кабинеты клиентов и фиды"),
    (r"DEV_NAVIGATION", "Навигация и работоспособность экранов"),
    (r"DEV_GUIDED_SCENARIOS", "Сценарии: BORIS ведёт клиента за руку"),
    (r"DEV_PARALLEL_SCALE", "Параллельная работа над задачами"),
]

PHASE_HUMAN = {"PLANNING": "разбирается", "BACKEND": "делает серверную часть",
               "FRONTEND": "правит экраны", "UX": "правит удобство",
               "INTEGRATION": "связывает части", "TESTING": "проверяет",
               "FIXING": "исправляет", "REGRESSION": "проверяет, не сломалось ли",
               "ACCEPTANCE": "сдаёт работу", "DEPLOYMENT": "выкатывает",
               "PRODUCTION_QA": "проверяет на боевом", "REVIEW": "на приёмке"}


def human_title(title):
    t = title or ""
    for pat, name in HUMAN:
        if re.search(pat, t):
            return name
    return re.sub(r"^[A-Z_0-9]+\s*[—-]\s*", "", t).strip() or t


def _plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


# Где клиенту смотреть результат. Иначе «Готово: 1» — это отчёт ни о чём.
WHERE_TO_LOOK = {
    "CLIENT_ANTON": "кабинет клиента antonybatono_78732 → Центр объявлений",
    "CLIENT_VASILY": "кабинет vasyailchenko_82650 → Центр объявлений, черновики",
    "CLIENT_PLANETA_MEBELI": "кабинет «Планета мебели» → Центр объявлений",
    "CLIENT_ANDREY_PENZA": "кабинет Пензы → Центр объявлений и фид",
    "CLIENT_BYTOVKI_REPORTS": "телеграм-чат клиента по бытовкам",
    "CLIENT_CABINET": "/dashboard/campaign — числа и карточки",
    "DEV_CALLS_REPORT_FIX": "суточная сводка по звонкам в Telegram",
    "DEV_NAVIGATION": "любой экран кабинета: переходы и карточки",
    "DEV_GUIDED_SCENARIOS": "/dashboard/scenarios",
}


def where_to_look(title):
    for key, place in WHERE_TO_LOOK.items():
        if (title or "").startswith(key):
            return place
    return None


# Служебные задачи владельцу не показываются: в его отчёте не должно быть
# ничего, что он не заказывал. Из внутренней диагностики они никуда не
# деваются — только из его экрана.
INTERNAL_TITLES = ("SELFTEST", "A2A E2E", "TG: SELFTEST", "DEV_DEPLOY_REPAIR")


def is_internal(title):
    t = (title or "").strip()
    return any(t.startswith(p) for p in INTERNAL_TITLES)


def gather(hours=24):
    jobs = [j for j in db.rows("""SELECT id, title, status, current_phase,
                             blocked_reason, wait_reason, acceptance_json,
                             final_result_json, updated_at
                        FROM ext_dev_jobs
                       WHERE status NOT IN ('cancelled')
                       ORDER BY updated_at DESC NULLS LAST LIMIT 100""")
            if not is_internal(j.get("title"))]
    orders = db.rows("""SELECT id, dev_job_id, phase, status, iteration,
                               max_iterations, blocked_reason, assigned_to,
                               updated_at
                          FROM ext_a2a_orders
                         WHERE updated_at > NOW() - (:h || ' hours')::interval
                         ORDER BY updated_at DESC LIMIT 200""", h=int(hours))
    accepted = [o for o in orders if o["status"] == "accepted"]
    returned = [o for o in orders if o["status"] == "returned"]
    blocked_h = [o for o in orders if o["status"] == "blocked_human"]
    blocked_i = [o for o in orders if o["status"] == "blocked_infra"]
    patches = db.rows("""SELECT id, status, note, created_at FROM ext_dev_patches
                          WHERE created_at > NOW() - (:h || ' hours')::interval
                          ORDER BY id DESC LIMIT 50""", h=int(hours))
    return {"hours": int(hours), "jobs": jobs, "orders": orders,
            "accepted": accepted, "returned": returned,
            "blocked_human": blocked_h, "blocked_infra": blocked_i,
            "patches": patches}


def _criteria(job):
    ac = job.get("acceptance_criteria") or job.get("acceptance_json")
    if isinstance(ac, str):
        try:
            ac = json.loads(ac)
        except ValueError:
            ac = []
    ac = ac or []
    done = sum(1 for c in ac if isinstance(c, dict) and c.get("done"))
    return done, len(ac)


def text(hours=24, short=False):
    d = gather(hours)
    jobs = d["jobs"]
    by_id = {j["id"]: j for j in jobs}
    done = [j for j in jobs if j["status"] == "completed"]
    running = [j for j in jobs if j["status"] == "running"]
    waiting = [j for j in jobs if j["status"] in ("waiting", "queued")]
    blocked = [j for j in jobs if j["status"] == "blocked"]

    lines = ["Отчёт BORIS за %d %s" % (d["hours"],
                                       _plural(d["hours"], "час", "часа", "часов")), ""]

    # Первым идёт не список задач, а проверка фактами: прибавилось ли у
    # клиентов хоть что-нибудь. Всё остальное в отчёте — про процесс, а
    # владельцу нужен результат.
    try:
        from . import factory
        lines.append(factory.offline_summary(mark=False))
        lines.append("")
        lines.extend(factory.summary_lines(3))
        lines.append("")
        lines.append(business_truth_text(hours))
        lines.append("")
    except Exception as e:
        lines.append("Проверка результата не выполнена: %s: %s"
                     % (type(e).__name__, e))
        lines.append("")

    if done:
        lines.append("ГОТОВО")
        for j in done[:10]:
            got, all_ = _criteria(j)
            lines.append("· %s — принято, %d из %d пунктов приёмки"
                         % (human_title(j["title"]), got, all_))
            summary = ""
            fr = j.get("final_result_json")
            if isinstance(fr, str) and fr.strip():
                try:
                    summary = (json.loads(fr) or {}).get("summary") or ""
                except ValueError:
                    summary = ""
            if summary:
                lines.append("  что сделано: " + summary[:300])
            place = where_to_look(j["title"])
            if place:
                lines.append("  смотреть: " + place)
        lines.append("")

    if running:
        lines.append("В РАБОТЕ ПРЯМО СЕЙЧАС")
        for j in running[:10]:
            got, all_ = _criteria(j)
            what = PHASE_HUMAN.get((j["current_phase"] or "").upper(), "работает")
            lines.append("· %s — %s, сделано %d из %d"
                         % (human_title(j["title"]), what, got, all_))
        lines.append("")

    if waiting:
        lines.append("ЖДУТ ОЧЕРЕДИ")
        for j in waiting[:8]:
            why = (j.get("wait_reason") or "ждёт свободной ёмкости").strip()
            lines.append("· %s — %s" % (human_title(j["title"]), why))
        lines.append("")

    if blocked or d["blocked_human"]:
        lines.append("НУЖНО ОТ ВАС")
        seen = set()
        for o in d["blocked_human"]:
            j = by_id.get(o["dev_job_id"]) or {}
            key = (o["dev_job_id"], o.get("blocked_reason"))
            if key in seen:
                continue
            seen.add(key)
            lines.append("· %s: %s" % (human_title(j.get("title") or ""),
                                       (o.get("blocked_reason") or "нужно ваше решение")))
        for j in blocked:
            if any(o["dev_job_id"] == j["id"] for o in d["blocked_human"]):
                continue
            lines.append("· %s: %s" % (human_title(j["title"]),
                                       j.get("blocked_reason") or "работа остановлена"))
        lines.append("")

    if d["blocked_infra"]:
        lines.append("НЕ ХВАТАЕТ ИНСТРУМЕНТОВ (решаем сами, вас не трогаем)")
        for o in d["blocked_infra"][:5]:
            j = by_id.get(o["dev_job_id"]) or {}
            lines.append("· %s: %s" % (human_title(j.get("title") or ""),
                                       (o.get("blocked_reason") or "")[:160]))
        lines.append("")

    applied = [p for p in d["patches"] if p["status"] == "applied"]
    proposed = [p for p in d["patches"] if p["status"] == "proposed"]
    work = []
    if d["accepted"]:
        work.append("принято работ: %d" % len(d["accepted"]))
    if d["returned"]:
        work.append("возвращено на доработку: %d" % len(d["returned"]))
    if applied:
        work.append("правок применено: %d" % len(applied))
    if proposed:
        work.append("правок ждут вашего слова: %d" % len(proposed))
    if work:
        lines.append("ЗА ПЕРИОД: " + ", ".join(work))

    _st = stalled()
    if _st:
        lines.insert(2, "ВНИМАНИЕ: работа стоит")
        for x in _st[:6]:
            lines.insert(3, "· %s — молчит %d мин, исполнитель %s"
                         % (x["title"], x["minutes"], x["agent"] or "не назначен"))
        lines.insert(3 + min(6, len(_st)), "")

    if not (done or running or waiting or blocked):
        lines.append("Новостей нет: очередь пуста, задач в работе нет.")

    out = "\n".join(lines).strip()
    if short:
        head = ["Готово: %d" % len(done), "в работе: %d" % len(running),
                "в очереди: %d" % len(waiting), "нужно от вас: %d" % len(blocked)]
        line = "BORIS: " + ", ".join(head)
        # «Готово: 1» без имени — бесполезная строка. Называем, что именно.
        if done:
            names = "; ".join(human_title(j["title"]) for j in done[:3])
            line += "\nготово: " + names
            place = where_to_look(done[0]["title"])
            if place:
                line += "\nсмотреть: " + place
        return line
    return out


STALL_MINUTES = 90          # задача числится в работе, а событий нет
SILENCE_LIMIT = 3           # подряд промолчали столько раз — сказать вслух


def _silence(count=None):
    """Сколько раз подряд отчёт промолчал. Полная тишина — тоже новость."""
    from . import repo
    path = os.path.join(repo.ROOT, "backend", ".ext_report_silence")
    if count is not None:
        try:
            with io.open(path, "w", encoding="utf-8") as fh:
                fh.write(str(int(count)))
        except OSError:
            pass
        return int(count)
    try:
        with io.open(path, encoding="utf-8") as fh:
            return int(fh.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def stalled(minutes=None):
    """Задачи, которые числятся в работе, но давно молчат.

    Это важнее любого «за период ничего не изменилось»: если работа стоит,
    владелец обязан узнать, а не получить тишину.
    """
    limit = int(minutes or STALL_MINUTES)
    rows = db.rows("""SELECT j.id, j.title, j.current_phase, j.assigned_agent,
                             GREATEST(COALESCE((SELECT MAX(created_at)
                                                  FROM ext_dev_events e
                                                 WHERE e.dev_job_id = j.id),
                                               j.updated_at), j.updated_at) AS beat
                        FROM ext_dev_jobs j
                       WHERE j.status = 'running'""")
    out = []
    for r in rows:
        beat = r.get("beat")
        mins = None
        try:
            from . import sched
            ts = sched._ts(beat)
            if ts:
                mins = int((time.time() - ts) / 60)
        except Exception:
            mins = None
        if mins is not None and mins >= limit:
            out.append({"title": human_title(r["title"]), "minutes": mins,
                        "phase": r.get("current_phase"),
                        "agent": r.get("assigned_agent")})
    return out


# ------------------------------------------------- проверка фактами
# Владелец задал единственный правильный вопрос: как отличить «работа идёт»
# от «время уходит». Ответ не может опираться на статусы задач, наряды и
# переписку агентов — всё это можно двигать бесконечно, не дав клиенту
# ничего. Мерой считается только то, что появилось у клиента в базе.

def _acct_id(ref):
    """Найти аккаунт клиента, не сдаваясь на первой попытке.

    Владелец подтвердил: у «Планеты» и почта, и зарегистрированный аккаунт
    есть — а поиск возвращал «измерить нечем». Одна строка-идентификатор
    может не совпасть буквально: почта записана иначе, домен другой, в базе
    только локальная часть. Пробуем несколько форм одного и того же имени и,
    если под запрос подходит несколько аккаунтов, берём самый старший, а не
    отказываемся вовсе.
    """
    if not ref:
        return None
    tries = [str(ref)]
    text = str(ref)
    if "@" in text:
        tries.append(text.split("@", 1)[0])
    if "_" in text:
        tries.append(text.rsplit("_", 1)[0])
    for t in tries:
        t = t.strip()
        if not t:
            continue
        try:
            a = db.find_account(t)
            if a:
                return a.get("id")
        except ValueError:
            # подходит несколько — берём первый по порядку, это лучше нуля
            try:
                cols = db.columns("accounts")
                usable = [c for c in db.ACCOUNT_NAME_CANDIDATES if c in cols]
                where = " OR ".join("CAST(%s AS TEXT) ILIKE :p" % c for c in usable)
                r = db.one("SELECT id FROM accounts WHERE %s ORDER BY id LIMIT 1"
                           % where, p="%" + t + "%")
                if r:
                    return r.get("id")
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=353)
        except Exception:
            continue
    return None


def _artifacts(account_id, hours):
    """Сколько у клиента реальных объектов и сколько из них появилось за период."""
    out = {"items": 0, "new": None, "campaigns": 0, "measurable": False}
    if account_id is None:
        return out
    cols = db.columns("campaign_items") or {}
    if not cols:
        return out
    out["measurable"] = True
    acc_col = "account_id" if "account_id" in cols else None
    try:
        if acc_col:
            out["items"] = (db.one(
                "SELECT COUNT(*) AS n FROM campaign_items "
                "WHERE CAST(%s AS TEXT) = :a" % acc_col, a=str(account_id))
                or {}).get("n") or 0
        else:
            out["items"] = (db.one(
                """SELECT COUNT(*) AS n FROM campaign_items i
                     JOIN campaigns c ON CAST(c.id AS TEXT) = CAST(i.campaign_id AS TEXT)
                    WHERE CAST(c.default_account_id AS TEXT) = :a""",
                a=str(account_id)) or {}).get("n") or 0
    except Exception:
        out["measurable"] = False
        return out
    time_col = "created_at" if "created_at" in cols else (
        "updated_at" if "updated_at" in cols else None)
    if time_col and acc_col:
        try:
            out["new"] = (db.one(
                "SELECT COUNT(*) AS n FROM campaign_items "
                "WHERE CAST(%s AS TEXT) = :a AND %s > NOW() - (:h || ' hours')::interval"
                % (acc_col, time_col), a=str(account_id), h=int(hours))
                or {}).get("n") or 0
        except Exception:
            out["new"] = None
    try:
        out["campaigns"] = (db.one(
            "SELECT COUNT(*) AS n FROM campaigns WHERE CAST(default_account_id AS TEXT) = :a",
            a=str(account_id)) or {}).get("n") or 0
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=400)
    return out


def truth(hours=2):
    """Что у клиентов реально прибавилось за период — фактами из базы.

    Ни одного слова о том, что «работа может идти дольше окна наблюдения».
    Либо у клиента прибавились объекты, либо нет; если нет — так и сказано,
    с числом часов и с тем, что этому мешает.
    """
    from . import clients
    out = {"hours": int(hours), "clients": [], "moved": 0, "measured": 0}
    for spec in clients.CLIENTS:
        key = spec.get("key") or ""
        if not key.startswith("CLIENT_"):
            continue
        ref = (spec.get("scope") or {}).get("account")
        job = clients._existing(key)
        row = {"key": key, "goal": clients.BUSINESS_GOAL.get(key, ""),
               "account": ref, "status": (job or {}).get("status"),
               "items": None, "new": None, "campaigns": None,
               "measurable": False}
        if ref:
            art = _artifacts(_acct_id(ref), hours)
            row.update(items=art["items"], new=art["new"],
                       campaigns=art["campaigns"], measurable=art["measurable"])
            if art["measurable"]:
                out["measured"] += 1
                if (art["new"] or 0) > 0:
                    out["moved"] += 1
        out["clients"].append(row)
    out["verdict"] = ("ЕСТЬ ДВИЖЕНИЕ" if out["moved"]
                      else ("НЕТ РЕЗУЛЬТАТА" if out["measured"]
                            else "НЕЧЕМ ИЗМЕРИТЬ"))
    return out


# --------------------------------------------- BUSINESS RESULT TRUTH
# Владелец сформулировал требование точно: результатом считается production
# artifact, а не события протокола. Задача может неделю «идти», и всё это
# время в кабинете клиента будет ноль. Здесь измеряется только то, что у
# клиента появилось, а отсутствие прибавки обязано быть названо состоянием,
# а не отговоркой.

SLA_MINUTES = int(os.environ.get("BORIS_CLIENT_SLA_MIN", "120"))
HEARTBEAT_MINUTES = int(os.environ.get("BORIS_HEARTBEAT_MIN", "20"))

# Чем измеряется результат по каждой задаче и что считается финалом.
ARTIFACTS = {
    "CLIENT_ANTON": {"kind": "listings", "expected": 5,
                     "image_expected": 25, "banner_expected": 5,
                     "final": "5 объявлений · 25 изображений · 5 баннеров · собранный фид"},
    "CLIENT_VASILY": {"kind": "drafts", "expected": 10,
                      "final": "пакет черновиков виден клиенту в кабинете"},
    "CLIENT_PLANETA_MEBELI": {"kind": "drafts", "expected": 36,
                              "source_ref_prefix": "planeta-real:",
                              "final": "36 проверенных карточек и собранный фид"},
    "CLIENT_ANDREY_PENZA": {"kind": "listings", "expected": 151,
                            "show_live": True,
                            "final": "151 готовая карточка в BORIS и полный внутренний фид"},
    "CLIENT_BYTOVKI_REPORTS": {
        "kind": "delivery",
        "expected": 2,
        "accounts": [
            "prodazha_bytovok_25677",
            "3411770_94346",
        ],
        "delivery_tz": "Europe/Moscow",
        # Production cron starts at 17:30 UTC = 20:30 Moscow. The SLA becomes
        # due five minutes later so both account reports can finish without a
        # false daily regression window.
        "delivery_due_hour": 20,
        "delivery_due_minute": 35,
        "final": "отчёты по обоим аккаунтам доставлены в чат клиента",
    },
    "CLIENT_CABINET": {
        "kind": "cabinet",
        "expected": 3,
        "accounts": [
            "antonybatono_78732",
            "vasyailchenko_82650",
            "planetazayavki_65985",
        ],
        "final": "в /dashboard/campaign по 3 клиентам видны реальные ненулевые числа",
    },
    "DEV_CALLS_REPORT_FIX": {
        "kind": "calls",
        "expected": 1,
        "owner_id": 42,
        "delivery_tz": "Europe/Moscow",
        "delivery_due_hour": 21,
        "delivery_due_minute": 0,
        "contract": "avito_calls_truth_v2",
        "final": "суточная сводка по звонкам с реальными числами и рекомендациями",
    },
}

STATES = ("WORKING_LONG_TASK", "WAITING_REVIEW", "WAITING_DEPLOY", "BLOCKED",
          "STALLED", "NO_OPEN_ORDER", "DEAD_WORKER", "FAILED_VALIDATION")


def _first_col(table, names):
    cols = db.columns(table) or {}
    for n in names:
        if n in cols:
            return n
    return None


def _measure(kind, account_id, hours, source_ref_prefix=None):
    """Production artifact по типу задачи. Ничего не выдумываем: если мерить
    нечем — так и говорим, это отдельный дефект, а не повод для оптимизма."""
    out = {"total": 0, "delta": 0, "last": None, "measurable": False,
           "unit": kind}
    if kind == "cabinet":
        targets = [str(x).strip() for x in (account_id or []) if str(x).strip()]             if isinstance(account_id, (list, tuple, set)) else []
        # CLIENT_CABINET_BUSINESS_TRUTH_V1: this goal spans three customer
        # workspaces. Measuring it as one campaign/listing account made the
        # LEVEL-4 gate permanently false even when the real UI showed data.
        # Keep this health check DB-only and cheap: dashboard cards themselves
        # are fed by media_assets and catalog_products.
        if not targets:
            return out
        if not (db.columns("accounts") and db.columns("media_assets")
                and db.columns("catalog_products")):
            return out
        try:
            rows = db.rows("""
                SELECT a.account_id,
                       (SELECT COUNT(*)
                          FROM media_assets m
                         WHERE m.account_id=a.account_id
                           AND m.deleted_at IS NULL) AS media_total,
                       (SELECT COUNT(*)
                          FROM catalog_products c
                         WHERE c.account_id=a.account_id
                           AND c.deleted_at IS NULL) AS catalog_total,
                       GREATEST(
                         (SELECT MAX(m.updated_at)
                            FROM media_assets m
                           WHERE m.account_id=a.account_id
                             AND m.deleted_at IS NULL),
                         (SELECT MAX(c.updated_at)
                            FROM catalog_products c
                           WHERE c.account_id=a.account_id
                             AND c.deleted_at IS NULL)
                       ) AS last_t,
                       (
                         COALESCE((SELECT MAX(m.updated_at)
                                    FROM media_assets m
                                   WHERE m.account_id=a.account_id
                                     AND m.deleted_at IS NULL),
                                  TIMESTAMPTZ 'epoch')
                           > NOW() - (:h || ' hours')::interval
                         OR
                         COALESCE((SELECT MAX(c.updated_at)
                                    FROM catalog_products c
                                   WHERE c.account_id=a.account_id
                                     AND c.deleted_at IS NULL),
                                  TIMESTAMPTZ 'epoch')
                           > NOW() - (:h || ' hours')::interval
                       ) AS recent
                  FROM accounts a
                 WHERE a.account_id = ANY(:accounts)
            """, accounts=targets, h=int(hours))
        except Exception:
            return out
        by_account = {str(r.get("account_id")): r for r in rows}
        if set(by_account) != set(targets):
            return out
        ready = []
        recent = 0
        latest = None
        for target in targets:
            row = by_account[target]
            visible = int(row.get("media_total") or 0) + int(row.get("catalog_total") or 0)
            if visible > 0:
                ready.append(target)
            if bool(row.get("recent")):
                recent += 1
            value = row.get("last_t")
            if value is not None and (latest is None or value > latest):
                latest = value
        out.update(
            total=len(ready),
            delta=recent,
            last=latest,
            measurable=True,
            unit="клиентских кабинетов с ненулевыми данными",
        )
        return out
    if kind in ("listings", "drafts"):
        cols = db.columns("campaign_items") or {}
        if not cols or account_id is None:
            return out
        acc = "account_id" if "account_id" in cols else None
        tcol = _first_col("campaign_items", ("created_at", "updated_at"))
        scol = _first_col("campaign_items", ("status", "state"))
        if not acc:
            return out
        aliases = [str(account_id)]
        try:
            numeric = _acct_id(account_id)
            if numeric is not None and str(numeric) not in aliases:
                aliases.append(str(numeric))
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=607)
        a1 = aliases[0]
        a2 = aliases[1] if len(aliases) > 1 else aliases[0]
        args = {"a1": a1, "a2": a2}
        # Some business packages are intentionally split across several current
        # campaigns (pilot + remainder + replacement). For those packages the
        # stable source_ref prefix is the logical identity of one current card.
        # Count DISTINCT logical identities across non-superseded campaigns so a
        # replacement neither removes a valid card from the goal nor double-counts
        # historical/test campaigns. Other clients keep the older single-campaign
        # measurement contract below.
        if source_ref_prefix and "source_ref" in cols:
            try:
                args["source_ref_prefix"] = str(source_ref_prefix) + "%"
                q_pkg = """
                    SELECT COUNT(DISTINCT split_part(COALESCE(i.source_ref,''),'|',1)) AS n,
                           MAX(i.{tc}) AS last_t
                      FROM campaigns c
                      JOIN campaign_items i ON i.campaign_id=c.id
                     WHERE CAST(c.default_account_id AS TEXT) IN (:a1,:a2)
                       AND LOWER(COALESCE(c.status,'')) NOT IN ('superseded','cancelled','deleted','expired')
                       AND LOWER(COALESCE(c.name,'')) NOT LIKE 'selftest%%'
                       AND LOWER(COALESCE(CAST(i.status AS TEXT),'')) NOT IN ('superseded','cancelled','deleted','expired')
                       AND COALESCE(i.source_ref,'') LIKE :source_ref_prefix
                """.format(tc=tcol or "created_at")
                r_pkg = db.one(q_pkg, **args) or {}
                out["total"] = int(r_pkg.get("n") or 0)
                out["last"] = r_pkg.get("last_t")
                out["measurable"] = True
                out["unit"] = "готовых объявлений" if kind == "listings" else "готовых карточек"
                if tcol:
                    q_delta = """
                        SELECT COUNT(DISTINCT split_part(COALESCE(i.source_ref,''),'|',1)) AS n
                          FROM campaigns c
                          JOIN campaign_items i ON i.campaign_id=c.id
                         WHERE CAST(c.default_account_id AS TEXT) IN (:a1,:a2)
                           AND LOWER(COALESCE(c.status,'')) NOT IN ('superseded','cancelled','deleted','expired')
                           AND LOWER(COALESCE(c.name,'')) NOT LIKE 'selftest%%'
                           AND LOWER(COALESCE(CAST(i.status AS TEXT),'')) NOT IN ('superseded','cancelled','deleted','expired')
                           AND COALESCE(i.source_ref,'') LIKE :source_ref_prefix
                           AND i.{tc} > NOW() - (:h || ' hours')::interval
                    """.format(tc=tcol)
                    out["delta"] = int((db.one(q_delta, h=int(hours), **args) or {}).get("n") or 0)
                return out
            except Exception as _suppressed_exc:
                # Fail closed into the established measurement path. We do not
                # claim the split-package result unless its identity query works.
                observe_suppressed(__name__, _suppressed_exc, line=652)
        # Measure one current business campaign, not the sum of every historical,
        # test and superseded campaign. Within each campaign prefer ready/live
        # items; if none reached ready, fall back to all viable draft items.
        try:
            status_expr = "LOWER(COALESCE(CAST(i.%s AS TEXT),''))" % scol if scol else "''"
            q = """
                WITH per_campaign AS (
                    SELECT c.id,
                           SUM(CASE WHEN {st} IN ('ready','published','active','live') THEN 1 ELSE 0 END) AS ready_n,
                           SUM(CASE WHEN {st} NOT IN ('superseded','cancelled','deleted','expired') THEN 1 ELSE 0 END) AS viable_n,
                           MAX(i.{tc}) AS last_t
                      FROM campaigns c
                      JOIN campaign_items i ON i.campaign_id=c.id
                     WHERE CAST(c.default_account_id AS TEXT) IN (:a1,:a2)
                       AND LOWER(COALESCE(c.status,'')) NOT IN ('superseded','cancelled','deleted','expired')
                       AND LOWER(COALESCE(c.name,'')) NOT LIKE 'selftest%%'
                     GROUP BY c.id
                )
                SELECT COALESCE(MAX(CASE WHEN :artifact_kind='drafts' THEN viable_n
                                             WHEN ready_n>0 THEN ready_n ELSE viable_n END),0) AS n,
                       MAX(last_t) AS last_t
                  FROM per_campaign
            """.format(st=status_expr, tc=tcol or "created_at")
            # BUSINESS_DRAFTS_VIABLE_COUNT_V1: a draft-package business goal means
            # all current visible viable cards. A safe title/content experiment
            # intentionally moves one previously-ready card back to draft until
            # external publication; that must not fabricate a client regression
            # or spawn a development order for an already complete package.
            args["artifact_kind"] = kind
            r = db.one(q, **args) or {}
            out["total"] = int(r.get("n") or 0)
            out["last"] = r.get("last_t")
            out["measurable"] = True
            out["unit"] = "готовых объявлений" if kind == "listings" else "готовых карточек"
        except Exception:
            return out
        if tcol:
            try:
                out["delta"] = int((db.one("""
                    SELECT COUNT(*) AS n FROM campaign_items i
                    JOIN campaigns c ON c.id=i.campaign_id
                    WHERE CAST(c.default_account_id AS TEXT) IN (:a1,:a2)
                      AND LOWER(COALESCE(c.status,'')) NOT IN ('superseded','cancelled','deleted','expired')
                      AND LOWER(COALESCE(c.name,'')) NOT LIKE 'selftest%%'
                      AND LOWER(COALESCE(CAST(i.status AS TEXT),'')) NOT IN ('superseded','cancelled','deleted','expired')
                      AND i.%s > NOW() - (:h || ' hours')::interval
                """ % tcol, h=int(hours), **args) or {}).get("n") or 0)
            except Exception:
                out["delta"] = 0
        return out
    if kind == "calls":
        if not (db.columns("storage") or {}):
            return out
        try:
            from datetime import datetime as _dt, timedelta as _timedelta
            from zoneinfo import ZoneInfo as _ZoneInfo
            conf = ARTIFACTS.get("DEV_CALLS_REPORT_FIX") or {}
            tz = _ZoneInfo(str(conf.get("delivery_tz") or "Europe/Moscow"))
            now_local = _dt.now(tz)
            due_hour = conf.get("delivery_due_hour")
            due_minute = conf.get("delivery_due_minute")
            due = now_local.replace(
                hour=int(21 if due_hour is None else due_hour),
                minute=int(0 if due_minute is None else due_minute),
                second=0,
                microsecond=0,
            )
            report_date = now_local.date().isoformat()
            due_now = now_local >= due
            previous_report_date = (
                now_local.date() - _timedelta(days=1)
            ).isoformat()
            accepted_report_dates = (
                [report_date] if due_now
                else [report_date, previous_report_date]
            )

            owner = "owner:%s" % int(conf.get("owner_id") or 42)
            rows = db.rows(
                "SELECT key, value FROM storage"
                " WHERE account_id=:owner AND key LIKE 'rop_digest_msg:%'"
                " ORDER BY id DESC LIMIT 30",
                owner=owner,
            )
            expected_contract = str(conf.get("contract") or "avito_calls_truth_v2")
            candidates = []
            for row in rows:
                try:
                    state = json.loads(row.get("value") or "{}")
                except Exception:
                    continue
                if not isinstance(state, dict):
                    continue
                text_body = str(state.get("text") or "")
                state_date = str(state.get("report_date") or "")
                if not state_date:
                    first = text_body.splitlines()[0] if text_body else ""
                    m = re.search(r"(20\d{2}-\d{2}-\d{2})", first)
                    state_date = m.group(1) if m else ""
                if state_date not in accepted_report_dates:
                    continue
                if not state.get("message_id") or not state.get("sha256"):
                    continue
                candidates.append((state, state_date, text_body))

            # Before SLA yesterday is only a fallback when no current-day
            # receipt exists at all. If today's digest already exists but is
            # invalid/incomplete, surface that regression immediately instead
            # of hiding it behind yesterday's green receipt.
            if any(item[1] == report_date for item in candidates):
                active_report_date = report_date
            elif not due_now and any(
                item[1] == previous_report_date for item in candidates
            ):
                active_report_date = previous_report_date
            else:
                active_report_date = report_date

            valid = []
            for state, state_date, text_body in candidates:
                if state_date != active_report_date:
                    continue
                if str(state.get("contract") or "") != expected_contract:
                    continue
                if "Всего звонков по Avito:" not in text_body:
                    continue
                if "Разобрано разговоров:" not in text_body:
                    continue
                # If paid ROP is active and BORIS explicitly says there are
                # answered calls waiting for analysis, the business result is
                # not complete yet. Expired/no-minutes is a truthful entitlement
                # state, not a report failure.
                if "Есть отвеченные звонки без AI-разбора" in text_body:
                    continue
                valid.append(state)

            out.update(
                measurable=True,
                total=1 if valid else 0,
                delta=1 if valid else 0,
                last=(valid[0].get("updated_at") if valid else None),
                unit="доставленных суточных сводок по звонкам",
                due=due_now,
                report_date=report_date,
                accepted_report_date=active_report_date,
                delivery_message_id=(valid[0].get("message_id") if valid else None),
                contract=expected_contract,
            )
        except Exception:
            out["measurable"] = False
        return out
    if kind == "delivery":
        targets = [str(x).strip() for x in (account_id or []) if str(x).strip()] \
                  if isinstance(account_id, (list, tuple, set)) else []
        if not targets or not (db.columns("storage") or {}):
            return out
        try:
            from datetime import datetime as _dt, timedelta as _timedelta
            from zoneinfo import ZoneInfo as _ZoneInfo
            conf = ARTIFACTS.get("CLIENT_BYTOVKI_REPORTS") or {}
            tz = _ZoneInfo(str(conf.get("delivery_tz") or "Europe/Moscow"))
            now_local = _dt.now(tz)
            due_hour = conf.get("delivery_due_hour")
            due_minute = conf.get("delivery_due_minute")
            due = now_local.replace(
                hour=int(17 if due_hour is None else due_hour),
                minute=int(30 if due_minute is None else due_minute),
                second=0,
                microsecond=0,
            )
            report_date = now_local.date().isoformat()
            due_now = now_local >= due
            previous_report_date = (
                now_local.date() - _timedelta(days=1)
            ).isoformat()
            accepted_report_dates = (
                [report_date] if due_now
                else [report_date, previous_report_date]
            )

            rows = db.rows(
                "SELECT account_id, value FROM storage"
                " WHERE account_id = ANY(:accounts)"
                " AND key='client_success_report_delivery'"
                " ORDER BY id DESC",
                accounts=targets,
            )
            valid = {}
            latest = None
            for row in rows:
                acc = str(row.get("account_id") or "")
                if acc not in targets or acc in valid:
                    continue
                try:
                    raw = json.loads(row.get("value") or "{}")
                except Exception:
                    continue
                receipt = raw.get("automated_daily") if isinstance(raw, dict) else None
                if not isinstance(receipt, dict):
                    continue
                if str(receipt.get("status") or "") != "delivered":
                    continue
                if str(receipt.get("report_date") or "") not in accepted_report_dates:
                    continue
                if str(receipt.get("account_id") or acc) != acc:
                    continue
                if receipt.get("telegram_message_id") in (None, ""):
                    continue
                valid[acc] = receipt
                sent_at = receipt.get("sent_at")
                if sent_at and (latest is None or str(sent_at) > str(latest)):
                    latest = sent_at

            out.update(
                measurable=True,
                total=len(valid),
                delta=len(valid),
                last=latest,
                unit="доставленных клиентских отчётов",
                due=due_now,
                report_date=report_date,
                accepted_report_date=(
                    report_date
                    if due_now or (
                        valid and all(
                            str(x.get("report_date") or "") == report_date
                            for x in valid.values()
                        )
                    )
                    else previous_report_date
                ),
                expected_accounts=len(targets),
                delivered_accounts=sorted(valid),
            )
        except Exception:
            out["measurable"] = False
        return out
    return out


def _minutes_since(value):
    try:
        from . import sched
        ts = sched._ts(value)
    except Exception:
        return None
    if not ts:
        return None
    return int((time.time() - ts) / 60)


def _state_of(job, delta):
    """Почему прибавки нет. Восемь состояний вместо одного «нет результата».

    WORKING_LONG_TASK выдаётся только при доказательстве идущей операции —
    свежем событии по задаче. Без доказательства это STALLED, как бы долго
    задача ни называлась долгой.
    """
    if delta and delta > 0:
        return {"state": "MOVING", "proof": "прибавка за окно: +%d" % delta}
    if not job:
        return {"state": "NO_OPEN_ORDER", "proof": "задача не заведена"}
    from . import a2a
    orders = db.rows("""SELECT id, status, blocked_reason, updated_at, claimed_by,
                               claimed_at
                          FROM ext_a2a_orders WHERE dev_job_id = :j
                         ORDER BY id DESC LIMIT 20""", j=job["id"])
    blocked = [o for o in orders if o["status"] in ("blocked_human", "blocked_infra")]
    if blocked:
        return {"state": "BLOCKED",
                "proof": (blocked[0].get("blocked_reason") or "наряд остановлен")[:200]}
    open_ = [o for o in orders if o["status"] in a2a.OPEN_STATES]
    if not open_:
        return {"state": "NO_OPEN_ORDER",
                "proof": "открытых нарядов нет — следующий шаг не поставлен"}
    if any(o["status"] == a2a.REVIEW for o in open_):
        mins = _minutes_since(max((o["updated_at"] for o in open_
                                   if o["status"] == a2a.REVIEW), default=None))
        return {"state": "WAITING_REVIEW",
                "proof": "у лида на приёмке%s" % ("" if mins is None else
                                                  " %d мин" % mins)}
    pending = db.one("""SELECT COUNT(*) AS n FROM ext_dev_patches
                         WHERE dev_job_id = :j AND status IN ('accepted','approved')""",
                     j=job["id"]) or {}
    if (pending.get("n") or 0) > 0:
        return {"state": "WAITING_DEPLOY",
                "proof": "правок ждёт выкатки: %d" % pending["n"]}
    fail = db.one("""SELECT note FROM ext_deploy_log WHERE NOT ok
                      ORDER BY id DESC LIMIT 1""") or {}
    beat = db.one("""SELECT MAX(created_at) AS t FROM ext_dev_events
                      WHERE dev_job_id = :j""", j=job["id"]) or {}
    mins = _minutes_since(beat.get("t"))
    claimed = [o for o in open_ if o.get("claimed_by")]
    if claimed and (mins is None or mins > HEARTBEAT_MINUTES * 3):
        return {"state": "DEAD_WORKER",
                "proof": "наряд занят %s, событий %s" %
                         (claimed[0]["claimed_by"],
                          "нет" if mins is None else "нет %d мин" % mins)}
    if mins is not None and mins <= HEARTBEAT_MINUTES:
        return {"state": "WORKING_LONG_TASK",
                "proof": "последнее событие по задаче %d мин назад" % mins}
    if fail.get("note") and (mins is None or mins > SLA_MINUTES):
        return {"state": "FAILED_VALIDATION", "proof": (fail["note"] or "")[:200]}
    return {"state": "STALLED",
            "proof": "нет ни прибавки, ни событий%s" %
                     ("" if mins is None else " %d мин" % mins)}


_TRUTH_CACHE = {"at": 0.0, "hours": None, "data": None}
TRUTH_CACHE_SECONDS = int(os.environ.get("BORIS_TRUTH_CACHE_SEC", "60"))


def _current_order(job):
    """Какой наряд сейчас в работе, кто его держит и сколько уже держит."""
    if not job:
        return {}
    from . import a2a
    o = db.one("""SELECT id, phase, status, claimed_by, claimed_at, created_at
                    FROM ext_a2a_orders
                   WHERE dev_job_id = :j AND status = ANY(:st)
                   ORDER BY id DESC LIMIT 1""",
               j=job["id"], st=list(a2a.OPEN_STATES))
    if not o:
        return {}
    return {"order_id": o["id"], "phase": o.get("phase"),
            "order_status": o.get("status"), "worker": o.get("claimed_by"),
            "minutes_on_order": _minutes_since(o.get("claimed_at")
                                               or o.get("created_at"))}


def _feed_item_count(account_ref):
    """Количество карточек в каноническом feed_items конкретного аккаунта."""
    if not account_ref:
        return None
    try:
        row = db.one("SELECT value FROM storage WHERE account_id=:a AND key='feed_items' ORDER BY id DESC LIMIT 1",
                     a=str(account_ref)) or {}
        raw = row.get("value")
        data = json.loads(raw) if isinstance(raw, str) else raw
        return len(data) if isinstance(data, list) else None
    except Exception:
        return None


def _local_asset_present(ref):
    """Доказать существование локального медиа, а не просто URL в JSON."""
    ref = str(ref or "").strip()
    if not ref:
        return False
    if ref.startswith("/images/"):
        return (Path(__file__).resolve().parents[2] / ref.lstrip("/")).is_file()
    if ref.startswith("images/"):
        return (Path(__file__).resolve().parents[2] / ref).is_file()
    # Внешний URL без отдельной HTTP-проверки не считаем доказанным файлом.
    return False


def _current_campaign_creative_stats(account_ref, expected_cards):
    """Фото/баннеры у той же текущей кампании, которой измеряется результат.

    Нельзя считать общий склад media_assets: старые картинки другого пакета
    создают ложный DONE. Берём текущую не-superseded кампанию с максимальным
    фактическим количеством готовых карточек, а при равенстве — более новую.
    """
    out = {
        "measurable": False, "campaign_id": None, "card_count": 0,
        "image_count": 0, "banner_count": 0, "hero_media_count": 0,
        "missing_local_refs": [],
    }
    if not account_ref:
        return out
    try:
        numeric = _acct_id(account_ref)
        aliases = [str(account_ref)]
        if numeric is not None and str(numeric) not in aliases:
            aliases.append(str(numeric))
        a1 = aliases[0]
        a2 = aliases[1] if len(aliases) > 1 else aliases[0]
        campaign = db.one("""
            WITH per_campaign AS (
                SELECT c.id,
                       SUM(CASE WHEN LOWER(COALESCE(i.status,'')) IN
                                      ('ready','published','active','live')
                                THEN 1 ELSE 0 END) AS ready_n,
                       SUM(CASE WHEN LOWER(COALESCE(i.status,'')) NOT IN
                                      ('superseded','cancelled','deleted','expired')
                                THEN 1 ELSE 0 END) AS viable_n
                  FROM campaigns c
                  JOIN campaign_items i ON i.campaign_id=c.id
                 WHERE CAST(c.default_account_id AS TEXT) IN (:a1,:a2)
                   AND LOWER(COALESCE(c.status,'')) NOT IN
                       ('superseded','cancelled','deleted','expired')
                   AND LOWER(COALESCE(c.name,'')) NOT LIKE 'selftest%%'
                 GROUP BY c.id
            )
            SELECT id, ready_n, viable_n,
                   CASE WHEN ready_n>0 THEN ready_n ELSE viable_n END AS effective_n
              FROM per_campaign
             ORDER BY effective_n DESC, id DESC
             LIMIT 1
        """, a1=a1, a2=a2) or {}
        cid = campaign.get("id")
        if not cid:
            return out
        ready_n = int(campaign.get("ready_n") or 0)
        rows = db.rows("""
            SELECT id,status,payload_json,hero_media_id
              FROM campaign_items
             WHERE campaign_id=:c
               AND (
                    (:ready_only AND LOWER(COALESCE(status,'')) IN
                        ('ready','published','active','live'))
                    OR
                    (NOT :ready_only AND LOWER(COALESCE(status,'')) NOT IN
                        ('superseded','cancelled','deleted','expired'))
               )
             ORDER BY id
        """, c=int(cid), ready_only=bool(ready_n > 0))
        image_refs = set()
        banner_refs = set()
        hero_ids = set()
        missing = set()
        for row in rows:
            try:
                payload = json.loads(row.get("payload_json") or "{}")
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            banner = str(payload.get("banner_url") or "").strip()
            if banner:
                banner_refs.add(banner)
            images = payload.get("images") or []
            if not isinstance(images, list):
                images = []
            for raw in images:
                ref = str(raw or "").strip()
                if not ref:
                    continue
                if "/banners/" in ref or ref == banner:
                    banner_refs.add(ref)
                else:
                    image_refs.add(ref)
            if row.get("hero_media_id"):
                hero_ids.add(int(row["hero_media_id"]))

        present_images = {r for r in image_refs if _local_asset_present(r)}
        present_banners = {r for r in banner_refs if _local_asset_present(r)}
        missing.update(image_refs - present_images)
        missing.update(banner_refs - present_banners)

        valid_heroes = 0
        if hero_ids:
            hr = db.one("""
                SELECT COUNT(*) AS n
                  FROM media_assets
                 WHERE id = ANY(:ids)
                   AND LOWER(COALESCE(status,''))='approved'
                   AND LOWER(COALESCE(file_state,''))='present'
                   AND deleted_at IS NULL
            """, ids=sorted(hero_ids)) or {}
            valid_heroes = int(hr.get("n") or 0)

        out.update(
            measurable=True,
            campaign_id=int(cid),
            card_count=len(rows),
            image_count=len(present_images),
            banner_count=len(present_banners),
            hero_media_count=valid_heroes,
            missing_local_refs=sorted(missing)[:20],
        )
        return out
    except Exception:
        return out


def _live_avito_count(account_ref):
    """Сколько карточек имеют доказательство существования на Avito.

    Считаем только явный published либо непустой avito_item_id. Готовая карточка
    BORIS без Avito ID не называется опубликованным объявлением.
    """
    if not account_ref:
        return None
    try:
        numeric = _acct_id(account_ref)
        aliases = [str(account_ref)]
        if numeric is not None and str(numeric) not in aliases:
            aliases.append(str(numeric))
        a1 = aliases[0]; a2 = aliases[1] if len(aliases) > 1 else aliases[0]
        r = db.one("""SELECT COUNT(*) AS n FROM campaign_items
                       WHERE CAST(account_id AS TEXT) IN (:a1,:a2)
                         AND (LOWER(COALESCE(status,''))='published'
                              OR COALESCE(CAST(avito_item_id AS TEXT),'')<>'')""",
                   a1=a1, a2=a2) or {}
        return int(r.get('n') or 0)
    except Exception:
        return None


def business_truth(hours=2, fresh=False):
    """Правда о результате по каждой активной клиентской задаче.

    Проверка обязана быть дешёвой: только счётчики и существование
    артефактов, никаких обходов и пересборок. Иначе доказательство начинает
    стоить дороже самой работы, а проверять станут реже — то есть хуже.
    """
    now = time.time()
    if (not fresh and _TRUTH_CACHE["data"] is not None
            and _TRUTH_CACHE["hours"] == int(hours)
            and now - _TRUTH_CACHE["at"] < TRUTH_CACHE_SECONDS):
        return _TRUTH_CACHE["data"]
    from . import clients
    out = {"hours": int(hours), "clients": [], "moving": 0, "stalled": 0,
           "done": 0}
    for spec in clients.CLIENTS:
        key = spec.get("key") or ""
        if key not in ARTIFACTS:
            continue
        conf = ARTIFACTS[key]
        ref = (spec.get("scope") or {}).get("account")
        job = clients._existing(key)
        if job:
            job = db.one("SELECT id, title, status FROM ext_dev_jobs WHERE id = :i",
                         i=job["id"])
        # campaign_items.account_id is canonical text on modern records, while
        # some legacy rows used the numeric accounts.id. Pass the canonical ref
        # to the meter; it resolves both aliases so the owner report cannot show
        # a false zero merely because storage format changed.
        acc = _acct_id(ref) if ref else None
        meter_target = (
            conf.get("accounts")
            if conf.get("accounts")
            else (ref if ref else acc)
        )
        m = _measure(conf["kind"], meter_target, hours,
                     source_ref_prefix=conf.get("source_ref_prefix"))
        st = _state_of(job, m["delta"])
        expected = conf.get("expected") or 0
        progress = (min(100, int(round(100.0 * (m["total"] or 0) / expected)))
                    if expected else None)
        requires_feed = "фид" in str(conf.get("final") or "").lower()
        feed_count = _feed_item_count(ref) if requires_feed else None
        feed_ok = (not requires_feed) or (feed_count is not None and feed_count >= expected)
        show_live = bool(conf.get("show_live") or conf.get("live_required"))
        live_required = bool(conf.get("live_required"))
        live_count = _live_avito_count(ref) if show_live else None
        live_ok = (not live_required) or (live_count is not None and live_count >= expected)

        image_expected = int(conf.get("image_expected") or 0)
        banner_expected = int(conf.get("banner_expected") or 0)
        creative_required = bool(image_expected or banner_expected)
        creative = (_current_campaign_creative_stats(ref, expected)
                    if creative_required else {
                        "measurable": True, "campaign_id": None, "card_count": 0,
                        "image_count": 0, "banner_count": 0, "hero_media_count": 0,
                        "missing_local_refs": [],
                    })
        image_count = int(creative.get("image_count") or 0)
        banner_count = int(creative.get("banner_count") or 0)
        image_ok = (not image_expected) or (
            bool(creative.get("measurable")) and image_count >= image_expected
        )
        banner_ok = (not banner_expected) or (
            bool(creative.get("measurable")) and banner_count >= banner_expected
        )
        creative_ok = bool(image_ok and banner_ok)
        contract_measurable = bool(
            m["measurable"]
            and ((not requires_feed) or feed_count is not None)
            and ((not live_required) or live_count is not None)
            and ((not creative_required) or creative.get("measurable"))
        )
        overall_done = bool(
            contract_measurable
            and progress is not None and progress >= 100
            and feed_ok and live_ok and creative_ok
        )
        if overall_done:
            if creative_required:
                proof = (
                    "контрольное количество, фид и креативы подтверждены: "
                    f"фото {image_count}/{image_expected}, "
                    f"баннеры {banner_count}/{banner_expected}"
                )
            else:
                proof = ("цель подтверждена данными BORIS и Avito" if live_required
                         else ("контрольное количество и фид подтверждены"
                               if requires_feed else "целевая величина достигнута"))
            st = {"state": "DONE", "proof": proof}
        elif progress is not None and progress >= 100 and creative_required and not creative_ok:
            missing_creative = []
            if not image_ok:
                missing_creative.append(f"фото {image_count}/{image_expected}")
            if not banner_ok:
                missing_creative.append(f"баннеры {banner_count}/{banner_expected}")
            st = {
                "state": "WAITING_DEPLOY",
                "proof": "карточки готовы, но не выполнен креативный контракт: "
                         + ", ".join(missing_creative),
            }
        elif progress is not None and progress >= 100 and live_required and not live_ok:
            st = {"state": "WAITING_DEPLOY", "proof": "карточки и фид готовы, но публикация на Avito ещё не подтверждена"}
        elif progress is not None and progress >= 100 and requires_feed and not feed_ok:
            st = {"state": "WAITING_DEPLOY", "proof": "карточки готовы, но фид ещё не подтверждён полностью"}
        last_min = _minutes_since(m.get("last"))
        row = {"key": key, "name": human_title(key),
               "goal": clients.BUSINESS_GOAL.get(key, ""),
               "account": ref, "unit": m["unit"], "measurable": m["measurable"],
               "contract_measurable": contract_measurable,
               "baseline": max(0, (m["total"] or 0) - (m["delta"] or 0)),
               "current": m["total"], "delta": m["delta"],
               "last_result_min": last_min, "expected_final": conf.get("final"),
               "expected_count": expected, "progress": progress,
               "requires_feed": requires_feed, "feed_count": feed_count, "feed_ok": feed_ok,
               "show_live": show_live, "live_required": live_required,
               "live_count": live_count, "live_ok": live_ok,
               "creative_required": creative_required,
               "creative_campaign_id": creative.get("campaign_id"),
               "creative_card_count": creative.get("card_count"),
               "image_expected": image_expected, "image_count": image_count,
               "image_ok": image_ok,
               "banner_expected": banner_expected, "banner_count": banner_count,
               "banner_ok": banner_ok,
               "hero_media_count": int(creative.get("hero_media_count") or 0),
               "missing_creative_refs": creative.get("missing_local_refs") or [],
               "creative_ok": creative_ok,
               "overall_done": overall_done, "where": where_to_look(key + " —"),
               "state": st["state"], "proof": st["proof"],
               "job_status": (job or {}).get("status")}
        row.update(_current_order(job))
        if overall_done:
            out["done"] += 1
        elif st["state"] == "MOVING":
            out["moving"] += 1
        elif st["state"] in ("STALLED", "DEAD_WORKER", "NO_OPEN_ORDER",
                             "FAILED_VALIDATION", "BLOCKED"):
            out["stalled"] += 1
        out["clients"].append(row)
    out["verdict"] = "YES" if out["moving"] or out["done"] else "NO"
    _TRUTH_CACHE.update(at=time.time(), hours=int(hours), data=out)
    return out


def business_gate(title):
    """LEVEL 4 для закрытия задачи. ACCEPT — это принятая реализация, а не
    результат: клиентская задача закрывается только когда результат реально
    появился там, где его видит клиент."""
    key = next((k for k in ARTIFACTS if (title or "").startswith(k)), None)
    if not key:
        return None
    row = next((c for c in business_truth(2)["clients"] if c["key"] == key), None)
    if not row:
        return None
    if not row["measurable"]:
        return {"ok": False, "state": "NOT_MEASURABLE",
                "why": "результат нечем измерить: артефакт «%s» не находится "
                       "в базе" % row["unit"]}
    if row.get("overall_done"):
        return {"ok": True, "state": "DONE", "why": ""}
    return {"ok": False, "state": row["state"],
            "why": "бизнес-результат не достигнут: %s из «%s» (%s)"
                   % ("%d%%" % row["progress"] if row["progress"] is not None
                      else "?", row["expected_final"], row["proof"])}


def false_green(hours=2):
    """Службы живы, наряды движутся, а у клиентов ноль — это не зелёный статус.

    Самая дорогая ложь системы: всё выглядит работающим, и именно поэтому
    никто не вмешивается. Здесь она названа прямо.
    """
    d = business_truth(hours)
    events = 0
    try:
        events = (db.one("""SELECT COUNT(*) AS n FROM ext_a2a_messages
                             WHERE created_at > NOW() - (:h || ' hours')::interval""",
                         h=int(hours)) or {}).get("n") or 0
    except Exception:
        events = 0
    delta = sum(int(c.get("delta") or 0) for c in d["clients"])
    if events > 0 and delta == 0:
        return {"false_green": True, "status": "CLIENT PRODUCTION NOT MOVING",
                "protocol_events": events, "business_delta": 0,
                "why": "протокол движется, у клиентов не прибавилось ничего"}
    return {"false_green": False,
            "status": "CLIENT PRODUCTION MOVING" if delta else "НЕТ СОБЫТИЙ",
            "protocol_events": events, "business_delta": delta}


def business_truth_text(hours=2):
    d = business_truth(hours)
    lines = ["CLIENT PRODUCTION MOVING = %s" % d["verdict"],
             "CLIENTS WITH REAL DELTA = %d · CLIENTS STALLED = %d · "
             "CLIENTS DONE = %d" % (d["moving"], d["stalled"], d["done"]), ""]
    for c in d["clients"]:
        lines.append("· %s" % c["name"])
        lines.append("  цель: %s" % (c["goal"] or "—"))
        if not c["measurable"]:
            lines.append("  ИЗМЕРИТЬ НЕЧЕМ: артефакт «%s» не находится в базе — "
                         "это дефект, а не отсутствие работы" % c["unit"])
        else:
            lines.append("  было %s → стало %s (за %d ч: %+d)"
                         % (c["baseline"], c["current"], d["hours"], c["delta"]))
            lines.append("  последний реальный результат: %s"
                         % ("никогда" if c["last_result_min"] is None
                            else "%d мин назад" % c["last_result_min"]))
            if c["progress"] is not None:
                lines.append("  готовность: %d%% от «%s»"
                             % (c["progress"], c["expected_final"]))
        if c["where"]:
            lines.append("  проверить: " + c["where"])
        if c["state"] != "MOVING":
            lines.append("  состояние: %s — %s" % (c["state"], c["proof"]))
        lines.append("")
    return "\n".join(lines).rstrip()


def owner_check(hours=2, act=True):
    """Отчёт владельцу и автоматическая реакция на застой.

    Застой дольше SLA — это не строчка в отчёте, а повод к действию:
    сначала штатное восстановление, и только если оно не помогло —
    P0 владельцу с конкретной причиной.
    """
    d = business_truth(hours)
    stuck = [c for c in d["clients"]
             if c["state"] in ("STALLED", "DEAD_WORKER", "NO_OPEN_ORDER")]
    res = {"truth": d, "stalled": [c["key"] for c in stuck], "recovered": False,
           "escalated": False}
    if not stuck or not act:
        return res
    try:
        from . import recover
        recover.once()
        res["recovered"] = True
    except Exception as e:
        res["recover_error"] = "%s: %s" % (type(e).__name__, e)
    after = business_truth(hours)
    res["after"] = after
    still = [c for c in after["clients"]
             if c["state"] in ("STALLED", "DEAD_WORKER", "NO_OPEN_ORDER")]
    if still:
        from . import notify
        body = ("P0: работа по клиентам стоит дольше %d мин, штатное "
                "восстановление не помогло.\n\n" % SLA_MINUTES +
                "\n".join("· %s — %s: %s" % (c["name"], c["state"], c["proof"])
                          for c in still))
        res["escalated"] = bool(notify.send(body))
        res["escalation_text"] = body
    return res


def truth_text(hours=2):
    d = truth(hours)
    lines = ["Что прибавилось у клиентов за %d ч (по базе, а не по статусам):"
             % d["hours"]]
    for c in d["clients"]:
        name = human_title(c["key"])
        if not c["measurable"]:
            lines.append("· %s — измерить нечем: аккаунт не найден в базе" % name)
            continue
        new = c["new"]
        got = ("+%d" % new) if isinstance(new, int) else "?"
        mark = "" if (new or 0) > 0 else "  ← НЕТ РЕЗУЛЬТАТА"
        lines.append("· %s: объявлений всего %d, за период %s%s"
                     % (name, c["items"] or 0, got, mark))
    lines.append("")
    lines.append("Вердикт: " + d["verdict"])
    if d["verdict"] == "НЕТ РЕЗУЛЬТАТА":
        st = stalled()
        if st:
            lines.append("Стоят: " + "; ".join(
                "%s — %d мин" % (s["title"], s["minutes"]) for s in st[:5]))
        else:
            lines.append("Задачи не помечены как вставшие — значит время уходит "
                         "внутри работы, а не на ожидании. Это повод чинить, "
                         "а не ждать.")
    return "\n".join(lines)


def is_idle(hours=24):
    """Ничего не изменилось — значит и слать нечего.

    Отчёт раз в два часа полезен, только если он не превращается в шум:
    одинаковые сообщения перестают читать на третий день. Но молчание не
    должно прятать остановку: если работа встала, это не тишина, а новость.
    """
    if stalled():
        return False
    d = gather(hours)
    moved = (d["accepted"] or d["returned"] or d["blocked_human"] or
             d["blocked_infra"] or d["patches"])
    completed = [j for j in d["jobs"] if j["status"] == "completed"]
    return not (moved or completed)


def send(hours=24, short=False, quiet_if_idle=False):
    from . import notify
    if quiet_if_idle and is_idle(hours):
        n = _silence() + 1
        if n < SILENCE_LIMIT:
            _silence(n)
            return {"sent": False, "text": text(hours, short), "skipped": True,
                    "why": "за период ничего не сдвинулось — не шумлю (%d из %d)"
                           % (n, SILENCE_LIMIT)}
        # Молчим слишком долго. Тишина может означать не покой, а остановку,
        # и владелец должен об этом узнать сам, а не спрашивать.
        _silence(0)
        body = ("Отчёт BORIS: за последние часы движения не было совсем.\n\n"
                + text(hours, short) +
                "\n\nЕсли так и дальше — стоит проверить службы: "
                "systemctl is-active boris-executor boris-lead")
        ok = notify.send(body)
        return {"sent": bool(ok), "text": body, "skipped": False,
                "why": "" if ok else "Telegram не принял сообщение"}
    _silence(0)
    body = text(hours, short)
    ok = notify.send(body)
    return {"sent": bool(ok), "text": body, "skipped": False,
            "why": "" if ok else "Telegram не настроен или не принял сообщение"}


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="report")
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--short", action="store_true")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--quiet-if-idle", action="store_true",
                    help="молчать, если за период ничего не изменилось")
    ap.add_argument("--truth", action="store_true",
                    help="только факты: что прибавилось у клиентов в базе")
    ap.add_argument("--audit", action="store_true",
                    help="фактическая работа фабрики за период")
    ap.add_argument("--ladder", action="store_true",
                    help="ступенчатый подъём параллельности: итог")
    ap.add_argument("--offline", action="store_true",
                    help="сводка за время отсутствия владельца")
    ap.add_argument("--trace", metavar="CLIENT",
                    help="пройти путь клиента до артефакта и найти первый разрыв")
    a = ap.parse_args()
    if a.offline:
        from . import factory
        body = factory.offline_summary()
        print(body)
        if a.send:
            from . import notify
            notify.send(body)
        return 0
    if a.trace:
        from . import factory
        print(factory.trace_text(a.trace))
        return 0
    if a.ladder:
        from . import factory
        print(factory.ladder_report())
        return 0
    if a.audit:
        from . import factory
        body = factory.audit_text(a.hours if a.hours != 24 else 3)
        print(body)
        if a.send:
            from . import notify
            notify.send(body)
        return 0
    if a.truth:
        h = a.hours if a.hours != 24 else 2
        print(business_truth_text(h))
        if a.send:
            r = owner_check(h)
            print("\n— проверено; восстановление: %s; эскалация: %s"
                  % (r["recovered"], r["escalated"]))
        return 0
    if a.send:
        # Owner per-account reports are scheduled by the dedicated
        # boris-owner-daily-report.timer. Keep the generic 2-hour report
        # completely separate to avoid duplicate/racing Telegram deliveries.
        r = send(a.hours, a.short, a.quiet_if_idle)
        print(r["text"])
        print("\n— отправлено в Telegram" if r["sent"] else "\n— " + r["why"])
        # REPORT_NOTIFY_WAIT_EXTERNAL_V1: the report was produced successfully.
        # Telegram delivery is an external dependency; a rejected/unconfigured
        # notification must stay visible in the text/result but must not mark
        # the local report worker failed in systemd.
        return 0
    print(text(a.hours, a.short))
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)
