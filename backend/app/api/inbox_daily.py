"""Ежедневный отчёт по необработанным диалогам. Без ИИ и без PDF — чистый SQL.
Автоотправки НЕТ: только ручной preview и тестовая отправка администратору.
Диалоги-покупки (мы сами покупатель) в отчёт не попадают.
"""
import datetime, os, json, hashlib, secrets, re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.db.session import SessionLocal
from app.api.inbox_slots import _connected_account_ids
from app.services.exception_observability import observe_suppressed
try:
    from app.api.auth import get_current_user
except ImportError:
    from app.auth import get_current_user
from app.services.platform_roles import is_private_platform_owner

router = APIRouter(prefix="/api/inbox", tags=["inbox-daily"])
MSK = datetime.timezone(datetime.timedelta(hours=3))
INBOX_URL = "https://boris-ai.pro/messages"

_NOT_PURCHASE = (" AND (m.item_owner_id IS NULL OR m.item_owner_id = "
                 "(SELECT avito_user_id FROM account_slots WHERE account_id = m.account_id LIMIT 1))")


def _hours(sec):
    if not sec: return 0.0
    return round((datetime.datetime.now(tz=MSK).timestamp() - int(sec)) / 3600, 1)


def _fmt_wait(h):
    if h >= 24: return f"{int(h // 24)} дн {int(h % 24)} ч"
    if h >= 1:  return f"{int(h)} ч"
    return f"{int(h * 60)} мин"


def account_stats(db, account_id, user_id, period_days: int = 1):
    since = int((datetime.datetime.now(tz=MSK) - datetime.timedelta(days=period_days)).timestamp())
    p = {"a": account_id, "d": since, "u": user_id}

    # Новый диалог = новое реальное входящее обращение клиента. Системные
    # события Avito и исходящие сообщения продавца не должны раздувать метрику.
    new_dialogs = db.execute(text(
        "SELECT count(*) FROM (SELECT avito_chat_id, min(avito_created_at) f FROM messenger_messages m "
        " WHERE account_id=:a AND lower(coalesce(direction,'')) LIKE 'in%' "
        "   AND coalesce(msg_type,'') <> 'system'" + _NOT_PURCHASE + " GROUP BY 1) t WHERE t.f >= :d"), p).scalar() or 0

    new_msgs = db.execute(text(
        "SELECT count(*) FROM messenger_messages m WHERE account_id=:a AND avito_created_at >= :d "
        "  AND lower(coalesce(direction,'')) LIKE 'in%' AND coalesce(msg_type,'') <> 'system'"
        + _NOT_PURCHASE), p).scalar() or 0

    unread = db.execute(text(
        "SELECT count(*) FROM messenger_messages m "
        "  LEFT JOIN messenger_dialog_state s ON s.user_id=:u AND s.account_id=m.account_id "
        "    AND s.avito_chat_id=m.avito_chat_id "
        " WHERE m.account_id=:a AND lower(coalesce(m.direction,'')) LIKE 'in%' "
        "   AND coalesce(m.msg_type,'') <> 'system'" + _NOT_PURCHASE +
        "   AND (s.last_read_at IS NULL OR to_timestamp(coalesce(m.avito_created_at,0)) > s.last_read_at)"), p).scalar() or 0

    rows = db.execute(text(
        "SELECT avito_chat_id, item_title, direction, avito_created_at FROM ("
        "  SELECT DISTINCT ON (avito_chat_id) avito_chat_id, item_title, direction, avito_created_at "
        "    FROM messenger_messages m WHERE account_id=:a AND coalesce(msg_type,'') <> 'system'"
        + _NOT_PURCHASE +
        "   ORDER BY avito_chat_id, avito_created_at DESC) t "
        " WHERE lower(coalesce(direction,'')) LIKE 'in%' ORDER BY avito_created_at ASC"), p).all()

    overdue = [{"chat_id": r[0], "item": r[1] or "без объявления", "wait_h": _hours(r[3])} for r in rows]
    return {
        "account_id": account_id, "new_dialogs": new_dialogs, "new_msgs": new_msgs,
        "unread": unread, "unanswered": len(overdue),
        "max_wait_h": overdue[0]["wait_h"] if overdue else 0.0,
        "overdue": overdue[:5],
    }


def _period(days: int) -> int:
    days = int(days or 1)
    if days not in (1, 7, 30):
        raise HTTPException(status_code=400, detail="Поддерживаются периоды 1, 7 или 30 дней")
    return days


def _client_safe_text(text_value: str) -> str:
    clean = re.sub(r"<[^>]+>", "", text_value or "")
    clean = clean.replace(f"\n👉 Открыть единое окно: {INBOX_URL}", "")
    clean = clean.replace("Итого по всем аккаунтам:", "Итого:")
    return clean.strip()


def build_report(user, account_id: str = "", period_days: int = 1, report_type: str = "messages", client_safe: bool = False):
    if report_type != "messages":
        return {"status": "error", "message": "Этот тип отчёта пока не поддерживается"}
    period_days = _period(period_days)
    db = SessionLocal()
    try:
        conn = _connected_account_ids(db, user)
        if account_id:
            conn = [(aid, name) for aid, name in conn if aid == account_id]
        if not conn:
            return {"status": "error", "message": "Аккаунт недоступен или не подключён" if account_id else "Нет подключённых аккаунтов"}
        parts, tot = [], {"new_dialogs": 0, "new_msgs": 0, "unread": 0, "unanswered": 0, "max_wait_h": 0.0}
        stats_all = []
        for acc_id, acc_name in conn:
            st = account_stats(db, acc_id, getattr(user, "id", None), period_days=period_days)
            st["account_name"] = acc_name
            stats_all.append(st)
            for k in ("new_dialogs", "new_msgs", "unread", "unanswered"):
                tot[k] += st[k]
            tot["max_wait_h"] = max(tot["max_wait_h"], st["max_wait_h"])

            block = [f"📌 <b>{acc_name}</b>",
                     f"   Новых диалогов за {period_days} дн.: {st['new_dialogs']}",
                     f"   Новых сообщений: {st['new_msgs']}",
                     f"   Непрочитанных: {st['unread']}",
                     f"   Без ответа: {st['unanswered']}"]
            if st["unanswered"]:
                block.append(f"   Дольше всех ждёт: {_fmt_wait(st['max_wait_h'])}")
                for o in st["overdue"]:
                    block.append(f"      • {o['item'][:38]} — {_fmt_wait(o['wait_h'])}")
            parts.append("\n".join(block))

        period_label = "за сутки" if period_days == 1 else f"за последние {period_days} дней"
        head = (f"📊 <b>Сводка по сообщениям Avito {period_label}</b>\n"
                f"{datetime.datetime.now(tz=MSK).strftime('%d.%m.%Y')}\n")
        total_label = "Итого по аккаунту:" if len(conn) == 1 else "Итого по всем аккаунтам:"
        foot = (f"\n<b>{total_label}</b>\n"
                f"   Новых диалогов: {tot['new_dialogs']} · новых сообщений: {tot['new_msgs']}\n"
                f"   Непрочитанных: {tot['unread']} · без ответа: {tot['unanswered']}\n")
        if tot["unanswered"]:
            foot += f"   Максимальное ожидание: {_fmt_wait(tot['max_wait_h'])}\n"
        else:
            foot += "   ✅ Все диалоги отвечены\n"
        if not client_safe:
            foot += f"\n👉 Открыть единое окно: {INBOX_URL}"
        report_text = head + "\n" + "\n\n".join(parts) + "\n" + foot
        if client_safe:
            report_text = _client_safe_text(report_text)
        return {"status": "ok", "report_type": "messages", "report_type_name": "Обращения и продажи",
                "period_days": period_days, "period_label": period_label,
                "account_id": conn[0][0] if len(conn) == 1 else "",
                "account_name": conn[0][1] if len(conn) == 1 else "Все доступные аккаунты",
                "headline": f"Сводка по сообщениям Avito {period_label}",
                "generated_at": datetime.datetime.now(tz=MSK).isoformat(),
                "metric_notes": {
                    "new_dialogs": "Новые реальные входящие обращения клиентов за период; системные события Avito и исходящие не считаются.",
                    "new_msgs": "Все входящие сообщения клиентов за период, без системных событий Avito.",
                    "unread": "Непрочитанные входящие сообщения в BORIS на текущий момент.",
                    "unanswered": "Диалоги, где последнее реальное сообщение сейчас от клиента и после него нет ответа продавца."
                },
                "text": report_text, "totals": tot, "accounts": stats_all,
                "client_safe": bool(client_safe)}
    finally:
        db.close()


@router.get("/daily_report/preview")
def preview(account_id: str = "", period_days: int = 7, report_type: str = "messages", user=Depends(get_current_user)):
    """Account-scoped owner preview. Ничего не отправляет."""
    return build_report(user, account_id=account_id, period_days=period_days, report_type=report_type)


def _client_link_token(account_id: str) -> str:
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "client_success_client_link").first()
        token = (row.value or "").strip() if row else ""
        if token:
            return token
        token = secrets.token_urlsafe(24)
        if row:
            row.value = token
        else:
            db.add(Storage(account_id=account_id, key="client_success_client_link", value=token))
        db.commit()
        return token
    finally:
        db.close()


def _account_for_client_token(token: str) -> str:
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.key == "client_success_client_link", Storage.value == token).first()
        return str(row.account_id) if row else ""
    finally:
        db.close()


@router.get("/daily_report/client_preview/{token}")
def client_preview(token: str, period_days: int = 7, report_type: str = "messages", user=Depends(get_current_user)):
    """CLIENT_SAFE_LINK target: opaque token + auth + account scope; owner-only/internal data is omitted."""
    account_id = _account_for_client_token(token)
    if not account_id:
        raise HTTPException(status_code=404, detail="Ссылка на отчёт не найдена")
    rep = build_report(user, account_id=account_id, period_days=period_days, report_type=report_type, client_safe=True)
    if rep.get("status") != "ok":
        return rep
    return {k: rep[k] for k in ("status", "report_type", "report_type_name", "period_days", "period_label", "account_name", "headline", "text", "totals", "client_safe")}


def _delivery_target(account_id: str):
    db = SessionLocal()
    try:
        try:
            from app import mop_core
            tgt = mop_core.target_for(db, account_id, "reports")
            if tgt:
                return {"channel": "telegram", "recipient": "Telegram · отчёты", "chat_id": str(tgt[0]), "thread_id": int(tgt[1] or 0)}
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=213)
        row = db.execute(text("SELECT telegram_chat_id FROM accounts WHERE account_id=:a"), {"a": account_id}).fetchone()
        if row and row[0]:
            return {"channel": "telegram", "recipient": "Telegram аккаунта", "chat_id": str(row[0]), "thread_id": 0}
        return None
    finally:
        db.close()


def _delivery_state(account_id: str) -> dict:
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "client_success_report_delivery").first()
        if not row:
            return {}
        try:
            return json.loads(row.value or "{}") or {}
        except Exception:
            return {}
    finally:
        db.close()


def _delivery_state_merge(account_id: str, delta: dict) -> dict:
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "client_success_report_delivery").first()
        try:
            current = json.loads(row.value or "{}") if row else {}
        except Exception:
            current = {}
        current.update(delta)
        raw = json.dumps(current, ensure_ascii=False)
        if row:
            row.value = raw
        else:
            db.add(Storage(account_id=account_id, key="client_success_report_delivery", value=raw))
        db.commit()
        return current
    finally:
        db.close()


def _owner_can_send(user, account_id: str) -> bool:
    if getattr(user, "role", "") == "owner":
        return True
    db = SessionLocal()
    try:
        return bool(db.execute(text("SELECT 1 FROM accounts WHERE account_id=:a AND owner_user_id=:u"), {"a": account_id, "u": getattr(user, "id", None)}).fetchone())
    finally:
        db.close()


class DeliveryPrepare(BaseModel):
    account_id: str
    period_days: int = 7
    report_type: str = "messages"


class DeliveryConfirm(BaseModel):
    account_id: str
    confirmation_id: str


@router.get("/daily_report/smart_links")
def smart_links(account_id: str, period_days: int = 7, report_type: str = "messages", user=Depends(get_current_user)):
    rep = build_report(user, account_id=account_id, period_days=period_days, report_type=report_type, client_safe=True)
    if rep.get("status") != "ok":
        return rep
    token = _client_link_token(account_id)
    client_path = f"/client/report/{token}?period_days={period_days}&report_type={report_type}"
    return {"status": "ok", "links": [
        {"key": "client_success", "label": "Открыть Client Success", "url": "/dashboard/client-success", "classification": "INTERNAL_LINK", "share_allowed": False},
        {"key": "client_report", "label": "Клиентский отчёт", "url": client_path, "classification": "CLIENT_SAFE_LINK", "share_allowed": True, "security": "AUTH_REQUIRED_ACCOUNT_SCOPED_OPAQUE_TOKEN"},
        {"key": "billing", "label": "Тарифы и финансы", "url": "/dashboard/billing", "classification": "INTERNAL_LINK", "share_allowed": False},
    ], "external_public_link": None, "external_public_reason": "Публичная ссылка без авторизации не создаётся без отдельного security contract"}


@router.post("/daily_report/delivery/prepare")
def delivery_prepare(req: DeliveryPrepare, user=Depends(get_current_user)):
    if not _owner_can_send(user, req.account_id):
        raise HTTPException(status_code=403, detail="Отправку должен подтвердить владелец аккаунта")
    rep = build_report(user, account_id=req.account_id, period_days=req.period_days, report_type=req.report_type, client_safe=True)
    if rep.get("status") != "ok":
        return rep
    target = _delivery_target(req.account_id)
    if not target:
        return {"status": "error", "code": "delivery_target_missing", "message": "Для этого аккаунта не настроен Telegram-канал отчётов"}
    confirmation_id = secrets.token_urlsafe(24)
    report_hash = hashlib.sha256(rep["text"].encode("utf-8")).hexdigest()
    now = datetime.datetime.utcnow()
    intent = {"confirmation_id": confirmation_id, "status": "prepared", "created_at": now.isoformat(),
              "expires_at": (now + datetime.timedelta(minutes=15)).isoformat(),
              "period_days": rep["period_days"], "report_type": rep["report_type"], "report_hash": report_hash,
              "channel": target["channel"], "recipient": target["recipient"]}
    _delivery_state_merge(req.account_id, {"pending": intent, "last_prepared_at": now.isoformat()})
    return {"status": "ok", "dry_run": True, "confirmation_required": True, "confirmation_id": confirmation_id,
            "account_name": rep["account_name"], "period_label": rep["period_label"], "report_type_name": rep["report_type_name"],
            "headline": rep["headline"], "totals": rep["totals"], "preview": rep["text"],
            "channel": target["channel"], "recipient": target["recipient"], "expires_at": intent["expires_at"]}


@router.post("/daily_report/delivery/confirm")
def delivery_confirm(req: DeliveryConfirm, user=Depends(get_current_user)):
    if not _owner_can_send(user, req.account_id):
        raise HTTPException(status_code=403, detail="Отправку должен подтвердить владелец аккаунта")
    state = _delivery_state(req.account_id)
    intent = state.get("pending") or {}
    if intent.get("confirmation_id") != req.confirmation_id:
        raise HTTPException(status_code=409, detail="Подтверждение устарело. Подготовьте отчёт заново")
    if intent.get("status") == "delivered":
        return {"status": "ok", "delivered": True, "duplicate_send": False, "idempotent": True, "sent_at": intent.get("sent_at")}
    if intent.get("status") in ("sending", "failed"):
        raise HTTPException(status_code=409, detail="Повторная отправка по этому подтверждению заблокирована. Подготовьте отчёт заново")
    try:
        expires = datetime.datetime.fromisoformat(intent.get("expires_at") or "")
    except Exception:
        expires = datetime.datetime.min
    if datetime.datetime.utcnow() > expires:
        raise HTTPException(status_code=409, detail="Время подтверждения истекло. Подготовьте отчёт заново")
    rep = build_report(user, account_id=req.account_id, period_days=int(intent.get("period_days") or 7), report_type=intent.get("report_type") or "messages", client_safe=True)
    if rep.get("status") != "ok":
        return rep
    if hashlib.sha256(rep["text"].encode("utf-8")).hexdigest() != intent.get("report_hash"):
        raise HTTPException(status_code=409, detail="Данные отчёта изменились. Проверьте новый предпросмотр")
    target = _delivery_target(req.account_id)
    if not target:
        return {"status": "error", "code": "delivery_target_missing", "message": "Telegram-канал отчётов больше не доступен"}
    intent["status"] = "sending"
    _delivery_state_merge(req.account_id, {"pending": intent})
    try:
        from app.telegram_bot import send_telegram_message
        send_telegram_message(target["chat_id"], rep["text"], thread_id=target.get("thread_id") or None)
    except Exception as e:
        intent["status"] = "failed"
        intent["failed_at"] = datetime.datetime.utcnow().isoformat()
        _delivery_state_merge(req.account_id, {"pending": intent, "last_error": type(e).__name__})
        return {"status": "error", "code": "delivery_failed", "message": "Не удалось отправить отчёт. Подготовьте отправку ещё раз"}
    sent_at = datetime.datetime.utcnow().isoformat()
    intent["status"] = "delivered"
    intent["sent_at"] = sent_at
    _delivery_state_merge(req.account_id, {"pending": intent, "last_delivered_at": sent_at, "last_error": ""})
    return {"status": "ok", "delivered": True, "duplicate_send": False, "sent_at": sent_at, "channel": target["channel"], "recipient": target["recipient"]}


class SendTest(BaseModel):
    chat_id: str = ""      # пусто → DIRECTOR_CHAT_ID из окружения


@router.post("/daily_report/send_test")
def send_test(req: SendTest, user=Depends(get_current_user)):
    """Тестовая отправка ТОЛЬКО администратору. Боевым клиентам не шлём."""
    rep = build_report(user)
    if rep.get("status") != "ok":
        return rep
    target = (req.chat_id or os.environ.get("DIRECTOR_CHAT_ID") or "").strip()
    if not target:
        return {"status": "error", "message": "Не задан получатель (DIRECTOR_CHAT_ID пуст)"}
    ok, err = False, ""
    try:
        from app.telegram_bot import send_telegram_message
        send_telegram_message(target, rep["text"])
        ok = True
    except Exception as e:
        err = repr(e)[:300]
    db = SessionLocal()
    try:
        db.execute(text("INSERT INTO inbox_daily_log (created_at, user_id, target_chat_id, status, error, totals) "
                        "VALUES (now(), :u, :t, :s, :e, :tot)"),
                   {"u": getattr(user, "id", None), "t": target, "s": "ok" if ok else "error",
                    "e": err, "tot": str(rep.get("totals"))})
        db.commit()
    finally:
        db.close()
    return {"status": "ok" if ok else "error", "sent_to": target, "error": err,
            "preview": rep["text"]}


class DailySettings(BaseModel):
    enabled: bool = False
    send_at_hour: int = 9
    timezone: str = "Europe/Moscow"
    telegram_chat_id: str = ""


