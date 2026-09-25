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


def _owner_report_schema(db):
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.commit()


def _active_owner_accounts(db):
    """Production client accounts only. QA/test fixtures are never owner-report clients.

    Billing is authoritative for a renewed paid tariff. `account_slots.paid_until`
    is an operational slot expiry and can lag behind a real subscription renewal;
    using it alone silently dropped active clients from owner reports.
    Legacy connected slots with no expiry remain reportable for compatibility.
    """
    return db.execute(text("""
        SELECT DISTINCT a.account_id, COALESCE(NULLIF(a.name,''), s.account_name, a.account_id) account_name,
               COALESCE(s.owner_user_id, a.owner_user_id) owner_user_id
          FROM account_slots s
          JOIN accounts a ON a.account_id=s.account_id
          LEFT JOIN LATERAL (
              SELECT CASE
                       WHEN (st.value::jsonb->>'tier') IN ('trial','tariff_1','tariff_2')
                        AND COALESCE(NULLIF(st.value::jsonb->>'paid_until','')::timestamptz,
                                     NULLIF(st.value::jsonb->>'period_end','')::timestamptz) > now()
                       THEN true ELSE false
                     END AS billing_active
                FROM storage st
               WHERE st.account_id=s.account_id AND st.key='billing'
               ORDER BY st.id DESC LIMIT 1
          ) bill ON true
         WHERE s.status='connected'
           AND s.account_id IS NOT NULL
           AND (s.paid_until IS NULL OR s.paid_until > now() OR COALESCE(bill.billing_active,false))
           AND a.avito_client_id IS NOT NULL AND a.avito_client_secret IS NOT NULL
           AND NOT EXISTS (
               SELECT 1 FROM reliability_kill_switches r
                WHERE r.account_id=a.account_id AND r.blocked=true
                  AND r.module IN ('actions','background','reports')
           )
           AND lower(a.account_id) NOT LIKE 'qa%%'
           AND lower(COALESCE(a.name,'')) NOT LIKE 'qa%%'
         ORDER BY account_name
    """)).mappings().all()


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


def _daily_avito_snapshot(db, account_id: str, report_date):
    raw = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key=:k ORDER BY id DESC LIMIT 1"),
                     {"a": account_id, "k": f"daily_stats:{report_date.isoformat()}"}).scalar()
    data = _safe_json(raw)
    if not data:
        return {"status": "no_data", "views": None, "contacts": None, "view_to_contact_pct": None,
                "active_items": None, "balance_rub": None, "stats_date": None,
                "spending_status": "no_data", "ad_spend_rub": None, "all_spend_rub": None,
                "spending_date": None, "spending_breakdown": {}}
    spending = data.get("spending") if isinstance(data.get("spending"), dict) else {}
    spending_status = str(spending.get("status") or "no_data")
    spending_date = str(spending.get("date") or "") or None
    try: ad_spend_rub = float(spending.get("ad_spend_rub")) if spending.get("ad_spend_rub") is not None else None
    except Exception: ad_spend_rub = None
    try: all_spend_rub = float(spending.get("all_spend_rub")) if spending.get("all_spend_rub") is not None else None
    except Exception: all_spend_rub = None
    spending_breakdown = spending.get("breakdown") if isinstance(spending.get("breakdown"), dict) else {}
    stats_date = str(data.get("stats_date") or data.get("date") or "")
    # Нельзя выдавать статистику соседнего дня за факт отчётной даты.
    # Некоторые Avito-ответы при лаге источника сохраняются под сегодняшним ключом,
    # но явно несут stats_date предыдущего дня. Такой snapshot считаем stale.
    if stats_date and stats_date != report_date.isoformat():
        return {"status": "stale", "views": None, "contacts": None, "view_to_contact_pct": None,
                "active_items": None, "balance_rub": None, "stats_date": stats_date,
                "spending_status": spending_status, "ad_spend_rub": ad_spend_rub,
                "all_spend_rub": all_spend_rub, "spending_date": spending_date,
                "spending_breakdown": spending_breakdown}
    items = data.get("items") if isinstance(data.get("items"), list) else []
    views = sum(int(x.get("views") or 0) for x in items if isinstance(x, dict))
    contacts = sum(int(x.get("contacts") or 0) for x in items if isinstance(x, dict))
    active_items = sum(1 for x in items if isinstance(x, dict) and str(x.get("status") or "").lower() == "active")
    balance = data.get("balance") if isinstance(data.get("balance"), dict) else {}
    return {"status": "ok", "views": views, "contacts": contacts,
            "view_to_contact_pct": round(contacts * 100.0 / views, 2) if views else (0.0 if contacts == 0 and views == 0 else None),
            "active_items": active_items,
            "balance_rub": float(balance.get("real")) if balance.get("real") is not None else None,
            "stats_date": stats_date or report_date.isoformat(),
            "spending_status": spending_status, "ad_spend_rub": ad_spend_rub,
            "all_spend_rub": all_spend_rub, "spending_date": spending_date,
            "spending_breakdown": spending_breakdown}


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
    return {"target_leads": target, "actual_leads": actual, "completion_pct": completion,
            "target_cpl": max_cpl, "actual_cpl": actual_cpl,
            "actual_spend_rub": actual_spend, "actual_spend_source": spend_source,
            "actual_leads_source": actual_source, "actual_leads_complete": actual_complete}


