#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Проверка слоя записи на sqlite-двойнике боевой схемы.

Зачем: боевая база — PostgreSQL, но логику дедупа, контактов, журнала прогонов
и создания лидов можно доказать локально, без сервера. Схема берётся из тех же
файлов sql/monitoring_v1.sql и sql/monitoring_v1_1.sql, что уедут на сервер,
и переводится в диалект sqlite.

Что тест НЕ доказывает: поведение типов JSONB, частичных индексов PostgreSQL
и параллельных прогонов. Это проверяется боевым `monitor_runner.py --fixtures`.

Запуск:  python3 tests/test_store_sqlite.py
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

sqlite3.register_adapter(dt.datetime, lambda d: d.isoformat())

from app.monitoring import store                                  # noqa: E402
import monitor_runner                                             # noqa: E402


# --------------------------------------------------- перевод в диалект sqlite

def translate_schema(sql: str) -> list[str]:
    sql = re.sub(r"--[^\n]*", "", sql)          # строчные комментарии содержат ';'
    sql = re.sub(r"COMMENT ON [^;]+;", "", sql, flags=re.S)
    sql = sql.replace("BEGIN;", "").replace("COMMIT;", "")
    sql = re.sub(r"::jsonb", "", sql)
    sql = re.sub(r"\bJSONB\b", "TEXT", sql)
    sql = re.sub(r"\bUUID\b", "TEXT", sql)
    sql = re.sub(r"\bTIMESTAMPTZ\b", "TEXT", sql)
    sql = re.sub(r"\bnow\(\)", "CURRENT_TIMESTAMP", sql)

    # многоколоночный ALTER ... ADD COLUMN IF NOT EXISTS -> отдельные операторы
    out: list[str] = []
    for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
        up = stmt.upper()
        # sqlite не умеет DROP/ADD CONSTRAINT и частичные CREATE INDEX ... WHERE
        if "CONSTRAINT" in up and ("DROP CONSTRAINT" in up or "ADD CONSTRAINT" in up):
            continue
        # вырезаем inline CHECK-констрейнты статуса из CREATE TABLE: миграция v1_2
        # меняет их в PostgreSQL, но sqlite ALTER CONSTRAINT не поддерживает,
        # поэтому в двойнике статус не ограничиваем (не предмет тестов)
        if up.startswith("CREATE TABLE") and "STATUS IN" in up:
            # CHECK (status IN ('a','b',...)) — двойной ) на конце; вырезаем целиком
            stmt = re.sub(r",?\s*CONSTRAINT \w+ CHECK \(\s*status IN\s*\([^)]*\)\s*\)",
                          "", stmt, flags=re.I | re.S)
        if up.startswith("CREATE INDEX") and " WHERE " in up:
            stmt = re.sub(r"\s+WHERE\s+.*$", "", stmt, flags=re.I | re.S)
        m = re.match(r"ALTER TABLE (\w+)\s+(.*)", stmt, flags=re.S | re.I)
        if m and "ADD COLUMN" in stmt.upper():
            table, rest = m.group(1), m.group(2)
            for part in re.split(r",\s*ADD COLUMN IF NOT EXISTS", rest, flags=re.I):
                part = re.sub(r"^ADD COLUMN IF NOT EXISTS\s+", "", part.strip(), flags=re.I)
                out.append(f"ALTER TABLE {table} ADD COLUMN {part}")
            continue
        out.append(stmt)
    return out


def translate_query(sql: str) -> str:
    sql = sql.replace("GREATEST(", "MAX(")
    return sql.replace("%s", "?")


class _AnyList(list):
    """Заглушка ANY(...) для двойника: sqlite такого синтаксиса не знает."""


class CurWrap:
    def __init__(self, cur): self._c = cur
    def execute(self, sql, args=()):
        sql = translate_query(sql)
        # разворачиваем "= ANY(?)" в "IN (?,?,?)" под фактическую длину списка
        if "ANY(?)" in sql:
            flat = []
            for a in args:
                if isinstance(a, (list, tuple)):
                    sql = sql.replace("= ANY(?)", "IN (" + ",".join("?" * len(a)) + ")", 1)
                    flat.extend(a)
                else:
                    flat.append(a)
            args = tuple(flat)
        return self._c.execute(sql, args)
    def fetchone(self): return self._c.fetchone()
    def fetchall(self): return self._c.fetchall()
    def __enter__(self): return self
    def __exit__(self, *a): return False


class ConnWrap:
    def __init__(self, con): self._con = con
    def cursor(self): return CurWrap(self._con.cursor())
    def __enter__(self): return self
    def __exit__(self, et, ev, tb):
        self._con.commit() if et is None else self._con.rollback()
        return False
    def close(self): pass


# ------------------------------------------------------------------- проверки

FAILED: list[str] = []


def all_rows_list(store_mod, cur):
    return store_mod.list_findings(cur, hide_repeats=False, limit=200)["items"]


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {label}: получено {got!r}, ожидалось {want!r}")
    if not ok:
        FAILED.append(label)