@router.get("/daily_report/settings")
def get_settings(user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        r = db.execute(text("SELECT enabled, send_at_hour, timezone, telegram_chat_id "
                            "  FROM inbox_daily_settings WHERE organization_id=:o"),
                       {"o": user.id}).first()
        if not r:
            return {"status": "ok", "settings": {"enabled": False, "send_at_hour": 9,
                    "timezone": "Europe/Moscow", "telegram_chat_id": ""}, "exists": False}
        return {"status": "ok", "exists": True, "settings": {
            "enabled": r[0], "send_at_hour": r[1], "timezone": r[2], "telegram_chat_id": r[3] or ""}}
    finally:
        db.close()


@router.post("/daily_report/settings")
def save_settings(req: DailySettings, user=Depends(get_current_user)):
    if not (0 <= int(req.send_at_hour) <= 23):
        return {"status": "error", "message": "Час должен быть от 0 до 23"}
    db = SessionLocal()
    try:
        upd = db.execute(text(
            "UPDATE inbox_daily_settings SET enabled=:e, send_at_hour=:h, timezone=:tz, "
            " telegram_chat_id=:c, updated_at=now() WHERE organization_id=:o"),
            {"e": req.enabled, "h": req.send_at_hour, "tz": req.timezone,
             "c": req.telegram_chat_id or None, "o": user.id})
        if upd.rowcount == 0:
            db.execute(text(
                "INSERT INTO inbox_daily_settings (organization_id, enabled, send_at_hour, "
                " timezone, telegram_chat_id) VALUES (:o,:e,:h,:tz,:c)"),
                {"o": user.id, "e": req.enabled, "h": req.send_at_hour,
                 "tz": req.timezone, "c": req.telegram_chat_id or None})
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


# ==========================================================================
# OWNER DAILY BUSINESS REPORTS — authoritative extension of inbox_daily
# ==========================================================================
OWNER_REPORT_CHAT_ID = os.environ.get("OWNER_DAILY_REPORT_CHAT_ID", "-1003952038222")
OWNER_REPORT_THREAD_ID = int(os.environ.get("OWNER_DAILY_REPORT_THREAD_ID", "1759") or 1759)
OWNER_REPORT_MESSENGER_ACCOUNT_ID = os.environ.get(
    "OWNER_DAILY_REPORT_MESSENGER_ACCOUNT_ID", "marketing_pod_klyuch_20035"
)
OWNER_REPORT_MESSENGER_TITLE = os.environ.get(
    "OWNER_DAILY_REPORT_MESSENGER_TITLE", "KPI / AI-маркетолог"
)


def _owner_messenger_text(value: str) -> str:
    """Convert Telegram HTML into readable BORIS Messenger plain text."""
    import html as _html
    cleaned = str(value or "").replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    cleaned = re.sub(r"</?b>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    return _html.unescape(cleaned).strip()


def _owner_report_part_message_id(report_date, slot: str, conversation_id: int, part: int) -> str:
    from uuid import NAMESPACE_URL, uuid5

    # Reuse the legacy first-message identity. An old worker and a new worker
    # cannot both create a different first message during a rolling release.
    identity = (
        f"boris-owner-report:auto:v1:{report_date.isoformat()}:{slot}:"
        f"conversation:{int(conversation_id)}"
    )
    if part > 1:
        identity += f":part:{part}"
    return str(uuid5(NAMESPACE_URL, identity))


def _owner_report_chunk_plan(body: str, report_date, slot: str, conversation_id: int, owner_id: int) -> dict:
    from hashlib import sha256

    plain = _owner_messenger_text(body)
    if not plain:
        raise ValueError("owner_report_body_empty")
    slot_label = "утро" if slot == "morning" else "вечер" if slot == "evening" else "контрольный"
    prefix = f"📊 KPI / AI-маркетолог · {report_date.strftime('%d.%m.%Y')} · {slot_label}"
    truth = "Только подтверждённый факт; queued/prepared не считаются выполнением."
    single_header = prefix + "\n" + truth + "\n\n"
    limit = 18000
    if len(single_header + plain) <= limit:
        contents = [plain]
    else:
        # Reserve the largest possible part numbering before splitting. Every
        # actual MessageCreate body, including its envelope, stays below limit.
        largest_number = len(plain)
        reserve = len(
            f"Часть {largest_number} из {largest_number}\n" + prefix + "\n" + truth + "\n\n"
            + f"\n\nКонец части {largest_number} из {largest_number}."
        )
        capacity = limit - reserve
        if capacity <= 0:
            raise ValueError("owner_report_envelope_too_large")
        contents = []
        start = 0
        while start < len(plain):
            end = min(start + capacity, len(plain))
            if end < len(plain):
                boundary = plain.rfind("\n", start + capacity // 2, end)
                if boundary >= 0:
                    end = boundary + 1
            contents.append(plain[start:end])
            start = end
    parts = []
    total = len(contents)
    for index, content in enumerate(contents, 1):
        header = single_header if total == 1 else (
            f"Часть {index} из {total}\n" + prefix + "\n" + truth + "\n\n"
        )
        # The canonical sender strips the whole message. A footer preserves
        # every whitespace character at a multi-part content boundary.
        footer = "" if total == 1 else f"\n\nКонец части {index} из {total}."
        message = header + content + footer
        if len(message) > limit:
            raise ValueError("owner_report_part_too_large")
        parts.append({
            "part": index,
            "client_message_id": _owner_report_part_message_id(report_date, slot, conversation_id, index),
            "header": header, "content": content, "footer": footer, "body": message,
            "body_sha256": sha256(message.encode("utf-8")).hexdigest(),
        })
    return {
        "version": 1, "report_date": report_date.isoformat(), "slot": slot,
        "conversation_id": int(conversation_id), "sender_user_id": int(owner_id),
        "report_prefix": prefix, "plain_body": plain,
        "body_sha256": sha256(plain.encode("utf-8")).hexdigest(), "parts": parts,
        "frozen_at": datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
    }


def _owner_report_validate_chunk_plan(plan, report_date, slot: str, conversation_id: int, owner_id: int) -> None:
    from hashlib import sha256

    if not isinstance(plan, dict) or plan.get("version") != 1:
        raise ValueError("owner_report_frozen_plan_version")
    expected = {
        "report_date": report_date.isoformat(), "slot": slot,
        "conversation_id": int(conversation_id), "sender_user_id": int(owner_id),
    }
    if any(plan.get(key) != value for key, value in expected.items()):
        raise ValueError("owner_report_frozen_plan_identity")
    plain, parts = plan.get("plain_body"), plan.get("parts")
    if not isinstance(plain, str) or not plain or not isinstance(parts, list) or not parts:
        raise ValueError("owner_report_frozen_plan_empty")
    if plan.get("body_sha256") != sha256(plain.encode("utf-8")).hexdigest():
        raise ValueError("owner_report_frozen_body_hash")
    contents = []
    for index, part in enumerate(parts, 1):
        if not isinstance(part, dict) or part.get("part") != index:
            raise ValueError("owner_report_frozen_part_index")
        if part.get("client_message_id") != _owner_report_part_message_id(report_date, slot, conversation_id, index):
            raise ValueError("owner_report_frozen_part_identity")
        if not all(isinstance(part.get(key), str) for key in ("header", "content", "footer", "body")):
            raise ValueError("owner_report_frozen_part_type")
        message = part["body"]
        if message != part["header"] + part["content"] + part["footer"]:
            raise ValueError("owner_report_frozen_part_content")
        if not message or len(message) > 18000 or message != message.strip():
            raise ValueError("owner_report_frozen_part_length")
        if part.get("body_sha256") != sha256(message.encode("utf-8")).hexdigest():
            raise ValueError("owner_report_frozen_part_hash")
        contents.append(part["content"])
    if "".join(contents) != plain:
        raise ValueError("owner_report_frozen_body_incomplete")


def _owner_report_private_members(db, conversation_id: int, owner_id: int) -> bool:
    members = db.execute(text(
        "SELECT user_id FROM internal_conversation_members WHERE conversation_id=:c ORDER BY user_id"
    ), {"c": int(conversation_id)}).scalars().all()
    return [int(value) for value in members] == [int(owner_id)]


def _send_owner_report_to_messenger(body: str, report_date, slot: str) -> dict:
    """Resume one frozen owner report using the canonical Messenger receipts."""
    from app.api.internal_messenger import MessageCreate, send_message
    from app.models.user import User
    from app.services import messenger_idempotency
    from app.services.platform_roles import PLATFORM_OWNER_ID

    db = SessionLocal()
    conversation_id, plan_key = None, None
    delivered_ids, plan = [], None
    sent_now = 0
    try:
        owner_id = int(PLATFORM_OWNER_ID)
        conversation_id = db.execute(text(
            "SELECT c.id FROM internal_conversations c "
            "JOIN internal_conversation_members m ON m.conversation_id=c.id "
            "WHERE c.account_id=:a AND c.kind='GROUP' AND c.title=:t AND m.user_id=:u "
            "ORDER BY c.id DESC LIMIT 1"
        ), {
            "a": OWNER_REPORT_MESSENGER_ACCOUNT_ID,
            "t": OWNER_REPORT_MESSENGER_TITLE, "u": owner_id,
        }).scalar()
        owner = db.query(User).filter(User.id == owner_id).first()
        if not conversation_id or owner is None or int(owner.id) != owner_id:
            return {"status": "error", "code": "owner_messenger_channel_missing",
                    "conversation_id": conversation_id}
        conversation_id = int(conversation_id)
        if not _owner_report_private_members(db, conversation_id, owner_id):
            return {"status": "error", "code": "owner_messenger_channel_not_private",
                    "conversation_id": conversation_id}

        plan_key = (
            f"owner_report_messenger_parts:v1:{report_date.isoformat()}:{slot}:"
            f"conversation:{conversation_id}"
        )
        # Storage has no account/key uniqueness constraint. The existing
        # transaction lock prevents concurrent workers freezing different bodies.
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                   {"key": f"owner-report-freeze:{OWNER_REPORT_MESSENGER_ACCOUNT_ID}:{plan_key}"})
        slot_label = "утро" if slot == "morning" else "вечер" if slot == "evening" else "контрольный"
        prefix = f"📊 KPI / AI-маркетолог · {report_date.strftime('%d.%m.%Y')} · {slot_label}"
        # Only the old one-message envelope qualifies. Multi-part bodies begin
        # with the part label BEFORE the legacy prefix, so an old worker's
        # broader prefix lookup cannot mistake part one for the whole report.
        legacy_id = db.execute(text(
            "SELECT id FROM internal_messages "
            "WHERE conversation_id=:c AND sender_user_id=:u AND deleted_at IS NULL "
            "AND body LIKE :prefix ORDER BY id DESC LIMIT 1"
        ), {
            "c": conversation_id, "u": owner_id,
            "prefix": prefix + "\nТолько подтверждённый факт; queued/prepared не считаются выполнением.\n\n%",
        }).scalar()
        if legacy_id:
            return {"status": "ok", "conversation_id": conversation_id,
                    "message_id": int(legacy_id), "message_ids": [int(legacy_id)],
                    "parts_total": 1, "parts_delivered": 1, "already_sent": True,
                    "legacy_unchunked": True}
        stored = db.execute(text(
            "SELECT value FROM storage WHERE account_id=:a AND key=:k ORDER BY id DESC LIMIT 1 FOR UPDATE"
        ), {"a": OWNER_REPORT_MESSENGER_ACCOUNT_ID, "k": plan_key}).scalar()
        if stored is not None:
            try:
                plan = json.loads(stored) if isinstance(stored, str) else stored
                _owner_report_validate_chunk_plan(plan, report_date, slot, conversation_id, owner_id)
            except (TypeError, ValueError, KeyError, OverflowError):
                return {"status": "error", "code": "owner_messenger_frozen_plan_invalid",
                        "conversation_id": conversation_id, "frozen_plan_key": plan_key}
        else:
            plan = _owner_report_chunk_plan(body, report_date, slot, conversation_id, owner_id)
            _owner_report_validate_chunk_plan(plan, report_date, slot, conversation_id, owner_id)
            db.execute(text(
                "INSERT INTO storage(account_id,key,value) VALUES(:a,:k,:v)"
            ), {"a": OWNER_REPORT_MESSENGER_ACCOUNT_ID,
                "k": plan_key, "v": json.dumps(plan, ensure_ascii=False)})
        # The original text and every exact part become durable BEFORE the first
        # send. Retries never re-plan from the new live statistics.
        db.commit()

        for part in plan["parts"]:
            index = int(part["part"])
            if not _owner_report_private_members(db, conversation_id, owner_id):
                return {"status": "error", "code": "owner_messenger_channel_not_private",
                        "conversation_id": conversation_id, "message_id": delivered_ids[-1] if delivered_ids else None,
                        "message_ids": delivered_ids, "parts_total": len(plan["parts"]),
                        "parts_delivered": len(delivered_ids), "partial": bool(delivered_ids),
                        "failed_part": index, "frozen_plan_key": plan_key}
            request = MessageCreate(
                client_message_id=part["client_message_id"], message_type="text", body=part["body"],
            )
            # Reuse canonical payload hashing and receipt scope. No independent
            # delivery ledger or guessed "sent" marker is created here.
            _receipt, replay = messenger_idempotency.begin(db, conversation_id, owner_id, request)
            db.commit()
            if replay is None:
                result = send_message(conversation_id, request, user=owner)
                if not isinstance(result, dict) or result.get("status") != "ok":
                    return {"status": "error", "code": "owner_messenger_part_failed",
                            "conversation_id": conversation_id, "message_id": delivered_ids[-1] if delivered_ids else None,
                            "message_ids": delivered_ids, "parts_total": len(plan["parts"]),
                            "parts_delivered": len(delivered_ids), "failed_part": index,
                            "partial": bool(delivered_ids), "frozen_plan_key": plan_key}
                _receipt, replay = messenger_idempotency.begin(db, conversation_id, owner_id, request)
                db.commit()
                sent_now += 1
            message = replay.get("message") if isinstance(replay, dict) else None
            if not isinstance(replay, dict) or replay.get("status") != "ok" or not isinstance(message, dict) or not message.get("id") or message.get("conversation_id") != conversation_id or message.get("sender_user_id") != owner_id or message.get("delivery_status") != "DELIVERED":
                return {"status": "error", "code": "owner_messenger_receipt_not_complete",
                        "conversation_id": conversation_id, "message_id": delivered_ids[-1] if delivered_ids else None,
                        "message_ids": delivered_ids, "parts_total": len(plan["parts"]),
                        "parts_delivered": len(delivered_ids), "failed_part": index,
                        "partial": bool(delivered_ids), "frozen_plan_key": plan_key}
            delivered_ids.append(int(message["id"]))
        return {"status": "ok", "conversation_id": conversation_id,
                "message_id": delivered_ids[-1], "message_ids": delivered_ids,
                "parts_total": len(plan["parts"]), "parts_delivered": len(delivered_ids),
                "already_sent": sent_now == 0, "frozen_plan_key": plan_key,
                "frozen_body_sha256": plan["body_sha256"]}
    except Exception as exc:
        db.rollback()
        return {"status": "error", "code": "owner_messenger_delivery_failed",
                "error_type": type(exc).__name__, "conversation_id": conversation_id,
                "message_id": delivered_ids[-1] if delivered_ids else None, "message_ids": delivered_ids,
                "parts_total": len(plan["parts"]) if isinstance(plan, dict) and isinstance(plan.get("parts"), list) else None,
                "parts_delivered": len(delivered_ids), "partial": bool(delivered_ids),
                "frozen_plan_key": plan_key}
    finally:
        db.close()




def _owner_report_schema(db):
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.commit()


def _owner_report_watch_ids():
    """The requested watch is a minimum; it never limits paid-client discovery."""
    default = (
        "mbroker_27859,albertardashov_79016,pbi_artashes_plitka_mo_83993,"
        "7473337_04122,planetazayavki_65985,greench84_48016,"
        "evgeniy_peregorodki_12447"
    )
    raw = os.environ.get("OWNER_DAILY_REPORT_WATCH_ACCOUNT_IDS", default)
    return list(dict.fromkeys(x.strip() for x in str(raw or "").split(",") if x.strip()))


def _owner_report_entitlements(db, account_id, now):
    """Read existing gates; reporting never grants, renews or changes a service."""
    from app.services.subscription_gate import base_subscription_access
    from app.client_config.entitlements import has_entitlement
    from app.services.control_plane_adapters_ext import marketing_service_entitlement
    from app.services.client_supervisor import _paid_inbox
    from app.services.social_entitlement import normalize_posting_subscription, posting_subscription_active

    raw = db.execute(text(
        "SELECT value FROM storage WHERE account_id=:a AND key='posting_subscription' "
        "ORDER BY id DESC LIMIT 1"
    ), {"a": account_id}).scalar()
    social, _ = normalize_posting_subscription(_safe_json(raw))
    result = {
        "base": base_subscription_access(db, account_id, now=now),
        "messages": _paid_inbox(db, account_id, now),
        "marketing": marketing_service_entitlement(db, account_id, now=now),
        "mop": has_entitlement(db, account_id, "mop"),
        "rop": has_entitlement(db, account_id, "rop"),
        "ecs": has_entitlement(db, account_id, "ecs"),
        "social": {
            "state": "active" if posting_subscription_active(social, today=now.date()) else "inactive",
            "source": "storage:posting_subscription",
        },
    }
    trial_raw = db.execute(text(
        "SELECT value FROM storage WHERE account_id=:a AND key='autopilot_settings' "
        "ORDER BY id DESC LIMIT 1"
    ), {"a": account_id}).scalar()
    result["marketing_trial"] = _owner_report_trial_evidence(_safe_json(trial_raw), result["marketing"], now)
    return result


def _owner_report_trial_evidence(settings, marketing, now):
    """Expose a recorded trial/gate conflict; this is not an entitlement grant."""
    settings = settings if isinstance(settings, dict) else {}
    until = None
    try:
        until = datetime.datetime.fromisoformat(str(settings.get("full_ai_marketer_trial_until") or "").replace("Z", "+00:00"))
        if until.tzinfo is None:
            until = until.replace(tzinfo=datetime.timezone.utc)
    except (ValueError, TypeError, OverflowError):
        until = None
    recorded = bool(settings.get("full_ai_marketer") is True
                    and settings.get("trial_scope") == "ai_marketer_only" and until is not None)
    current = bool(recorded and until > now)
    return {
        "state": "recorded_trial" if recorded else "not_recorded",
        "source": "storage:autopilot_settings",
        "trial_source": settings.get("trial_source") if recorded else None,
        "trial_scope": settings.get("trial_scope") if recorded else None,
        "trial_until": until.isoformat() if until is not None else None,
        "report_watch": current,
        "runtime_entitlement_state": marketing.get("state"),
        "entitlement_conflict": bool(current and marketing.get("state") != "active"),
        "permission_granted_by_report": False,
    }


def _owner_report_right_active(right):
    return bool(isinstance(right, dict) and (
        right.get("state") == "active"
        or (right.get("state") == "known" and right.get("allowed") is True)
    ))


def _select_owner_report_registry(rows, watch_ids, priority_ids=()):
    """Pure report projection, deduplicated by the real Avito provider identity."""
    watch_ids = list(dict.fromkeys(str(x) for x in watch_ids))
    priorities = {aid: i for i, aid in enumerate(dict.fromkeys([*priority_ids, *watch_ids]))}
    watch = set(watch_ids)
    groups, excluded = {}, []
    for raw in rows:
        row = dict(raw)
        aid = str(row.get("account_id") or "").strip()
        row["account_id"] = aid
        row["account_name"] = str(row.get("account_name") or row.get("name") or aid)
        if not aid:
            excluded.append({"account_id": aid, "reason": "invalid_account_id"})
            continue
        if row.get("policy_excluded") or "reports" in (row.get("blocked_modules") or []):
            excluded.append({"account_id": aid, "reason": "reports_disabled_by_policy"})
            continue
        if aid.lower().startswith(("qa_", "qa-", "__qa", "test_", "test-", "testref_", "demo_", "sandbox_")) or str(row.get("billing_mode") or "") == "test":
            excluded.append({"account_id": aid, "reason": "test_account"})
            continue
        provider = str(row.get("avito_user_id") or "").strip()
        identity = ("internal" if row.get("is_own") else "client", provider or "account:" + aid)
        groups.setdefault(identity, []).append(row)

    clients, internal, linked = [], [], []
    for members in groups.values():
        active = any(_owner_report_right_active(right)
                     for row in members for right in (row.get("rights") or {}).values())
        recorded_trial = any((row.get("rights") or {}).get("marketing_trial", {}).get("report_watch") is True for row in members)
        selected = active or recorded_trial or any(row["account_id"] in watch or row.get("is_own") or row.get("read_error") for row in members)
        if not selected:
            excluded.extend({"account_id": row["account_id"], "reason": "no_current_paid_service_or_watch"} for row in members)
            continue
        members.sort(key=lambda row: (
            priorities.get(row["account_id"], len(priorities)),
            0 if row.get("billing_mode") == "auto" else 1,
            0 if row.get("avito_configured") else 1,
            row["account_id"],
        ))
        primary = dict(members[0])
        primary["related_accounts"] = [dict(row) for row in members[1:]]
        primary["watch_account_ids"] = [row["account_id"] for row in members if row["account_id"] in watch]
        primary["selection_reason"] = "explicit_watch" if primary["watch_account_ids"] else "active_service" if active else "recorded_trial_watch" if recorded_trial else "internal_account" if primary.get("is_own") else "entitlement_read_error"
        blocked = set(primary.get("blocked_modules") or [])
        primary["avito_refresh_allowed"] = bool(primary.get("avito_configured")) and not bool(blocked & {"actions", "background", "reports"})
        primary["avito_reporting_reason"] = (
            "available" if primary["avito_refresh_allowed"]
            else "actions_or_background_blocked" if blocked & {"actions", "background", "reports"}
            else "avito_credentials_not_configured"
        )
        for alias in primary["related_accounts"]:
            linked.append({"account_id": alias["account_id"], "canonical_account_id": primary["account_id"],
                           "reason": "same_avito_provider_account", "rights": alias.get("rights") or {}})
        (internal if primary.get("is_own") else clients).append(primary)

    order = lambda row: (priorities.get(row["account_id"], len(priorities)), row["account_name"], row["account_id"])
    clients.sort(key=order); internal.sort(key=order)
    represented = {row["account_id"] for row in [*clients, *internal, *linked]}
    missing = [aid for aid in watch_ids if aid not in represented]
    return {
        "accounts": clients, "internal_accounts": internal, "linked_accounts": linked, "excluded": excluded,
        "coverage": {
            "registry_accounts_checked": len(rows),
            "canonical_clients": len(clients),
            "internal_accounts": len(internal),
            "linked_accounts": len(linked),
            "excluded_accounts": len(excluded),
            "watched_expected": len(watch_ids),
            "watched_covered": len(watch_ids) - len(missing),
            "missing_watched_accounts": missing,
            "entitlement_read_errors": [row["account_id"] for row in rows if row.get("read_error")],
            "scope_is_dynamic": True,
            "module_e2e_verified": False,
        },
    }


def _owner_report_registry(db):
    """Read every account, then select current services plus the minimum watch."""
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    excluded = {x.strip().lower() for x in os.environ.get(
        "BORIS_POLICY_EXCLUDED_ACCOUNTS", "grant_kaluzhskaya_oblast_beton_plity_77392"
    ).split(",") if x.strip()}
    rows = db.execute(text("""
        SELECT a.account_id, COALESCE(NULLIF(a.name,''), a.account_id) AS account_name,
               a.owner_user_id, a.billing_mode, COALESCE(a.is_own,false) AS is_own,
               a.avito_user_id,
               (NULLIF(btrim(coalesce(a.avito_client_id,'')),'') IS NOT NULL
                AND NULLIF(btrim(coalesce(a.avito_client_secret,'')),'') IS NOT NULL) AS avito_configured,
               ARRAY(SELECT r.module FROM reliability_kill_switches r
                     WHERE r.account_id=a.account_id AND r.blocked=true) AS blocked_modules
          FROM accounts a ORDER BY a.account_id
    """)).mappings().all()
    evidence = []
    for raw in rows:
        row = dict(raw)
        aid = str(row.get("account_id") or "")
        row["policy_excluded"] = aid.lower() in excluded
        try:
            with db.begin_nested():
                row["rights"] = _owner_report_entitlements(db, aid, now)
        except Exception as exc:
            row["rights"] = {}
            row["read_error"] = type(exc).__name__
        evidence.append(row)
    priority = [x.strip() for x in os.environ.get("OWNER_DAILY_REPORT_ACCOUNT_IDS", "").split(",") if x.strip()]
    registry = _select_owner_report_registry(evidence, _owner_report_watch_ids(), priority)
    registry["checked_at"] = now.isoformat()
    registry["coverage"]["checked_at"] = now.isoformat()
    return registry


def _active_owner_accounts(db):
    """Avito-capable daily KPI scope; the full module registry is reported separately.

    The existing refresh scheduler may only call Avito for configured/unblocked
    accounts. Module-only or suspended clients remain visible in the registry.
    """
    return [row for row in _owner_report_registry(db)["accounts"] if row["avito_refresh_allowed"]]


def _safe_json(raw, fallback=None):
    if raw is None:
        return {} if fallback is None else fallback
    try:
        out = json.loads(raw) if isinstance(raw, str) else raw
        return out if out is not None else ({} if fallback is None else fallback)
    except Exception:
        return {} if fallback is None else fallback


def _day_bounds(report_date):
    start = datetime.datetime.combine(report_date, datetime.time.min, tzinfo=MSK)
    end = start + datetime.timedelta(days=1)
    return start, end, int(start.timestamp()), int(end.timestamp())


def _owner_report_timestamp(value):
    if value is None:
        return None
    try:
        parsed = value if isinstance(value, datetime.datetime) else datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=datetime.timezone.utc) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError, OverflowError):
        return None


