"""
Telegram-бот BORIS для уведомлений и напоминаний.
Работает через простой поллинг getUpdates (без вебхука — не требует домена/SSL).
Обрабатывает /start <account_id> для привязки чата к аккаунту.
"""
import time
import requests
from app.services.reliability import dependency_call

import os
import re
import socket
import urllib3.util.connection as _urllib3_cn
from app.services.exception_observability import observe_suppressed

BOT_TOKEN = os.environ.get("BORIS_TELEGRAM_BOT_TOKEN")
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Telegram must not inherit the process-wide HTTP(S)_PROXY used for external
# browser/AI traffic. The host has direct Telegram connectivity; inheriting the
# global proxy caused ProxyError -> circuit-open even while api.telegram.org was
# reachable directly.
_TELEGRAM_SESSION = requests.Session()
_TELEGRAM_SESSION.trust_env = False


def _safe_telegram_error(exc) -> str:
    """Redact bot credentials from request exceptions before they reach logs."""
    msg = repr(exc)
    msg = re.sub(r"https://api\.telegram\.org/bot[^/\s'\"]+", "https://api.telegram.org/bot<redacted>", msg)
    msg = re.sub(r"/bot[^/\s'\"]+", "/bot<redacted>", msg)
    return msg[:300]


def _telegram_proxies():
    """
    Возвращает proxy только для Telegram fallback.

    Production env может содержать:
      PROXY_INTL_HOST
    или:
      PROXY_INTL_HOSTS

    BORIS сначала пробует прямой Telegram transport.
    Proxy используется только при сетевой ошибке.
    """
    import random

    host = (os.environ.get("PROXY_INTL_HOST") or "").strip()

    if not host:
        raw_hosts = (os.environ.get("PROXY_INTL_HOSTS") or "").strip()

        if raw_hosts:
            hosts = [
                x.strip()
                for x in re.split(r"[,;\s]+", raw_hosts)
                if x.strip()
            ]

            if hosts:
                host = random.choice(hosts)

    user = (os.environ.get("PROXY_INTL_USER") or "").strip()
    pwd = (os.environ.get("PROXY_INTL_PASS") or "").strip()

    port_min = int(
        os.environ.get(
            "PROXY_INTL_PORT_MIN",
            os.environ.get("PROXY_PORT_MIN", "10000"),
        )
    )

    port_max = int(
        os.environ.get(
            "PROXY_INTL_PORT_MAX",
            os.environ.get("PROXY_PORT_MAX", "10999"),
        )
    )

    if not all([host, user, pwd]):
        return None

    if port_min > port_max:
        port_min, port_max = port_max, port_min

    port = random.randint(port_min, port_max)

    url = f"http://{user}:{pwd}@{host}:{port}"

    return {
        "http": url,
        "https": url,
    }

def _telegram_post(url, json_payload, timeout=8):
    """
    Direct Telegram POST through Telegram-only IPv6 transport.
    Three attempts for transient failures.
    """
    last_error = None

    for attempt in range(3):
        try:
            return dependency_call(
                "telegram", _TELEGRAM_SESSION.post, url,
                json=json_payload,
                timeout=(4, timeout),
            )

        except requests.RequestException as e:
            last_error = e

            if attempt < 2:
                time.sleep(0.6 * (attempt + 1))

    raise last_error




def _telegram_get(url, params=None, timeout=10):
    """
    Direct Telegram GET through Telegram-only IPv6 transport.
    Three attempts for transient failures.
    """
    last_error = None

    for attempt in range(3):
        try:
            return dependency_call(
                "telegram", _TELEGRAM_SESSION.get, url,
                params=params,
                timeout=(4, timeout),
            )

        except requests.RequestException as e:
            last_error = e

            if attempt < 2:
                time.sleep(0.6 * (attempt + 1))

    raise last_error





_original_create_connection = _urllib3_cn.create_connection

def _create_connection_ipv6_for_telegram(address, *args, **kwargs):
    host, port = address[0], address[1]
    if host == "api.telegram.org":
        err = None
        for res in socket.getaddrinfo(host, port, socket.AF_INET6, socket.SOCK_STREAM):
            af, socktype, proto, canonname, sa = res
            sock = None
            try:
                sock = socket.socket(af, socktype, proto)
                timeout = kwargs.get("timeout")
                if timeout is not None and timeout != socket._GLOBAL_DEFAULT_TIMEOUT:
                    sock.settimeout(timeout)
                sock.connect(sa)
                return sock
            except OSError as e:
                err = e
                if sock is not None:
                    sock.close()
        if err is not None:
            raise err
        raise OSError("getaddrinfo вернул пустой список для IPv6")
    return _original_create_connection(address, *args, **kwargs)