def _account_recommendations(kpi: dict, avito: dict, messages: dict, qual: dict, calls: dict):
    """Deterministic per-account actions from real daily facts; no cross-account KPI."""
    actions = []
    target = kpi.get('target_leads')
    actual = kpi.get('actual_leads')
    completion = kpi.get('completion_pct')
    target_cpl = kpi.get('target_cpl')
    actual_cpl = kpi.get('actual_cpl')
    views = avito.get('views')
    contacts = avito.get('contacts')
    conv = avito.get('view_to_contact_pct')
    dialogs = int(messages.get('dialogs') or 0)
    replied = int(messages.get('replied_dialogs') or 0)
    advanced = int(messages.get('advanced') or 0)
    missed = int(calls.get('calls_missed') or 0)
    hot = int(((qual.get('temperatures') or {}).get('hot')) or 0)
    warm = int(((qual.get('temperatures') or {}).get('warm')) or 0)
    unclassified = int(qual.get('unclassified') or 0)

    if target is None:
        actions.append('Нужно ваше решение: задать персональный план лидов в день. Без бизнес-цели BORIS не может честно оценить выполнение плана этого аккаунта.')
    elif actual is None:
        actions.append('BORIS восстанавливает источник факта лидов: пока факт не подтверждён, система не делает вывод о выполнении KPI.')
    elif completion is not None and completion < 100:
        gap = max(int(round(float(target) - float(actual))), 0)
        actions.append(f'Отставание от дневного плана: {gap}. BORIS увеличивает поток. Целевой уровень: {target:g} {_lead_word(target)} в день.')
        if views is not None and views < 30:
            actions.append('BORIS увеличивает охват: проверяет активность объявлений, позиции, продвижение, ставки, географию и количество работающих объявлений.')
        elif conv is not None and conv < 2:
            actions.append(f'BORIS улучшает конверсию просмотров в контакт: сейчас {conv:g}%. Проверяются первое фото, заголовок, цена/оффер, УТП, призыв к действию и качество трафика.')
        elif contacts is not None and contacts > 0:
            actions.append('Контакты уже идут. BORIS масштабирует лучшие объявления и связки только при сохранении допустимого CPL.')
    elif completion is not None and completion >= 100:
        actions.append('План по лидам выполнен. BORIS сохраняет рабочую связку и масштабирует её только при контроле CPL и качества лидов.')

    if target_cpl is not None and actual_cpl is not None and actual_cpl > target_cpl:
        actions.append(f'CPL выше цели: {actual_cpl:g} ₽ при лимите {target_cpl:g} ₽. BORIS не наращивает слабые объявления и перераспределяет расход в объявления с лучшей конверсией.')
    elif target_cpl is not None and actual_cpl is not None and actual_cpl <= target_cpl and completion is not None and completion < 100:
        actions.append(f'CPL {actual_cpl:g} ₽ укладывается в цель {target_cpl:g} ₽. BORIS может активнее продвигать рабочие объявления в пределах бюджетных ограничений.')

    if dialogs and replied < dialogs:
        actions.append(f'Диалогов без ответа: {dialogs-replied}. BORIS закрывает пропуски и контролирует скорость реакции МОПа.')
    if dialogs and advanced < dialogs:
        actions.append(f'Диалогов без следующего шага: {dialogs-advanced}. BORIS усиливает квалификацию и фиксирует следующее действие.')
    if hot or warm:
        actions.append(f'Горячих лидов: {hot}; тёплых: {warm}. BORIS приоритетно ведёт их к звонку, замеру, расчёту или сделке.')
    if unclassified:
        actions.append(f'Входящих диалогов без классификации: {unclassified}. BORIS определяет температуру и следующий шаг.')
    if missed:
        actions.append(f'Пропущено звонков: {missed}. BORIS ставит быстрый обратный звонок и контролирует создание CRM-карточки/задачи.')
    if avito.get('status') == 'stale':
        actions.append(f'Статистика Avito устарела ({avito.get("stats_date") or "другая дата"}). BORIS сначала обновляет данные и не принимает рекламные решения по старым цифрам.')
    return actions[:7]