def _owner_report_time(value):
    parsed = _owner_report_timestamp(value)
    return parsed.astimezone(MSK).strftime("%d.%m.%Y %H:%M МСК") if parsed else "время не подтверждено"


def _daily_avito_snapshot(db, account_id: str, report_date):
    raw = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key=:k ORDER BY id DESC LIMIT 1"),
                     {"a": account_id, "k": f"daily_stats:{report_date.isoformat()}"}).scalar()
    data = _safe_json(raw)
    if not isinstance(data, dict):
        data = {}
    spending = data.get("spending") if isinstance(data.get("spending"), dict) else {}

    def number(value):
        import math
        try:
            parsed = float(value) if value is not None else None
            return parsed if parsed is not None and math.isfinite(parsed) else None
        except (TypeError, ValueError, OverflowError):
            return None

    # The storage key/date identifies our cache, not the provider's statistics
    # day. In particular an inventory with zero statistics batches does not
    # establish zero contacts or views.
    stats_date = str(data.get("stats_date") or "").strip() or None
    spending_date = str(spending.get("date") or "").strip() or None
    completeness = data.get("completeness") if isinstance(data.get("completeness"), dict) else {}
    items = data.get("items") if isinstance(data.get("items"), list) else []
    result = {
        "status": "no_data" if not data else "not_proven",
        "views": None, "contacts": None, "view_to_contact_pct": None,
        "active_items": None, "balance_rub": None,
        "stats_date": stats_date, "stats_date_proven": False,
        "cached_report_date": data.get("date"), "collected_at": data.get("collected_at"),
        "stats_batches_total": completeness.get("stats_batches_total"),
        "spending_status": str(spending.get("status") or "no_data"),
        "ad_spend_rub": number(spending.get("ad_spend_rub")),
        "all_spend_rub": number(spending.get("all_spend_rub")),
        "spending_date": spending_date, "spending_confirmed_at": spending.get("confirmed_at"),
        "spending_breakdown": spending.get("breakdown") if isinstance(spending.get("breakdown"), dict) else {},
        "stats_unavailable_reason": None,
    }
    if not data:
        result["stats_unavailable_reason"] = "daily_cache_missing"
        return result
    if not stats_date:
        result["stats_unavailable_reason"] = "provider_stats_date_missing"
        return result
    if stats_date != report_date.isoformat():
        result.update(status="stale", stats_unavailable_reason="provider_returned_other_day")
        return result
    if completeness.get("complete") is False or completeness.get("stats_batches_total") == 0:
        result["stats_unavailable_reason"] = "provider_statistics_incomplete"
        return result
    result["stats_date_proven"] = True
    valid = [item for item in items if isinstance(item, dict)]
    for field in ("views", "contacts"):
        values = [number(item.get(field)) for item in valid]
        if len(valid) == len(items) and all(value is not None and value >= 0 and value.is_integer() for value in values):
            result[field] = sum(int(value) for value in values)
    if result["views"] is None or result["contacts"] is None:
        result["stats_unavailable_reason"] = "provider_counters_incomplete"
        return result
    views, contacts = result["views"], result["contacts"]
    balance = data.get("balance") if isinstance(data.get("balance"), dict) else {}
    result.update(
        status="ok",
        view_to_contact_pct=round(contacts * 100.0 / views, 2) if views else (0.0 if contacts == 0 else None),
        active_items=sum(1 for item in valid if str(item.get("status") or "").lower() == "active"),
        balance_rub=number(balance.get("real")),
    )
    return result



def _daily_message_funnel(db, account_id: str, report_date):
    start, end, start_ts, end_ts = _day_bounds(report_date)
    p = {"a": account_id, "s": start_ts, "e": end_ts}
    incoming_messages = int(db.execute(text("""
        SELECT count(*) FROM messenger_messages
         WHERE account_id=:a AND avito_created_at>=:s AND avito_created_at<:e
           AND lower(coalesce(direction,'')) LIKE 'in%%' AND coalesce(msg_type,'')<>'system'
    """), p).scalar() or 0)
    dialogs = int(db.execute(text("""
        SELECT count(DISTINCT avito_chat_id) FROM messenger_messages
         WHERE account_id=:a AND avito_created_at>=:s AND avito_created_at<:e
           AND lower(coalesce(direction,'')) LIKE 'in%%' AND coalesce(msg_type,'')<>'system'
    """), p).scalar() or 0)
    replied = int(db.execute(text("""
        WITH first_in AS (
          SELECT avito_chat_id, min(avito_created_at) first_in
            FROM messenger_messages
           WHERE account_id=:a AND avito_created_at>=:s AND avito_created_at<:e
             AND lower(coalesce(direction,'')) LIKE 'in%%' AND coalesce(msg_type,'')<>'system'
           GROUP BY avito_chat_id
        )
        SELECT count(*) FROM first_in f
         WHERE EXISTS (SELECT 1 FROM messenger_messages o
                        WHERE o.account_id=:a AND o.avito_chat_id=f.avito_chat_id
                          AND lower(coalesce(o.direction,'')) LIKE 'out%%'
                          AND o.avito_created_at>=f.first_in
                          AND o.avito_created_at<:e)
    """), p).scalar() or 0)
    # `messenger_leads.stage` is mutable current state and BORIS has no complete
    # historical stage ledger for old dates. Never let today's stage retroactively
    # improve a 7/30-day report. Same-day snapshots may use the live stage; older
    # reconstruction marks this metric unavailable instead of inventing history.
    _today_msk = datetime.datetime.now(tz=MSK).date()
    advanced = None
    if report_date >= _today_msk:
        advanced = int(db.execute(text("""
            WITH day_chats AS (
              SELECT DISTINCT avito_chat_id FROM messenger_messages
               WHERE account_id=:a AND avito_created_at>=:s AND avito_created_at<:e
                 AND lower(coalesce(direction,'')) LIKE 'in%%' AND coalesce(msg_type,'')<>'system'
            )
            SELECT count(*) FROM day_chats d
             WHERE EXISTS (
               SELECT 1 FROM messenger_leads l
                WHERE l.account_id=:a AND l.avito_chat_id=d.avito_chat_id
                  AND lower(coalesce(l.stage,'')) NOT IN ('new','inquiry','')
             )
        """), p).scalar() or 0)
    # Сделки из звонков считаются отдельно в calls_crm. Не смешиваем их с
    # CRM-сделками из переписок: при первичном импорте calltracking старые
    # звонки могут создать карточки сегодня и искусственно раздуть «новых сделок».
    # Count only CRM deals tied to chats that had a real inbound message on
    # the report date. created_at alone is unsafe because historical Avito
    # dialogs can be bulk-imported today and look like dozens of "new deals".
    deals = int(db.execute(text("""
        WITH day_chats AS (
          SELECT DISTINCT avito_chat_id FROM messenger_messages
           WHERE account_id=:a AND avito_created_at>=:s AND avito_created_at<:e
             AND lower(coalesce(direction,'')) LIKE 'in%%' AND coalesce(msg_type,'')<>'system'
        )
        SELECT count(DISTINCT d.avito_chat_id) FROM boris_crm_deals d
         WHERE d.avito_account_id=:a AND coalesce(d.source,'') <> 'avito_calltracking'
           AND d.avito_chat_id IN (SELECT avito_chat_id FROM day_chats)
           AND d.created_at<:deal_end
    """), {"a": account_id, "s": start_ts, "e": end_ts, "deal_end": end}).scalar() or 0)
    won = int(db.execute(text("""
        WITH day_chats AS (
          SELECT DISTINCT avito_chat_id FROM messenger_messages
           WHERE account_id=:a AND avito_created_at>=:s AND avito_created_at<:e
             AND lower(coalesce(direction,'')) LIKE 'in%%' AND coalesce(msg_type,'')<>'system'
        )
        SELECT count(DISTINCT d.avito_chat_id) FROM boris_crm_deals d
         WHERE d.avito_account_id=:a AND coalesce(d.source,'') <> 'avito_calltracking'
           AND d.avito_chat_id IN (SELECT avito_chat_id FROM day_chats)
           AND lower(coalesce(d.status,'')) IN ('won','success','closed_won')
           AND d.updated_at<:deal_end
    """), {"a": account_id, "s": start_ts, "e": end_ts, "deal_end": end}).scalar() or 0)
    reply_pct = round(replied * 100.0 / dialogs, 1) if dialogs else None
    advance_pct = round(advanced * 100.0 / dialogs, 1) if dialogs and advanced is not None else None
    # Operational score only, deterministic and explainable; no fabricated AI quality grade.
    mop_score = None
    if dialogs and advanced is not None:
        mop_score = round(min(100.0, (reply_pct or 0) * 0.7 + min(100.0, (advance_pct or 0)) * 0.3))
    return {"dialogs": dialogs, "incoming_messages": incoming_messages, "replied_dialogs": replied,
            "reply_rate_pct": reply_pct, "advanced": advanced, "advance_rate_pct": advance_pct,
            # Compatibility key `deals_created` is retained for API consumers,
            # but the factual metric is chats from this day that have a CRM deal.
            # We deliberately do not use deal.created_at because historical CRM
            # imports can create a row today for an older customer conversation.
            "crm_chats_with_deal": deals, "deals_created": deals,
            "won_deals": won, "mop_operational_score": mop_score}


def _qualification_snapshot(db, account_id: str, report_date=None):
    report_date = report_date or (datetime.datetime.now(tz=MSK) - datetime.timedelta(days=1)).date()
    start, end, start_ts, end_ts = _day_bounds(report_date)
    # Qualification belongs only to chats with a real inbound customer message
    # during this business day. Training/reactivation fixtures are excluded by
    # construction because they are not messenger day chats.
    day_chats = set(db.execute(text("""
        SELECT DISTINCT avito_chat_id FROM messenger_messages
         WHERE account_id=:a AND avito_created_at>=:s AND avito_created_at<:e
           AND lower(coalesce(direction,'')) LIKE 'in%%' AND coalesce(msg_type,'')<>'system'
           AND avito_chat_id IS NOT NULL
    """), {"a": account_id, "s": start_ts, "e": end_ts}).scalars().all())
    temps = {"hot": 0, "warm": 0, "cold": 0, "unknown": 0}
    stages = {"INQUIRY": 0, "ENGAGED": 0, "QUALIFIED": 0, "TARGET_ACTION": 0, "UNKNOWN": 0}
    covered = 0
    today_msk = datetime.datetime.now(tz=MSK).date()
    for chat_id in day_chats:
        # mop_drafts.ai_summary is timestamped historical evidence. Use the last
        # structured qualification generated INSIDE the report day so later
        # conversation progress cannot rewrite yesterday's funnel.
        raw = db.execute(text("""
            SELECT ai_summary FROM mop_drafts
             WHERE account_id=:a AND avito_chat_id=:c
               AND created_at>=:ds AND created_at<:de
               AND NULLIF(BTRIM(ai_summary),'') IS NOT NULL
             ORDER BY created_at DESC,id DESC LIMIT 1
        """), {"a": account_id, "c": chat_id, "ds": start, "de": end}).scalar()
        # Same-day legacy fallback only. Storage has no timestamp, therefore it
        # is forbidden for historical reconstruction.
        if raw is None and report_date == today_msk:
            raw = db.execute(text("""
                SELECT value FROM storage
                 WHERE account_id=:a AND key=:k
                 ORDER BY id DESC LIMIT 1
            """), {"a": account_id, "k": f"mop_qualification:{chat_id}"}).scalar()
        if raw is None:
            continue
        q = _safe_json(raw)
        if not isinstance(q, dict) or not q:
            continue
        covered += 1
        t = str(q.get("lead_temperature") or "unknown").lower()
        temps[t if t in temps else "unknown"] += 1
        st = str(q.get("qualification_stage") or "UNKNOWN").upper()
        stages[st if st in stages else "UNKNOWN"] += 1
    return {"status": "ok", "coverage": covered, "day_dialogs": len(day_chats),
            "unclassified": max(len(day_chats) - covered, 0),
            "temperatures": temps, "stages": stages,
            "source": "mop_drafts.ai_summary_day_bounded"}


def _call_crm_snapshot(db, account_id: str, report_date):
    start, end, _, _ = _day_bounds(report_date)
    calls = db.execute(text("""
        SELECT count(*) total,
               count(*) FILTER (WHERE COALESCE((metadata_json->>'talk_duration')::int,0)>0) answered,
               count(*) FILTER (WHERE COALESCE((metadata_json->>'talk_duration')::int,0)=0 OR COALESCE((metadata_json->>'missed')::boolean,false)) missed,
               count(DISTINCT contact_id) FILTER (WHERE contact_id IS NOT NULL) contacts,
               count(DISTINCT deal_id) FILTER (WHERE deal_id IS NOT NULL) deals
          FROM boris_crm_activities
         WHERE channel='avito_calltracking' AND source='avito_calltracking'
           AND metadata_json->>'account_id'=:a AND created_at>=:ds AND created_at<:de
    """), {"a": account_id, "ds": start, "de": end}).mappings().one()
    call_contacts = int(calls['contacts'] or 0)
    cards = int(calls['deals'] or 0)
    return {"calls_total": int(calls['total'] or 0), "calls_answered": int(calls['answered'] or 0),
            "calls_missed": int(calls['missed'] or 0), "call_contacts": call_contacts,
            "crm_cards_from_calls": cards}