_urllib3_cn.create_connection = _create_connection_ipv6_for_telegram


def _telegram_post_files(url, data, files, timeout=60):
    """
    Direct multipart Telegram POST.
    Used by sendPhoto.
    """
    last_error = None

    for attempt in range(3):
        try:
            for obj in (files or {}).values():
                try:
                    obj.seek(0)
                except Exception as _suppressed_exc:
                    observe_suppressed(__name__, _suppressed_exc, line=189)

            return dependency_call(
                "telegram", requests.post, url,
                data=data,
                files=files,
                timeout=(5, timeout),
            )

        except requests.RequestException as e:
            last_error = e

            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))

    raise last_error


def send_telegram_message(chat_id: str, text: str, thread_id=None):
    """Отправляет сообщение в Telegram. thread_id — id темы (Topic) в супергруппе, если нужно писать в конкретную тему."""
    if not BOT_TOKEN or not chat_id:
        return {"ok": False, "error": "no_token_or_chat_id"}
    try:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if thread_id:
            payload["message_thread_id"] = int(thread_id)
        resp = _telegram_post(f"{API_BASE}/sendMessage", payload)
        return resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def send_telegram_photo(chat_id: str, photo_path: str, caption: str = "", thread_id=None):
    """
    Sends photo to Telegram through direct IPv6 transport.
    Falls back to text when photo file is unavailable.
    """
    if not BOT_TOKEN or not chat_id:
        return {
            "ok": False,
            "error": "no_token_or_chat_id",
        }

    try:
        if not photo_path or not os.path.exists(photo_path):
            return send_telegram_message(
                chat_id,
                caption,
                thread_id=thread_id,
            )

        data = {
            "chat_id": str(chat_id),
            "caption": caption[:1024],
            "parse_mode": "HTML",
        }

        if thread_id:
            data["message_thread_id"] = int(thread_id)

        with open(photo_path, "rb") as ph:
            resp = _telegram_post_files(
                f"{API_BASE}/sendPhoto",
                data=data,
                files={"photo": ph},
                timeout=60,
            )

        return resp.json()

    except Exception as e:
        return {
            "ok": False,
            "error": str(e)[:200],
        }




def _normalize_keyboard(buttons: list) -> list:
    """
    Приводит buttons к формату inline_keyboard (список рядов).
    Обратная совместимость:
      - плоский список [{"text","callback_data"}]  -> один ряд (как было);
      - список рядов [[{...},{...}],[{...}]]        -> используется как есть,
        поддерживает и url-кнопки, и callback_data.
    """
    if not buttons:
        return []
    if isinstance(buttons[0], list):          # уже ряды
        rows = buttons
    else:                                     # плоский список -> один ряд
        rows = [buttons]
    out = []
    for row in rows:
        cells = []
        for b in row:
            cell = {"text": b["text"]}
            if b.get("url"):
                cell["url"] = b["url"]
            if b.get("callback_data"):
                cell["callback_data"] = b["callback_data"]
            cells.append(cell)
        out.append(cells)
    return out


def send_telegram_message_with_buttons(chat_id: str, text: str, buttons: list, thread_id=None):
    """buttons — список [{"text": "...", "callback_data": "..."}], один ряд кнопок.
    thread_id — необязательный id темы супергруппы; без него поведение прежнее."""
    if not BOT_TOKEN or not chat_id:
        return {"ok": False, "error": "no_token_or_chat_id"}
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": _normalize_keyboard(buttons)},
        }
        if thread_id:
            payload["message_thread_id"] = int(thread_id)
        resp = _telegram_post(f"{API_BASE}/sendMessage", payload)
        return resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def edit_telegram_message(chat_id: str, message_id: int, text: str):
    """Убирает кнопки и меняет текст сообщения (после решения ✅/✏️/❌)."""
    if not BOT_TOKEN:
        return {"ok": False}
    try:
        resp = _telegram_post(
            f"{API_BASE}/editMessageText",
            {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"},
        )
        return resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _draft_storage_key(draft_id: str):
    return f"messenger_draft:{draft_id}"


def save_pending_draft(draft_id: str, account_id: str, avito_chat_id: str, text: str):
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    db = SessionLocal()
    try:
        key = _draft_storage_key(draft_id)
        value = _json.dumps({
            "account_id": account_id, "avito_chat_id": avito_chat_id, "text": text, "status": "pending",
        }, ensure_ascii=False)
        row = Storage(account_id="_telegram_drafts", key=key, value=value)
        db.add(row)
        db.commit()
    finally:
        db.close()


def _load_pending_draft(draft_id: str):
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == "_telegram_drafts", Storage.key == _draft_storage_key(draft_id)).first()
        if row:
            return _json.loads(row.value)
        return None
    finally:
        db.close()


