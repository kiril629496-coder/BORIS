# -*- coding: utf-8 -*-
"""
Слой записи системы мониторинга BORIS.

Работает через psycopg2 напрямую, без SQLAlchemy: таблицы созданы SQL-скриптом,
модели нужны только приложению. Так слой записи не зависит от того, как в проекте
объявлен Base, и не может уронить бэкенд импортом.

Все функции принимают курсор — транзакцией управляет вызывающий код.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

PLACEHOLDER = "%s"


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def connect():
    """Подключение по DATABASE_URL из backend/.env."""
    import psycopg2
    from dotenv import load_dotenv

    load_dotenv()
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg2://", "postgresql://")
    return psycopg2.connect(url)


# ------------------------------------------------------------------ ПРОГОНЫ

def start_run(cur, platform: str | None, mode: str = "scheduled") -> str:
    run_id = _uuid()
    cur.execute(
        "INSERT INTO monitor_runs (id, platform, mode, status, started_at) "
        "VALUES (%s, %s, %s, 'running', %s)",
        (run_id, platform, mode, _now()),
    )
    return run_id


def finish_run(cur, run_id: str, stats: dict[str, int],
               errors: list[dict[str, Any]] | None = None,
               status: str | None = None) -> None:
    errors = errors or []
    cur.execute(
        "UPDATE monitor_runs SET finished_at=%s, status=%s, scanned_sources=%s, "
        "scanned_messages=%s, matched_messages=%s, new_messages=%s, "
        "duplicate_messages=%s, sent_alerts=%s, flood_wait_seconds=%s, errors=%s "
        "WHERE id=%s",
        (_now(), status or ("failed" if errors else "ok"),
         stats.get("scanned_sources", 0), stats.get("scanned_messages", 0),
         stats.get("matched_messages", 0), stats.get("new_messages", 0),
         stats.get("duplicate_messages", 0), stats.get("sent_alerts", 0),
         stats.get("flood_wait_seconds", 0), _j(errors), run_id),
    )


# ----------------------------------------------------------------- ИСТОЧНИКИ

def load_sources(cur, platform: str | None = None) -> list[dict[str, Any]]:
    sql = ("SELECT id, account_id, platform, source_kind, external_id, username, "
           "title, topic_id, topic_title, last_seen_external_id, is_active, weight "
           "FROM monitor_sources WHERE is_active = TRUE")
    args: list[Any] = []
    if platform:
        sql += " AND platform = %s"
        args.append(platform)
    sql += " ORDER BY created_at"
    cur.execute(sql, tuple(args))
    cols = ["id", "account_id", "platform", "source_kind", "external_id", "username",
            "title", "topic_id", "topic_title", "last_seen_external_id", "is_active", "weight"]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def upsert_source(cur, src: dict[str, Any]) -> str:
    """Заведение источника вручную или из сид-файла. Повтор не создаёт дубль."""
    existing = None
    if src.get("external_id"):
        cur.execute(
            "SELECT id FROM monitor_sources WHERE platform=%s AND external_id=%s "
            "AND COALESCE(topic_id,-1)=COALESCE(%s,-1)",
            (src["platform"], src["external_id"], src.get("topic_id")),
        )
        row = cur.fetchone()
        existing = row[0] if row else None
    if existing is None and src.get("username"):
        cur.execute(
            "SELECT id FROM monitor_sources WHERE platform=%s AND lower(username)=lower(%s) "
            "AND COALESCE(topic_id,-1)=COALESCE(%s,-1)",
            (src["platform"], src["username"], src.get("topic_id")),
        )
        row = cur.fetchone()
        existing = row[0] if row else None

    if existing:
        cur.execute(
            "UPDATE monitor_sources SET title=COALESCE(%s,title), url=COALESCE(%s,url), "
            "topic_title=COALESCE(%s,topic_title), weight=COALESCE(%s,weight), updated_at=%s "
            "WHERE id=%s",
            (src.get("title"), src.get("url"), src.get("topic_title"),
             src.get("weight"), _now(), existing),
        )
        return existing

    sid = src.get("id") or _uuid()
    cur.execute(
        "INSERT INTO monitor_sources (id, account_id, platform, source_kind, external_id, "
        "username, title, url, topic_id, topic_title, is_active, weight, "
        "last_seen_external_id, created_at, updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (sid, src.get("account_id"), src["platform"], src["source_kind"],
         src.get("external_id"), src.get("username"), src.get("title"), src.get("url"),
         src.get("topic_id"), src.get("topic_title"), True,
         src.get("weight", 10), src.get("last_seen_external_id"), _now(), _now()),
    )
    return sid


def bump_source_cursor(cur, source_id: str, last_id: int | None,
                       error: str | None = None) -> None:
    if error:
        cur.execute(
            "UPDATE monitor_sources SET error_count=error_count+1, last_error=%s, "
            "last_scanned_at=%s, updated_at=%s WHERE id=%s",
            (error[:2000], _now(), _now(), source_id),
        )
        return
    cur.execute(
        "UPDATE monitor_sources SET last_seen_external_id=GREATEST("
        "COALESCE(last_seen_external_id,0), COALESCE(%s,0)), error_count=0, "
        "last_error=NULL, last_scanned_at=%s, updated_at=%s WHERE id=%s",
        (last_id, _now(), _now(), source_id),
    )


# ------------------------------------------------------------------- ПРАВИЛА

def load_rules(cur) -> list[dict[str, Any]]:
    cur.execute(
        "SELECT id, name, category, rule_type, params, platforms, source_ids, "
        "source_kinds, weights, min_score, is_active, version "
        "FROM monitor_rules WHERE is_active = TRUE ORDER BY created_at"
    )
    cols = ["id", "name", "category", "rule_type", "params", "platforms", "source_ids",
            "source_kinds", "weights", "min_score", "is_active", "version"]
    out = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        for k in ("params", "platforms", "source_ids", "source_kinds", "weights"):
            if isinstance(d[k], str):          # sqlite отдаёт строку, psycopg2 — объект
                d[k] = json.loads(d[k]) if d[k] else None
        out.append(d)
    return out


def save_rule(cur, rule: dict[str, Any], changed_by: str = "system",
              comment: str = "") -> str:
    """
    Создаёт или обновляет правило и ВСЕГДА пишет слепок в monitor_rule_versions.
    История правок не теряется даже при ручной правке через бота.
    """
    rid = rule.get("id") or _uuid()
    cur.execute("SELECT version FROM monitor_rules WHERE id=%s", (rid,))
    row = cur.fetchone()

    if row:
        version = int(row[0]) + 1
        cur.execute(
            "UPDATE monitor_rules SET name=%s, category=%s, rule_type=%s, params=%s, "
            "platforms=%s, source_ids=%s, source_kinds=%s, weights=%s, min_score=%s, "
            "is_active=%s, version=%s, updated_by=%s, updated_at=%s WHERE id=%s",
            (rule.get("name"), rule.get("category"), rule.get("rule_type", "keyword"),
             _j(rule.get("params") or {}),
             _j(rule["platforms"]) if rule.get("platforms") else None,
             _j(rule["source_ids"]) if rule.get("source_ids") else None,
             _j(rule["source_kinds"]) if rule.get("source_kinds") else None,
             _j(rule.get("weights") or {}), int(rule.get("min_score", 40)),
             bool(rule.get("is_active", True)), version, changed_by, _now(), rid),
        )
    else:
        version = 1
        cur.execute(
            "INSERT INTO monitor_rules (id, account_id, name, category, rule_type, params, "
            "platforms, source_ids, source_kinds, weights, min_score, is_active, version, "
            "updated_by, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (rid, rule.get("account_id"), rule.get("name"), rule.get("category"),
             rule.get("rule_type", "keyword"), _j(rule.get("params") or {}),
             _j(rule["platforms"]) if rule.get("platforms") else None,
             _j(rule["source_ids"]) if rule.get("source_ids") else None,
             _j(rule["source_kinds"]) if rule.get("source_kinds") else None,
             _j(rule.get("weights") or {}), int(rule.get("min_score", 40)),
             bool(rule.get("is_active", True)), version, changed_by, _now(), _now()),
        )

    snapshot = dict(rule)
    snapshot["id"] = rid
    snapshot["version"] = version
    cur.execute(
        "INSERT INTO monitor_rule_versions (id, rule_id, version, snapshot, changed_by, "
        "comment, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (_uuid(), rid, version, _j(snapshot), changed_by, comment or None, _now()),
    )
    return rid


# ------------------------------------------------------------------ КОНТАКТЫ

def upsert_contact(cur, platform: str, external_author_id: str | None,
                   username: str | None, display_name: str | None) -> str | None:
    """
    Ключ контакта — (platform, external_author_id). username меняется владельцем
    и идентичностью не является: он обновляется, но не ищется по нему.

    Счётчик сообщений здесь НЕ трогается: на этом шаге ещё неизвестно, дубль это
    или новая находка. Инкремент делает bump_contact_messages() после успешной
    вставки — иначе при повторном сборе счётчики контактов раздуваются.
    """
    if not external_author_id:
        return None
    cur.execute(
        "SELECT id FROM contacts WHERE platform=%s AND external_author_id=%s",
        (platform, str(external_author_id)),
    )
    row = cur.fetchone()
    if row:
        cid = row[0]
        cur.execute(
            "UPDATE contacts SET username=COALESCE(%s,username), "
            "display_name=COALESCE(%s,display_name), last_seen_at=%s WHERE id=%s",
            (username, display_name, _now(), cid),
        )
        return cid

    cid = _uuid()
    cur.execute(
        "INSERT INTO contacts (id, platform, external_author_id, username, display_name, "
        "contact_type, first_seen_at, last_seen_at, messages_count) "
        "VALUES (%s,%s,%s,%s,%s,'person',%s,%s,0)",
        (cid, platform, str(external_author_id), username, display_name, _now(), _now()),
    )
    return cid


def bump_contact_messages(cur, contact_id: str | None) -> None:
    """Вызывается ТОЛЬКО после реальной вставки сообщения."""
    if not contact_id:
        return
    cur.execute(
        "UPDATE contacts SET messages_count = messages_count + 1 WHERE id = %s",
        (contact_id,),
    )


# ----------------------------------------------------------------- СООБЩЕНИЯ

def save_message(cur, msg: dict[str, Any]) -> tuple[str | None, bool]:
    """
    Возвращает (id, is_new). Дубль отсекается уникальным индексом
    (platform, source_id, external_message_id) — гонка двух прогонов безопасна.
    """
    mid = _uuid()
    cur.execute(
        "INSERT INTO monitor_messages ("
        "id, platform, source_id, run_id, external_message_id, external_author_id, "
        "contact_id, author_name, author_username, topic_id, topic_title, text, "
        "posted_at, url, matched_rule_id, matched_terms, matched_rules, rules_hash, "
        "normalizer, score_total, score_breakdown, language, intent, priority, "
        "duplicates_group, summary, suggested_reply, ai, payload, status, budget_block, collected_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (platform, source_id, external_message_id) DO NOTHING "
        "RETURNING id",
        (mid, msg["platform"], msg["source_id"], msg.get("run_id"),
         str(msg["external_message_id"]), msg.get("external_author_id"),
         msg.get("contact_id"), msg.get("author_name"), msg.get("author_username"),
         msg.get("topic_id"), msg.get("topic_title"), msg.get("text"),
         msg.get("posted_at"), msg.get("url"), msg.get("matched_rule_id"),
         _j(msg.get("matched_terms") or []), _j(msg.get("matched_rules") or []),
         msg.get("rules_hash"), msg.get("normalizer"),
         int(msg.get("score_total") or 0), _j(msg.get("score_breakdown") or {}),
         msg.get("language"), msg.get("intent"), msg.get("priority"),
         msg.get("duplicates_group"),
         msg.get("summary"), msg.get("suggested_reply"),
         _j(msg.get("ai") or {}), _j(msg.get("payload") or {}),
         msg.get("status", "new"), msg.get("budget_block"), _now()),
    )
    row = cur.fetchone()
    if row:
        return row[0], True
    return None, False


def mark_sent(cur, message_id: str) -> None:
    cur.execute(
        "UPDATE monitor_messages SET status='sent', sent_at=%s WHERE id=%s AND status='new'",
        (_now(), message_id),
    )


def pending_alerts(cur, limit: int = 50, min_score: int = 40) -> list[dict[str, Any]]:
    cur.execute(
        "SELECT m.id, m.platform, m.text, m.url, m.author_username, m.author_name, "
        "m.posted_at, m.score_total, m.score_breakdown, m.matched_terms, m.summary, "
        "m.priority, s.title, s.topic_title, r.category, r.name, m.ai "
        "FROM monitor_messages m "
        "JOIN monitor_sources s ON s.id = m.source_id "
        "LEFT JOIN monitor_rules r ON r.id = m.matched_rule_id "
        "WHERE m.status='new' AND m.score_total >= %s "
        # смысловой повтор не шлём: у группы есть более раннее сообщение
        "AND (m.duplicates_group IS NULL OR NOT EXISTS ("
        "  SELECT 1 FROM monitor_messages d "
        "   WHERE d.duplicates_group = m.duplicates_group AND d.id <> m.id "
        "     AND (d.posted_at < m.posted_at OR (d.posted_at = m.posted_at AND d.id < m.id))"
        ")) "
        "ORDER BY m.score_total DESC, m.posted_at DESC LIMIT %s",
        (min_score, limit),
    )
    cols = ["id", "platform", "text", "url", "author_username", "author_name",
            "posted_at", "score_total", "score_breakdown", "matched_terms", "summary",
            "priority", "source_title", "topic_title", "category", "rule_name", "ai"]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# --------------------------------------------------------------------- ЛИДЫ

def create_lead(cur, message_id: str, manager_email: str | None = None) -> str | None:
    """Создаётся по кнопке «Создать лид». Повторное нажатие лид не дублирует."""
    cur.execute("SELECT id FROM leads WHERE message_id=%s", (message_id,))
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        "SELECT contact_id, text, score_total, suggested_reply FROM monitor_messages WHERE id=%s",
        (message_id,),
    )
    m = cur.fetchone()
    if not m:
        return None
    contact_id, text, score, reply = m

    lid = _uuid()
    cur.execute(
        "INSERT INTO leads (id, contact_id, message_id, title, status, manager_email, "
        "score_total, suggested_reply, created_at, updated_at) "
        "VALUES (%s,%s,%s,%s,'new',%s,%s,%s,%s,%s)",
        (lid, contact_id, message_id, (text or "")[:200], manager_email, score,
         reply, _now(), _now()),
    )
    cur.execute("UPDATE monitor_messages SET status='in_work' WHERE id=%s", (message_id,))
    if contact_id:
        cur.execute("UPDATE contacts SET leads_count=leads_count+1 WHERE id=%s", (contact_id,))
    return lid


# ------------------------------------------------- статусы, ответы, служебное

def set_status(cur, message_id: str, status: str) -> None:
    cur.execute("UPDATE monitor_messages SET status=%s WHERE id=%s", (status, message_id))


def prepare_reply(cur, message_id: str) -> str | None:
    """
    Отдаёт черновик ответа. Если он ещё не сгенерирован — просит модель
    и сохраняет. Ничего никому НЕ отправляет: текст уходит только в группу,
    менеджер пишет человеку сам.
    """
    cur.execute(
        "SELECT suggested_reply, text, matched_terms, score_breakdown, summary "
        "FROM monitor_messages WHERE id=%s", (message_id,))
    row = cur.fetchone()
    if not row:
        return None
    existing, text, terms, breakdown, summary = row
    if existing:
        return existing

    from . import llm
    if isinstance(terms, str):
        terms = json.loads(terms or "[]")
    if isinstance(breakdown, str):
        breakdown = json.loads(breakdown or "{}")
    data = llm.analyze(text or "", list(terms or []), dict(breakdown or {}),
                       "потенциальный клиент")
    reply = data.get("reply")
    if not reply:
        reply = (
            "Здравствуйте! Увидел ваше сообщение. Занимаюсь как раз такими задачами — "
            "могу посмотреть, что сейчас мешает получать заявки, и предложить план. "
            "Подскажите, о какой нише и городе речь?"
        )
    cur.execute("UPDATE monitor_messages SET suggested_reply=%s WHERE id=%s",
                (reply, message_id))
    return reply


def recount_contacts(cur) -> int:
    """
    Пересчёт messages_count. Поле — кеш, а не источник истины:
    после ручных удалений или сбоя его всегда можно восстановить.
    """
    # без алиаса таблицы в UPDATE — работает и в PostgreSQL, и в тестовом двойнике
    cur.execute(
        "UPDATE contacts SET messages_count = ("
        "  SELECT COUNT(*) FROM monitor_messages m WHERE m.contact_id = contacts.id)")
    cur.execute("SELECT COUNT(*) FROM contacts")
    return cur.fetchone()[0]


def request_scan(cur) -> None:
    """Заявка на внеочередной прогон — строка-маркер в monitor_runs."""
    cur.execute(
        "INSERT INTO monitor_runs (id, platform, mode, status, started_at) "
        "VALUES (%s, %s, 'manual', 'requested', %s)",
        (_uuid(), "telegram", _now()))


def take_scan_request(cur) -> bool:
    """Забирает заявку, если она есть. Возвращает True, если прогон запрошен вручную."""
    cur.execute(
        "SELECT id FROM monitor_runs WHERE status='requested' "
        "ORDER BY started_at LIMIT 1")
    row = cur.fetchone()
    if not row:
        return False
    cur.execute("UPDATE monitor_runs SET status='ok', finished_at=%s WHERE id=%s",
                (_now(), row[0]))
    return True


# ------------------------------------------------------------- тексты для бота

def status_text(cur) -> str:
    cur.execute(
        "SELECT started_at, finished_at, status, scanned_sources, scanned_messages, "
        "matched_messages, new_messages, sent_alerts FROM monitor_runs "
        "WHERE status <> 'requested' ORDER BY started_at DESC LIMIT 1")
    r = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM monitor_sources WHERE is_active")
    src = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM monitor_rules WHERE is_active")
    rul = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM monitor_messages WHERE status='new'")
    queue = cur.fetchone()[0]
    if not r:
        return f"Прогонов ещё не было.\nИсточников: {src} · правил: {rul}"
    return (
        f"<b>Последний прогон</b>: {r[2]}\n"
        f"начат: {r[0]}\nзавершён: {r[1] or '—'}\n"
        f"источников: {r[3]} · сообщений: {r[4]}\n"
        f"совпадений: {r[5]} · новых: {r[6]} · отправлено: {r[7]}\n\n"
        f"Активных источников: {src} · правил: {rul}\nВ очереди на отправку: {queue}"
    )


def stats_text(cur) -> str:
    cur.execute(
        "SELECT COUNT(*) FILTER (WHERE score_total >= 90), "
        "COUNT(*) FILTER (WHERE score_total BETWEEN 70 AND 89), "
        "COUNT(*) FILTER (WHERE score_total BETWEEN 40 AND 69), COUNT(*) "
        "FROM monitor_messages WHERE collected_at > %s",
        (_now() - __import__("datetime").timedelta(days=1),))
    hot, warm, topic, total = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM leads WHERE created_at > %s",
                (_now() - __import__("datetime").timedelta(days=1),))
    leads = cur.fetchone()[0]
    return (f"<b>За сутки</b>\nвсего находок: {total}\n"
            f"🔥 горячих: {hot} · 🙂 потенциальных: {warm} · 💬 обсуждений: {topic}\n"
            f"создано лидов: {leads}")


def sources_text(cur) -> str:
    cur.execute(
        "SELECT title, source_kind, topic_title, is_active, error_count, weight "
        "FROM monitor_sources ORDER BY created_at LIMIT 40")
    rows = cur.fetchall()
    if not rows:
        return "Источников нет."
    out = ["<b>Источники</b>"]
    for t, kind, topic, active, err, w in rows:
        mark = "✅" if active else "⏸"
        line = f"{mark} {t or '—'} · {kind}"
        if topic:
            line += f" · тема «{topic}»"
        line += f" · вес {w}"
        if err:
            line += f" · ошибок {err}"
        out.append(line)
    return "\n".join(out)


def rules_text(cur) -> str:
    cur.execute(
        "SELECT name, category, rule_type, min_score, is_active, version "
        "FROM monitor_rules ORDER BY created_at LIMIT 40")
    rows = cur.fetchall()
    if not rows:
        return "Правил нет."
    out = ["<b>Правила</b>"]
    for name, cat, rtype, ms, active, ver in rows:
        mark = "✅" if active else "⏸"
        out.append(f"{mark} {name} · {rtype} · порог {ms} · v{ver}"
                   + (f"\n   <i>{cat}</i>" if cat else ""))
    return "\n".join(out)


# ------------------------------------------------------ блокировки прогона

def try_lock_run(cur, key: int = 815127) -> bool:
    """
    Блокировка уровня прогона: два воркера не запускаются одновременно.
    Снимается автоматически при закрытии соединения.
    """
    try:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
        row = cur.fetchone()
        return bool(row and row[0])
    except Exception:  # noqa: BLE001
        return True     # не PostgreSQL (тестовый двойник) — блокировка не нужна


def claim_sources(cur, platform: str | None = None) -> list[dict[str, Any]]:
    """
    Забирает источники под обработку с пропуском занятых другим воркером.
    Аналог приёма, уже используемого в очереди задач БОРИСа.
    """
    sql = ("SELECT id, account_id, platform, source_kind, external_id, username, "
           "title, topic_id, topic_title, last_seen_external_id, is_active, weight "
           "FROM monitor_sources WHERE is_active = TRUE")
    args: list[Any] = []
    if platform:
        sql += " AND platform = %s"
        args.append(platform)
    sql += " ORDER BY created_at FOR UPDATE SKIP LOCKED"
    try:
        cur.execute(sql, tuple(args))
    except Exception:  # noqa: BLE001
        return load_sources(cur, platform)   # двойник без FOR UPDATE
    cols = ["id", "account_id", "platform", "source_kind", "external_id", "username",
            "title", "topic_id", "topic_title", "last_seen_external_id", "is_active", "weight"]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ------------------------------------------------ выборки для экрана и API
# Весь SQL живёт здесь, а не в роутере: так он проверяется тестами на двойнике,
# а слой FastAPI остаётся тонкой обвязкой без логики.

_FINDING_COLS = [
    "id", "platform", "external_message_id", "text", "url", "posted_at",
    "author_name", "author_username", "external_author_id", "contact_id",
    "score_total", "score_breakdown", "matched_terms", "matched_rules",
    "rules_hash", "normalizer", "language", "intent", "priority",
    "duplicates_group", "summary", "suggested_reply", "ai", "payload",
    "status", "sent_at", "collected_at", "source_title", "source_kind",
    "topic_title", "rule_name", "rule_category", "lead_id",
]

_FINDING_SELECT = (
    "SELECT m.id, m.platform, m.external_message_id, m.text, m.url, m.posted_at, "
    "m.author_name, m.author_username, m.external_author_id, m.contact_id, "
    "m.score_total, m.score_breakdown, m.matched_terms, m.matched_rules, "
    "m.rules_hash, m.normalizer, m.language, m.intent, m.priority, "
    "m.duplicates_group, m.summary, m.suggested_reply, m.ai, m.payload, "
    "m.status, m.sent_at, m.collected_at, s.title, s.source_kind, "
    "m.topic_title, r.name, r.category, l.id "
    "FROM monitor_messages m "
    "JOIN monitor_sources s ON s.id = m.source_id "
    "LEFT JOIN monitor_rules r ON r.id = m.matched_rule_id "
    "LEFT JOIN leads l ON l.message_id = m.id "
)


def _finding_row(r) -> dict[str, Any]:
    d = dict(zip(_FINDING_COLS, r))
    for k in ("score_breakdown", "matched_terms", "matched_rules", "ai", "payload"):
        if isinstance(d.get(k), str):
            try:
                d[k] = json.loads(d[k] or "null")
            except (json.JSONDecodeError, ValueError):
                d[k] = None
    d["is_repeat"] = False
    return d


def list_findings(cur, status: str | None = None, min_score: int | None = None,
                  source_id: str | None = None, query: str | None = None,
                  hide_repeats: bool = True, limit: int = 50,
                  offset: int = 0) -> dict[str, Any]:
    """Список находок для экрана «Поиск клиентов». Возвращает {items, total}."""
    where, args = ["1=1"], []
    if status:
        where.append("m.status = %s")
        args.append(status)
    if min_score is not None:
        where.append("m.score_total >= %s")
        args.append(int(min_score))
    if source_id:
        where.append("m.source_id = %s")
        args.append(source_id)
    if query:
        where.append("lower(m.text) LIKE %s")
        args.append(f"%{query.lower()}%")
    if hide_repeats:
        # повтор — это не отдельная находка, показываем основное сообщение группы
        where.append(
            "(m.duplicates_group IS NULL OR NOT EXISTS ("
            " SELECT 1 FROM monitor_messages d WHERE d.duplicates_group = m.duplicates_group"
            " AND d.id <> m.id AND (d.posted_at < m.posted_at"
            " OR (d.posted_at = m.posted_at AND d.id < m.id))))")

    cond = " AND ".join(where)
    cur.execute("SELECT COUNT(*) FROM monitor_messages m WHERE " + cond, tuple(args))
    total = cur.fetchone()[0]

    cur.execute(
        _FINDING_SELECT + " WHERE " + cond +
        " ORDER BY m.score_total DESC, m.posted_at DESC LIMIT %s OFFSET %s",
        tuple(args) + (int(limit), int(offset)),
    )
    return {"items": [_finding_row(r) for r in cur.fetchall()], "total": total}


def finding_detail(cur, message_id: str) -> dict[str, Any] | None:
    cur.execute(_FINDING_SELECT + " WHERE m.id = %s", (message_id,))
    r = cur.fetchone()
    if not r:
        return None
    item = _finding_row(r)

    if item.get("duplicates_group"):
        cur.execute(
            "SELECT id, url, posted_at, status FROM monitor_messages "
            "WHERE duplicates_group = %s AND id <> %s ORDER BY posted_at",
            (item["duplicates_group"], message_id))
        item["repeats"] = [
            {"id": x[0], "url": x[1], "posted_at": x[2], "status": x[3]}
            for x in cur.fetchall()
        ]
    else:
        item["repeats"] = []

    if item.get("contact_id"):
        cur.execute(
            "SELECT username, display_name, messages_count, leads_count, first_seen_at "
            "FROM contacts WHERE id = %s", (item["contact_id"],))
        c = cur.fetchone()
        if c:
            item["contact"] = {"username": c[0], "display_name": c[1],
                               "messages_count": c[2], "leads_count": c[3],
                               "first_seen_at": c[4]}
    return item


def list_leads(cur, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    sql = ("SELECT l.id, l.title, l.status, l.manager_email, l.score_total, "
           "l.created_at, c.username, c.display_name, m.url "
           "FROM leads l LEFT JOIN contacts c ON c.id = l.contact_id "
           "LEFT JOIN monitor_messages m ON m.id = l.message_id")
    args: list[Any] = []
    if status:
        sql += " WHERE l.status = %s"
        args.append(status)
    sql += " ORDER BY l.created_at DESC LIMIT %s"
    args.append(int(limit))
    cur.execute(sql, tuple(args))
    cols = ["id", "title", "status", "manager_email", "score_total", "created_at",
            "username", "display_name", "url"]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def dashboard_stats(cur, days: int = 7) -> dict[str, Any]:
    """Сводка для шапки экрана. Только реальные записи, ничего выдуманного."""
    import datetime as _dt
    since = _now() - _dt.timedelta(days=days)

    cur.execute(
        "SELECT COUNT(*), "
        "COUNT(*) FILTER (WHERE score_total >= 90), "
        "COUNT(*) FILTER (WHERE score_total BETWEEN 70 AND 89), "
        "COUNT(*) FILTER (WHERE score_total BETWEEN 40 AND 69), "
        "COUNT(*) FILTER (WHERE status = 'new') "
        "FROM monitor_messages WHERE collected_at > %s", (since,))
    total, hot, warm, topic, fresh = cur.fetchone()

    cur.execute("SELECT COUNT(*) FROM leads WHERE created_at > %s", (since,))
    leads = cur.fetchone()[0]

    # "найдено сегодня" — отдельно от окна в N дней
    today = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    cur.execute("SELECT COUNT(*), "
                "COUNT(*) FILTER (WHERE score_total >= 90), "
                "COUNT(*) FILTER (WHERE score_total BETWEEN 70 AND 89) "
                "FROM monitor_messages WHERE collected_at >= %s", (today,))
    t_total, t_hot, t_warm = cur.fetchone()

    cur.execute(
        "SELECT s.title, COUNT(*) FROM monitor_messages m "
        "JOIN monitor_sources s ON s.id = m.source_id "
        "WHERE m.collected_at > %s GROUP BY s.title ORDER BY COUNT(*) DESC LIMIT 10",
        (since,))
    by_source = [{"title": r[0], "count": r[1]} for r in cur.fetchall()]

    cur.execute(
        "SELECT r.name, COUNT(*) FROM monitor_messages m "
        "JOIN monitor_rules r ON r.id = m.matched_rule_id "
        "WHERE m.collected_at > %s GROUP BY r.name ORDER BY COUNT(*) DESC LIMIT 10",
        (since,))
    by_rule = [{"name": r[0], "count": r[1]} for r in cur.fetchall()]

    cur.execute(
        "SELECT started_at, status, scanned_sources, scanned_messages, "
        "matched_messages, new_messages, sent_alerts FROM monitor_runs "
        "WHERE status <> 'requested' ORDER BY started_at DESC LIMIT 1")
    r = cur.fetchone()
    last_run = None
    if r:
        last_run = {"started_at": r[0], "status": r[1], "scanned_sources": r[2],
                    "scanned_messages": r[3], "matched_messages": r[4],
                    "new_messages": r[5], "sent_alerts": r[6]}

    return {"days": days, "total": total, "hot": hot, "warm": warm, "topic": topic,
            "unhandled": fresh, "leads": leads,
            "today_total": t_total, "today_hot": t_hot, "today_warm": t_warm,
            "by_source": by_source, "by_rule": by_rule, "last_run": last_run}