def _rop_quality_snapshot(db, account_id: str, report_date):
    """Persisted ROP quality for the interactions that actually happened this MSK day.

    No AI calls happen here. Historical reports join existing call/chat analyses back
    to factual day interactions, so a batch analysis performed later cannot migrate
    an old conversation into the wrong report date.
    """
    start, end, start_ts, end_ts = _day_bounds(report_date)
    call_rows = db.execute(text("""
        WITH day_calls AS (
          SELECT DISTINCT (metadata_json->>'call_id')::bigint call_id
            FROM boris_crm_activities
           WHERE source='avito_calltracking' AND channel='avito_calltracking'
             AND metadata_json->>'account_id'=:a AND created_at>=:ds AND created_at<:de
             AND NULLIF(metadata_json->>'call_id','') IS NOT NULL
        )
        SELECT d.call_id, ca.analysis
          FROM day_calls d
          LEFT JOIN call_analysis ca ON ca.account_id=:a AND ca.call_id=d.call_id
    """), {"a":account_id,"ds":start,"de":end}).mappings().all()
    chat_rows = db.execute(text("""
        WITH day_chats AS (
          SELECT DISTINCT avito_chat_id chat_id
            FROM messenger_messages
           WHERE account_id=:a AND avito_created_at>=:s AND avito_created_at<:e
             AND lower(coalesce(direction,'')) LIKE 'in%%' AND coalesce(msg_type,'')<>'system'
             AND avito_chat_id IS NOT NULL
        )
        SELECT d.chat_id, ca.analysis
          FROM day_chats d
          LEFT JOIN chat_analysis ca ON ca.account_id=:a AND ca.chat_id=d.chat_id
    """), {"a":account_id,"s":start_ts,"e":end_ts}).mappings().all()

    scored=[]; recommendations=[]; call_analyzed=0; chat_analyzed=0
    for kind, rows in (("call",call_rows),("chat",chat_rows)):
        for row in rows:
            a=row.get('analysis') if isinstance(row.get('analysis'),dict) else _safe_json(row.get('analysis'))
            if not a: continue
            if kind=='call': call_analyzed += 1
            else: chat_analyzed += 1
            try: score=float(a.get('score')) if a.get('score') is not None else None
            except Exception: score=None
            if score is not None: scored.append(score)
            rec=str(a.get('recommendation') or '').strip()
            if rec and rec not in recommendations: recommendations.append(rec[:700])
    low=sum(1 for x in scored if x < 60)
    return {"status":"ok","calls_total":len(call_rows),"calls_analyzed":call_analyzed,
            "chats_total":len(chat_rows),"chats_analyzed":chat_analyzed,
            "avg_score":round(sum(scored)/len(scored),1) if scored else None,
            "scored":len(scored),"attention_low_score":low,
            "recommendations":recommendations[:5]}


def _kpi_snapshot(db, account_id: str, report_date, avito: dict, messages: dict | None = None, calls: dict | None = None):
    yesterday = (datetime.datetime.now(tz=MSK) - datetime.timedelta(days=1)).date()
    raw = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key='kpi_settings' ORDER BY id DESC LIMIT 1"), {"a": account_id}).scalar()
    cfg = _safe_json(raw) if report_date >= yesterday else {}
    target = cfg.get("target_leads_per_day") or cfg.get("daily_leads_goal") or cfg.get("leads_per_day")
    max_cpl = cfg.get("max_cost_per_lead_rub") or cfg.get("max_cpl")
    try: target = float(target) if target is not None else None
    except Exception: target = None
    try: max_cpl = float(max_cpl) if max_cpl is not None else None
    except Exception: max_cpl = None
    # Lead fact: fresh Avito contacts are authoritative. When Avito's daily
    # statistics are stale/missing but BORIS has undeniable same-day inbound
    # evidence, use a conservative verified floor instead of reporting
    # "нет данных". We take MAX, not SUM, to avoid double-counting one person
    # who both wrote and called.
    messages = messages or {}
    calls = calls or {}
    avito_contacts = avito.get("contacts")
    dialogs = int(messages.get("dialogs") or 0)
    call_contacts = int(calls.get("call_contacts") or calls.get("crm_cards_from_calls") or 0)
    operational_floor = max(dialogs, call_contacts)
    if avito.get("status") == "ok" and avito_contacts is not None:
        actual = int(avito_contacts)
        actual_source = "avito_contacts"
        actual_complete = True
    elif operational_floor > 0:
        actual = operational_floor
        actual_source = "verified_inbound_floor"
        actual_complete = False
    else:
        actual = None
        actual_source = "unavailable"
        actual_complete = False
    completion = round(float(actual) * 100.0 / target, 1) if target and actual is not None else None
    # Fact CPL comes from Avito's dedicated spendings API, never from a
    # wallet balance delta. For the owner's management report we use ALL
    # platform spendings returned for the day: promotion, placement/target
    # actions, tariff/tools and other Avito charges. This is the full economic
    # cost of acquiring leads on the platform, not only bid/promotion spend.
    actual_cpl = None
    actual_spend = None
    spend_source = "unavailable"
    if (avito.get("spending_status") == "ok"
            and avito.get("spending_date") == report_date.isoformat()
            and avito.get("all_spend_rub") is not None):
        try:
            actual_spend = float(avito.get("all_spend_rub"))
            spend_source = "avito_spendings_v2_all"
            if actual and actual_complete:
                actual_cpl = round(actual_spend / float(actual), 2)
        except Exception:
            actual_spend = None
            actual_cpl = None
            spend_source = "unavailable"
    import math
    try:
        budget_raw = cfg.get("daily_budget_limit_rub")
        budget = float(budget_raw) if budget_raw is not None else None
        if budget is not None and not math.isfinite(budget):
            budget = None
    except (TypeError, ValueError, OverflowError):
        budget = None
    return {"target_leads": target, "actual_leads": actual, "completion_pct": completion,
            "daily_budget_limit_rub": budget,
            "target_cpl": max_cpl, "actual_cpl": actual_cpl,
            "actual_spend_rub": actual_spend, "actual_spend_source": spend_source,
            "actual_leads_source": actual_source, "actual_leads_complete": actual_complete}


def _owner_report_marketing_access(kpi: dict, rights: dict | None = None) -> dict:
    rights = rights or {}
    marketing = rights.get("marketing") or {}
    trial = rights.get("marketing_trial") or {}
    active = _owner_report_right_active(marketing)
    if trial.get("entitlement_conflict"):
        status = "trial_conflict"
        label = (
            "Текущий доступ маркетинга: тест записан до "
            + _owner_report_time(trial.get("trial_until"))
            + ", но исполнитель видит завершённый или неизвестный период; расхождение не устранено"
        )
    elif str(marketing.get("state") or "") == "expired":
        status = "expired"
        label = "Текущий доступ маркетинга: срок услуги завершён"
    elif active:
        status = "active"
        label = "Текущий доступ маркетинга: пакет действует"
    else:
        status = "not_proven"
        label = "Текущий доступ маркетинга: право на услугу не подтверждено"
    budget = kpi.get("daily_budget_limit_rub")
    if budget is None:
        label += "; дневной рекламный лимит не подтверждён"
    elif float(budget) <= 0:
        label += "; дневной рекламный лимит 0 ₽, разрешение на платные изменения не подтверждено"
    else:
        label += f"; дневной рекламный лимит {_fmt_num(budget, ' ₽')}"
    return {
        "state": status, "text": label,
        "may_consider_paid_change": bool(active and not trial.get("entitlement_conflict") and budget is not None and float(budget) > 0),
    }


def _account_recommendations(kpi: dict, avito: dict, messages: dict, qual: dict, calls: dict, rights: dict | None = None):
    """Recommendations only. Existing rights/limits are read, never changed."""
    actions = []
    access = _owner_report_marketing_access(kpi, rights)
    if access["state"] == "trial_conflict":
        actions.append("Согласовать учёт уже назначенного маркетингового теста с проверкой исполнителя; до устранения расхождения платное масштабирование не рекомендовано.")
    elif access["state"] == "expired":
        actions.append("Сначала подтвердить действующее право на маркетинговую услугу: по текущей проверке срок завершён.")
    elif access["state"] != "active":
        actions.append("Подтвердить право на маркетинговую услугу до рассмотрения платных изменений.")
    budget = kpi.get("daily_budget_limit_rub")
    if budget is None or float(budget) <= 0:
        actions.append("Для рассмотрения платных изменений требуется подтверждённый положительный рекламный лимит; текущий лимит отсутствует или равен нулю.")

    target, actual = kpi.get("target_leads"), kpi.get("actual_leads")
    completion = kpi.get("completion_pct")
    complete = kpi.get("actual_leads_complete") is not False
    target_cpl, actual_cpl = kpi.get("target_cpl"), kpi.get("actual_cpl")
    views, contacts, conv = avito.get("views"), avito.get("contacts"), avito.get("view_to_contact_pct")
    if target is None:
        actions.append("Задать персональный план лидов в день; без заданной цели выполнение плана не оценивается.")
    elif actual is None or not complete:
        actions.append("Подтвердить полный факт лидов за отчётную дату; сохранённый минимум входящих не определяет окончательное выполнение плана.")
    elif completion is not None and completion < 100:
        gap = max(int(round(float(target) - float(actual))), 0)
        actions.append(f"Отставание от дневного плана: {gap}; цель {target:g} {_lead_word(target)} в день. Разобрать причину отставания.")
        if views is not None and views < 30:
            actions.append("Проверить активность объявлений, доступный охват, географию и состав опубликованных объявлений.")
        elif conv is not None and conv < 2:
            actions.append(f"Проверить причины конверсии {conv:g}%: первое фото, заголовок, предложение, цену и качество трафика.")
        elif contacts and access["may_consider_paid_change"] and avito.get("status") == "ok":
            actions.append("Рассмотреть усиление результативных объявлений после проверки текущих мандата, баланса, лимитов и периода наблюдения; это рекомендация, не выполненное действие.")
    elif completion is not None and completion >= 100:
        actions.append("План по лидам за отчётный день выполнен по доступным данным; отдельно проверить качество лидов и допустимость дальнейших действий.")

    if target_cpl is not None and actual_cpl is not None and actual_cpl > target_cpl:
        actions.append(f"CPL по всем списаниям выше цели: {actual_cpl:g} ₽ против {target_cpl:g} ₽. Разобрать состав расходов и конверсию; факт перераспределения бюджета здесь не установлен.")
    elif target_cpl is not None and actual_cpl is not None and actual_cpl <= target_cpl and completion is not None and completion < 100:
        actions.append(f"CPL по всем списаниям {actual_cpl:g} ₽ в пределах цели {target_cpl:g} ₽; возможность роста проверить отдельно по действующим ограничениям.")

    dialogs, replied = int(messages.get("dialogs") or 0), int(messages.get("replied_dialogs") or 0)
    advanced = int(messages.get("advanced") or 0)
    if dialogs and replied < dialogs:
        actions.append(f"Проверить {dialogs-replied} диалогов без подтверждённого ответа и доступность ответственного сотрудника или МОПа.")
    if dialogs and advanced < dialogs:
        actions.append(f"Проверить следующий шаг по {dialogs-advanced} диалогам; выполнение квалификации не следует из этой рекомендации.")
    unclassified = int(qual.get("unclassified") or 0)
    if unclassified:
        actions.append(f"Квалифицировать {unclassified} входящих диалогов, для которых классификация не записана.")
    missed = int(calls.get("calls_missed") or 0)
    if missed:
        actions.append(f"Проверить обратный звонок и CRM-задачу по {missed} пропущенным звонкам.")
    if avito.get("status") != "ok":
        actions.insert(0, "Получить подтверждённую статистику Avito именно за отчётную дату; отсутствие статистики не равно нулю просмотров или контактов.")
    return actions[:7]



def _marketer_actions_snapshot(db, account_id: str, report_date):
    """Read confirmed bid effects and separate observations for this MSK day."""
    from decimal import Decimal, InvalidOperation
    start, end, _, _ = _day_bounds(report_date)
    receipts = db.execute(text("""
        SELECT id,account_id,request_key,item_id,action,source,status,
               old_bid_penny,intended_bid_penny,observed_bid_penny,provider_http_status,finished_at
        FROM cpx_execution_receipts
        WHERE account_id=:a AND finished_at>=:s AND finished_at<:e
        ORDER BY finished_at ASC,id ASC
    """), {"a": account_id, "s": start, "e": end}).mappings().all()
    actions = []
    for raw in receipts:
        row = dict(raw)
        finished = _owner_report_timestamp(row.get("finished_at"))
        try:
            old = Decimal(str(row.get("old_bid_penny")))
            intended = Decimal(str(row.get("intended_bid_penny")))
            observed = Decimal(str(row.get("observed_bid_penny")))
            values_valid = all(value.is_finite() and value == value.to_integral_value() for value in (old, intended, observed))
        except (InvalidOperation, ValueError, TypeError):
            values_valid = False
        if (
            not values_valid or str(row.get("status")) != "succeeded"
            or row.get("provider_http_status") != 200
            or intended != observed or old == observed
            or str(row.get("account_id")) != str(account_id)
            or finished is None or not (start <= finished < end)
        ):
            continue
        actions.append({
            "receipt_id": int(row["id"]), "request_key": row.get("request_key"),
            "ts": finished.isoformat(), "finished_at": finished.isoformat(), "source": row.get("source"),
            "action": "Повысил ставку" if observed > old else "Снизил ставку",
            "object_kind": "объявление", "object_name": str(row.get("item_id")),
            "before_val": f"{old / 100:g} ₽", "after_val": f"{observed / 100:g} ₽",
            "provider_http_status": 200, "status": "succeeded", "readback_matches": True,
        })
    log_rows = db.execute(text("""
        SELECT id,ts,source,action,object_kind,object_name,before_val,after_val
        FROM action_log
        WHERE account_id=:a AND ts>=:s AND ts<:e
          AND source='cpx_daily_learning'
        ORDER BY ts ASC,id ASC
    """), {
        "a": account_id,
        "s": start.astimezone(datetime.timezone.utc).replace(tzinfo=None),
        "e": end.astimezone(datetime.timezone.utc).replace(tzinfo=None),
    }).mappings().all()
    measurements = []
    for raw in log_rows:
        row = dict(raw)
        # action_log.ts is stored as UTC without a time-zone column.
        timestamp = _owner_report_timestamp(row.get("ts"))
        after = _safe_json(row.get("after_val"))
        measurements.append({
            "event_id": row.get("id"), "ts": timestamp.isoformat() if timestamp else None,
            "source": row.get("source"), "object_name": row.get("object_name"),
            "result": after.get("эффект") if isinstance(after, dict) else None,
            "timestamp_source": "action_log.ts_utc",
        })
    return {
        "count": len(actions), "bid_changes": len(actions),
        "unique_bid_items": len({action["object_name"] for action in actions}),
        "actions": actions, "measurement_count": len(measurements), "measurements": measurements,
        "receipt_source": "cpx_execution_receipts",
        "confirmation": "succeeded + HTTP 200 + observed bid equals intended and differs from old bid",
        "business_date": report_date.isoformat(), "timezone": "Europe/Moscow",
    }



def build_owner_account_snapshot(db, account_id: str, account_name: str, owner_user_id, report_date=None, rights: dict | None = None):
    report_date = report_date or (datetime.datetime.now(tz=MSK) - datetime.timedelta(days=1)).date()
    avito = _daily_avito_snapshot(db, account_id, report_date)
    messages = _daily_message_funnel(db, account_id, report_date)
    qual = _qualification_snapshot(db, account_id, report_date)
    # Funnel progression comes from the timestamped MOP qualification generated
    # for this exact business day, not from the mutable current messenger_leads
    # row. This makes 7/30-day reconstruction stable and non-retroactive.
    _stages = qual.get("stages") or {}
    _advanced = sum(int(_stages.get(k) or 0) for k in ("ENGAGED", "QUALIFIED", "TARGET_ACTION"))
    _dialogs = int(messages.get("dialogs") or 0)
    _qual_coverage = int(qual.get("coverage") or 0)
    _advanced_complete = (_qual_coverage == _dialogs)
    messages["advanced"] = min(_advanced, _dialogs)
    messages["advanced_complete"] = _advanced_complete
    messages["advanced_source"] = "mop_drafts.ai_summary_day_bounded"
    messages["advance_rate_pct"] = round(messages["advanced"] * 100.0 / _dialogs, 1) if _dialogs and _advanced_complete else None
    messages["mop_operational_score"] = (round(min(100.0, float(messages.get("reply_rate_pct") or 0) * 0.7 + min(100.0, float(messages.get("advance_rate_pct") or 0)) * 0.3))
                                         if _dialogs and _advanced_complete else None)
    calls = _call_crm_snapshot(db, account_id, report_date)
    rop_quality = _rop_quality_snapshot(db, account_id, report_date)
    kpi = _kpi_snapshot(db, account_id, report_date, avito, messages=messages, calls=calls)
    recommendations = _account_recommendations(kpi, avito, messages, qual, calls, rights=rights)
    marketer = _marketer_actions_snapshot(db, account_id, report_date)
    return {"report_date": report_date.isoformat(), "account_id": account_id, "account_name": account_name,
            "owner_user_id": owner_user_id, "kpi": kpi, "avito": avito, "messages": messages,
            "qualification": qual, "calls_crm": calls, "rop_quality": rop_quality, "recommendations": recommendations,
            "ai_marketer": marketer, "current_service_rights": rights or {}, "report_contract_version": 2,
            "real_data_only": True, "generated_at": datetime.datetime.now(tz=MSK).isoformat()}


def save_owner_daily_snapshots(report_date=None):
    """Immutable-by-day UPSERT: repeated same-day runs repair the same snapshot, never duplicate it."""
    report_date = report_date or (datetime.datetime.now(tz=MSK) - datetime.timedelta(days=1)).date()
    db = SessionLocal()
    saved, errors = [], []
    try:
        _owner_report_schema(db)
        for row in _active_owner_accounts(db):
            try:
                payload = build_owner_account_snapshot(db, row['account_id'], row['account_name'], row['owner_user_id'], report_date, rights=row.get('rights'))
                db.execute(text("""
                    INSERT INTO owner_daily_report_snapshots(report_date,account_id,account_name,owner_user_id,payload_json,created_at)
                    VALUES(:d,:a,:n,:o,CAST(:p AS jsonb),now())
                    ON CONFLICT(report_date,account_id) DO UPDATE SET
                      account_name=excluded.account_name, owner_user_id=excluded.owner_user_id,
                      payload_json=excluded.payload_json, created_at=now()
                """), {"d": report_date, "a": row['account_id'], "n": row['account_name'], "o": row['owner_user_id'],
                         "p": json.dumps(payload, ensure_ascii=False)})
                db.commit(); saved.append(row['account_id'])
            except Exception as e:
                db.rollback(); errors.append({"account_id": row['account_id'], "error": type(e).__name__})
        return {"status": "ok" if not errors else "partial", "report_date": report_date.isoformat(),
                "saved": saved, "saved_count": len(saved), "errors": errors}
    finally:
        db.close()