def _set_awaiting_edit(telegram_chat_id: str, draft_id: str):
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    db = SessionLocal()
    try:
        key = f"awaiting_edit:{telegram_chat_id}"
        value = _json.dumps({"draft_id": draft_id}, ensure_ascii=False)
        row = db.query(Storage).filter(Storage.account_id == "_telegram_drafts", Storage.key == key).first()
        if row:
            row.value = value
        else:
            row = Storage(account_id="_telegram_drafts", key=key, value=value)
            db.add(row)
        db.commit()
    finally:
        db.close()


def _pop_awaiting_edit(telegram_chat_id: str):
    """Возвращает draft_id, если этот telegram-чат ждал текст правки, и сразу очищает state."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    db = SessionLocal()
    try:
        key = f"awaiting_edit:{telegram_chat_id}"
        row = db.query(Storage).filter(Storage.account_id == "_telegram_drafts", Storage.key == key).first()
        if not row:
            return None
        draft_id = _json.loads(row.value).get("draft_id")
        db.delete(row)
        db.commit()
        return draft_id
    finally:
        db.close()


def _handle_callback_query(callback_query: dict):
    """Обрабатывает нажатия кнопок ✅/✏️/❌ под черновиком ответа клиенту."""
    from app.api.messenger import send_message as avito_send_message

    data = callback_query.get("data", "")
    message = callback_query.get("message", {})
    telegram_chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")

    if ":" not in data:
        return
    # --- кнопки мониторинга: перехват ДО menedžer-логики msgr_* ---
    # наши callback (mlead:/mreply:/mskip:) содержат ':', поэтому обрабатываем
    # их раньше, чем data уйдёт в _load_pending_draft
    if data.split(":", 1)[0] in ("mlead", "mreply", "mskip"):
        from app.monitoring import alerts_handler
        alerts_handler.handle(data, callback_query)
        return

    # --- новый контур AI-МОПа: ветка mp:* ДО старого разбора action:draft_id ---
    if data.split(":", 1)[0] == "mp":
        try:
            from app.mop_core import handle_mop_callback
            if handle_mop_callback(data, callback_query):
                return
        except Exception as _e:
            print(f"MOP callback error: {_e}", flush=True)
        return

    action, draft_id = data.split(":", 1)

    draft = _load_pending_draft(draft_id)
    if not draft:
        edit_telegram_message(telegram_chat_id, message_id, "⚠️ Черновик устарел или уже обработан.")
        return

    if action == "msgr_approve":
        result = avito_send_message(draft["account_id"], draft["avito_chat_id"], draft["text"])
        if result.get("status") == "ok":
            edit_telegram_message(telegram_chat_id, message_id, f"✅ Отправлено клиенту:\n\n{draft['text']}")
        else:
            edit_telegram_message(telegram_chat_id, message_id, f"⚠️ Ошибка отправки: {result.get('message', '?')}")
    elif action == "msgr_discard":
        edit_telegram_message(telegram_chat_id, message_id, "❌ Пропущено, отвечу вручную.")
    elif action == "msgr_edit":
        _set_awaiting_edit(str(telegram_chat_id), draft_id)
        send_telegram_message(telegram_chat_id, "✏️ Напиши текст, который отправить клиенту вместо черновика:")


def _handle_plain_message_for_edit(chat_id: str, text: str) -> bool:
    """Если этот telegram-чат ждал текст правки — использует его как замену черновику и отправляет.
    Возвращает True если сообщение было обработано как правка (и не нужно обрабатывать дальше как /start)."""
    from app.api.messenger import send_message as avito_send_message

    draft_id = _pop_awaiting_edit(str(chat_id))
    if not draft_id:
        return False
    draft = _load_pending_draft(draft_id)
    if not draft:
        send_telegram_message(chat_id, "⚠️ Черновик устарел, не могу отправить правку.")
        return True
    result = avito_send_message(draft["account_id"], draft["avito_chat_id"], text)
    if result.get("status") == "ok":
        send_telegram_message(chat_id, f"✅ Отправлено клиенту (твой вариант):\n\n{text}")
    else:
        send_telegram_message(chat_id, f"⚠️ Ошибка отправки: {result.get('message', '?')}")
    return True


def _handle_start_command(chat_id: str, account_id: str):
    """Привязывает telegram_chat_id к аккаунту в БД."""
    from app.db.session import SessionLocal
    from app.models.account import Account

    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.account_id == account_id).first()
        if not account:
            send_telegram_message(chat_id, f"⚠️ Аккаунт «{account_id}» не найден.")
            return
        account.telegram_chat_id = str(chat_id)
        db.commit()
        send_telegram_message(
            chat_id,
            f"✅ Готово! Уведомления и напоминания BORIS по аккаунту «{account_id}» теперь будут приходить сюда."
        )
    except Exception as e:
        send_telegram_message(chat_id, f"⚠️ Ошибка привязки: {str(e)[:150]}")
    finally:
        db.close()


def _handle_owner_report_command(chat_id, thread_id, text_value: str) -> bool:
    """Owner-only operational report commands in the dedicated Telegram topic."""
    if str(chat_id) != "-1003952038222" or int(thread_id or 0) != 1759:
        return False
    cmd = (text_value or "").strip().split()
    if not cmd:
        return False
    base = cmd[0].split('@')[0].lower()
    days = {"/report": 1, "/report1": 1, "/report7": 7, "/report30": 30}.get(base)
    if not days:
        return False
    account_id = cmd[1].strip() if len(cmd) > 1 else ""
    try:
        from app.api.inbox_daily import render_owner_daily_report, render_owner_period_report, _telegram_chunks
        if days == 1:
            rep = render_owner_daily_report()
        else:
            rep = render_owner_period_report(days=days, account_id=account_id)
        for chunk in _telegram_chunks(rep.get("text") or "Нет данных за выбранный период"):
            send_telegram_message(str(chat_id), chunk, thread_id=int(thread_id))
    except Exception as e:
        print(f"OWNER_REPORT_COMMAND_ERROR: {repr(e)[:200]}", flush=True)
        send_telegram_message(str(chat_id), "⚠️ Не удалось собрать отчёт. Ошибка записана в журнал BORIS.", thread_id=int(thread_id))
    return True


def _telegram_poll_loop():
    """Фоновый поллинг getUpdates раз в 3 секунды: /start, owner reports, MOP and edit flows."""
    if not BOT_TOKEN:
        print("BORIS_TELEGRAM_BOT_TOKEN не задан — поллинг Telegram-бота не запущен")
        return

    offset = 0
    print(f"TELEGRAM_POLL: цикл стартовал, BOT_TOKEN задан={bool(BOT_TOKEN)}", flush=True)
    while True:
        time.sleep(3)
        try:
            resp = _telegram_get(
                f"{API_BASE}/getUpdates",
                params={"offset": offset, "timeout": 2},
                timeout=10,
            )
            data = resp.json()
            if not data.get("ok"):
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1

                callback_query = update.get("callback_query")
                if callback_query:
                    _handle_callback_query(callback_query)
                    continue

                message = update.get("message")
                if not message:
                    continue
                chat_id = message["chat"]["id"]
                text = message.get("text", "")
                _thr = message.get("message_thread_id")
                if _thr:
                    print(f"TG_TOPIC chat_id={chat_id} thread_id={_thr} text={text[:40]!r}", flush=True)

                if not text.startswith("/start") and _handle_owner_report_command(chat_id, _thr, text):
                    continue

                # OWNER_DEV_INBOX_BRIDGE_V1: the production bot is already the
                # sole getUpdates consumer. Hand each ordinary update to the
                # autonomous development inbox before MOP/edit handlers; inbox
                # filters exact owner chat/thread/user and returns None for all
                # unrelated traffic, so no second Telegram bot/poller is needed.
                if not text.startswith("/start"):
                    try:
                        from app.ext_api.inbox import handle_update as _dev_inbox_update
                        if _dev_inbox_update(update, send_reply=True):
                            continue
                    except Exception as _e:
                        print(f"DEV_INBOX_BRIDGE_ERROR: {_e}", flush=True)

                if not text.startswith("/start"):
                    try:
                        from app.mop_core import handle_mop_message
                        if handle_mop_message(message):
                            continue
                    except Exception as _e:
                        print(f"MOP message error: {_e}", flush=True)

                if not text.startswith("/start") and _handle_plain_message_for_edit(chat_id, text):
                    continue

                if text.startswith("/start"):
                    parts = text.split(maxsplit=1)
                    if len(parts) == 2:
                        account_id = parts[1].strip()
                        _handle_start_command(chat_id, account_id)
                    else:
                        send_telegram_message(
                            chat_id,
                            "👋 Привет! Это бот уведомлений BORIS.\n"
                            "Чтобы привязать этот чат к аккаунту, перейдите по ссылке из BORIS "
                            "(вкладка «Задачи и план» → «Подключить Telegram»)."
                        )
        except Exception as e:
            print(f"TELEGRAM_POLL_ERROR: {_safe_telegram_error(e)}", flush=True)
