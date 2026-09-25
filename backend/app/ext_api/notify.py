# -*- coding: utf-8 -*-
"""Уведомления владельцу. Только два повода: DONE и настоящий BLOCKED_HUMAN.

Всё остальное — внутренняя жизнь агентов, и владельца она не касается.
Токен и chat_id находим в конфигурации BORIS сами, наружу не отдаём.
"""
import os, json, re

_CONF = None


def _extra_env_files():
    """Телеграм-бот BORIS может жить отдельным сервисом со своим .env —
    поэтому смотрим не только backend, но и соседние каталоги проекта."""
    from .repo import ROOT
    out = []
    for base in (ROOT, os.path.dirname(ROOT.rstrip("/")) or "/root", "/etc/boris"):
        if not os.path.isdir(base):
            continue
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        for name in entries:
            full = os.path.join(base, name)
            if name.endswith(".env") or name == ".env":
                out.append(full)
            elif os.path.isdir(full) and not name.startswith(".") \
                    and ("boris" in name.lower() or "telegram" in name.lower()
                         or "bot" in name.lower()):
                for sub in (".env", "config.env", "app/.env", "backend/.env"):
                    cand = os.path.join(full, sub)
                    if os.path.isfile(cand):
                        out.append(cand)
    return out[:40]


TOKEN_NAMES = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN", "BOT_TOKEN", "TG_BOT_TOKEN",
               "TELEGRAM_API_TOKEN", "TELEGRAM_BOT_API_TOKEN", "TG_TOKEN",
               "TELEGRAM_NOTIFY_TOKEN", "ADMIN_BOT_TOKEN")
CHAT_NAMES = ("TELEGRAM_CHAT_ID", "TELEGRAM_OWNER_CHAT_ID", "TG_CHAT_ID",
              "TELEGRAM_ADMIN_CHAT_ID", "OWNER_CHAT_ID", "ADMIN_CHAT_ID",
              "TELEGRAM_NOTIFY_CHAT_ID", "CHAT_ID", "TELEGRAM_USER_ID")


# Токен бота узнаётся по форме, а не по имени переменной: у BORIS телеграм-бот
# уже есть, но переменная может называться как угодно.
TOKEN_SHAPE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
CHAT_SHAPE = re.compile(r"^-?\d{5,15}$")

# Свой файл настроек: сюда кладётся то, что владелец задал командой,
# чтобы не править чужие .env проекта.
def _own_conf_path():
    from . import repo
    return os.path.join(repo.ROOT, "backend", ".ext_notify.env")


def parse_chat(value):
    """Принять и голый id, и ссылку вида t.me/c/<номер>/<тема>/<сообщение>.

    Считать id руками — верный способ промахнуться чатом, поэтому ссылку
    разбираем сами: к внутреннему номеру группы дописывается -100, а если в
    ссылке три числа, среднее — это тема форума.
    """
    s = str(value or "").strip()
    if not s:
        return None, None
    m = re.search(r"t\.me/c/(\d+)(?:/(\d+))?(?:/(\d+))?", s)
    if m:
        internal, a, b = m.group(1), m.group(2), m.group(3)
        chat = internal if internal.startswith("-100") else "-100" + internal
        # три числа: /c/<группа>/<тема>/<сообщение>; два: тема не указана
        return chat, (a if b else None)
    return s, None


def configure(token=None, chat_id=None, thread_id=None):
    """Записать токен и чат один раз. Значения наружу не отдаются."""
    global _CONF
    if chat_id:
        chat_id, thread_from_link = parse_chat(chat_id)
        thread_id = thread_id or thread_from_link
    path = _own_conf_path()
    cur = {}
    if os.path.isfile(path):
        for line in open(path, encoding="utf-8", errors="ignore"):
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.strip().partition("=")
                cur[k.strip()] = v.strip()
    if token:
        if not TOKEN_SHAPE.match(token.strip()):
            from .errors import ApiError
            raise ApiError("BAD_INPUT", "это не похоже на токен бота: ожидается "
                                        "вид 123456789:AA...")
        cur["TELEGRAM_BOT_TOKEN"] = token.strip()
    if chat_id:
        cur["TELEGRAM_CHAT_ID"] = str(chat_id).strip()
    if thread_id:
        cur["TELEGRAM_THREAD_ID"] = str(thread_id).strip()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("# настройки уведомлений BORIS, создано app.ext_api.notify\n")
        for k, v in sorted(cur.items()):
            f.write("%s=%s\n" % (k, v))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    _CONF = None
    return status()


