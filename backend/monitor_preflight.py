#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Чек-лист Go / No-Go перед первым боевым запуском мониторинга BORIS.

Проходит все восемь пунктов приёмки САМ и печатает итог. Возвращает код 0
только если все проверки GO. Если хотя бы одна NO-GO — в крон не ставить.

    cd /root/BORIS/backend
    venv/bin/python3 monitor_preflight.py              # без отправки в Telegram
    venv/bin/python3 monitor_preflight.py --send-test  # плюс тестовая карточка в группу

Отправка карточки отдельным флагом намеренно: скрипт не должен писать ничего
в чат без явного разрешения. Никаких сообщений найденным людям не шлётся
ни при каком флаге — такой возможности в коде нет.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# .env в окружение ДО импорта app.* — иначе app/db/session.py берёт дефолт
# postgres:postgres и учёт расхода падает на аутентификации (см. monitor_runner).
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:  # noqa: BLE001
    pass

from app.monitoring import alerts, llm, store   # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def report(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'✅ GO   ' if ok else '❌ NO-GO'} · {name}")
    if detail:
        print(f"         {detail}")


# ------------------------------------------------------------------ проверки

def check_schema(cur) -> None:
    want_tables = ["contacts", "leads", "monitor_messages", "monitor_rule_versions",
                   "monitor_rules", "monitor_runs", "monitor_sources"]
    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name = ANY(%s)",
        (want_tables,))
    have = sorted(r[0] for r in cur.fetchall())
    ok_t = have == sorted(want_tables)

    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='monitor_messages' AND column_name = ANY(%s)",
        (["matched_rules", "rules_hash", "normalizer", "duplicates_group",
          "summary", "suggested_reply", "ai"],))
    cols = sorted(r[0] for r in cur.fetchall())
    ok_c = len(cols) == 7
    report("Схема PostgreSQL применена",
           ok_t and ok_c,
           f"таблиц {len(have)}/7, колонок {len(cols)}/7"
           + ("" if ok_t and ok_c else f" · есть: {have} / {cols}"))


def check_seed(cur) -> None:
    cur.execute("SELECT COUNT(*) FROM monitor_sources WHERE is_active")
    src = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM monitor_rules WHERE is_active")
    rul = cur.fetchone()[0]
    report("Источники и правила загружены", src > 0 and rul > 0,
           f"источников {src}, правил {rul}"
           + ("" if src and rul else " · выполните monitor_runner.py --seed"))


