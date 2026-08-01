"""Ежедневный отчёт по необработанным диалогам. Без ИИ и без PDF — чистый SQL.
Автоотправки НЕТ: только ручной preview и тестовая отправка администратору.
Диалоги-покупки (мы сами покупатель) в отчёт не попадают.
"""
import datetime, os
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from app.db.session import SessionLocal
from app.api.inbox_slots import _connected_account_ids
try:
    from app.api.auth import get_current_user
except ImportError:
    from app.auth import get_current_user

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


def account_stats(db, account_id, user_id):
    day_ago = int((datetime.datetime.now(tz=MSK) - datetime.timedelta(days=1)).timestamp())
    p = {"a": account_id, "d": day_ago, "u": user_id}

    new_dialogs = db.execute(text(
        "SELECT count(*) FROM (SELECT avito_chat_id, min(avito_created_at) f FROM messenger_messages m "
        " WHERE account_id=:a" + _NOT_PURCHASE + " GROUP BY 1) t WHERE t.f >= :d"), p).scalar() or 0

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


def build_report(user):
    db = SessionLocal()
    try:
        conn = _connected_account_ids(db, user)
        if not conn:
            return {"status": "error", "message": "Нет подключённых аккаунтов"}
        parts, tot = [], {"new_dialogs": 0, "new_msgs": 0, "unread": 0, "unanswered": 0, "max_wait_h": 0.0}
        stats_all = []
        for acc_id, acc_name in conn:
            st = account_stats(db, acc_id, getattr(user, "id", None))
            st["account_name"] = acc_name
            stats_all.append(st)
            for k in ("new_dialogs", "new_msgs", "unread", "unanswered"):
                tot[k] += st[k]
            tot["max_wait_h"] = max(tot["max_wait_h"], st["max_wait_h"])

            block = [f"📌 <b>{acc_name}</b>",
                     f"   Новых диалогов за сутки: {st['new_dialogs']}",
                     f"   Новых сообщений: {st['new_msgs']}",
                     f"   Непрочитанных: {st['unread']}",
                     f"   Без ответа: {st['unanswered']}"]
            if st["unanswered"]:
                block.append(f"   Дольше всех ждёт: {_fmt_wait(st['max_wait_h'])}")
                for o in st["overdue"]:
                    block.append(f"      • {o['item'][:38]} — {_fmt_wait(o['wait_h'])}")
            parts.append("\n".join(block))

        head = (f"📊 <b>Сводка по сообщениям Avito</b>\n"
                f"{datetime.datetime.now(tz=MSK).strftime('%d.%m.%Y')}\n")
        foot = (f"\n<b>Итого по всем аккаунтам:</b>\n"
                f"   Новых диалогов: {tot['new_dialogs']} · новых сообщений: {tot['new_msgs']}\n"
                f"   Непрочитанных: {tot['unread']} · без ответа: {tot['unanswered']}\n")
        if tot["unanswered"]:
            foot += f"   Максимальное ожидание: {_fmt_wait(tot['max_wait_h'])}\n"
        else:
            foot += "   ✅ Все диалоги отвечены\n"
        foot += f"\n👉 Открыть единое окно: {INBOX_URL}"
        return {"status": "ok", "text": head + "\n" + "\n\n".join(parts) + "\n" + foot,
                "totals": tot, "accounts": stats_all}
    finally:
        db.close()


@router.get("/daily_report/preview")
def preview(user=Depends(get_current_user)):
    """Ручной предпросмотр. Ничего не отправляет."""
    return build_report(user)


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
