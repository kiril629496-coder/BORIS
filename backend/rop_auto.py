# -*- coding: utf-8 -*-
"""Автоматический разбор новых звонков у ПЛАТЯЩИХ клиентов.
Правило Кирилла 06.08: владелец BORIS — вручную, клиент с активным пакетом
РОП — автоматически. Аккаунт без активного пакета не трогается вовсе,
поэтому собственные аккаунты владельца сюда не попадают."""
import sys, os, json, time, urllib.request, urllib.error
sys.path.insert(0, "/root/BORIS/backend")
for line in open("/root/BORIS/backend/.env", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v.strip().strip('"').strip("'"))

from sqlalchemy import text
from app.db.session import SessionLocal
from app.api.auth import create_access_token
from app.api.calltracking import (calltracking_stats, _cache_get_analysis, get_rop_minutes,
    _get_chats, _chat_cache_get, _chat_cache_put, _analyze_chat, _chat_day_used,
    get_rop_chats, _add_overflow_minutes)

DRY = "--apply" not in sys.argv
HOST = "https://boris-ai.pro"
MAX_PER_RUN = max(1, min(int(os.environ.get("BORIS_ROP_MAX_PER_RUN", "20")), 20))
MAX_CHAT_PER_RUN = max(1, min(int(os.environ.get("BORIS_ROP_MAX_CHAT_PER_RUN", "5")), 15))
BACKLOG_DAYS = max(7, min(int(os.environ.get("BORIS_ROP_BACKLOG_DAYS", "30")), 90))
QA_NO_NOTIFY = os.environ.get("BORIS_ROP_QA_NO_NOTIFY", "0") == "1"
STAMP = time.strftime("%Y-%m-%d %H:%M")


def log(msg):
    print("[rop_auto %s] %s" % (STAMP, msg), flush=True)


def notify(account_id, msg):
    if QA_NO_NOTIFY:
        log("%s — Telegram suppressed by BORIS_ROP_QA_NO_NOTIFY" % account_id)
        return
    try:
        from app.api.calltracking import _tg_notify
        _tg_notify(account_id, msg, topic="sales")
    except Exception:
        pass


def digest(acc, ok, left):
    """Один связный отчёт за сутки вместо счётчика: сколько разобрано,
    средний балл и что чаще всего проваливают. Руководителю важна
    повторяющаяся ошибка, а не отдельные баллы."""
    from collections import Counter
    import json as _j
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT analysis FROM call_analysis WHERE account_id = :a"
            " AND created_at > now() - interval '26 hours'"), {"a": acc}).fetchall()
    finally:
        db.close()
    scores, fail = [], Counter()
    for r in rows:
        a = r[0] if isinstance(r[0], dict) else _j.loads(r[0] or "{}")
        if isinstance(a.get("score"), int):
            scores.append(a["score"])
        for c in (a.get("checklist") or []):
            if not c.get("done"):
                fail[c.get("item", "")] += 1
    lines = ["Отчёт по звонкам за сутки", ""]
    lines.append("Разобрано звонков: %s" % ok)
    if scores:
        lines.append("Средний балл: %s из 100" % round(sum(scores) / len(scores)))
    if fail:
        lines.append("")
        lines.append("Чаще всего упускают:")
        for item, n in fail.most_common(3):
            lines.append("- %s (%s из %s)" % (item, n, len(rows)))
    lines.append("")
    lines.append("Осталось минут в пакете: %s из %s" % (left.get("left"), left.get("purchased")))
    return "\n".join(lines)


db = SessionLocal()
accounts = [r[0] for r in db.execute(text(
    "SELECT DISTINCT account_id FROM ai_bindings WHERE product = 'rop'")).fetchall()]
db.close()
_scope = os.environ.get("BORIS_ROP_ACCOUNT", "").strip()
if _scope:
    accounts = [a for a in accounts if a == _scope]
log("аккаунтов с РОП: %s | режим: %s" % (len(accounts), "ПРОСМОТР" if DRY else "РАБОТА"))