def _fixture_source_ids() -> list[str]:
    """Идентификаторы фикстурных источников берём из самого файла фикстур."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "app", "monitoring", "fixtures", "telegram_raw.json")
    with open(path, encoding="utf-8") as f:
        return [s["id"] for s in json.load(f)["sources"]]


def _reset_fixture_state(cur) -> int:
    """
    Возвращает фикстурные источники в исходное состояние, чтобы чек-лист
    можно было запускать сколько угодно раз и после ручных прогонов.
    Трогает ТОЛЬКО фикстурные источники — боевые находки не затрагиваются.
    """
    ids = _fixture_source_ids()
    cur.execute("DELETE FROM leads WHERE message_id IN "
                "(SELECT id FROM monitor_messages WHERE source_id = ANY(%s))", (ids,))
    cur.execute("DELETE FROM monitor_messages WHERE source_id = ANY(%s)", (ids,))
    cur.execute("UPDATE monitor_sources SET last_seen_external_id = NULL, "
                "last_scanned_at = NULL WHERE id = ANY(%s)", (ids,))
    store.recount_contacts(cur)
    return len(ids)


def check_fixtures() -> None:
    """Два прогона подряд: первый создаёт, второй обязан дать ноль."""
    import monitor_runner

    con = store.connect()
    try:
        with con, con.cursor() as cur:
            n = _reset_fixture_state(cur)
        print(f"\n--- фикстурные источники сброшены ({n} шт.) ---")
    except Exception as e:  # noqa: BLE001
        # молча продолжать нельзя: без сброса следующие проверки ничего не значат
        report("Сброс фикстурного состояния", False, str(e)[:200])
        return
    finally:
        con.close()

    print("\n--- прогон фикстур (1/2) ---")
    monitor_runner.cmd_fixtures(write=True)
    con = store.connect()
    with con, con.cursor() as cur:
        cur.execute("SELECT new_messages FROM monitor_runs "
                    "WHERE status <> 'requested' ORDER BY started_at DESC LIMIT 1")
        first = cur.fetchone()[0]
        cur.execute("SELECT username, messages_count FROM contacts ORDER BY username")
        counts_before = dict(cur.fetchall())
    con.close()

    print("--- прогон фикстур (2/2) ---")
    monitor_runner.cmd_fixtures(write=True)
    con = store.connect()
    with con, con.cursor() as cur:
        cur.execute("SELECT new_messages FROM monitor_runs "
                    "WHERE status <> 'requested' ORDER BY started_at DESC LIMIT 1")
        second = cur.fetchone()[0]
        cur.execute("SELECT username, messages_count FROM contacts ORDER BY username")
        counts_after = dict(cur.fetchall())
    con.close()
    print()

    report("Первый прогон создаёт записи", first > 0, f"новых: {first}")
    report("Второй прогон не создаёт новых", second == 0, f"новых: {second}")
    report("Счётчики контактов не выросли повторно",
           counts_before == counts_after,
           "было != стало" if counts_before != counts_after else f"контактов: {len(counts_after)}")


def check_llm(cur) -> None:
    info = llm.describe_pool()
    if not info.get("available"):
        report("Пул моделей доступен", False, str(info.get("error")))
        report("Расход модели учитывается", False, "нечего проверять — пул не найден")
        return
    report("Пул моделей доступен", True,
           f"{info.get('module_file')} · форма вызова: {info.get('strategy')}")

    cur.execute("SELECT COUNT(*) FROM api_usage")
    before = cur.fetchone()[0]
    try:
        llm.call_llm("Ответь одним словом: работает")
        called = True
    except Exception as e:  # noqa: BLE001
        called = False
        report("Пробный вызов модели", False, str(e)[:200])
    cur.execute("SELECT COUNT(*) FROM api_usage")
    after = cur.fetchone()[0]

    if not called:
        report("Расход модели учитывается", False, "вызов не прошёл")
        return
    if after > before:
        report("Расход модели учитывается", True,
               f"api_usage: {before} -> {after}, пишет сам пул")
    elif llm.LOG_USAGE:
        report("Расход модели учитывается", False,
               "MONITOR_LLM_LOG_USAGE=1, но записи не появилось — проверьте функцию учёта")
    else:
        report("Расход модели учитывается", False,
               "вызов в учёт НЕ попал · включите MONITOR_LLM_LOG_USAGE=1 и повторите")


def check_llm_fallback(cur) -> None:
    """Отключаем пул и убеждаемся, что находка всё равно получает резюме."""
    saved_fn, saved_strategy = llm._FN, llm._STRATEGY
    llm._FN, llm._STRATEGY = None, None
    import sys as _sys
    stub = _sys.modules.pop("gigachat_pool", None)
    app_stub = _sys.modules.pop("app.gigachat_pool", None)
    _sys.modules["gigachat_pool"] = type(_sys)("gigachat_pool")  # без chat_with_fallback
    try:
        data = llm.analyze("Ищу авитолога, заявок нет", ["ищу авитолога"],
                           {"keyword": 40, "freshness": 15}, "горячий лид")
        ok = bool(data.get("summary")) and data.get("source") == "rules"
        report("При недоступном LLM карточка не теряется", ok,
               f"резюме: {(data.get('summary') or '')[:70]}")
    finally:
        _sys.modules.pop("gigachat_pool", None)
        if stub is not None:
            _sys.modules["gigachat_pool"] = stub
        if app_stub is not None:
            _sys.modules["app.gigachat_pool"] = app_stub
        llm._FN, llm._STRATEGY = saved_fn, saved_strategy


def check_buttons(cur) -> None:
    cur.execute("SELECT id, status FROM monitor_messages ORDER BY score_total DESC LIMIT 1")
    row = cur.fetchone()
    if not row:
        report("Кнопка «Создать лид» идемпотентна", False, "нет ни одной находки")
        report("Кнопка «Подготовить ответ» только генерирует текст", False, "нет находки")
        return
    mid = row[0]

    lead1 = store.create_lead(cur, mid)
    lead2 = store.create_lead(cur, mid)
    cur.execute("SELECT COUNT(*) FROM leads WHERE message_id=%s", (mid,))
    n = cur.fetchone()[0]
    report("Кнопка «Создать лид» идемпотентна", lead1 == lead2 and n == 1,
           f"лидов на сообщение: {n}")

    text = store.prepare_reply(cur, mid)
    cur.execute("SELECT status, sent_at FROM monitor_messages WHERE id=%s", (mid,))
    st, sent = cur.fetchone()
    report("Кнопка «Подготовить ответ» только генерирует текст",
           bool(text) and st == "in_work",
           f"черновик {len(text or '')} символов, статус остался «{st}»")


def check_card(send: bool) -> None:
    con = store.connect()
    try:
        with con, con.cursor() as cur:
            rows = store.pending_alerts(cur, limit=1)
    finally:
        con.close()
    if not rows:
        report("Карточка собирается", False, "очередь пуста — нечего показать")
        return
    text, buttons = alerts.render_card(rows[0])
    labels = [b["text"] for r in buttons for b in r]
    ok = len(labels) == 4 and not any(w in " ".join(labels) for w in ("Отправить", "Написать"))
    report("Карточка собирается, кнопок 4, автоотправки нет", ok, " · ".join(labels))
    print("\n--- как будет выглядеть ---")
    print(text.replace("<b>", "").replace("</b>", "")
              .replace("<i>", "").replace("</i>", ""))
    print("---\n")

    if send:
        sent = alerts.send_card(rows[0])
        report("Telegram-бот отправляет карточку", sent,
               "проверьте группу" if sent else "см. лог: токен, chat_id, права бота")
    else:
        token_ok = bool(os.getenv("MONITOR_BOT_TOKEN")) and bool(os.getenv("MONITOR_ALERT_CHAT_ID"))
        report("Настройки бота заданы", token_ok,
               "запустите с --send-test для реальной проверки отправки"
               if token_ok else "нет MONITOR_BOT_TOKEN / MONITOR_ALERT_CHAT_ID")


def check_env() -> None:
    have_tg = all(os.getenv(k) for k in
                  ("MONITOR_TG_API_ID", "MONITOR_TG_API_HASH", "MONITOR_TG_SESSION_ENC"))
    try:
        import telethon  # noqa: F401
        tele = True
    except ImportError:
        tele = False
    report("Telethon установлен", tele, "" if tele else "venv/bin/pip install telethon")
    report("Ключи Telegram заданы", have_tg,
           "" if have_tg else "нет api_id / api_hash / зашифрованной сессии")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send-test", action="store_true",
                    help="отправить одну карточку в группу оповещений")
    args = ap.parse_args()

    print("=" * 70)
    print("ЧЕК-ЛИСТ GO / NO-GO — мониторинг источников BORIS")
    print("=" * 70)

    con = store.connect()
    try:
        with con, con.cursor() as cur:
            check_schema(cur)
            check_seed(cur)
    finally:
        con.close()

    check_fixtures()

    con = store.connect()
    try:
        with con, con.cursor() as cur:
            check_llm(cur)
            check_llm_fallback(cur)
            check_buttons(cur)
    finally:
        con.close()

    check_card(args.send_test)
    check_env()

    print("=" * 70)
    bad = [n for n, ok, _ in RESULTS if not ok]
    if bad:
        print(f"NO-GO · не прошло {len(bad)} из {len(RESULTS)}:")
        for n in bad:
            print(f"   — {n}")
        print("\nВ боевой крон НЕ ставить.")
        return 1
    print(f"GO · все {len(RESULTS)} проверок пройдены.")
    print("Первые дни — запускать вручную, затем крон на минуту 15 каждого часа.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
