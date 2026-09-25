# -*- coding: utf-8 -*-
"""Приём заданий из Telegram: написал в тему — задача пошла в работу.

Владельцу не нужен ни терминал, ни этот чат: он пишет обычным языком в ту же
тему, куда приходят отчёты, и BORIS заводит задачу в автономный контур.
Ответ приходит туда же: номер задачи, а дальше только DONE или настоящий
блокер.

    venv/bin/python3 -m app.ext_api.inbox once     # разобрать накопившееся
    venv/bin/python3 -m app.ext_api.inbox run      # служба

Второй бот НЕ нужен. Конфликт 409 возникает только при чтении очереди
обновлений: два getUpdates на один токен не уживаются. Отправка ничем не
занята. Поэтому правильный путь — отдать нам обновление из уже существующего
обработчика боевого бота:

    from app.ext_api.inbox import handle_update
    handle_update(update)

Опрос своим токеном (once/run) оставлен запасным вариантом на случай, когда
у BORIS обработчика нет: тогда токен кладётся в BORIS_INBOX_BOT_TOKEN.
"""
import io, json, os, re, sys, time

from . import db, dev, a2a, notify, report
from .errors import ApiError

LEAD_KEY = {"scope_set": {"read", "write", "tasks"}, "actor_type": "ai_agent",
            "actor_name": "claude-lead", "name": "claude-lead", "id": None,
            "max_items_per_call": 200, "allowed_accounts": None}
EXECUTOR = "gpt-executor"

TOKEN_VARS = ("BORIS_INBOX_BOT_TOKEN", "TELEGRAM_INBOX_BOT_TOKEN", "INBOX_BOT_TOKEN")

HELP = ("Пишите задачу обычными словами — заведу и отдам в работу.\n"
        "/status — короткая сводка\n"
        "/report — полный отчёт за сутки\n"
        "/help — эта подсказка")

FORBIDDEN = [
    "публикация на Avito без явного разрешения владельца",
    "любые платные операции: генерация изображений, баннеров, внешние вызовы",
    "удаление или перезапись существующих кампаний и объявлений",
    "правки чужих клиентских аккаунтов",
]


def _offset_path():
    from . import repo
    return os.path.join(repo.ROOT, "backend", ".ext_inbox_offset")