def _api(method, params=None):
    """Вызов Telegram API через тот же прокси, что и отправка."""
    import urllib.request, urllib.parse
    from .executor import _opener
    c = _conf()
    if not c["token"]:
        return {"ok": False, "description": "токен бота не найден"}
    url = "https://api.telegram.org/bot%s/%s" % (c["token"], method)
    data = urllib.parse.urlencode(params or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with _opener(c["proxy"]).open(req, timeout=30) as r:
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


def where():
    """Куда именно уходят отчёты — название чата, а не голый номер.

    Номер чата глазами не проверишь, а ошибиться им очень легко: поэтому
    спрашиваем у самого Telegram, как называется то, куда мы пишем.
    """
    c = _conf()
    if not c["chat_id"]:
        return {"ok": False, "why": "чат не задан"}
    r = _api("getChat", {"chat_id": c["chat_id"]})
    if not r.get("ok"):
        return {"ok": False, "chat_id": c["chat_id"],
                "why": r.get("description") or "Telegram не ответил"}
    ch = r["result"]
    me = _api("getMe").get("result") or {}
    return {"ok": True, "chat_id": c["chat_id"],
            "chat_title": ch.get("title") or ch.get("username") or
                          (" ".join(x for x in (ch.get("first_name"),
                                                ch.get("last_name")) if x)),
            "chat_type": ch.get("type"),
            "forum": bool(ch.get("is_forum")),
            "thread_id": c.get("thread_id"),
            "bot": me.get("username")}


def chats():
    """Показать чаты, которые бот видел недавно, с их номерами.

    Нужно, когда номер группы взять неоткуда: добавьте бота в группу,
    напишите там любое сообщение и запустите это.
    """
    r = _api("getUpdates", {"limit": 100, "timeout": 0})
    if not r.get("ok"):
        why = r.get("description") or "Telegram не ответил"
        if "conflict" in why.lower() or "terminated" in why.lower():
            why = ("этот же бот уже читает обновления в другом процессе BORIS — "
                   "список чатов так не получить. Возьмите номер иначе: перешлите "
                   "любое сообщение из нужной группы боту @userinfobot")
        return {"ok": False, "why": why, "chats": []}
    seen, out = set(), []
    for u in r.get("result") or []:
        msg = u.get("message") or u.get("channel_post") or {}
        ch = msg.get("chat") or {}
        if not ch.get("id") or ch["id"] in seen:
            continue
        seen.add(ch["id"])
        out.append({"chat_id": ch["id"],
                    "title": ch.get("title") or ch.get("username") or "личный чат",
                    "type": ch.get("type"),
                    "thread_id": msg.get("message_thread_id")})
    return {"ok": True, "chats": out,
            "why": "" if out else "бот пока не видел ни одного сообщения: добавьте "
                                  "его в нужную группу и напишите там что-нибудь"}


def probe():
    """Проверить связь и вернуть человеческий ответ, а не голый False."""
    c = _conf()
    if not c["token"]:
        return {"ok": False, "why": "токен бота не найден"}
    if not c["chat_id"]:
        return {"ok": False, "why": "не задан чат для отчётов"}
    ok = send("BORIS на связи. Сюда будут приходить отчёты о работе.")
    return {"ok": bool(ok),
            "why": "" if ok else "Telegram не принял сообщение: проверьте, что бот "
                                  "добавлен в этот чат и не заблокирован"}


def _conf():
    global _CONF
    if _CONF is not None:
        return _CONF
    from .executor import _env_all
    env = dict(_env_all())
    found_in = []
    own = _own_conf_path()
    if os.path.isfile(own):
        for line in open(own, encoding="utf-8", errors="ignore"):
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.strip().partition("=")
                env[k.strip()] = v.strip()
                found_in.append(own)
    for path in _extra_env_files():
        try:
            for line in open(path, encoding="utf-8", errors="ignore"):
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if v and k not in env:
                    env[k] = v
                    if k in TOKEN_NAMES or k in CHAT_NAMES:
                        found_in.append(path)
        except OSError:
            pass
    token = next((env[k] for k in TOKEN_NAMES if env.get(k)), None)
    chat = next((env[k] for k in CHAT_NAMES if env.get(k)), None)
    # Имя переменной не угадали — ищем по форме значения. Так находится
    # телеграм-бот, который у BORIS уже работает под своим названием.
    if not token:
        for k, v in env.items():
            if isinstance(v, str) and TOKEN_SHAPE.match(v.strip()):
                token, _ = v.strip(), found_in.append("по форме значения: %s" % k)
                break
    if not chat:
        for k, v in env.items():
            if ("CHAT" in k.upper() or "TELEGRAM" in k.upper()) \
                    and isinstance(v, str) and CHAT_SHAPE.match(v.strip()):
                chat, _ = v.strip(), found_in.append("по форме значения: %s" % k)
                break
    if token and not chat:
        chat = _chat_from_db()
        if chat:
            found_in.append("chat_id взят из базы BORIS")
    proxy = next((env[k] for k in ("TELEGRAM_PROXY", "HTTPS_PROXY", "https_proxy",
                                   "ALL_PROXY") if env.get(k)), None)
    thread = env.get("TELEGRAM_THREAD_ID")
    _CONF = {"token": token, "chat_id": chat, "proxy": proxy,
             "thread_id": thread if (thread or "").strip().isdigit() else None,
             "sources": sorted(set(found_in))}
    return _CONF


def _chat_from_db():
    """У BORIS уже есть телеграм-связки в базе — берём чат оттуда."""
    from . import db
    try:
        cols = db.rows(
            """SELECT table_name, column_name FROM information_schema.columns
                WHERE table_schema='public' AND column_name ILIKE '%telegram%chat%'
                LIMIT 10""")
    except Exception:
        return None
    for c in cols:
        try:
            r = db.one("SELECT %s AS v FROM %s WHERE %s IS NOT NULL LIMIT 1"
                       % (c["column_name"], c["table_name"], c["column_name"]))
        except Exception:
            continue
        if r and r.get("v") and CHAT_SHAPE.match(str(r["v"]).strip()):
            return str(r["v"]).strip()
    return None


def status():
    """Что настроено — без значений."""
    c = _conf()
    return {"telegram_token": bool(c["token"]), "chat_id": bool(c["chat_id"]),
            "ready": bool(c["token"] and c["chat_id"]),
            "sources": c.get("sources") or [],
            "searched": {"token_names": list(TOKEN_NAMES), "chat_names": list(CHAT_NAMES)}}


def send(text):
    """Отправка. Молча возвращает False, если Telegram не настроен —
    отсутствие уведомления не должно ронять работу агентов."""
    if os.environ.get("EXT_API_NOTIFY_DISABLED") == "1":
        return False
    c = _conf()
    if not (c["token"] and c["chat_id"]):
        return False
    import urllib.request, urllib.error
    from .executor import _opener
    body = {"chat_id": c["chat_id"], "text": text[:3900],
            "disable_web_page_preview": True}
    if c.get("thread_id"):
        body["message_thread_id"] = int(c["thread_id"])
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{c['token']}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with _opener(c["proxy"]).open(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8")).get("ok", False)
    except Exception:
        return False


def done(job):
    """Сообщать владельцу только о подтверждённом бизнес-результате.

    Техническое завершение development-задачи не является поводом для Telegram.
    Для клиентских задач дополнительно сверяем единый production truth: пока
    клиентский результат не доказан, слово «готово» наружу не отправляется.
    """
    title = str(job.get("title") or "")
    scope = job.get("scope") or {}
    try:
        from . import report
        client_key = next((k for k in report.ARTIFACTS if title.startswith(k)), None)
        if not client_key and not bool(scope.get("owner_notify")):
            return False
        human = report.human_title(title)
        if client_key:
            row = next((r for r in report.business_truth(24, fresh=True).get("clients", [])
                        if r.get("key") == client_key), None)
            if not row or not row.get("overall_done"):
                return False
            target = row.get("expected_final") or job.get("goal") or "цель клиента"
            cur, exp = row.get("current"), row.get("expected_count")
            fact = ("не удалось измерить" if cur is None else
                    ("%s из %s" % (cur, exp) if exp else str(cur)))
            place = report.where_to_look(title)
            text = "✅ %s — результат подтверждён.\nЦель: %s.\nФакт: %s." % (
                human, str(target).rstrip("."), fact)
            if place:
                text += "\nГде проверить: " + place
            return send(text)
        summary = ((job.get("final_result") or {}).get("summary") or job.get("goal") or "")[:700]
        return send("✅ BORIS завершил задачу: %s.\nРезультат: %s" % (human, summary))
    except Exception:
        return False


def main():
    """Настройка уведомлений одной командой, без правки чужих .env.

        python3 -m app.ext_api.notify status
        python3 -m app.ext_api.notify set --token 123:AA... --chat -100123
        python3 -m app.ext_api.notify test
    """
    import argparse
    ap = argparse.ArgumentParser(prog="notify")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    st = sub.add_parser("set")
    st.add_argument("--token", default=None)
    st.add_argument("--chat", default=None)
    st.add_argument("--thread", default=None)
    sub.add_parser("test")
    sub.add_parser("where")
    sub.add_parser("chats")
    a = ap.parse_args()
    if a.cmd == "set":
        out = configure(a.token, a.chat, a.thread)
    elif a.cmd == "test":
        out = probe()
    elif a.cmd == "where":
        out = where()
    elif a.cmd == "chats":
        out = chats()
    else:
        out = status()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


def blocked_human(order):
    """Один понятный запрос владельцу только для настоящего внешнего решения."""
    reason = str(order.get("blocked_reason") or "").strip()
    if not reason:
        return False
    try:
        from . import report, dev
        job = dev.get(order.get("dev_job_id"))
        title = report.human_title(str((job or {}).get("title") or order.get("dev_job_id") or ""))
    except Exception:
        title = str(order.get("dev_job_id") or "задача BORIS")
    # Внутренние названия статусов/фаз владельцу не нужны. Причина уже прошла
    # owner-only классификацию в a2a.review.
    return send(
        "⚠️ BORIS действительно не может продолжить без вашего решения.\n"
        f"Задача: {title}.\n"
        f"Что требуется: {reason[:700]}\n"
        "После этого BORIS продолжит работу автоматически.")


if __name__ == "__main__":
    raise SystemExit(main() or 0)