def _marketer_actions_snapshot(db, account_id: str, report_date):
    """Полная account-scoped память действий AI-маркетолога за бизнес-сутки MSK."""
    start, end, _, _ = _day_bounds(report_date)
    rows = db.execute(text("""
        SELECT ts,actor,source,action,object_kind,object_name,before_val,after_val,reason,guard_reason
        FROM action_log
        WHERE account_id=:a AND ts>=:s AND ts<:e
          AND (actor IN ('boris_auto','system','boris')
               OR source LIKE 'cpx_%' OR source LIKE 'boris_kpi%')
        ORDER BY ts ASC,id ASC
    """), {"a":account_id,"s":start,"e":end}).mappings().all()
    actions=[]
    for r in rows:
        actions.append({k:(v.isoformat() if k=='ts' and v else v) for k,v in dict(r).items()})
    bid=[x for x in actions if 'ставк' in str(x.get('action') or '').lower()]
    return {"count":len(actions),"bid_changes":len(bid),"unique_bid_items":len(set(str(x.get('object_name') or '') for x in bid)),"actions":actions}


def build_owner_account_snapshot(db, account_id: str, account_name: str, owner_user_id, report_date=None):
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
    recommendations = _account_recommendations(kpi, avito, messages, qual, calls)
    marketer = _marketer_actions_snapshot(db, account_id, report_date)
    return {"report_date": report_date.isoformat(), "account_id": account_id, "account_name": account_name,
            "owner_user_id": owner_user_id, "kpi": kpi, "avito": avito, "messages": messages,
            "qualification": qual, "calls_crm": calls, "rop_quality": rop_quality, "recommendations": recommendations,
            "ai_marketer": marketer,
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
                payload = build_owner_account_snapshot(db, row['account_id'], row['account_name'], row['owner_user_id'], report_date)
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
        rows = db.execute(text("SELECT payload_json FROM owner_daily_report_snapshots WHERE report_date=:d ORDER BY account_name"), {"d": report_date}).scalars().all()
        payloads = [r if isinstance(r, dict) else _safe_json(r) for r in rows]
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
        if _t is None or _a is None:
            unknown_names.append(_name)
        elif float(_a) >= float(_t):
            ok_names.append(_name)
        else:
            gap_names.append(_name)
    _generated = sorted(str(p.get('generated_at') or '') for p in payloads if p.get('generated_at'))
    _cut = (_generated[-1][11:16] + ' МСК') if _generated and len(_generated[-1]) >= 16 else 'время не записано'
    lines = [f"📊 <b>BORIS · отчёты по аккаунтам · {report_date.strftime('%d.%m.%Y')}</b>",
             f"Срез данных: <b>{_cut}</b>.",
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
        elif float(_actual) >= float(_target):
            account_result=f"✅ План выполнен: факт {_fmt_num(_actual)} · план {_fmt_num(_target)} {_lead_word(_target)}"
        else:
            _gap=max(int(round(float(_target)-float(_actual))),0)
            account_result=f"🔴 Ниже плана: факт {_fmt_num(_actual)} · план {_fmt_num(_target)} {_lead_word(_target)} · отставание {_gap}"
        kpi_line = f"План/факт лидов: {_fmt_num(_target)} / {_fmt_num(_actual)}"
        if k.get('completion_pct') is not None: kpi_line += f" · выполнено {k['completion_pct']}%"
        target_cpl = k.get('target_cpl')
        actual_cpl = k.get('actual_cpl')
        actual_leads = k.get('actual_leads')
        target_cpl_text = _fmt_num(target_cpl, ' ₽') if target_cpl is not None else 'не задана'
        if actual_cpl is not None:
            actual_cpl_text = _fmt_num(actual_cpl, ' ₽')
        elif actual_leads == 0:
            actual_cpl_text = '— лидов нет'
        else:
            actual_cpl_text = '— нет полного расхода за день'
        cpl_line = f"CPL (стоимость лида): цель {target_cpl_text} · факт {actual_cpl_text}"
        if k.get('actual_spend_rub') is not None:
            cpl_line += f" · расход Avito всего {_fmt_num(k.get('actual_spend_rub'), ' ₽')}"
        # Owner-facing money safety: show whether autonomous raises are allowed
        # under the same daily-budget contract used by the live marketer.
        budget_limit = None
        try:
            from app.models.storage import Storage as _BudgetStorage
            _bdb = SessionLocal()
            _br = _bdb.query(_BudgetStorage).filter(_BudgetStorage.account_id==p.get('account_id'), _BudgetStorage.key=='kpi_settings').first()
            _bk = json.loads(_br.value or '{}') if _br else {}
            budget_limit = float(_bk.get('daily_budget_limit_rub') or 0)
        except Exception:
            budget_limit = None
        finally:
            try: _bdb.close()
            except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=1054)
        if budget_limit and k.get('actual_spend_rub') is not None:
            _sp=float(k.get('actual_spend_rub') or 0)
            if _sp >= budget_limit:
                cpl_line += f" · ⛔ дневной лимит {budget_limit:g} ₽ превышен, повышение ставок остановлено"
            elif _sp >= budget_limit*0.90:
                cpl_line += f" · 🛡️ достигнуто 90% лимита {budget_limit:g} ₽, повышение ставок остановлено"
        conv = a.get('view_to_contact_pct')
        avito_line = f"Avito: просмотры {_fmt_num(a.get('views'))} → контакты {_fmt_num(a.get('contacts'))} · конверсия {_fmt_num(conv,'%')}"
        if a.get('status') == 'stale':
            avito_line = f"Avito: нет точных данных за день · источник вернул статистику за {a.get('stats_date') or 'другую дату'}"
        q_unclassified = int(q.get('unclassified') or 0)
        lines += ["", f"<b>{p.get('account_name')}</b>",
                  account_result,
                  kpi_line, cpl_line,
                  avito_line,
                  f"МОП: диалогов {m.get('dialogs',0)} · входящих сообщений {m.get('incoming_messages',0)} · отвечено диалогов {m.get('replied_dialogs',0)} · дальше по воронке {m.get('advanced',0)} · оценка {_fmt_num(m.get('mop_operational_score'),'/100')}",
                  f"Квалификация за день: 🔥 {temps.get('hot',0)} · 🟡 {temps.get('warm',0)} · ❄️ {temps.get('cold',0)} · не классифицировано {q_unclassified} · квалифицировано {stages.get('QUALIFIED',0)} · целевое действие {stages.get('TARGET_ACTION',0)}",
                  f"Звонки: {c.get('calls_total',0)} · отвечено {c.get('calls_answered',0)} · пропущено {c.get('calls_missed',0)} · CRM-карточек со звонков {c.get('crm_cards_from_calls',0)}",
                  f"CRM: диалогов со сделкой {m.get('crm_chats_with_deal',m.get('deals_created',0))} · успешных {m.get('won_deals',0)}",
                  f"РОП: звонков разобрано {rq.get('calls_analyzed',0)}/{rq.get('calls_total',0)} · переписок {rq.get('chats_analyzed',0)}/{rq.get('chats_total',0)} · средняя оценка {_fmt_num(rq.get('avg_score'),'/100')} · требуют внимания {rq.get('attention_low_score',0)}"]
        rq_recs=list(rq.get('recommendations') or [])[:3]
        if rq_recs:
            lines.append("<b>РОП рекомендует:</b>")
            lines.extend([f"• {x}" for x in rq_recs])
        mk=p.get('ai_marketer') or {}; mk_actions=mk.get('actions') or []
        lines.append(f"ИИ-маркетолог: действий {mk.get('count',0)} · изменений ставок {mk.get('bid_changes',0)} · объявлений со ставкой {mk.get('unique_bid_items',0)}")
        if mk_actions:
            lines.append("<b>Что конкретно сделал ИИ-маркетолог:</b>")
            # В Telegram не вываливаем сотни однотипных строк: показываем
            # последние 20 конкретных шагов, полный журнал остаётся в snapshot.
            visible = mk_actions[-20:]
            if len(mk_actions) > len(visible):
                lines.append(f"• Полный журнал: {len(mk_actions)} действий сохранено; ниже последние {len(visible)}.")
            for x in visible:
                when=str(x.get('ts') or '')[11:16]
                change=(f" · {x.get('before_val')} → {x.get('after_val')}" if (x.get('before_val') or x.get('after_val')) else '')
                why=(f" · причина: {x.get('reason')}" if x.get('reason') else '')
                lines.append(f"• {when} · {x.get('action') or 'действие'} · {x.get('object_name') or x.get('object_kind') or 'аккаунт'}{change}{why}")
        # Recommendations are deterministic from the stored daily facts. Rebuild
        # them at render time so old snapshots do not keep obsolete machine-like
        # phrasing after the owner-report contract is improved.
        recs = _account_recommendations(k, a, m, q, c)
        if recs:
            lines.append("<b>Следующие действия:</b>")
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
            payloads=[build_owner_account_snapshot(db, r['account_id'], r['account_name'], r['owner_user_id'], report_date) for r in accounts]
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
    action=(
        f"⚠️ Действие владельца требуется: открыто P1-инцидентов {len(incidents)}"
        if incidents else "✅ Действие владельца: не требуется"
    )
    text_lines=["🧭 <b>Автоконтроль BORIS</b>",email_line]
    if email_quality_line:
        text_lines.append(email_quality_line)
    text_lines.extend([*social_lines,action])
    return {
        'text':"\n".join(text_lines),
        'email':primary,
        'social':social_projects[0] if social_projects else {},
        'social_projects':social_projects,
        'owner_incidents':len(incidents),
        'owner_action_required':bool(incidents),
    }


def _owner_source_refresh_warning(source_refresh=None):
    source_refresh = source_refresh or {}
    if source_refresh.get('status') != 'degraded':
        return ''
    count = int(source_refresh.get('warning_count') or len(source_refresh.get('errors') or []))
    return (
        f"⚠️ <b>Avito временно ограничил обновление по {count} аккаунт(ам).</b> "
        "В отчёте использованы последние сохранённые данные; "
        "BORIS повторит обновление автоматически."
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
        chunks=_telegram_chunks(body)
        for chunk in chunks: send_telegram_message(OWNER_REPORT_CHAT_ID, chunk, thread_id=OWNER_REPORT_THREAD_ID)
        db=SessionLocal()
        try:
            db.execute(text("UPDATE owner_daily_report_delivery_slots SET status='ok',message_count=:n,sent_at=now(),updated_at=now() WHERE report_date=:d AND slot=:s"),{'n':len(chunks),'d':report_date,'s':slot}); db.commit()
        finally: db.close()
        return {
            'status':'ok','report_date':report_date.isoformat(),'slot':slot,
            'sent':True,'message_count':len(chunks),'snapshot':snap,
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