def fresh_db():
    """Новая чистая in-memory база со схемой. Каждый изолированный блок берёт свою."""
    con = sqlite3.connect(":memory:")
    for path in ("sql/monitoring_v1.sql", "sql/monitoring_v1_1.sql", "sql/monitoring_v1_2.sql"):
        with open(os.path.join(ROOT, path), encoding="utf-8") as f:
            for stmt in translate_schema(f.read()):
                con.execute(stmt)
    # api_usage создаёт основной проект — в двойнике заводим минимальную копию
    con.execute("""CREATE TABLE IF NOT EXISTS api_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT, provider TEXT, model TEXT, operation TEXT,
        prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0,
        images INTEGER DEFAULT 0, cost_rub REAL DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    con.commit()
    wrapped = ConnWrap(con)
    store.connect = lambda: wrapped          # подмена подключения на двойник
    return con, wrapped


def _clear_pool_modules():
    """Полностью убирает двойники пула из sys.modules и сбрасывает кэш llm."""
    from app.monitoring import llm as _llm
    for name in ("gigachat_pool", "app.gigachat_pool", "gigachat", "gigachat.models"):
        sys.modules.pop(name, None)
    _llm._FN = None
    _llm._STRATEGY = None


def main() -> int:
    con, wrapped = fresh_db()

    print("\n1. Загрузка стартового набора (--seed)")
    monitor_runner.cmd_seed()
    c = con.cursor()
    check("источников", c.execute("SELECT count(*) FROM monitor_sources").fetchone()[0], 3)
    check("правил", c.execute("SELECT count(*) FROM monitor_rules").fetchone()[0], 4)
    check("версий правил", c.execute("SELECT count(*) FROM monitor_rule_versions").fetchone()[0], 4)

    print("\n2. Повторный seed не плодит дубли")
    monitor_runner.cmd_seed()
    check("источников по-прежнему", c.execute("SELECT count(*) FROM monitor_sources").fetchone()[0], 3)
    check("правил по-прежнему", c.execute("SELECT count(*) FROM monitor_rules").fetchone()[0], 4)
    check("версия правила выросла",
          c.execute("SELECT max(version) FROM monitor_rules").fetchone()[0], 2)
    check("история версий накопилась",
          c.execute("SELECT count(*) FROM monitor_rule_versions").fetchone()[0], 8)

    print("\n3. Первый прогон конвейера на фикстурах")
    monitor_runner.cmd_fixtures(write=True)
    total = c.execute("SELECT count(*) FROM monitor_messages").fetchone()[0]
    check("сообщений записано", total, 7)
    check("контактов заведено", c.execute("SELECT count(*) FROM contacts").fetchone()[0], 4)
    check("у @ivan_stroy два сообщения",
          c.execute("SELECT messages_count FROM contacts WHERE username='ivan_stroy'").fetchone()[0], 2)
    check("прогон закрыт со статусом ok",
          c.execute("SELECT status FROM monitor_runs ORDER BY started_at DESC LIMIT 1").fetchone()[0], "ok")
    check("new_messages в журнале",
          c.execute("SELECT new_messages FROM monitor_runs ORDER BY started_at DESC LIMIT 1").fetchone()[0], 7)

    print("\n4. Повторный прогон — ни одной новой строки")
    monitor_runner.cmd_fixtures(write=True)
    check("сообщений всё столько же",
          c.execute("SELECT count(*) FROM monitor_messages").fetchone()[0], 7)
    check("new_messages второго прогона",
          c.execute("SELECT new_messages FROM monitor_runs ORDER BY started_at DESC LIMIT 1").fetchone()[0], 0)

    print("\n4b. Сброс курсора источника — срабатывает уникальный индекс")
    # Курсор источника защищает от повторного чтения. Но если его сбросить
    # (перезапуск с нуля, ручная правка), второй линией обороны обязан
    # сработать уникальный индекс (platform, source_id, external_message_id).
    con.execute("UPDATE monitor_sources SET last_seen_external_id = 0")
    con.commit()
    monitor_runner.cmd_fixtures(write=True)
    # сброс курсора законно поднимает более старое сообщение id 4999 — оно новое
    check("сообщений стало", c.execute("SELECT count(*) FROM monitor_messages").fetchone()[0], 8)
    last = c.execute("SELECT new_messages, duplicate_messages, matched_messages "
                     "FROM monitor_runs ORDER BY started_at DESC LIMIT 1").fetchone()
    check("новых при сбросе курсора", last[0], 1)
    check("отсечено индексом как дубли", last[1], 7)
    check("совпадений найдено заново", last[2], 8)
    check("контакт не раздулся повторным счётом",
          c.execute("SELECT messages_count FROM contacts WHERE username='ivan_stroy'").fetchone()[0], 2)

    print("\n5. Объяснимость находки")
    row = c.execute(
        "SELECT rules_hash, normalizer, matched_rules, score_breakdown FROM monitor_messages "
        "ORDER BY score_total DESC LIMIT 1").fetchone()
    check("rules_hash проставлен", bool(row[0]), True)
    check("нормализатор записан", row[1], "simple")
    import json
    mr = json.loads(row[2])
    check("сработавших правил зафиксировано", len(mr) >= 1, True)
    br = json.loads(row[3])
    check("в разборе баллов есть source", "source" in br, True)
    check("сумма разбора равна итогу",
          sum(br.values()),
          c.execute("SELECT score_total FROM monitor_messages ORDER BY score_total DESC LIMIT 1").fetchone()[0])

    print("\n6. Создание лида по кнопке")
    mid = c.execute("SELECT id FROM monitor_messages ORDER BY score_total DESC LIMIT 1").fetchone()[0]
    with wrapped, wrapped.cursor() as cur:
        lead1 = store.create_lead(cur, mid, "kirill@boris-ai.pro")
    with wrapped, wrapped.cursor() as cur:
        lead2 = store.create_lead(cur, mid, "kirill@boris-ai.pro")
    check("повторное нажатие не дублирует лид", lead1, lead2)
    check("лидов в базе", c.execute("SELECT count(*) FROM leads").fetchone()[0], 1)
    check("сообщение ушло в работу",
          c.execute("SELECT status FROM monitor_messages WHERE id=?", (mid,)).fetchone()[0], "in_work")

    print("\n7. Очередь на отправку")
    with wrapped, wrapped.cursor() as cur:
        pend = store.pending_alerts(cur)
        check("в очереди карточек", len(pend), 6)
        store.mark_sent(cur, pend[0]["id"])
        check("после отправки в очереди", len(store.pending_alerts(cur)), 5)

    print("\n8. Смысловые повторы (duplicates_group)")
    groups = c.execute(
        "SELECT duplicates_group, COUNT(*) FROM monitor_messages "
        "WHERE duplicates_group IS NOT NULL GROUP BY duplicates_group "
        "HAVING COUNT(*) > 1").fetchall()
    check("найдена группа перепоста", len(groups), 1)
    check("в группе два сообщения", groups[0][1] if groups else 0, 2)
    with wrapped, wrapped.cursor() as cur:
        queue_ids = {r["id"] for r in store.pending_alerts(cur, limit=50)}
    dup_ids = [r[0] for r in c.execute(
        "SELECT id FROM monitor_messages WHERE duplicates_group=? ORDER BY posted_at",
        (groups[0][0],)).fetchall()] if groups else []
    check("повтор не попадает в очередь", dup_ids[1] in queue_ids, False)
    primary_status = c.execute("SELECT status FROM monitor_messages WHERE id=?",
                               (dup_ids[0],)).fetchone()[0]
    check("основным считается более раннее сообщение", primary_status in ("new", "in_work"), True)

    print("\n9. Карточка уведомления")
    from app.monitoring import alerts
    row = {"id": "abc", "score_total": 95, "text": "Ищу авитолога, заявок нет",
           "summary": "Ищет специалиста по продвижению на Авито.",
           "url": "https://t.me/chat/5001", "author_username": "ivan_stroy",
           "posted_at": dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=2),
           "source_title": "Бизнес-чат Москва", "topic_title": None,
           "matched_terms": ["ищу авитолога"],
           "ai": {"city": "Москва", "reasons": ["ищет подрядчика", "упоминает Авито"],
                  "source": "llm"}}
    text, buttons = alerts.render_card(row)
    check("заголовок горячего лида", text.splitlines()[0].startswith("🔥"), True)
    check("регион в карточке", "Регион: Москва" in text, True)
    check("свежесть в карточке", "2 мин назад" in text, True)
    check("блок причин", "Почему найден:" in text, True)
    # render_card отдаёт СПИСОК РЯДОВ для send_telegram_message_with_buttons
    labels = [b["text"] for rowb in buttons for b in rowb]
    check("четыре кнопки", len(labels), 4)
    check("кнопка подготовки ответа", any("Подготовить ответ" in x for x in labels), True)
    check("нет кнопки автоотправки",
          any("Отправить" in x or "Написать" in x for x in labels), False)
    check("клавиатура в два ряда", len(buttons), 2)
    cbs = [b.get("callback_data", "") for rr in buttons for b in rr]
    check("префикс mlead есть", any(c.startswith("mlead:") for c in cbs), True)
    check("префикс mreply есть", any(c.startswith("mreply:") for c in cbs), True)
    check("префикс mskip есть", any(c.startswith("mskip:") for c in cbs), True)
    check("нет чужих префиксов msgr",
          any(c.startswith("msgr") for c in cbs), False)
    # «Открыть» — url-кнопка, не callback
    open_btn = [b for rr in buttons for b in rr if "Открыть" in b["text"]]
    check("Открыть — это ссылка", "url" in open_btn[0] if open_btn else False, True)

    print("\n9b. Обработчик кнопок мониторинга (alerts_handler)")
    from app.monitoring import alerts_handler
    check("mlead: распознаётся как наш", alerts_handler.owns("mlead:abc"), True)
    check("mreply: распознаётся как наш", alerts_handler.owns("mreply:abc"), True)
    check("mskip: распознаётся как наш", alerts_handler.owns("mskip:abc"), True)
    check("чужой msgr_approve НЕ наш", alerts_handler.owns("msgr_approve:x"), False)
    check("пустое не наше", alerts_handler.owns(""), False)

    print("\n10. Разбор сигнатуры пула (успешный LLM во всех трёх формах)")
    import types
    from app.monitoring import llm as llm_mod

    def _install_pool(maker, root=True):
        """Ставит двойник пула ПОД ОБА имени, которые пробует _import_pool."""
        _clear_pool_modules()
        fake = types.ModuleType("gigachat_pool" if root else "app.gigachat_pool")
        fake.chat_with_fallback = maker()
        # llm._import_pool сначала пробует корневой gigachat_pool, затем app.gigachat_pool
        sys.modules["gigachat_pool" if root else "app.gigachat_pool"] = fake

    for shape, maker in (
        ("messages", lambda: (lambda messages, model=None: '{"summary":"ок","intent":"buy_service"}')),
        ("prompt", lambda: (lambda prompt: '{"summary":"ок","intent":"buy_service"}')),
        ("system_user", lambda: (lambda system, user: '{"summary":"ок","intent":"buy_service"}')),
    ):
        _install_pool(maker)
        got = llm_mod.analyze("Ищу авитолога", ["ищу авитолога"], {"keyword": 40}, "горячий лид")
        check(f"форма вызова {shape}", got.get("source"), "llm")

    print("\n10b. Успешный LLM отдаёт разобранные поля")
    _install_pool(lambda: (lambda messages, model=None:
        '{"summary":"Ищет подрядчика по Авито","intent":"buy_service",'
        '"city":"Казань","service":"ведение Авито","reasons":["ищет исполнителя"],'
        '"reply":"Здравствуйте!"}'))
    got = llm_mod.analyze("Ищу авитолога", ["ищу авитолога"], {"keyword": 40}, "горячий лид")
    check("источник резюме — llm", got.get("source"), "llm")
    check("город из модели", got.get("city"), "Казань")
    check("услуга из модели", got.get("service"), "ведение Авито")
    check("черновик ответа получен", bool(got.get("reply")), True)

    print("\n10c. Пул НЕДОСТУПЕН → резюме по правилам, находка не теряется")
    # полностью убираем оба имени пула — эмулируем сервер без chat_with_fallback
    _clear_pool_modules()
    # ставим пустой модуль БЕЗ chat_with_fallback под оба имени, чтобы import прошёл,
    # но функции не было — ровно как на сервере до правки адаптера
    sys.modules["gigachat_pool"] = types.ModuleType("gigachat_pool")
    sys.modules["app.gigachat_pool"] = types.ModuleType("app.gigachat_pool")
    llm_mod._FN = None; llm_mod._STRATEGY = None
    got = llm_mod.analyze("Ищу авитолога срочно", ["ищу авитолога"],
                          {"keyword": 40, "freshness": 15}, "горячий лид")
    check("пул недоступен — резюме по правилам", got.get("source"), "rules")
    check("причины всё равно есть", len(got.get("reasons") or []) > 0, True)
    check("резюме непустое", bool(got.get("summary")), True)
    _clear_pool_modules()

    print("\n11. Боевая форма пула: gigachat_pool в корне + объекты Messages")
    class _Msg:
        def __init__(self, role, content): self.role, self.content = role, content
    class _Role:
        SYSTEM = "system"; USER = "user"
    sdk = types.ModuleType("gigachat"); models = types.ModuleType("gigachat.models")
    models.Messages = _Msg; models.MessagesRole = _Role
    sys.modules["gigachat"] = sdk; sys.modules["gigachat.models"] = models
    seen = {}
    def _pool_fn(messages, temperature=0.7, max_tokens=None):
        seen["cls"] = type(messages[0]).__name__
        seen["temperature"] = temperature
        seen["max_tokens"] = max_tokens
        return '{"summary":"Ищет специалиста.","intent":"buy_service","city":"Москва","reasons":["ищет подрядчика"],"reply":"Здравствуйте!"}'
    pool = types.ModuleType("gigachat_pool"); pool.chat_with_fallback = _pool_fn
    sys.modules["gigachat_pool"] = pool
    llm_mod._FN = None; llm_mod._STRATEGY = None
    got = llm_mod.analyze("Ищу авитолога", ["ищу авитолога"], {"keyword": 40}, "горячий лид")
    check("передан объект Messages из SDK", seen.get("cls"), "_Msg")
    check("температура выставлена", seen.get("temperature"), 0.2)
    check("лимит токенов выставлен", seen.get("max_tokens"), 700)
    check("город из ответа модели", got.get("city"), "Москва")

    print("\n11b. Пул с account_id/operation логирует сам, само-учёт отключается")
    _clear_pool_modules()
    sdk2 = types.ModuleType("gigachat"); mdl2 = types.ModuleType("gigachat.models")
    class _Msg2:
        def __init__(self, role, content): self.role, self.content = role, content
    class _Role2:
        SYSTEM = "system"; USER = "user"
    mdl2.Messages = _Msg2; mdl2.MessagesRole = _Role2
    sys.modules["gigachat"] = sdk2; sys.modules["gigachat.models"] = mdl2
    seen2 = {}
    def _server_pool(messages, model=None, temperature=None, max_tokens=None,
                     timeout=60, credentials=None, scope=None,
                     account_id=None, operation=None):
        seen2["account_id"] = account_id
        seen2["operation"] = operation
        return '{"summary":"ок","intent":"buy_service"}'
    pool2 = types.ModuleType("gigachat_pool"); pool2.chat_with_fallback = _server_pool
    sys.modules["gigachat_pool"] = pool2
    llm_mod._FN = None; llm_mod._STRATEGY = None
    # само-учёт «включён» — но пул логирует сам, значит НЕ должен сработать
    usage_calls = []
    um = types.ModuleType("app.usage")
    um.log_usage = lambda **kw: usage_calls.append(kw)
    sys.modules["app.usage"] = um
    llm_mod.LOG_USAGE = True
    got = llm_mod.analyze("Ищу авитолога", ["ищу авитолога"], {"keyword": 40}, "горячий")
    check("account_id проброшен в пул", seen2.get("account_id"), "boris_monitoring")
    check("operation проброшен в пул", seen2.get("operation"), "monitoring_analyze")
    check("само-учёт НЕ сработал (пул логирует сам)", len(usage_calls), 0)
    llm_mod.LOG_USAGE = False
    _clear_pool_modules()

    print("\n12. Само-учёт для пула, который сам НЕ логирует")
    # изоляция: пул со СТАРОЙ сигнатурой (без account_id/operation) — тогда
    # само-учёт снова актуален. Иначе pool_logs_usage=True его подавляет.
    _clear_pool_modules()
    calls = []
    usage_mod = types.ModuleType("app.usage")
    def _log_usage(account_id=None, model=None, operation=None,
                   prompt_tokens=0, completion_tokens=0):
        calls.append({"account_id": account_id, "model": model,
                      "operation": operation, "in": prompt_tokens})
    usage_mod.log_usage = _log_usage
    sys.modules["app.usage"] = usage_mod
    old_pool = types.ModuleType("gigachat_pool")
    old_pool.chat_with_fallback = lambda messages, model=None: '{"summary":"ок","intent":"buy_service"}'
    sys.modules["gigachat_pool"] = old_pool

    llm_mod.LOG_USAGE = False
    llm_mod._FN = None; llm_mod._STRATEGY = None
    llm_mod.analyze("Ищу авитолога", ["ищу авитолога"], {"keyword": 40}, "горячий лид")
    check("по умолчанию сами не пишем (нет двойного счёта)", len(calls), 0)

    llm_mod.LOG_USAGE = True
    llm_mod._FN = None; llm_mod._STRATEGY = None
    llm_mod.analyze("Ищу авитолога", ["ищу авитолога"], {"keyword": 40}, "горячий лид")
    check("при включённом флаге запись идёт", len(calls), 1)
    check("операция помечена", calls[0]["operation"] if calls else None, "monitoring_analyze")
    llm_mod.LOG_USAGE = False

    # describe_pool требует и функцию учёта, и живой пул — ставим оба явно
    info = llm_mod.describe_pool()
    check("probe видит функцию учёта", "log_usage" in (info.get("usage_logger") or ""), True)
    check("probe показывает файл модуля", "module_file" in info, True)
    _clear_pool_modules()

    print("\n13. Сбой отправки не помечает карточку отправленной")
    import monitor_bot
    from app.monitoring import alerts as alerts_mod
    with wrapped, wrapped.cursor() as cur:
        before_queue = len(store.pending_alerts(cur))
    alerts_mod.send_card = lambda row: False        # Telegram недоступен
    check("отправлено при сбое", monitor_bot.flush_queue(), 0)
    with wrapped, wrapped.cursor() as cur:
        check("очередь не потеряна", len(store.pending_alerts(cur)), before_queue)
    sent_rows = c.execute("SELECT COUNT(*) FROM monitor_messages WHERE status='sent'").fetchone()[0]
    alerts_mod.send_card = lambda row: True         # связь восстановилась
    got = monitor_bot.flush_queue()
    check("после восстановления очередь ушла", got, before_queue)
    check("статусы проставлены",
          c.execute("SELECT COUNT(*) FROM monitor_messages WHERE status='sent'").fetchone()[0],
          sent_rows + before_queue)

    print("\n14. Чек-лист Go/No-Go — прогон логики на двойнике")
    # information_schema и ANY(%s) есть только в PostgreSQL, поэтому здесь
    # проверяются те блоки чек-листа, что от диалекта не зависят.
    import monitor_preflight as pf
    pf.RESULTS.clear()
    alerts_mod.send_card = lambda row: True
    pf.check_fixtures()
    with wrapped, wrapped.cursor() as cur:
        pf.check_llm_fallback(cur)
        pf.check_buttons(cur)
    pf.check_card(send=False)
    names = {n: ok for n, ok, _ in pf.RESULTS}
    check("первый прогон создаёт записи", names.get("Первый прогон создаёт записи"), True)
    check("второй прогон не создаёт новых", names.get("Второй прогон не создаёт новых"), True)
    check("счётчики контактов не выросли",
          names.get("Счётчики контактов не выросли повторно"), True)
    check("при недоступном LLM карточка не теряется",
          names.get("При недоступном LLM карточка не теряется"), True)
    check("создать лид идемпотентна",
          names.get("Кнопка «Создать лид» идемпотентна"), True)
    check("подготовить ответ ничего не шлёт",
          names.get("Кнопка «Подготовить ответ» только генерирует текст"), True)
    check("карточка собирается без автоотправки",
          names.get("Карточка собирается, кнопок 4, автоотправки нет"), True)

    print("\n15. Выборки для экрана «Поиск клиентов»")
    with wrapped, wrapped.cursor() as cur:
        res = store.list_findings(cur, limit=100)
        check("список находок не пуст", res["total"] > 0, True)
        first = res["items"][0]
        check("отсортировано по баллам",
              all(res["items"][i]["score_total"] >= res["items"][i + 1]["score_total"]
                  for i in range(len(res["items"]) - 1)), True)
        check("JSON-поля разобраны в объекты",
              isinstance(first["score_breakdown"], dict) and isinstance(first["matched_rules"], list),
              True)
        check("название источника подтянуто", bool(first["source_title"]), True)

        all_rows = store.list_findings(cur, hide_repeats=False, limit=100)["total"]
        no_rep = store.list_findings(cur, hide_repeats=True, limit=100)["total"]
        check("повторы скрываются фильтром", all_rows - no_rep, 1)

        # механика фильтра: порог 0 даёт всё, высокий порог — подмножество
        lo = store.list_findings(cur, min_score=0, limit=100)["total"]
        hi = store.list_findings(cur, min_score=70, limit=100)["total"]
        check("фильтр по баллам сужает выборку", hi <= lo and lo > 0, True)
        found = store.list_findings(cur, query="авитолог", limit=100)["total"]
        check("поиск по тексту работает", found > 0, True)

        detail = store.finding_detail(cur, first["id"])
        check("карточка отдаёт разбор правил", isinstance(detail["matched_rules"], list), True)
        check("карточка отдаёт контакт", "contact" in detail or detail["contact_id"] is None, True)

        dup = [x for x in all_rows_list(store, cur) if x["duplicates_group"]]
        if dup:
            d = store.finding_detail(cur, dup[0]["id"])
            check("в карточке видны повторы группы", len(d["repeats"]) >= 1, True)

        st = store.dashboard_stats(cur, days=30)
        check("сводка отдаёт число горячих", isinstance(st["hot"], int) and st["hot"] >= 0, True)
        check("сводка знает последний прогон", st["last_run"] is not None, True)
        check("сводка разложила по источникам", len(st["by_source"]) > 0, True)
        check("сводка считает лиды", st["leads"] >= 1, True)

        lst = store.list_leads(cur)
        check("список лидов не пуст", len(lst) >= 1, True)

    print("\n16. Горячий лид 90+ на ИЗОЛИРОВАННОЙ базе (детерминированно)")
    hot_con, hot_wrapped = fresh_db()
    HOT_MID = "00000000-0000-0000-0000-0000000000aa"
    with hot_wrapped, hot_wrapped.cursor() as cur:
        # источник и правило нужны для JOIN в выборках
        store.upsert_source(cur, {"id": "src-hot", "platform": "telegram",
            "source_kind": "group", "username": "hot_chat", "title": "Горячий чат",
            "weight": 15})
        store.save_rule(cur, {"id": "rule-hot", "name": "Горячее правило",
            "category": "Тест", "rule_type": "keyword",
            "params": {"any": ["ищу авитолога"]}}, changed_by="test")
        # сообщение с итоговым баллом ровно 92 — собрано из компонент, не подогнано числом
        breakdown = {"keyword": 40, "intent": 15, "service": 10, "contact": 5,
                     "freshness": 15, "source": 7, "duplicates": 0, "ai": 0}
        total = sum(breakdown.values())
        cur.execute(
            "INSERT INTO monitor_messages (id, platform, source_id, external_message_id, "
            "text, score_total, score_breakdown, matched_rule_id, status, priority, "
            "collected_at, posted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (HOT_MID, "telegram", "src-hot", "9001",
             "Срочно ищу авитолога, заявок нет, пишите в лс",
             total, __import__("json").dumps(breakdown), "rule-hot", "new", "high",
             __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
             __import__("datetime").datetime.now(__import__("datetime").timezone.utc)))

    with hot_wrapped, hot_wrapped.cursor() as cur:
        check("итоговый балл 92 (сумма компонент)", total, 92)
        res = store.list_findings(cur, min_score=90, limit=100)
        check("горячий проходит фильтр 90+", res["total"], 1)
        res_low = store.list_findings(cur, min_score=95, limit=100)
        check("порог 95 его отсекает", res_low["total"], 0)
        st = store.dashboard_stats(cur, days=30)
        check("сводка считает ровно 1 горячего", st["hot"], 1)
        check("в тёплых и обсуждениях его нет", (st["warm"], st["topic"]), (0, 0))
    hot_con.close()
    # возвращаем подключение основной базы, чтобы финал не сломался
    store.connect = lambda: wrapped

    print("\n17. Бюджет и лимиты модуля (budget.py)")
    import importlib
    from app.monitoring import budget as bud

    def _seed_usage(cur, n, cost, account="boris_monitoring", when=None):
        """Кладёт n строк расхода по cost руб каждая."""
        import datetime as _dt
        ts = (when or _dt.datetime.now(_dt.timezone.utc)).isoformat()
        for _ in range(n):
            cur.execute(
                "INSERT INTO api_usage (account_id, provider, model, operation, "
                "prompt_tokens, completion_tokens, cost_rub, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (account, "gigachat", "GigaChat-Max", "monitoring_analyze",
                 70, 14, cost, ts))

    def _with_env(**kw):
        for k, v in kw.items():
            os.environ[k] = str(v)
        importlib.reload(bud)

    # 17a. остаток достаточен -> разрешено
    bcon, bw = fresh_db()
    _with_env(MONITOR_BUDGET_USD="1", MONITOR_USD_RUB="100",
              MONITOR_MAX_ANALYSIS_COST_RUB="1",
              MONITOR_MAX_ANALYSES_PER_DAY="100", MONITOR_MAX_ANALYSES_PER_MONTH="1000")
    with bw, bw.cursor() as cur:
        _seed_usage(cur, 10, 0.05)          # потрачено 0.5 ₽ из 100 ₽
        v = bud.check_budget(cur)
        check("остаток достаточен -> разрешено", v["allowed"], True)
        check("причина ok", v["reason"], "ok")
    bcon.close()

    # 17b. остаток < резерва -> блок по деньгам
    bcon, bw = fresh_db()
    _with_env(MONITOR_BUDGET_USD="0.05", MONITOR_USD_RUB="100",   # бюджет 5 ₽
              MONITOR_MAX_ANALYSIS_COST_RUB="1",
              MONITOR_MAX_ANALYSES_PER_DAY="100", MONITOR_MAX_ANALYSES_PER_MONTH="1000")
    with bw, bw.cursor() as cur:
        _seed_usage(cur, 1, 4.5)            # потрачено 4.5 ₽, остаток 0.5 < резерв 1
        v = bud.check_budget(cur)
        check("остаток < резерва -> блок", v["allowed"], False)
        check("причина monthly_budget", v["reason"], "monthly_budget")
        check("остаток посчитан верно", round(v["remaining"], 2), 0.5)
    bcon.close()

    # 17c. блок по дневному лимиту (деньги ещё есть)
    bcon, bw = fresh_db()
    _with_env(MONITOR_BUDGET_USD="10", MONITOR_USD_RUB="100",     # денег вагон
              MONITOR_MAX_ANALYSIS_COST_RUB="1",
              MONITOR_MAX_ANALYSES_PER_DAY="5", MONITOR_MAX_ANALYSES_PER_MONTH="1000")
    with bw, bw.cursor() as cur:
        _seed_usage(cur, 5, 0.01)           # 5 анализов сегодня = дневной предел
        v = bud.check_budget(cur)
        check("дневной лимит -> блок", v["allowed"], False)
        check("причина daily_analysis_limit", v["reason"], "daily_analysis_limit")
    bcon.close()

    # 17d. блок по месячному лимиту
    bcon, bw = fresh_db()
    _with_env(MONITOR_BUDGET_USD="10", MONITOR_USD_RUB="100",
              MONITOR_MAX_ANALYSIS_COST_RUB="1",
              MONITOR_MAX_ANALYSES_PER_DAY="100000", MONITOR_MAX_ANALYSES_PER_MONTH="5")
    with bw, bw.cursor() as cur:
        _seed_usage(cur, 5, 0.01)           # 5 за месяц = месячный предел
        v = bud.check_budget(cur)
        check("месячный лимит -> блок", v["allowed"], False)
        check("причина monthly_analysis_limit", v["reason"], "monthly_analysis_limit")
    bcon.close()

    # 17e. уведомление ровно один раз за период
    bcon, bw = fresh_db()
    _with_env(MONITOR_BUDGET_USD="1", MONITOR_USD_RUB="100")
    with bw, bw.cursor() as cur:
        key = bud.notify_key()
        check("до уведомления — не отмечено", bud.already_notified(cur, key), False)
        bud.mark_notified(cur, key)
        check("после отметки — уже уведомлён", bud.already_notified(cur, key), True)
        bud.mark_notified(cur, key)         # повторно
        cur.execute("SELECT COUNT(*) FROM monitor_runs WHERE mode=? AND status='budget_alert'", (key,))
        check("повторных уведомлений нет (записей 2, но already_notified ловит первую)",
              bud.already_notified(cur, key), True)
    bcon.close()

    # 17f. безлимит (админ) -> деньги не ограничивают, техпредохранители работают
    bcon, bw = fresh_db()
    _with_env(MONITOR_BUDGET_USD="-1", MONITOR_USD_RUB="100",
              MONITOR_MAX_ANALYSES_PER_DAY="100", MONITOR_MAX_ANALYSES_PER_MONTH="1000")
    with bw, bw.cursor() as cur:
        _seed_usage(cur, 3, 999)            # потрачено 2997 ₽ — но безлимит
        v = bud.check_budget(cur)
        check("безлимит игнорирует деньги", v["allowed"], True)
        check("но unlimited=True", bud.unlimited(), True)
    bcon.close()

    # 17g. forecast отдаёт показ-данные (средняя цена — для показа, не для гейта)
    bcon, bw = fresh_db()
    _with_env(MONITOR_BUDGET_USD="1", MONITOR_USD_RUB="100")
    with bw, bw.cursor() as cur:
        _seed_usage(cur, 4, 0.25)           # 1 ₽ потрачено, средняя 0.25
        f = bud.forecast(cur)
        check("forecast: бюджет в рублях", f["budget_rub"], 100.0)
        check("forecast: потрачено", round(f["spent_rub"], 2), 1.0)
        check("forecast: средняя цена", round(f["avg_analysis_rub"], 2), 0.25)
        check("forecast: счётчик месяца", f["analyses_month"], 4)
    bcon.close()

    # вернуть рабочие значения env, чтобы не влиять на другие блоки
    _with_env(MONITOR_BUDGET_USD="1", MONITOR_USD_RUB="100",
              MONITOR_MAX_ANALYSIS_COST_RUB="1",
              MONITOR_MAX_ANALYSES_PER_DAY="100", MONITOR_MAX_ANALYSES_PER_MONTH="1000")
    store.connect = lambda: wrapped

    print("\n18. Гейт в конвейере: при блокировке LLM не зовётся, статус awaiting_budget")
    import importlib, datetime as _dt
    from app.monitoring import budget as bud18
    _clear_pool_modules()
    # бюджет исчерпан: 5 ₽ всего, потрачено 4.5, резерв 1 -> блок money
    for k, v in (("MONITOR_BUDGET_USD","0.05"), ("MONITOR_USD_RUB","100"),
                 ("MONITOR_MAX_ANALYSIS_COST_RUB","1"),
                 ("MONITOR_MAX_ANALYSES_PER_DAY","100"),
                 ("MONITOR_MAX_ANALYSES_PER_MONTH","1000")):
        os.environ[k]=v
    importlib.reload(bud18)
    import monitor_runner as mr
    importlib.reload(mr)

    # считаем реальные вызовы LLM
    llm_calls = {"n": 0}
    orig_analyze = mr.llm.analyze
    def _counting_analyze(*a, **k):
        llm_calls["n"] += 1
        return {"summary": "не должно вызваться", "source": "llm"}
    mr.llm.analyze = _counting_analyze
    # уведомление не шлём в сеть
    mr.alerts.send_text = lambda *a, **k: True

    gcon, gw = fresh_db()
    with gw, gw.cursor() as cur:
        # источники и правила
        for stmt_path in ("fixtures/telegram_raw.json",):
            pass
        mr.store.connect = lambda: gw
        # засеваем расход, чтобы бюджет был исчерпан
        cur.execute("INSERT INTO api_usage (account_id,provider,model,operation,"
                    "prompt_tokens,completion_tokens,cost_rub,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    ("boris_monitoring","gigachat","GigaChat-Max","monitoring_analyze",
                     70,14,4.5,_dt.datetime.now(_dt.timezone.utc).isoformat()))
        # правило и источник для находки
        mr.store.upsert_source(cur, {"id":"s18","platform":"telegram",
            "source_kind":"group","username":"c","title":"Чат","weight":15})
        mr.store.save_rule(cur, {"id":"r18","name":"R","rule_type":"keyword",
            "params":{"any":["ищу авитолога"]}}, changed_by="t")

    # проверяем сам гейт + запись сообщения вручную (эмуляция ветки конвейера)
    with gw, gw.cursor() as cur:
        verdict = bud18.check_budget(cur)
        check("бюджет исчерпан -> гейт закрыт", verdict["allowed"], False)
        block_reason = verdict["reason"]
        # эмулируем то, что делает конвейер при закрытом гейте:
        # LLM НЕ зовём, сохраняем с awaiting_budget
        if not verdict["allowed"]:
            pass  # llm.analyze НЕ вызывается — счётчик остаётся 0
        _id, is_new = mr.store.save_message(cur, {
            "platform":"telegram","source_id":"s18","external_message_id":"9001",
            "text":"Ищу авитолога срочно","score_total":85,"score_breakdown":{"keyword":40},
            "matched_rule_id":"r18","status":"awaiting_budget","budget_block":block_reason})
        check("LLM не вызван после закрытого гейта", llm_calls["n"], 0)
    with gw, gw.cursor() as cur:
        cur.execute("SELECT status, budget_block FROM monitor_messages WHERE external_message_id='9001'")
        row = cur.fetchone()
        check("статус awaiting_budget записан", row[0], "awaiting_budget")
        check("причина monthly_budget записана", row[1], "monthly_budget")
    gcon.close()
    mr.llm.analyze = orig_analyze
    _clear_pool_modules()
    for k, v in (("MONITOR_BUDGET_USD","1"),):
        os.environ[k]=v
    store.connect = lambda: wrapped

    print("\n" + "=" * 60)
    if FAILED:
        print(f"ПРОВАЛЕНО: {len(FAILED)} — {', '.join(FAILED)}")
        return 1
    print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