token = create_access_token(2)
for acc in accounts:
    bal = get_rop_minutes(acc)
    if not bal.get("active"):
        log("%s — пакет неактивен, пропуск (ручной режим владельца)" % acc)
        continue

    # Calls and chats are independent inputs of the same ROP scheduler. A
    # temporary calltracking failure must not block ready local chat analyses.
    todo = []
    try:
        st = calltracking_stats(acc, BACKLOG_DAYS)
    except Exception as e:
        st = {"status": "error", "message": "%s: %s" % (type(e).__name__, str(e)[:180])}
    if st.get("status") == "ok":
        for c in st.get("calls", []):
            cid, dur = c.get("call_id"), int(c.get("talk_duration") or 0)
            if dur > 0 and not _cache_get_analysis(acc, cid):
                todo.append((cid, dur, c.get("call_time") or ""))
        todo.sort(key=lambda x: x[2])
    else:
        log("%s — звонки временно недоступны: %s (%s)" % (acc, st.get("status"), st.get("message", "")))

    if todo:
        todo = todo[:MAX_PER_RUN]
        need = round(sum(d for _, d, _ in todo) / 60, 1)
        log("%s — новых звонков: %s, минут %s, в пакете осталось %s" % (acc, len(todo), need, bal.get("left")))
        if not DRY:
            ok = stopped = 0
            for cid, dur, _call_time in todo:
                if get_rop_minutes(acc)["left"] <= 0:
                    stopped = 1
                    break
                req = urllib.request.Request(
                    HOST + "/api/calltracking/analyze_call?account_id=" + acc,
                    data=json.dumps({"call_id": cid}).encode(), method="POST",
                    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
                res = None
                for _try in range(3):
                    try:
                        with urllib.request.urlopen(req, timeout=240) as r:
                            res = json.loads(r.read().decode("utf-8", "replace"))
                        break
                    except Exception:
                        time.sleep(10)
                if not res or res.get("status") != "ok":
                    log("  звонок %s: %s" % (cid, (res or {}).get("status", "нет ответа")))
                    if res and res.get("status") == "no_minutes":
                        stopped = 1
                        break
                    continue
                ok += 1
            left = get_rop_minutes(acc)
            log("%s — звонков разобрано %s, осталось минут %s" % (acc, ok, left.get("left")))
            if ok:
                notify(acc, digest(acc, ok, left))
            if stopped:
                notify(acc, "Минуты пакета РОП закончились — автоматический разбор остановлен. Докупите пакет, чтобы продолжить.")
    else:
        log("%s — новых звонков нет" % acc)

    # Chat readiness uses the existing authoritative selector: recent lead,
    # >=4 messages, not already in chat_analysis. No PDF/report is generated.
    try:
        ready = _get_chats(acc, BACKLOG_DAYS, 100)
        pending_chats = [c for c in ready if not _chat_cache_get(acc, c["chat_id"])]
        # _get_chats returns newest-first; drain oldest first to avoid starvation.
        pending_chats = list(reversed(pending_chats))
        day_left = max(0, 15 - _chat_day_used(acc))
        pending_chats = pending_chats[:min(MAX_CHAT_PER_RUN, day_left)]
    except Exception as e:
        log("%s — selector переписок exception: %s: %s" % (acc, type(e).__name__, str(e)[:180]))
        pending_chats = []

    if not pending_chats:
        log("%s — готовых новых переписок нет" % acc)
        continue
    log("%s — готовых переписок к разбору: %s" % (acc, len(pending_chats)))
    if DRY:
        continue

    chat_ok = 0
    chat_left = get_rop_chats(acc)["left"]
    for ch in pending_chats:
        # Existing product contract: dedicated chat package first; when empty,
        # one minute from the ROP minute pool per analyzed dialogue.
        if chat_left <= 0:
            if get_rop_minutes(acc)["left"] <= 0:
                log("%s — переписки остановлены: нет chat/minute balance" % acc)
                break
        try:
            an = _analyze_chat(ch["convo"], acc)
        except Exception as e:
            log("  переписка %s: exception %s" % (ch["chat_id"], str(e)[:140]))
            continue
        if not isinstance(an, dict) or an.get("error"):
            log("  переписка %s: %s" % (ch["chat_id"], (an or {}).get("error", "analysis error")))
            continue
        if chat_left <= 0:
            _add_overflow_minutes(acc, 1)
        _chat_cache_put(acc, ch["chat_id"], an)
        chat_left -= 1
        chat_ok += 1
    log("%s — переписок разобрано %s" % (acc, chat_ok))