def _fmt_num(v, suffix=""):
    return "нет данных" if v is None else f"{v:g}{suffix}" if isinstance(v, float) else f"{v}{suffix}"


def _lead_word(n):
    n = abs(int(round(float(n or 0))))
    if n % 10 == 1 and n % 100 != 11:
        return "лид"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "лида"
    return "лидов"


def render_owner_daily_report(report_date=None):
    report_date = report_date or (datetime.datetime.now(tz=MSK) - datetime.timedelta(days=1)).date()
    db = SessionLocal()
    try:
        _owner_report_schema(db)
        scope_rows = _active_owner_accounts(db)
        current_rights = {row["account_id"]: row.get("rights") or {} for row in scope_rows}
        account_scope = [row["account_id"] for row in scope_rows]
        rows = db.execute(text(
            "SELECT payload_json FROM owner_daily_report_snapshots "
            "WHERE report_date=:d AND account_id=ANY(:ids) ORDER BY account_name"
        ), {"d": report_date, "ids": account_scope}).scalars().all()
        payloads = [r if isinstance(r, dict) else _safe_json(r) for r in rows]
        for payload in payloads:
            payload["current_service_rights"] = current_rights.get(payload.get("account_id"), {})
            avito = payload.get("avito") or {}
            kpi = payload.get("kpi") or {}
            # Old snapshots did not retain whether stats_date came from Avito
            # or our cache key. They cannot prove a zero or a complete lead fact.
            if avito.get("stats_date_proven") is not True:
                avito.update(views=None, contacts=None, view_to_contact_pct=None)
                if avito.get("status") == "ok":
                    avito.update(status="not_proven", stats_unavailable_reason="legacy_provider_date_not_proven")
                if kpi.get("actual_leads_source") == "avito_contacts":
                    kpi.update(actual_leads=None, actual_leads_complete=False, actual_leads_source="unavailable", completion_pct=None, actual_cpl=None)
    finally:
        db.close()
    if not payloads:
        return {"status": "no_data", "report_date": report_date.isoformat(), "text": "", "accounts": []}
    # Владелец просматривает KPI только по каждому аккаунту отдельно.
    # Никаких суммарных лидов/KPI/CPL между разными бизнесами в шапке.
    ok_names=[]; gap_names=[]; unknown_names=[]
    for _p in payloads:
        _k=_p.get('kpi') or {}; _name=_p.get('account_name') or _p.get('account_id') or 'аккаунт'
        _t=_k.get('target_leads'); _a=_k.get('actual_leads')
        if _t is None or _a is None or _k.get("actual_leads_complete") is False:
            unknown_names.append(_name)
        elif float(_a) >= float(_t):
            ok_names.append(_name)
        else:
            gap_names.append(_name)
    _generated = sorted(str(p.get('generated_at') or '') for p in payloads if p.get('generated_at'))
    _cut = _owner_report_time(_generated[-1]) if _generated else 'время не записано'
    lines = [f"📊 <b>BORIS · отчёты по аккаунтам · {report_date.strftime('%d.%m.%Y')}</b>",
             f"Отчёт сформирован: <b>{_cut}</b>. Даты и время источников указаны по аккаунтам.",
             f"Аккаунтов в отчёте: <b>{len(payloads)}</b>. План, факт, CPL, воронка и рекомендации ниже считаются отдельно для каждого аккаунта.",
             f"Коротко: ✅ план выполнен — {len(ok_names)} · 🔴 ниже плана — {len(gap_names)} · ⚪ план/факт не определён — {len(unknown_names)}."]
    if gap_names:
        lines.append("Ниже плана: " + "; ".join(gap_names[:10]) + ("…" if len(gap_names)>10 else ""))
    if unknown_names:
        lines.append("Нельзя честно оценить KPI: " + "; ".join(unknown_names[:10]) + ("…" if len(unknown_names)>10 else ""))
    for p in payloads:
        k=p.get('kpi') or {}; a=p.get('avito') or {}; m=p.get('messages') or {}; q=p.get('qualification') or {}; c=p.get('calls_crm') or {}; rq=p.get('rop_quality') or {}
        temps=q.get('temperatures') or {}; stages=q.get('stages') or {}
        _target=k.get('target_leads'); _actual=k.get('actual_leads')
        if _target is None:
            account_result="⚪ План лидов не задан"
        elif _actual is None:
            account_result="⚪ Факт лидов не подтверждён — сравнивать с планом нельзя"
        elif k.get("actual_leads_complete") is False:
            account_result=f"⚪ Подтверждённый минимум {_fmt_num(_actual)}; полный факт лидов не установлен, итог плана не определён"
        elif float(_actual) >= float(_target):
            account_result=f"✅ План выполнен: факт {_fmt_num(_actual)} · план {_fmt_num(_target)} {_lead_word(_target)}"
        else:
            _gap=max(int(round(float(_target)-float(_actual))),0)
            account_result=f"🔴 Ниже плана: факт {_fmt_num(_actual)} · план {_fmt_num(_target)} {_lead_word(_target)} · отставание {_gap}"
        kpi_line = f"План/факт лидов: {_fmt_num(_target)} / {_fmt_num(_actual)}"
        if k.get('completion_pct') is not None and k.get("actual_leads_complete") is not False: kpi_line += f" · выполнено {k['completion_pct']}%"
        target_cpl = k.get('target_cpl')
        actual_cpl = k.get('actual_cpl')
        actual_leads = k.get('actual_leads')
        target_cpl_text = _fmt_num(target_cpl, ' ₽') if target_cpl is not None else 'не задана'
        if actual_cpl is not None:
            actual_cpl_text = _fmt_num(actual_cpl, ' ₽')
        elif actual_leads is None or k.get("actual_leads_complete") is False:
            actual_cpl_text = 'не определён: полный факт контактов не подтверждён'
        elif actual_leads == 0:
            actual_cpl_text = '— контактов нет'
        else:
            actual_cpl_text = '— расход за день не подтверждён'
        cpl_line = f"CPL по всем списаниям Avito: цель {target_cpl_text} · факт {actual_cpl_text}"
        spend_line = "API списаний: подтверждённого ответа за отчётную дату нет"
        if a.get("spending_status") == "ok" and a.get("spending_date") == report_date.isoformat():
            spend_line = (
                f"API списаний за {report_date.strftime('%d.%m.%Y')}: рекламный расход отдельно {_fmt_num(a.get('ad_spend_rub'), ' ₽')}"
                f" · расход Avito всего {_fmt_num(a.get('all_spend_rub'), ' ₽')}"
                f" · ответ {_owner_report_time(a.get('spending_confirmed_at'))}"
            )
        if k.get("actual_leads_source") == "avito_contacts":
            kpi_line += " · источник: контакты Avito, квалификация показана отдельно"
        elif k.get("actual_leads_source") == "verified_inbound_floor":
            kpi_line += " · подтверждённый минимум входящих, полный факт Avito недоступен"
        
        conv = a.get('view_to_contact_pct')
        avito_line = f"Avito: просмотры {_fmt_num(a.get('views'))} → контакты {_fmt_num(a.get('contacts'))} · конверсия {_fmt_num(conv,'%')}"
        if a.get('status') == 'stale':
            avito_line = f"Avito: нет точных данных за день · источник вернул статистику за {a.get('stats_date') or 'другую дату'}"
        elif a.get('status') != 'ok':
            avito_line = "Avito: просмотры и контакты не подтверждены; дата статистики провайдера или полнота ответа не установлена"
        avito_line += f" · дата провайдера {a.get('stats_date') or 'не установлена'} · кэш получен {_owner_report_time(a.get('collected_at'))}"
        rights = p.get("current_service_rights") or {}
        access = _owner_report_marketing_access(k, rights)
        rop_active = _owner_report_right_active(rights.get("rop"))
        analyzed = int(rq.get('calls_analyzed') or 0) + int(rq.get('chats_analyzed') or 0)
        if not rop_active:
            rop_line = f"РОП: текущий пакет не подтверждён; сохранённых разборов взаимодействий дня {analyzed}. Подключение и работа модуля не доказаны."
        elif not int(rq.get('calls_total') or 0) and not int(rq.get('chats_total') or 0):
            rop_line = "РОП: пакет действует; за отчётный день нет сохранённых взаимодействий для разбора. Работа модуля по 0/0 не подтверждается."
        else:
            rop_line = (f"РОП: пакет действует; на момент среза разобрано звонков {rq.get('calls_analyzed',0)} из {rq.get('calls_total',0)}, "
                        f"переписок {rq.get('chats_analyzed',0)} из {rq.get('chats_total',0)} · средняя оценка {_fmt_num(rq.get('avg_score'),'/100')} "
                        f"· требуют внимания {rq.get('attention_low_score',0)}")
        q_unclassified = int(q.get('unclassified') or 0)
        lines += ["", f"<b>{p.get('account_name')}</b>",
                  account_result,
                  access["text"],
                  kpi_line, cpl_line, spend_line,
                  avito_line,
                  f"Диалоги Avito / ответы канала за день (включая ручные): диалогов {m.get('dialogs',0)} · входящих сообщений {m.get('incoming_messages',0)} · отвечено диалогов {m.get('replied_dialogs',0)} · дальше по воронке {m.get('advanced',0)} · оценка воронки {_fmt_num(m.get('mop_operational_score'),'/100')}; автор ответа здесь не классифицирован. Отправки МОПа за последние 24 часа показаны отдельно в текущей матрице модулей.",
                  f"Квалификация за день: 🔥 {temps.get('hot',0)} · 🟡 {temps.get('warm',0)} · ❄️ {temps.get('cold',0)} · не классифицировано {q_unclassified} · квалифицировано {stages.get('QUALIFIED',0)} · целевое действие {stages.get('TARGET_ACTION',0)}",
                  f"Звонки: {c.get('calls_total',0)} · отвечено {c.get('calls_answered',0)} · пропущено {c.get('calls_missed',0)} · CRM-карточек со звонков {c.get('crm_cards_from_calls',0)}",
                  f"CRM · сохранённые факты дня: диалогов со сделкой {m.get('crm_chats_with_deal',m.get('deals_created',0))} · успешных {m.get('won_deals',0)}",
                  rop_line]
        rq_recs=list(rq.get('recommendations') or [])[:3]
        if rq_recs:
            lines.append("<b>РОП рекомендует:</b>")
            lines.extend([f"• {x}" for x in rq_recs])
        mk=p.get('ai_marketer') or {}; mk_actions=mk.get('actions') or []
        if mk.get("receipt_source") == "cpx_execution_receipts":
            lines.append(
                f"ИИ-маркетолог за {report_date.strftime('%d.%m.%Y')}: подтверждённых изменений ставок {mk.get('bid_changes',0)} "
                f"· затронуто объявлений {mk.get('unique_bid_items',0)} · оценок прошлых изменений {mk.get('measurement_count',0)}"
            )
            if mk_actions:
                lines.append("<b>Изменения с успешным ответом провайдера и совпавшей проверкой ставки:</b>")
                visible = mk_actions[-20:]
                if len(mk_actions) > len(visible):
                    lines.append(f"• Полный журнал: {len(mk_actions)} подтверждённых изменений; ниже последние {len(visible)}.")
                for x in visible:
                    lines.append(
                        f"• {_owner_report_time(x.get('finished_at') or x.get('ts'))} · {x.get('action')} · объявление {x.get('object_name')} "
                        f"· {x.get('before_val')} → {x.get('after_val')} · подтверждение #{x.get('receipt_id')}"
                    )
            for x in (mk.get("measurements") or [])[-10:]:
                effect = {
                    "inconclusive_first_bid_no_signal": "недостаточно нового сигнала",
                    "worsened": "результат ухудшился", "improved": "результат улучшился",
                    "unchanged": "заметного изменения не установлено",
                }.get(str(x.get("result") or ""), "оценка записана; итог требует отдельной интерпретации")
                lines.append(f"• {_owner_report_time(x.get('ts'))} · оценка прошлого изменения по объявлению {x.get('object_name')}: {effect}; это не новая смена ставки.")
        else:
            lines.append("ИИ-маркетолог: сохранённый снимок не содержит сверки с реестром исполнения; число выполненных изменений ставок не подтверждено.")
        # Recommendations are deterministic from the stored daily facts. Rebuild
        # them at render time so old snapshots do not keep obsolete machine-like
        # phrasing after the owner-report contract is improved.
        recs = _account_recommendations(k, a, m, q, c, rights=rights)
        if recs:
            lines.append("<b>Рекомендации по данным дня — выполнение не подтверждено:</b>")
            lines.extend([f"• {r}" for r in recs])
    lines += ["", "Периоды в BORIS: 1 / 7 / 30 дней. История хранится по дням; если источник не дал данные, BORIS прямо пишет «нет данных», а не подставляет ноль."]
    return {"status": "ok", "report_date": report_date.isoformat(), "text": "\n".join(lines), "accounts": payloads,
            "totals": {"active_clients": len(payloads)}}


def owner_report_history(days: int = 30, account_id: str = ""):
    days = max(1, min(int(days or 30), 30))
    # Report business day is MSK. PostgreSQL `current_date` follows the DB/session
    # timezone (UTC in production), which made 00:00-02:59 MSK windows one day too
    # wide and could even include a newly-created "tomorrow" snapshot. Bound the
    # history explicitly in the same timezone as snapshot creation/rendering.
    end_date = datetime.datetime.now(tz=MSK).date()
    start_date = end_date - datetime.timedelta(days=days-1)
    db = SessionLocal()
    try:
        _owner_report_schema(db)
        p={"start_date": start_date, "end_date": end_date}
        extra=""
        if account_id:
            extra=" AND account_id=:a"; p['a']=account_id
        rows=db.execute(text("SELECT report_date,account_id,account_name,payload_json,created_at FROM owner_daily_report_snapshots WHERE report_date>=:start_date AND report_date<=:end_date"+extra+" ORDER BY report_date DESC,account_name"),p).mappings().all()
        return {"status":"ok","days":days,"account_id":account_id,"start_date":start_date.isoformat(),"end_date":end_date.isoformat(),"items":[{"report_date":r['report_date'].isoformat(),"account_id":r['account_id'],"account_name":r['account_name'],"payload":r['payload_json'],"created_at":r['created_at'].isoformat()} for r in rows]}
    finally:
        db.close()


@router.get("/owner_daily/history")
def owner_daily_history(days: int = 30, account_id: str = "", user=Depends(get_current_user)):
    if not is_private_platform_owner(user):
        raise HTTPException(status_code=403, detail="Только для владельца платформы")
    return owner_report_history(days=days, account_id=account_id)


@router.get("/owner_daily/preview")
def owner_daily_preview(date: str = "", user=Depends(get_current_user)):
    if not is_private_platform_owner(user):
        raise HTTPException(status_code=403, detail="Только для владельца платформы")
    try: d = datetime.date.fromisoformat(date) if date else (datetime.datetime.now(tz=MSK)-datetime.timedelta(days=1)).date()
    except Exception: raise HTTPException(status_code=400, detail="Дата в формате YYYY-MM-DD")
    return render_owner_daily_report(d)