def _offset(new=None):
    path = _offset_path()
    if new is not None:
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(str(int(new)))
        return int(new)
    try:
        with io.open(path, encoding="utf-8") as fh:
            return int(fh.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def token():
    from .executor import _env_all
    env = _env_all()
    for k in TOKEN_VARS:
        if env.get(k):
            return env[k].strip(), k
    return None, None


def _api(method, params, tok):
    import urllib.request, urllib.parse
    from .executor import _opener
    c = notify._conf()
    url = "https://api.telegram.org/bot%s/%s" % (tok, method)
    data = urllib.parse.urlencode(params or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with _opener(c.get("proxy")).open(req, timeout=70) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        body = ""
        if hasattr(e, "read"):
            try:
                body = e.read().decode("utf-8")[:300]
            except Exception:
                body = ""
        try:
            return json.loads(body) if body else {"ok": False, "description": str(e)[:200]}
        except ValueError:
            return {"ok": False, "description": (body or str(e))[:200]}


def _reply(tok, chat_id, thread_id, text_):
    p = {"chat_id": chat_id, "text": text_[:3900], "disable_web_page_preview": True}
    if thread_id:
        p["message_thread_id"] = int(thread_id)
    return _api("sendMessage", p, tok).get("ok", False)


def _target():
    """Только та же тема, куда уходят отчёты: чужой чат команд не отдаёт."""
    c = notify._conf()
    return c.get("chat_id"), c.get("thread_id")


def _allowed_users():
    from .executor import _env_all
    raw = (_env_all().get("TELEGRAM_ALLOWED_USER_IDS") or "").strip()
    return {x.strip() for x in re.split(r"[,\s]+", raw) if x.strip()}


def _title_of(text_):
    first = (text_.strip().split("\n")[0] or "").strip()
    first = re.sub(r"\s+", " ", first)
    return (first[:110] + "…") if len(first) > 110 else (first or "Задача из Telegram")


def create_task(text_, author="telegram"):
    """Сообщение владельца превращается в задачу с проверяемой приёмкой."""
    title = _title_of(text_)
    dup = db.one("""SELECT id FROM ext_dev_jobs
                     WHERE title = :t AND status NOT IN ('completed','cancelled')
                     ORDER BY id DESC LIMIT 1""", t="TG: " + title)
    if dup:
        return {"duplicate": True, "dev_job_id": "dev_%s" % dup["id"], "title": title}
    goal = ("Задание владельца, присланное в Telegram, дословно:\n\n" + text_.strip() +
            "\n\nРаботать штатными средствами BORIS. Ничего платного не запускать, "
            "в Avito не публиковать. Если для выполнения нужно решение владельца — "
            "вернуть блокер с одной конкретной просьбой, а не выдумывать обход.")
    acceptance = [
        "Сделано ровно то, что просил владелец: " + title,
        "Показаны доказательства фактами: вывод запросов к базе, чтение кода "
        "или обход экранов браузером — не пересказ",
        "Названо, что осталось за границей задачи и почему",
    ]
    job = dev.create(LEAD_KEY, "tg_%d" % int(time.time()), "TG: " + title, goal,
                     acceptance, scope={"source": "telegram", "author": author},
                     priority=70, owner=EXECUTOR)
    order = a2a.create_order(LEAD_KEY, job["dev_job_id"], "PLANNING", goal,
                             scope={"source": "telegram"}, acceptance=acceptance,
                             forbidden=FORBIDDEN,
                             required_qa=["db_query", "repo_read"],
                             required_evidence=["вывод db_query"],
                             assigned_to=EXECUTOR, max_iterations=6, priority=70)
    return {"duplicate": False, "dev_job_id": job["dev_job_id"], "title": title,
            "order_id": order["order_id"]}


def handle(text_, author="telegram"):
    """Что ответить на сообщение. Отдельно от Telegram — чтобы проверять."""
    t = (text_ or "").strip()
    if not t:
        return None
    low = t.lower()
    if low.startswith(("/help", "/start", "помощь")):
        return HELP
    if low.startswith(("/status", "/devstatus", "статус")):
        return report.text(hours=24, short=True)
    if low.startswith(("/report", "отчёт", "отчет")):
        return report.text(hours=24)
    if t.startswith("/"):
        return "Такой команды нет.\n\n" + HELP
    try:
        r = create_task(t, author)
    except ApiError as e:
        return "Не смог завести задачу: " + e.message
    if r["duplicate"]:
        return "Такая задача уже в работе: %s — «%s». Новую не завожу." % (
            r["dev_job_id"], r["title"])
    return ("Принял: %s — «%s».\nОтдал исполнителю, наряд %s.\n"
            "Напишу, когда будет готово или если понадобится ваше решение."
            % (r["dev_job_id"], r["title"], r["order_id"]))


def handle_update(update, send_reply=True):
    """Точка входа для БОЕВОГО бота BORIS — без второго бота и без опроса.

    Конфликт 409 возникает только при ЧТЕНИИ очереди обновлений: два
    getUpdates на один токен не уживаются. Отправка ничем не занята. Значит
    правильный путь — не заводить ещё одного бота, а отдать нам обновление
    из уже существующего обработчика:

        from app.ext_api.inbox import handle_update
        handle_update(update)   # dict как пришёл от Telegram

    Возвращает текст ответа (он же по умолчанию сам уходит в чат) или None,
    если сообщение не из нашей темы и нас не касается.
    """
    m = (update or {}).get("message") or (update or {}).get("edited_message") or {}
    chat, thread = _target()
    ch = str((m.get("chat") or {}).get("id") or "")
    th = m.get("message_thread_id")
    who = m.get("from") or {}
    text_ = m.get("text") or m.get("caption") or ""
    if not chat or ch != str(chat):
        return None
    if thread and str(th or "") != str(thread):
        return None
    if who.get("is_bot") or not text_.strip():
        return None
    allowed = _allowed_users()
    if allowed and str(who.get("id")) not in allowed:
        return None
    answer = handle(text_, who.get("username") or str(who.get("id")))
    if answer and send_reply:
        tok, _ = token()
        if tok:
            _reply(tok, chat, thread, answer)
        else:
            notify.send(answer)   # тот же бот, что шлёт отчёты
    return answer


def webhook_info():
    """Как боевой бот получает обновления — вебхуком или опросом.

    От этого зависит, куда встраиваться, и гадать тут нельзя.
    """
    c = notify._conf()
    if not c.get("token"):
        return {"ok": False, "why": "токен бота не найден"}
    r = notify._api("getWebhookInfo")
    if not r.get("ok"):
        return {"ok": False, "why": r.get("description") or "Telegram не ответил"}
    info = r.get("result") or {}
    url = info.get("url") or ""
    return {"ok": True, "mode": "вебхук" if url else "опрос (getUpdates)",
            "webhook_url": url, "pending": info.get("pending_update_count"),
            "last_error": info.get("last_error_message"),
            "how_to_wire": ("добавьте в обработчик вебхука одну строку: "
                            "from app.ext_api.inbox import handle_update; "
                            "handle_update(update)") if url else
                           ("в цикле опроса боевого бота вызовите "
                            "inbox.handle_update(update) для каждого обновления")}


def once(timeout=0):
    """Один проход по накопившимся сообщениям."""
    tok, var = token()
    if not tok:
        return {"ok": False, "why": "нет отдельного бота для приёма заданий: "
                                    "заведите бота у @BotFather, добавьте его в ту же "
                                    "тему и положите токен в BORIS_INBOX_BOT_TOKEN"}
    chat, thread = _target()
    if not chat:
        return {"ok": False, "why": "не задан чат: сначала notify set --chat"}
    r = _api("getUpdates", {"offset": _offset() + 1, "limit": 50,
                            "timeout": int(timeout),
                            "allowed_updates": json.dumps(["message"])}, tok)
    if not r.get("ok"):
        why = r.get("description") or "Telegram не ответил"
        if "conflict" in why.lower():
            why = ("этот бот уже читается другим процессом. Для приёма заданий нужен "
                   "ОТДЕЛЬНЫЙ бот, не тот, что шлёт уведомления")
        return {"ok": False, "why": why}
    allowed = _allowed_users()
    handled, skipped, last = [], 0, _offset()
    for u in r.get("result") or []:
        last = max(last, int(u.get("update_id") or 0))
        m = u.get("message") or {}
        ch = str((m.get("chat") or {}).get("id") or "")
        th = m.get("message_thread_id")
        text_ = m.get("text") or m.get("caption") or ""
        who = (m.get("from") or {})
        if ch != str(chat) or (thread and str(th or "") != str(thread)):
            skipped += 1
            continue
        if who.get("is_bot") or not text_.strip():
            skipped += 1
            continue
        if allowed and str(who.get("id")) not in allowed:
            skipped += 1
            continue
        answer = handle(text_, who.get("username") or str(who.get("id")))
        if answer:
            _reply(tok, chat, thread, answer)
            handled.append({"from": who.get("username") or who.get("id"),
                            "text": text_[:80], "answered": answer[:80]})
    _offset(last)
    return {"ok": True, "token_var": var, "handled": handled,
            "skipped": skipped, "offset": last}


def run(idle=20):
    """Служба: длинный опрос, падать не имеет права."""
    while True:
        try:
            r = once(timeout=25)
            if not r.get("ok"):
                print("inbox: " + str(r.get("why"))[:200], file=sys.stderr, flush=True)
                time.sleep(max(30, idle))
                continue
        except Exception as e:
            print("inbox: сбой %s: %s" % (type(e).__name__, e), file=sys.stderr,
                  flush=True)
            time.sleep(max(30, idle))
            continue
        time.sleep(2)


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="inbox")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("once")
    sub.add_parser("webhook")
    rn = sub.add_parser("run")
    rn.add_argument("--idle", type=int, default=20)
    a = ap.parse_args()
    if a.cmd == "run":
        return run(a.idle)
    if a.cmd == "webhook":
        print(json.dumps(webhook_info(), ensure_ascii=False, indent=2, default=str))
        return 0
    print(json.dumps(once(), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)