def render_owner_period_report(days: int = 30, account_id: str = ""):
    """Aggregate only persisted daily snapshots; never reconstruct historical numbers from today's state."""
    days = max(1, min(int(days or 30), 30))
    hist = owner_report_history(days=days, account_id=account_id)
    items = hist.get("items") or []
    if not account_id and items:
        # Default owner dashboard is about currently active production clients.
        # Historical snapshots of an expired/disconnected client stay queryable by
        # explicit account_id, but must not silently reappear in the active fleet.
        _scope_db = SessionLocal()
        try:
            _active_ids = {str(r['account_id']) for r in _active_owner_accounts(_scope_db)}
        finally:
            _scope_db.close()
        items = [r for r in items if str(r.get('account_id') or '') in _active_ids]
    if not items:
        return {"status":"no_data","days":days,"account_id":account_id,"text":"За выбранный период сохранённых отчётов пока нет."}
    by_account = {}
    by_date = {}
    for row in items:
        p = row.get("payload") or {}
        aid = row.get("account_id")
        x = by_account.setdefault(aid, {"name":row.get("account_name") or aid,"days":0,"target":0.0,"target_days":0,"leads":0,"lead_days":0,
                                        "views":0,"view_days":0,"contacts":0,"contact_days":0,"dialogs":0,"advanced":0,"advanced_days":0,"advanced_required_days":0,"calls":0,"answered":0,"missed":0,"cards":0,"deals":0,"won":0,
                                        "spend":0.0,"spend_days":0,"mop_score_sum":0.0,"mop_score_days":0,"hot":0,"warm":0,"cold":0,"unclassified":0,"qualification_days":0,"qualification_required_days":0,
                                        "q_inquiry":0,"q_engaged":0,"q_qualified":0,"q_target":0,
                                        "rop_calls_total":0,"rop_calls_analyzed":0,"rop_chats_total":0,"rop_chats_analyzed":0,"rop_score_sum":0.0,"rop_score_n":0,"rop_attention":0})
        x['days'] += 1
        k=p.get('kpi') or {}; a=p.get('avito') or {}; m=p.get('messages') or {}; c=p.get('calls_crm') or {}; q=p.get('qualification') or {}; rq=p.get('rop_quality') or {}
        if k.get('target_leads') is not None: x['target'] += float(k['target_leads']); x['target_days'] += 1
        if k.get('actual_leads') is not None: x['leads'] += int(k['actual_leads']); x['lead_days'] += 1
        if a.get('views') is not None: x['views'] += int(a['views']); x['view_days'] += 1
        if a.get('contacts') is not None: x['contacts'] += int(a['contacts']); x['contact_days'] += 1
        x['dialogs'] += int(m.get('dialogs') or 0)
        if int(m.get('dialogs') or 0) > 0:
            x['advanced_required_days'] += 1
            if bool(m.get('advanced_complete')):
                x['advanced'] += int(m.get('advanced') or 0); x['advanced_days'] += 1
        x['deals'] += int(m.get('crm_chats_with_deal') if m.get('crm_chats_with_deal') is not None else (m.get('deals_created') or 0)); x['won'] += int(m.get('won_deals') or 0)
        x['calls'] += int(c.get('calls_total') or 0); x['answered'] += int(c.get('calls_answered') or 0); x['missed'] += int(c.get('calls_missed') or 0); x['cards'] += int(c.get('crm_cards_from_calls') or 0)
        if k.get('actual_spend_rub') is not None: x['spend'] += float(k.get('actual_spend_rub') or 0); x['spend_days'] += 1
        if m.get('mop_operational_score') is not None: x['mop_score_sum'] += float(m.get('mop_operational_score') or 0); x['mop_score_days'] += 1
        if q.get('status') == 'ok' and int(q.get('day_dialogs') or 0) > 0:
            x['qualification_required_days'] += 1
            if int(q.get('coverage') or 0) == int(q.get('day_dialogs') or 0):
                temps=q.get('temperatures') or {}; stages=q.get('stages') or {}
                x['hot'] += int(temps.get('hot') or 0); x['warm'] += int(temps.get('warm') or 0); x['cold'] += int(temps.get('cold') or 0); x['unclassified'] += int(q.get('unclassified') or 0); x['qualification_days'] += 1
                x['q_inquiry'] += int(stages.get('INQUIRY') or 0); x['q_engaged'] += int(stages.get('ENGAGED') or 0); x['q_qualified'] += int(stages.get('QUALIFIED') or 0); x['q_target'] += int(stages.get('TARGET_ACTION') or 0)
        x['rop_calls_total'] += int(rq.get('calls_total') or 0); x['rop_calls_analyzed'] += int(rq.get('calls_analyzed') or 0)
        x['rop_chats_total'] += int(rq.get('chats_total') or 0); x['rop_chats_analyzed'] += int(rq.get('chats_analyzed') or 0)
        if rq.get('avg_score') is not None and int(rq.get('scored') or 0) > 0:
            x['rop_score_sum'] += float(rq.get('avg_score')) * int(rq.get('scored')); x['rop_score_n'] += int(rq.get('scored'))
        x['rop_attention'] += int(rq.get('attention_low_score') or 0)
        d=by_date.setdefault(row['report_date'],{"leads":0,"lead_known_clients":0,"dialogs":0,"calls":0,"missed":0,"clients":0})
        if k.get('actual_leads') is not None:
            d['leads'] += int(k.get('actual_leads') or 0); d['lead_known_clients'] += 1
        d['dialogs'] += int(m.get('dialogs') or 0); d['calls'] += int(c.get('calls_total') or 0); d['missed'] += int(c.get('calls_missed') or 0); d['clients'] += 1
    dates=sorted(by_date)
    lines=[f"📈 <b>BORIS · отчёт за {days} дн.</b>", f"Период подтверждённых дневных данных: {dates[0]} — {dates[-1]}"]
    for x in sorted(by_account.values(), key=lambda z:z['name']):
        # A 7/30-day report must not call a metric complete merely because every
        # EXISTING snapshot has it. Missing snapshot days are missing evidence.
        target_complete = x['target_days'] == days
        leads_complete = x['lead_days'] == days
        spend_complete = x['spend_days'] == days
        views_complete = x['view_days'] == days
        contacts_complete = x['contact_days'] == days
        full_target = x['target'] if target_complete else None
        full_leads = float(x['leads']) if leads_complete else None
        completion=round(x['leads']*100.0/full_target,1) if full_target and leads_complete else None
        conv=round(x['contacts']*100.0/x['views'],2) if views_complete and contacts_complete and x['views'] else None
        period_cpl=round(x['spend']/x['leads'],2) if spend_complete and leads_complete and x['leads'] else None
        mop_score=round(x['mop_score_sum']/x['mop_score_days'],1) if x['mop_score_days'] else None
        rop_score=round(x['rop_score_sum']/x['rop_score_n'],1) if x['rop_score_n'] else None
        coverage = f"полнота данных: дни {x['days']}/{days} · лиды {x['lead_days']}/{days} · расход {x['spend_days']}/{days} · Avito {min(x['view_days'],x['contact_days'])}/{days} дн."
        lines += ["",f"<b>{x['name']}</b>",
                  f"KPI: план {_fmt_num(full_target)} → факт {_fmt_num(full_leads)} · выполнение {_fmt_num(completion,'%')}",
                  f"CPL за период: {_fmt_num(period_cpl,' ₽')} · подтверждённый расход {_fmt_num(x['spend'] if spend_complete else None,' ₽')}",
                  f"Avito: просмотры {_fmt_num(float(x['views']) if views_complete else None)} → контакты {_fmt_num(float(x['contacts']) if contacts_complete else None)} · конверсия {_fmt_num(conv,'%')}",
                  coverage,
                  f"МОП: диалогов {x['dialogs']} · продвинулись {_fmt_num(float(x['advanced']) if x['advanced_days'] else None)} · покрытие этапов {x['advanced_days']}/{x['advanced_required_days']} дней с диалогами · средняя оценка {_fmt_num(mop_score,'/100')}",
                  (f"Квалификация: 🔥 {x['hot']} · 🟡 {x['warm']} · ❄️ {x['cold']} · не классифицировано {x['unclassified']} · покрытие {x['qualification_days']}/{x['qualification_required_days']} дней с диалогами" if x['qualification_required_days'] else "Квалификация: за период не было входящих диалогов"),
                  f"Этапы: запрос {x['q_inquiry']} · в диалоге {x['q_engaged']} · квалифицирован {x['q_qualified']} · целевое действие {x['q_target']}",
                  f"Воронка: диалогов со сделкой {x['deals']} · успешных {x['won']}",
                  f"Звонки: {x['calls']} · отвечено {x['answered']} · пропущено {x['missed']} · CRM-карточек со звонков {x['cards']}",
                  f"РОП: звонков разобрано {x['rop_calls_analyzed']}/{x['rop_calls_total']} · переписок {x['rop_chats_analyzed']}/{x['rop_chats_total']} · средняя оценка {_fmt_num(rop_score,'/100')} · требуют внимания {x['rop_attention']}"]
    lines += ["", "<b>По датам</b>"]
    for d in sorted(by_date, reverse=True)[:30]:
        z=by_date[d]; weekday=('пн','вт','ср','чт','пт','сб','вс')[datetime.date.fromisoformat(str(d)).weekday()]
        leads_text = str(z['leads']) if z['lead_known_clients'] == z['clients'] else f"{z['leads']} (данные {z['lead_known_clients']}/{z['clients']} акк.)"
        lines.append(f"{d} ({weekday}): лидов {leads_text} · диалогов {z['dialogs']} · звонков {z['calls']} · пропущено {z['missed']}")
    return {"status":"ok","days":days,"account_id":account_id,"text":"\n".join(lines),"accounts":by_account,"dates":by_date}


def _telegram_chunks(text_value: str, limit: int = 3900):
    chunks=[]; cur=[]; size=0
    for line in (text_value or "").splitlines():
        add=len(line)+1
        if cur and size+add>limit:
            chunks.append("\n".join(cur)); cur=[line]; size=add
        else:
            cur.append(line); size += add
    if cur: chunks.append("\n".join(cur))
    return chunks or [""]


def _verification_signature(payload: dict):
    """Only business numbers that must be stable between verification passes."""
    k=payload.get('kpi') or {}; a=payload.get('avito') or {}; m=payload.get('messages') or {}
    q=payload.get('qualification') or {}; c=payload.get('calls_crm') or {}; rq=payload.get('rop_quality') or {}; mk=payload.get('ai_marketer') or {}
    return {
        'account_id': payload.get('account_id'),
        'target_leads': k.get('target_leads'), 'actual_leads': k.get('actual_leads'),
        'target_cpl': k.get('target_cpl'), 'actual_cpl': k.get('actual_cpl'),
        'actual_spend_rub': k.get('actual_spend_rub'), 'actual_spend_source': k.get('actual_spend_source'),
        'views': a.get('views'), 'contacts': a.get('contacts'), 'avito_status': a.get('status'), 'stats_date': a.get('stats_date'),
        'dialogs': m.get('dialogs'), 'incoming_messages': m.get('incoming_messages'), 'replied_dialogs': m.get('replied_dialogs'),
        'advanced': m.get('advanced'), 'deals_created': m.get('deals_created'), 'won_deals': m.get('won_deals'),
        'temperatures': q.get('temperatures'), 'stages': q.get('stages'), 'unclassified': q.get('unclassified'),
        'calls_total': c.get('calls_total'), 'calls_answered': c.get('calls_answered'), 'calls_missed': c.get('calls_missed'),
        'crm_cards_from_calls': c.get('crm_cards_from_calls'),
        'rop_calls_analyzed': rq.get('calls_analyzed'), 'rop_calls_total': rq.get('calls_total'),
        'rop_chats_analyzed': rq.get('chats_analyzed'), 'rop_chats_total': rq.get('chats_total'),
        'rop_avg_score': rq.get('avg_score'), 'rop_attention': rq.get('attention_low_score'),
        'marketer_actions': mk.get('count'), 'marketer_bid_changes': mk.get('bid_changes'),
        'marketer_measurements': mk.get('measurement_count'),
        'marketer_receipt_ids': [row.get('receipt_id') for row in mk.get('actions') or []],
        'marketer_bid_items': mk.get('unique_bid_items')}


def verify_owner_daily_numbers(report_date=None, required_stable_passes: int = 3, max_passes: int = 5):
    """Re-read every account from authoritative sources until several consecutive passes agree.

    This is intentionally independent from the persisted snapshot. A report is not sent when
    numbers keep changing or internal arithmetic is inconsistent during the verification window.
    """
    report_date = report_date or datetime.datetime.now(tz=MSK).date()
    db=SessionLocal()
    try:
        accounts=_active_owner_accounts(db)
        previous=None; stable=0; last_payloads=None; mismatches=[]
        for attempt in range(1, max(1, int(max_passes))+1):
            payloads=[build_owner_account_snapshot(db, r['account_id'], r['account_name'], r['owner_user_id'], report_date, rights=r.get('rights')) for r in accounts]
            sig=[_verification_signature(p) for p in payloads]
            arithmetic=[]
            for p in payloads:
                k=p.get('kpi') or {}; a=p.get('avito') or {}
                actual=k.get('actual_leads'); contacts=a.get('contacts'); target=k.get('target_leads')
                if actual is not None and contacts is not None and actual != contacts:
                    arithmetic.append({'account_id':p.get('account_id'),'field':'actual_leads_vs_contacts','values':[actual,contacts]})
                if target and actual is not None:
                    expected=round(float(actual)*100.0/float(target),1)
                    if k.get('completion_pct') != expected:
                        arithmetic.append({'account_id':p.get('account_id'),'field':'completion_pct','values':[k.get('completion_pct'),expected]})
                views=a.get('views'); conv=a.get('view_to_contact_pct')
                if views is not None and contacts is not None:
                    expected_conv=round(float(contacts)*100.0/float(views),2) if views else 0.0
                    if conv is not None and conv != expected_conv:
                        arithmetic.append({'account_id':p.get('account_id'),'field':'view_to_contact_pct','values':[conv,expected_conv]})
            if arithmetic:
                return {'status':'error','verified':False,'reason':'arithmetic_mismatch','attempts':attempt,'mismatches':arithmetic}
            if previous == sig:
                stable += 1
            else:
                if previous is not None: mismatches.append({'attempt':attempt,'reason':'source_changed'})
                stable = 1
            previous=sig; last_payloads=payloads
            if stable >= max(2, int(required_stable_passes)):
                return {'status':'ok','verified':True,'stable_passes':stable,'attempts':attempt,'payloads':last_payloads,'mismatches':mismatches}
        return {'status':'error','verified':False,'reason':'numbers_not_stable','attempts':max_passes,'mismatches':mismatches}
    finally:
        db.close()



_OWNER_MODULE_LABELS = {
    "messages": "Сообщения", "mop": "МОП", "crm": "CRM", "rop": "РОП", "ecs": "ЕЦС",
    "marketing": "AI-маркетолог", "feed_factory": "Объявления", "analytics": "Аналитика",
    "reactivation": "Реактивация", "social": "Соцсети", "telephony": "Телефония",
}
_OWNER_MODULE_HEALTH = {
    "pass": "проверка состояния пройдена", "healthy": "проверка состояния пройдена",
    "working": "в работе", "degraded": "есть ограничение", "blocked": "заблокирован",
    "fail": "ошибка", "error": "ошибка", "warning": "требует проверки", "off": "выключен",
    "inactive": "нет действующего доступа", "not_checked": "работа не подтверждена", "not_proven": "работа не подтверждена",
    "unknown": "работа не подтверждена",
    "waiting_owner": "ожидает решения владельца", "waiting_external": "ожидает внешний сервис",
    "human_required": "передан сотруднику", "needs_self_heal": "требует внутреннего исправления",
}


def _owner_report_module_matrix(state, rights):
    """Project every configured/expected/purchased module without inventing health."""
    adapters = {str(row.get("module")): row for row in (state.get("modules") or []) if row.get("module")}
    expected = set(state.get("expected_modules") or [])
    configured = set(state.get("bindings") or [])
    purchased = {name for name, right in rights.items() if name != "base" and _owner_report_right_active(right)}
    trial = rights.get("marketing_trial") or {}
    trial_modules = {"marketing"} if trial.get("report_watch") is True else set()
    rows = []
    safe_actual_keys = {
        "sent_24h", "sent_today", "incoming_24h", "incoming_today", "queued", "overdue_tasks",
        "target_leads_per_day", "actual_leads", "runtime_mode", "stats_date", "business_date",
        "provider_stats_lagging", "daily_budget_limit_rub", "active_items",
    }
    for name in sorted(expected | configured | purchased | set(adapters) | trial_modules):
        adapter = adapters.get(name) or {}
        actual = adapter.get("actual_state") or {}
        fact = {key: actual[key] for key in safe_actual_keys if key in actual}
        provider = actual.get("provider_health") or {}
        if isinstance(provider, dict):
            fact["provider_health"] = {key: provider[key] for key in (
                "status", "checked_at", "provider_code", "provider_synced_chats", "provider_ok_chats", "provider_blocked_chats"
            ) if key in provider}
        rows.append({
            "module": name, "expected": name in expected, "configured": name in configured,
            "purchased": name in purchased, "entitlement": rights.get(name),
            "health": str(adapter.get("health") or "not_proven"),
            "classification": adapter.get("classification") or ("NOT_PROVEN" if not adapter else None),
            "owner_action_required": bool(adapter.get("owner_action_required")),
            "safe_action": adapter.get("safe_action"),
            "freshness": adapter.get("freshness") or {},
            "actual_state": fact,
            "runtime_evidence_present": bool(adapter),
            "source": "client_supervisor",
            "e2e_verified": False,
        })
        if name == "marketing" and trial.get("entitlement_conflict"):
            rows[-1].update({
                "health": "not_proven", "classification": "MARKETING_TRIAL_ENTITLEMENT_CONFLICT",
                "trial_evidence": trial,
                "safe_action": "reconcile_existing_marketing_trial_entitlement",
            })
    return rows


def _owner_report_rule_evidence(watch, now):
    """Malformed or old evidence is local to one client, never a fleet failure."""
    unproven = {"status": "not_proven", "checked_at": None, "age_seconds": None,
                "entries": [], "coverage_is_not_success": True}
    if not isinstance(watch, dict):
        return dict(unproven, reason="invalid_watch_payload")
    checked = None
    try:
        raw_version = watch.get("version")
        version = int(raw_version or 0)
        raw_entries = watch.get("rule_coverage") or []
        if not isinstance(raw_entries, list):
            return dict(unproven, reason="invalid_rule_coverage")
        entries = []
        for value in raw_entries:
            if not isinstance(value, dict):
                return dict(unproven, reason="invalid_rule_coverage")
            row = {key: value.get(key) for key in (
                "module", "expected", "evaluated", "global_expected", "global_evaluated",
                "account_expected", "account_evaluated", "evidence_model"
            )}
            for key in ("expected", "evaluated", "global_expected", "global_evaluated", "account_expected", "account_evaluated"):
                row[key] = int(row.get(key) or 0)
                if row[key] < 0:
                    return dict(unproven, reason="negative_rule_count")
            outcomes = value.get("evaluation_outcomes") or {}
            if not isinstance(outcomes, dict):
                return dict(unproven, reason="invalid_evaluation_outcomes")
            row["evaluation_outcomes"] = {str(key): int(count) for key, count in outcomes.items()}
            if any(count < 0 for count in row["evaluation_outcomes"].values()):
                return dict(unproven, reason="negative_outcome_count")
            entries.append(row)
    except (ValueError, TypeError, OverflowError):
        return dict(unproven, reason="invalid_rule_coverage")
    try:
        checked = datetime.datetime.fromisoformat(str(watch.get("checked_at") or "").replace("Z", "+00:00"))
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=datetime.timezone.utc)
    except (ValueError, TypeError, OverflowError):
        checked = None
    age = (now - checked).total_seconds() if checked is not None else None
    fresh = bool(checked is not None and -60 <= age <= 1200
                 and checked.astimezone(MSK).date() == now.astimezone(MSK).date() and version >= 2)
    for row in entries:
        row["evaluation_coverage_complete"] = bool(
            fresh and row["expected"] > 0 and row["evaluated"] >= row["expected"]
        )
    return {
        "status": "current" if fresh else "stale" if checked is not None else "not_proven",
        "checked_at": checked.isoformat() if checked is not None else None,
        "age_seconds": round(age, 1) if age is not None else None,
        "entries": entries, "coverage_is_not_success": True,
    }



def _owner_report_kpi_rule_ledger(db, account_id, now):
    """Project existing exact-account, current-version evidence; never execute rules."""
    day_start = now.astimezone(MSK).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(datetime.timezone.utc)
    day_end = day_start + datetime.timedelta(days=1)
    rows = db.execute(text("""
        /* OWNER_REPORT_ACCOUNT_KPI_LEDGER_V1 */
        SELECT r.id::text AS requirement_id, r.key AS rule_key, r.title, r.version,
               r.verification_json,
               latest.id::text AS execution_id, latest.status AS execution_status,
               latest.created_at AS execution_created_at, latest.idempotency_key,
               proof.positive_levels, proof.l5_settings_readback, proof.l6_effect,
               proof.l5_observed_at, proof.l6_observed_at,
               proof.observed_at AS evidence_observed_at,
               today.record_count AS records_today
        FROM control_requirements r
        LEFT JOIN LATERAL (
            SELECT e.id, e.status, e.created_at, e.updated_at, e.idempotency_key
            FROM control_executions e
            WHERE e.requirement_id=r.id AND e.account_id=:a
              AND EXISTS (
                  SELECT 1 FROM control_evidence v
                  WHERE v.execution_id=e.id AND v.requirement_id=r.id
                    AND v.payload_json->>'requirement_version'=r.version::text
              )
            ORDER BY e.created_at DESC, e.updated_at DESC, e.id DESC LIMIT 1
        ) latest ON true
        LEFT JOIN LATERAL (
            SELECT array_agg(DISTINCT v.level) FILTER (WHERE v.passed=true) AS positive_levels,
                   bool_or(v.passed=true AND v.level='L5'
                           AND v.evidence_type='actual_state_readback') AS l5_settings_readback,
                   bool_or(v.passed=true AND v.level='L6') AS l6_effect,
                   max(v.observed_at) FILTER (
                       WHERE v.passed=true AND v.level='L5'
                         AND v.evidence_type='actual_state_readback'
                   ) AS l5_observed_at,
                   max(v.observed_at) FILTER (
                       WHERE v.passed=true AND v.level='L6'
                   ) AS l6_observed_at,
                   max(v.observed_at) AS observed_at
            FROM control_evidence v
            WHERE v.execution_id=latest.id AND v.requirement_id=r.id
              AND v.payload_json->>'requirement_version'=r.version::text
        ) proof ON true
        LEFT JOIN LATERAL (
            SELECT count(DISTINCT e.id) AS record_count
            FROM control_executions e
            WHERE e.requirement_id=r.id AND e.account_id=:a
              AND e.created_at>=:day_start AND e.created_at<:day_end
              AND EXISTS (
                  SELECT 1 FROM control_evidence v
                  WHERE v.execution_id=e.id AND v.requirement_id=r.id
                    AND v.payload_json->>'requirement_version'=r.version::text
              )
        ) today ON true
        WHERE r.status='active' AND r.requirement_type='kpi_rule'
          AND r.scope_type='account' AND r.scope_id=:a
        ORDER BY r.key, r.id
    """), {"a": str(account_id), "day_start": day_start, "day_end": day_end}).mappings().all()

    def timestamp(value):
        if value is None:
            return None
        try:
            parsed = value if isinstance(value, datetime.datetime) else datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.replace(tzinfo=datetime.timezone.utc) if parsed.tzinfo is None else parsed.astimezone(datetime.timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None

    def proof_state(present, observed_at):
        if not present or observed_at is None or (observed_at - now).total_seconds() > 60:
            return "not_proven"
        return (
            "report_day_observed"
            if observed_at.astimezone(MSK).date() == day_start.astimezone(MSK).date()
            else "historical"
        )

    entries = []
    for raw in rows:
        row = dict(raw)
        verification = row.get("verification_json") or []
        levels = row.get("positive_levels") or []
        if not isinstance(verification, list) or not isinstance(levels, list):
            raise ValueError("invalid_account_kpi_evidence")
        created = timestamp(row.get("execution_created_at"))
        observed = timestamp(row.get("evidence_observed_at"))
        l5_observed = timestamp(row.get("l5_observed_at"))
        l6_observed = timestamp(row.get("l6_observed_at"))
        l5_state = proof_state(row.get("execution_id") and row.get("l5_settings_readback"), l5_observed)
        l6_state = proof_state(row.get("execution_id") and row.get("l6_effect"), l6_observed)
        l5 = l5_state == "report_day_observed"
        l6 = l6_state == "report_day_observed"
        required_l6 = any(
            isinstance(value, dict) and value.get("required") is not False
            and (value.get("level") == "L6" or value.get("type") == "kpi_effect")
            for value in verification
        )
        entries.append({
            "requirement_id": str(row["requirement_id"]), "rule_key": str(row.get("rule_key") or ""),
            "title": str(row.get("title") or row.get("rule_key") or ""), "rule_version": int(row.get("version") or 0),
            "account_id": str(account_id), "execution_id": row.get("execution_id"),
            "execution_status": str(row.get("execution_status") or "not_proven"),
            "execution_created_at": created.isoformat() if created else None,
            "evidence_observed_at": observed.isoformat() if observed else None,
            "positive_levels": [str(value) for value in levels if value is not None],
            "records_today": int(row.get("records_today") or 0),
            "l5_settings_readback_proven": l5, "l6_effect_proven": l6,
            "l5_positive_observed_at": l5_observed.isoformat() if l5_observed else None,
            "l6_positive_observed_at": l6_observed.isoformat() if l6_observed else None,
            "l5_evidence_status": l5_state, "l6_evidence_status": l6_state,
            "l5_historical_evidence_present": l5_state == "historical",
            "l6_historical_evidence_present": l6_state == "historical",
            "proof_date_scope": "report_day_observation_not_scheduler_freshness",
            "required_l6_effect": required_l6,
            "l6_status": "proven" if l6 else l6_state,
            "current_hour_observation": bool(created and created.replace(minute=0, second=0, microsecond=0) == now.replace(minute=0, second=0, microsecond=0)),
            "schedule_evidence": "not_proven_from_execution_ledger",
            "status": "observed" if row.get("execution_id") else "not_proven",
        })
    return {
        "status": "observed" if entries else "not_registered", "entries": entries,
        "checked_at": now.isoformat(), "report_date": day_start.astimezone(MSK).date().isoformat(),
        "source": "control_requirements/control_executions/control_evidence",
        "scope": "exact_account_and_current_requirement_version",
        "settings_verification_is_not_kpi_effect": True,
        "missing_schedule_is_not_pass": True,
    }


def _owner_report_mop_provider_fact(provider, now):
    """Read-only evidence projection; queue freshness is not provider freshness."""
    try:
        from app.services.client_supervisor import MOP_PROVIDER_HEALTH_TTL_SECONDS
    except ImportError:
        MOP_PROVIDER_HEALTH_TTL_SECONDS = 900

    provider = provider if isinstance(provider, dict) else {}
    checked = _owner_report_timestamp(provider.get("checked_at"))
    age = (now - checked).total_seconds() if checked else None
    clock_fresh = age is not None and -60 <= age <= MOP_PROVIDER_HEALTH_TTL_SECONDS
    fact_fresh = clock_fresh and provider.get("provider_fact_fresh") is True
    status = str(provider.get("status") or "")
    if fact_fresh and status in {"degraded_provider", "blocked_provider"}:
        state = "blocked" if status == "blocked_provider" else "degraded"
    elif fact_fresh and status == "ok" and provider.get("provider_fact_state") == "healthy" and provider.get("binding") is True:
        state = "healthy"
    elif checked and (not clock_fresh or provider.get("provider_fact_fresh") is False):
        state = "stale"
    else:
        state = "not_proven"
    safe = {key: provider[key] for key in (
        "status", "checked_at", "provider_code", "provider_synced_chats",
        "provider_ok_chats", "provider_blocked_chats", "provider_fact_state",
        "provider_fact_fresh", "binding"
    ) if key in provider}
    return {
        "state": state, "source": "storage:mop_provider_health",
        "checked_at": checked.isoformat() if checked else None,
        "age_seconds": round(age, 1) if age is not None else None,
        "ttl_seconds": MOP_PROVIDER_HEALTH_TTL_SECONDS,
        "provider_health": safe,
        "provider_pass_is_not_end_to_end_delivery": True,
    }



def _owner_report_apply_mop_provider(matrix, provider, now, error_type=None):
    try:
        from app.services.client_supervisor import _mop_provider_health_override
    except ImportError:
        def _mop_provider_health_override(provider, now=None, ttl_seconds=900):
            if not isinstance(provider, dict):
                return None
            status = str(provider.get("status") or "").strip().lower()
            if status not in {"degraded_provider", "blocked_provider"}:
                return None
            try:
                checked = datetime.datetime.fromisoformat(
                    str(provider.get("checked_at") or "").replace("Z", "+00:00")
                )
                if checked.tzinfo is None:
                    checked = checked.replace(tzinfo=datetime.timezone.utc)
                current = now or datetime.datetime.now(datetime.timezone.utc)
                if current.tzinfo is None:
                    current = current.replace(tzinfo=datetime.timezone.utc)
                age = (
                    current.astimezone(datetime.timezone.utc)
                    - checked.astimezone(datetime.timezone.utc)
                ).total_seconds()
                if age < -60 or age > max(60, int(ttl_seconds)):
                    return None
            except Exception:
                return None
            blocked = status == "blocked_provider"
            return {
                "health": "blocked" if blocked else "degraded",
                "classification": (
                    "MOP_PROVIDER_BLOCKED_402"
                    if blocked else "MOP_PROVIDER_PARTIAL_402"
                ),
                "owner_action_required": True,
                "safe_action": "restore_avito_messenger_api_access",
                "dependency_state": "waiting_owner",
                "provider_health": provider,
            }

    row = next((item for item in matrix if item["module"] == "mop"), None)
    if row is None:
        return
    fact = _owner_report_mop_provider_fact(provider, now)
    if error_type:
        fact.update({"state": "not_proven", "error_type": error_type})
    row["supervisor_evidence"] = {
        "health": row["health"], "classification": row["classification"],
        "freshness": row["freshness"],
    }
    row["provider_evidence"] = fact
    row["actual_state"]["provider_health"] = fact["provider_health"]
    was_provider_override = str(row.get("classification") or "").startswith("MOP_PROVIDER_")
    if fact["state"] in {"degraded", "blocked"}:
        override = _mop_provider_health_override(provider, now=now)
        if override:
            for key in ("health", "classification", "owner_action_required", "safe_action"):
                row[key] = override[key]
    elif fact["state"] == "healthy" and was_provider_override:
        # A new positive provider observation cannot reconstruct queue evidence
        # that an older provider override replaced in the supervisor snapshot.
        row.update({"health": "not_proven", "classification": "MOP_SUPERVISOR_RECHECK_REQUIRED",
                    "owner_action_required": False, "safe_action": "refresh_existing_mop_supervisor"})
    elif fact["state"] != "healthy":
        if row["health"] in {"pass", "healthy"} or was_provider_override:
            row.update({"health": "not_proven", "classification": "MOP_PROVIDER_OBSERVATION_NOT_PROVEN"})
        if was_provider_override:
            row["owner_action_required"] = False
        if not row.get("safe_action") or was_provider_override:
            row["safe_action"] = "wait_for_existing_mop_provider_sync"



def _owner_report_event_rules(db, account_id, now, modules):
    from app.services.control_rule_runtime import account_rule_projection
    start = now.astimezone(MSK).replace(hour=0, minute=0, second=0, microsecond=0)
    return account_rule_projection(db, account_id, since=start, until=now, modules=modules)


def _owner_event_rule_lines(ledger):
    """Small factual projection; event coverage is not business success."""
    from html import escape
    if ledger.get("state") == "READ_ERROR":
        return ["Журнал событий и правил: данные не прочитаны; выполнение не подтверждено."]
    entries = ledger.get("rules") or []
    current = [row for row in entries if row.get("current_version") is True]
    evaluated = sum((row.get("actual") or {}).get("evaluation_status") == "EVALUATED" for row in current)
    pending = sum((row.get("actual") or {}).get("evaluation_status") == "PENDING" for row in current)
    unproven = sum((row.get("actual") or {}).get("evaluation_status") == "NOT_PROVEN" for row in current)
    gaps = ledger.get("gaps") or []
    waiting = sum((row.get("actual") or {}).get("evaluation_status") == "WAITING_EVENT" for row in current)
    if ledger.get("aggregate_complete") is True:
        aggregate = [row for row in (ledger.get("rule_coverage_counts") or []) if row.get("current_version") is True]
        evaluated = sum(int(row.get("evaluated") or 0) for row in aggregate)
        pending = sum(int(row.get("pending") or 0) for row in aggregate)
        unproven = sum(int(row.get("not_proven") or 0) for row in aggregate)
        waiting = sum(int(row.get("waiting_event") or 0) for row in aggregate)
    lines = [f"Журнал событий и правил: решений текущих версий {evaluated}; ожидают оценки {pending}; недостаточно доказательств {unproven}; ожидают внешнего события {waiting}; открытых пробелов в выборке {len(gaps)}."]
    if ledger.get("aggregate_complete") is True and "due_rule_versions" in ledger:
        due_count = int(ledger.get("due_rule_versions") or 0)
        covered_count = int(ledger.get("evaluated_due_rule_versions") or 0)
        lines.append(f"Уникальные применимые версии правил с наступившим сроком за окно: оценены {covered_count} из {due_count}. Повторы событий не увеличивают покрытие; любая пропущенная оценка этой версии оставляет её непокрытой.")
        if due_count == 0:
            lines.append("Подтверждённых применимых проверок с наступившим сроком за окно нет; это не означает успешное выполнение правил.")
    if ledger.get("state") == "NO_EVENT":
        lines.append("В сохранённом входящем потоке за окно событий нет; полнота внешнего потока отдельно не подтверждена.")
    elif ledger.get("state") == "NOT_PROVEN":
        lines.append("Входящие события есть, но связь с проверкой правила не подтверждена.")
    if not ledger.get("source_complete"):
        lines.append("Подробности журнала показаны частично; сводные количества охватывают все сохранённые проверки за окно." if ledger.get("aggregate_complete") is True else "Показана ограниченная часть журнала; полное покрытие не подтверждено.")
    coverage = ledger.get("binding_coverage") or {}
    absent = sorted({str(row.get("module")) for row in coverage.get("entries", []) if row.get("binding_status") != "IMPLEMENTED" or row.get("protected_writer_capture_missing") is True or row.get("protected_feed_writer_branches_missing") is True})
    if absent:
        names = ", ".join(_OWNER_MODULE_LABELS.get(name, name) for name in absent)
        lines.append("Часть правил ещё не связана с событиями модулей: " + escape(names) + ". Общий реестр не считается проверками этого клиента.")
    for row in current:
        actual = row.get("actual") or {}
        if actual.get("delivery_proven") is True:
            lines.append("МОП: доставка точного ответа подтверждена чтением у провайдера (" + escape(str(row.get("verified_at") or "время не подтверждено")) + "); статус дальнейшей работы: " + escape(str(row.get("status") or "неизвестен")) + ".")
    unknown = [row for row in current if (row.get("actual") or {}).get("evaluation_status") == "NOT_PROVEN"]
    if unknown:
        lines.append("Не подтверждены проверки: " + ", ".join(escape(str(row.get("rule_id") or (row.get("identity") or {}).get("requirement_key") or "неизвестно")) for row in unknown[:8]) + ". Нужны доказательства результата; запись действия сама по себе не считается успехом.")
    for gap in gaps[:5]:
        detail = gap.get("details") or {}
        if gap.get("type") == "RULE_NOT_EVALUATED":
            lines.append("⚠️ Срок проверки события прошёл, решение не записано; срок: " + escape(str(detail.get("verification_due_at") or "не подтверждён")) + ". Повтор клиентского действия не разрешён.")
        else:
            lines.append("⚠️ Не подтверждена связь события с правилом: " + escape(str(detail.get("rule_key") or detail.get("reason") or "неизвестно")) + ".")
    return lines


def _owner_active_client_module_summary():
    """One dynamic owner projection of the canonical client supervisor."""
    from html import escape
    from app.services.client_supervisor import build_client_state, load_supervisor_snapshot

    db = SessionLocal()
    try:
        registry = _owner_report_registry(db)
        coverage = registry["coverage"]
        current_cut = datetime.datetime.now(tz=MSK)
        lines = [
            "👥 <b>Клиенты и подключённые модули</b>",
            f"Реестр проверен: {coverage['registry_accounts_checked']} аккаунтов. "
            f"Внешних клиентов под контролем: {coverage['canonical_clients']}. "
            f"Обязательный контроль: {coverage['watched_covered']}/{coverage['watched_expected']}.",
            f"Текущий срез модулей и правил: {_owner_report_time(current_cut)}; бизнес-показатели за выбранный день приведены отдельным блоком.",
            "Права на пакет, проверка состояния и подтверждённый результат — отдельные факты.",
            "Счётчики бизнес-правил и KPI-журнал показаны отдельно. Сверка настроек (L5) и подтверждённый эффект (L6) — разные доказательства.",
            f"L5/L6 относятся к положительным доказательствам за день текущего среза {current_cut.strftime('%d.%m.%Y')}; прошлые дни показаны как история.",
        ]
        if coverage["missing_watched_accounts"]:
            lines.append("⚠️ Не найдены в доступном реестре: " + ", ".join(escape(x) for x in coverage["missing_watched_accounts"]))
        if coverage["entitlement_read_errors"]:
            lines.append("⚠️ Не удалось проверить права: " + ", ".join(escape(x) for x in coverage["entitlement_read_errors"]))
        details, internal_details, owner_actions = [], [], []
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        for internal, accounts in ((False, registry["accounts"]), (True, registry["internal_accounts"])):
            if internal and accounts:
                lines += ["", "<b>Внутренние аккаунты платформы</b>"]
            for account in accounts:
                aid = account["account_id"]
                label = escape(account["account_name"])
                state_error = None
                try:
                    with db.begin_nested():
                        state = load_supervisor_snapshot(db, aid) or build_client_state(db, aid)
                except Exception as exc:
                    state_error = type(exc).__name__
                    state = {"account_id": aid, "overall": "read_error", "modules": [], "expected_modules": []}
                matrix = _owner_report_module_matrix(state, account.get("rights") or {})
                if any(row["module"] == "mop" for row in matrix):
                    provider_error = None
                    try:
                        with db.begin_nested():
                            provider_raw = db.execute(text(
                                "SELECT value FROM storage WHERE account_id=:a AND key='mop_provider_health' "
                                "ORDER BY id DESC LIMIT 1"
                            ), {"a": aid}).scalar()
                        provider = _safe_json(provider_raw)
                    except Exception as exc:
                        provider_error, provider = type(exc).__name__, {}
                    _owner_report_apply_mop_provider(matrix, provider, now, provider_error)
                try:
                    with db.begin_nested():
                        watch_raw = db.execute(text(
                            "SELECT value FROM storage WHERE account_id=:a AND key='active_module_watch_v1' "
                            "ORDER BY id DESC LIMIT 1"
                        ), {"a": aid}).scalar()
                    rules = _owner_report_rule_evidence(_safe_json(watch_raw), now)
                except Exception as exc:
                    rules = {"status": "not_proven", "checked_at": None, "age_seconds": None,
                             "entries": [], "coverage_is_not_success": True, "error_type": type(exc).__name__}
                kpi_ledger = {"status": "not_applicable", "entries": []}
                event_rule_ledger = {"state": "NOT_APPLICABLE", "rules": [], "gaps": []}
                if not internal:
                    try:
                        with db.begin_nested():
                            event_rule_ledger = _owner_report_event_rules(db, aid, now, [row["module"] for row in matrix])
                    except Exception as exc:
                        event_rule_ledger = {"state": "READ_ERROR", "rules": [], "gaps": [], "error_type": type(exc).__name__}
                    try:
                        with db.begin_nested():
                            kpi_ledger = _owner_report_kpi_rule_ledger(db, aid, now)
                    except Exception as exc:
                        kpi_ledger = {"status": "not_proven", "entries": [], "error_type": type(exc).__name__,
                                      "settings_verification_is_not_kpi_effect": True,
                                      "missing_schedule_is_not_pass": True}
                base = (account.get("rights") or {}).get("base") or {}
                base_text = {
                    "active": "действует", "expired": "срок завершён", "platform_owned": "внутренний аккаунт",
                }.get(str(base.get("state") or ""), "период не подтверждён")
                lines += ["", f"<b>{label}</b> · основной доступ: {base_text}"
                          + (f" · до {escape(str(base['paid_until']))}" if base.get("paid_until") else "")]
                if account.get("avito_reporting_reason") != "available":
                    why = "не настроено подключение" if account.get("avito_reporting_reason") == "avito_credentials_not_configured" else "действует ограничение фоновых действий"
                    lines.append(f"Avito: {why}; обновление KPI недоступно, модули остаются в отчёте.")
                if state_error:
                    lines.append("⚠️ Текущее состояние модулей не прочитано; исправность не подтверждена.")
                if not matrix:
                    lines.append("Ожидаемых или приобретённых модулей с подтверждённым текущим периодом не найдено.")
                for row in matrix:
                    name = _OWNER_MODULE_LABELS.get(row["module"], row["module"])
                    health = _OWNER_MODULE_HEALTH.get(row["health"], "требует проверки")
                    suffix = ""
                    if row["module"] == "mop":
                        if row["health"] in {"pass", "healthy"}:
                            health = "проверка очереди пройдена"
                        provider_fact = row.get("provider_evidence") or {}
                        provider_state = provider_fact.get("state")
                        provider_when = _owner_report_time(provider_fact.get("checked_at"))
                        if provider_state == "healthy":
                            suffix += f"; API переписок: свежая сверка без ограничений ({provider_when})"
                        elif provider_state in {"degraded", "blocked"}:
                            suffix += f"; API переписок: свежая сверка ({provider_when})"
                        elif provider_state == "stale":
                            suffix += f"; API переписок: прежняя сверка устарела ({provider_when}); текущее состояние не подтверждено"
                        else:
                            suffix += "; API переписок: свежая проверка не подтверждена"
                        if provider_state not in {"healthy", "degraded", "blocked"}:
                            last_provider = provider_fact.get("provider_health") or {}
                            if last_provider.get("provider_code") == 402:
                                suffix += "; последняя запись — ограничение 402, восстановление не подтверждено"
                        if "sent_24h" in row["actual_state"]:
                            suffix += f"; отправок за 24 часа {row['actual_state']['sent_24h']}"
                    if row["module"] == "crm" and "overdue_tasks" in row["actual_state"]:
                        suffix += f"; просроченных задач {row['actual_state']['overdue_tasks']} (включая исторические)"
                    if row["purchased"] and not row["runtime_evidence_present"]:
                        suffix += "; пакет действует, сквозная работа не проверена"
                    reason = {
                        "MOP_PROVIDER_PARTIAL_402": "API Авито вернул 402 для части переписок",
                        "MOP_PROVIDER_BLOCKED_402": "API Авито не предоставляет доступ к перепискам (402)",
                        "PAID_PERIOD_EVIDENCE_MISSING": "не подтверждён период обслуживания",
                        "NOT_PROVEN": "нет подтверждения работы модуля",
                        "MARKETING_TRIAL_ENTITLEMENT_CONFLICT": "согласованный тестовый период ещё действует, но исполнитель видит завершённый или неизвестный период",
                        "MOP_SUPERVISOR_RECHECK_REQUIRED": "новая сверка API есть; состояние очереди требует обновления штатного снимка",
                    }.get(str(row.get("classification") or ""))
                    if reason:
                        suffix += "; " + reason
                    if row.get("trial_evidence"):
                        suffix += "; тест записан до " + escape(str(row["trial_evidence"].get("trial_until") or "неизвестно"))
                    provider = row["actual_state"].get("provider_health") or {}
                    synced = provider.get("provider_synced_chats", provider.get("provider_ok_chats"))
                    blocked_chats = provider.get("provider_blocked_chats")
                    if synced is not None or blocked_chats is not None:
                        suffix += f"; синхронизировано {synced if synced is not None else 'неизвестно'}, недоступно {blocked_chats if blocked_chats is not None else 'неизвестно'}"
                    next_action = {
                        "restore_avito_messenger_api_access": "проверить и восстановить доступ к API переписок Авито",
                        "surface_in_manager_workspaces": "обработать обращение в кабинете сотрудника",
                        "wait_for_existing_mop_provider_sync": "дождаться штатной синхронизации переписок и повторно проверить доступ",
                        "refresh_existing_mop_supervisor": "обновить состояние через штатный клиентский контроллер",
                        "reconcile_existing_marketing_trial_entitlement": "согласовать проверку уже назначенного теста с ограничениями исполнителя",
                    }.get(str(row.get("safe_action") or ""))
                    if next_action:
                        suffix += "; следующее действие: " + next_action
                    if row["owner_action_required"]:
                        suffix += "; требуется решение владельца"
                    lines.append(f"• {escape(str(name))}: {health}{suffix}")
                for item in state.get("needs_owner") or []:
                    owner_actions.append({"account_id": aid, "module": item.get("module"),
                                          "reason": item.get("reason"), "safe_action": item.get("safe_action")})
                for row in matrix:
                    if row["owner_action_required"] and not any(
                        x["account_id"] == aid and x.get("module") == row["module"] for x in owner_actions
                    ):
                        owner_actions.append({"account_id": aid, "module": row["module"],
                                              "reason": row["classification"], "safe_action": row["safe_action"]})
                if rules["status"] != "current":
                    lines.append("Проверки правил: свежих подтверждений нет.")
                elif not rules["entries"]:
                    lines.append("Проверки правил: применимые правила в снимке не перечислены.")
                else:
                    for rule in rules["entries"]:
                        expected, evaluated = int(rule.get("expected") or 0), int(rule.get("evaluated") or 0)
                        name = _OWNER_MODULE_LABELS.get(rule.get("module"), rule.get("module") or "модуль")
                        if not expected:
                            lines.append(f"Правила · {escape(str(name))}: применимые правила не заданы.")
                            continue
                        outcomes = ", ".join(f"{escape(str(key))}: {int(value)}" for key, value in (rule.get("evaluation_outcomes") or {}).items())
                        if rule.get("evidence_model") == "global_contract_plus_exact_account_scope_v2":
                            global_expected = int(rule.get("global_expected") or 0)
                            global_evaluated = int(rule.get("global_evaluated") or 0)
                            account_expected = int(rule.get("account_expected") or 0)
                            account_evaluated = int(rule.get("account_evaluated") or 0)
                            account_scope = (
                                f"проверки именно этого клиента {account_evaluated}/{account_expected}"
                                if account_expected else "проверки именно этого клиента не заданы"
                            )
                            scope_text = f"общие контракты {global_evaluated}/{global_expected}; {account_scope}"
                        else:
                            scope_text = f"записи {evaluated}/{expected}; область проверок не подтверждена"
                        lines.append(f"Бизнес-правила · {escape(str(name))}: {scope_text}; итоги журнала: {outcomes or 'не записаны'}.")
                if not internal:
                    lines.extend(_owner_event_rule_lines(event_rule_ledger))
                    if kpi_ledger["status"] == "not_proven":
                        lines.append("KPI-журнал: прочитать доказательства не удалось; сверка настроек и эффект не подтверждены.")
                    elif kpi_ledger["status"] == "not_registered":
                        lines.append("KPI-журнал: индивидуальное правило не зарегистрировано; проверка не подтверждена.")
                    for check in kpi_ledger["entries"]:
                        rule_name = escape(check["title"])
                        if not check["execution_id"]:
                            lines.append(f"KPI · {rule_name}, v{check['rule_version']}: записи этой версии не найдены; проверка не подтверждена.")
                            continue
                        when = escape(check["evidence_observed_at"] or check["execution_created_at"] or "время не подтверждено")
                        l5_text = (
                            "подтверждена" if check["l5_settings_readback_proven"]
                            else "историческая запись; текущий день не подтверждён" if check["l5_historical_evidence_present"]
                            else "не подтверждена"
                        )
                        l6_text = (
                            "подтверждён" if check["l6_effect_proven"]
                            else "историческая запись; текущий день не подтверждён" if check["l6_historical_evidence_present"]
                            else "не подтверждён"
                        )
                        lines.append(
                            f"KPI · {rule_name}, v{check['rule_version']}: записей за день {check['records_today']}; "
                            f"последняя {when}; статус журнала {escape(check['execution_status'])}; "
                            f"L5 (сверка настроек) {l5_text}; L6 (эффект) {l6_text}. "
                            f"Даты положительных доказательств: L5 {escape(check['l5_positive_observed_at'] or 'нет')}; "
                            f"L6 {escape(check['l6_positive_observed_at'] or 'нет')}."
                        )
                related = []
                for alias in account.get("related_accounts") or []:
                    paid = [name for name, value in (alias.get("rights") or {}).items()
                            if name != "base" and _owner_report_right_active(value)]
                    related.append({"account_id": alias["account_id"], "rights": alias.get("rights") or {},
                                    "runtime_status": "NOT_PROVEN", "runtime_evidence_present": False})
                    if paid:
                        lines.append("Связанная подписка " + escape(alias["account_id"]) + ": "
                                     + ", ".join(escape(_OWNER_MODULE_LABELS.get(name, name)) for name in paid)
                                     + "; отдельная проверка работы не выполнена; клиент и расходы повторно не считаются.")
                entry = {
                    "account_id": aid, "name": account["account_name"], "internal": internal,
                    "client_state": state.get("client_state"), "overall": state.get("overall"),
                    "checked_at": state.get("checked_at"), "read_error": state_error or account.get("read_error"),
                    "expected_modules": state.get("expected_modules") or [],
                    "rights": account.get("rights") or {}, "modules": matrix,
                    "rule_evidence": rules, "kpi_rule_ledger": kpi_ledger, "event_rule_ledger": event_rule_ledger, "related_accounts": related,
                    "avito_reporting_reason": account.get("avito_reporting_reason"),
                }
                # Preserve existing machine consumers while extending the complete matrix.
                entry.update({name: next((row for row in matrix if row["module"] == name), {})
                              for name in ("marketing", "mop", "crm", "reactivation")})
                (internal_details if internal else details).append(entry)
        coverage["kpi_accounts_registered"] = sum(bool(row["kpi_rule_ledger"]["entries"]) for row in details)
        coverage["kpi_accounts_with_l5_readback"] = sum(
            any(check["l5_settings_readback_proven"] for check in row["kpi_rule_ledger"]["entries"]) for row in details
        )
        coverage["kpi_accounts_with_l6_effect"] = sum(
            any(check["l6_effect_proven"] for check in row["kpi_rule_ledger"]["entries"]) for row in details
        )
        coverage["external_module_rows"] = sum(len(row["modules"]) for row in details)
        coverage["internal_module_rows"] = sum(len(row["modules"]) for row in internal_details)
        coverage["module_rows"] = sum(len(row["modules"]) for row in [*details, *internal_details])
        coverage["runtime_evidence_missing"] = sum(
            1 for row in [*details, *internal_details] for module in row["modules"] if not module["runtime_evidence_present"]
        )
        return {
            "text": "\n".join(lines), "clients": details, "internal_clients": internal_details,
            "coverage": coverage, "linked_accounts": registry["linked_accounts"], "excluded_accounts": registry["excluded"],
            "owner_actions": owner_actions, "owner_action_required": bool(owner_actions),
        }
    finally:
        db.close()


def _owner_runtime_outcome_summary():
    """Compact owner-facing plan/fact for autonomous email + Social services."""
    from app.services.reliability import email_delivery_health
    from app.models.control_plane import ControlIncident
    import posting_runner as _social_runner

    email=email_delivery_health()
    daily=list(email.get('daily_outreach') or [])
    primary=daily[0] if daily else {}
    social_projects=[
        _social_runner.ocean_daily_delivery_health('u2'),
        _social_runner.dushi_daily_delivery_health('u2'),
    ]

    db=SessionLocal()
    try:
        incidents=db.query(ControlIncident).filter(
            ControlIncident.state=='open',
            ControlIncident.incident_type.in_([
                'OWNER_EMAIL_DAILY_PLAN_FAILED',
                'OWNER_SOCIAL_DAILY_PLAN_FAILED',
            ]),
        ).all()
    finally:
        db.close()

    email_line=(
        f"Email: факт {int(primary.get('successful') or 0)} / "
        f"ожидается к этому часу {int(primary.get('expected_by_now') or 0)} "
        f"(план дня {int(primary.get('daily_cap') or 0)})"
        if primary else "Email: активной owner-рассылки нет"
    )
    email_quality_line=""
    if primary:
        email_quality_line=(
            f"Email · версия текста: подтверждено {int(primary.get('quality_versioned_sent') or 0)}, "
            f"legacy без версии {int(primary.get('quality_untracked_sent') or 0)}, "
            f"нарушений после включения контроля {int(primary.get('quality_unversioned_after_enforcement') or 0)}"
        )
    client_modules=_owner_active_client_module_summary()
    social_lines=[]
    for item in social_projects:
        name=str(item.get('project_name') or item.get('project_id') or 'Social')
        status=str(item.get('status') or '')
        if status=='disabled_intentionally':
            social_lines.append(f"Social · {name}: автопостинг сознательно выключен")
            continue
        social_lines.append(
            f"Social · {name}: факт {int(item.get('complete_due') or 0)} / "
            f"{int(item.get('due_by_now') or 0)} наступивших слотов "
            f"(план дня {int(item.get('planned_total') or 0)})"
        )
    client_owner_actions=list(client_modules.get('owner_actions') or [])
    total_owner_actions=len(incidents)+len(client_owner_actions)
    action=(
        f"⚠️ Действие владельца требуется: "
        f"клиентских blockers {len(client_owner_actions)}, P1-инцидентов {len(incidents)}"
        if total_owner_actions
        else "✅ Действие владельца: не требуется"
    )
    text_lines=["🧭 <b>Автоконтроль BORIS</b>",client_modules.get('text') or '',email_line]
    if email_quality_line:
        text_lines.append(email_quality_line)
    text_lines.extend([*social_lines,action])
    text_lines=[x for x in text_lines if x]
    return {
        'text':"\n".join(text_lines),
        'clients':client_modules.get('clients') or [],
        'internal_clients':client_modules.get('internal_clients') or [],
        'client_coverage':client_modules.get('coverage') or {},
        'linked_accounts':client_modules.get('linked_accounts') or [],
        'email':primary,
        'social':social_projects[0] if social_projects else {},
        'social_projects':social_projects,
        'client_owner_actions':client_owner_actions,
        'owner_incidents':len(incidents),
        'owner_action_required':bool(total_owner_actions),
    }


def _owner_source_refresh_warning(source_refresh=None):
    source_refresh = source_refresh or {}
    if source_refresh.get('status') != 'degraded':
        return ''
    errors = list(source_refresh.get('errors') or [])
    count = int(source_refresh.get('warning_count') or len(errors))
    auth_count = sum(
        1 for item in errors
        if 'авториз' in str(item.get('reason') or '').lower()
        or str(item.get('provider_status') or '') in {'401', '403'}
    )
    rate_count = sum(
        1 for item in errors
        if int(item.get('provider_status') or 0) == 429
    )
    details = []
    if auth_count:
        details.append(f"авторизация — {auth_count}")
    if rate_count:
        details.append(f"лимит API — {rate_count}")
    known = auth_count + rate_count
    if count > known:
        details.append(f"другое — {count-known}")
    why = (" Причины: " + "; ".join(details) + ".") if details else ""
    return (
        f"⚠️ <b>По {count} аккаунт(ам) Avito не дал свежий срез.</b> "
        "В отчёте использованы последние сохранённые данные; "
        "BORIS повторит обновление автоматически."
        + why
    )


def send_owner_daily_report(report_date=None, force: bool = False, slot: str = 'daily', source_refresh=None):
    report_date = report_date or datetime.datetime.now(tz=MSK).date()
    slot=(slot or 'daily').strip().lower()
    if slot not in {'daily','morning','evening'}: slot='daily'

    verification=verify_owner_daily_numbers(report_date, required_stable_passes=3, max_passes=5)
    if not verification.get('verified'):
        return {'status':'error','sent':False,'report_date':report_date.isoformat(),'slot':slot,'verification':verification}

    # Persist only after the independent multi-pass check succeeded.
    snap=save_owner_daily_snapshots(report_date)
    if snap.get('status') not in {'ok','partial'} or snap.get('errors'):
        return {'status':'error','sent':False,'report_date':report_date.isoformat(),'slot':slot,'message':'snapshot_save_failed','snapshot':snap,'verification':verification}
    rep=render_owner_daily_report(report_date)
    if rep.get('status') != 'ok': return {'status':'error','message':'Нет snapshot для отправки','snapshot':snap,'verification':verification}
    try:
        runtime_outcome=_owner_runtime_outcome_summary()
    except Exception as exc:
        return {
            'status':'error','sent':False,'report_date':report_date.isoformat(),'slot':slot,
            'stage':'runtime_outcome_summary','error':type(exc).__name__,
            'snapshot':snap,'verification':verification,
        }

    db=SessionLocal()
    try:
        _owner_report_schema(db)
        db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */')); db.commit()
        done=db.execute(text("SELECT status FROM owner_daily_report_delivery_slots WHERE report_date=:d AND slot=:s"),{'d':report_date,'s':slot}).scalar()
        if done=='ok' and not force:
            return {'status':'ok','already_sent':True,'report_date':report_date.isoformat(),'slot':slot,'verification':{'verified':True,'stable_passes':verification.get('stable_passes'),'attempts':verification.get('attempts')}}
        db.execute(text("""INSERT INTO owner_daily_report_delivery_slots(report_date,slot,chat_id,thread_id,status,verification_passes,updated_at)
          VALUES(:d,:s,:c,:t,'sending',:v,now()) ON CONFLICT(report_date,slot) DO UPDATE SET status='sending',error=NULL,verification_passes=:v,updated_at=now()"""),
          {'d':report_date,'s':slot,'c':OWNER_REPORT_CHAT_ID,'t':OWNER_REPORT_THREAD_ID,'v':int(verification.get('stable_passes') or 0)}); db.commit()
    finally: db.close()
    try:
        try:
            from dotenv import load_dotenv
            load_dotenv('/root/BORIS/backend/.env', override=False)
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=1437)
        from app.telegram_bot import send_telegram_message
        slot_title='09:00 МСК' if slot=='morning' else '17:00 МСК' if slot=='evening' else 'контрольный'
        refresh_warning = _owner_source_refresh_warning(source_refresh)
        body=(
            f"✅ <b>Цифры перепроверены {verification.get('stable_passes',0)} раза перед отправкой</b> · {slot_title}\n"
            + (refresh_warning + "\n" if refresh_warning else "")
            + runtime_outcome['text'] + "\n\n"
            + rep['text']
        )
        messenger_delivery = _send_owner_report_to_messenger(body, report_date, slot)
        if messenger_delivery.get('status') != 'ok':
            raise RuntimeError(
                'owner_messenger_delivery_failed:'
                + str(messenger_delivery.get('code') or messenger_delivery.get('status') or 'unknown')
            )
        chunks=_telegram_chunks(body)
        for chunk in chunks: send_telegram_message(OWNER_REPORT_CHAT_ID, chunk, thread_id=OWNER_REPORT_THREAD_ID)
        db=SessionLocal()
        try:
            db.execute(text("UPDATE owner_daily_report_delivery_slots SET status='ok',message_count=:n,sent_at=now(),updated_at=now() WHERE report_date=:d AND slot=:s"),{'n':len(chunks),'d':report_date,'s':slot}); db.commit()
        finally: db.close()
        return {
            'status':'ok','report_date':report_date.isoformat(),'slot':slot,
            'sent':True,'message_count':len(chunks),'snapshot':snap,
            'messenger_delivery':messenger_delivery,
            'verification':{'verified':True,'stable_passes':verification.get('stable_passes'),'attempts':verification.get('attempts')},
            'runtime_outcome':{
                'owner_action_required':bool(runtime_outcome.get('owner_action_required')),
                'owner_incidents':int(runtime_outcome.get('owner_incidents') or 0),
                'email':runtime_outcome.get('email') or {},
                'social':runtime_outcome.get('social') or {},
                'social_projects':runtime_outcome.get('social_projects') or [],
            },
            'totals':rep.get('totals'),
        }
    except Exception as e:
        db=SessionLocal()
        try:
            db.execute(text("UPDATE owner_daily_report_delivery_slots SET status='error',error=:e,updated_at=now() WHERE report_date=:d AND slot=:s"),{'e':repr(e)[:500],'d':report_date,'s':slot}); db.commit()
        finally: db.close()
        return {'status':'error','report_date':report_date.isoformat(),'slot':slot,'error':type(e).__name__}


@router.get("/owner_daily/period")
def owner_daily_period(days: int = 30, account_id: str = "", user=Depends(get_current_user)):
    if not is_private_platform_owner(user): raise HTTPException(status_code=403,detail="Только для владельца платформы")
    return render_owner_period_report(days=days, account_id=account_id)
