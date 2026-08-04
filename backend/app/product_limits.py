# -*- coding: utf-8 -*-
"""Единый сервис начисления лимитов продукта.
Вызывается ПОСЛЕ начисления самого пакета (add_manager_package и подобных),
период не создаёт и не продлевает — только читает уже установленный.
Работает в транзакции вызывающего: своего commit не делает."""
import json
import logging

from sqlalchemy import text

log = logging.getLogger(__name__)

# Что каждый пакет даёт сверх своего основного объёма.
# Базовые пакеты открывают период и дают лимит реактивации,
# докупки (addon) период не трогают и реактивацию не добавляют.
PRODUCT_LIMITS = {
    "msg1500": {"reactivation": 300},
    "msg1700": {"reactivation": 300},
    "msg2000": {},
    "msg3000": {},
}


def _billing_row(db, account_id):
    row = db.execute(text(
        "SELECT value FROM storage WHERE account_id=:a AND key='billing'"),
        {"a": account_id}).fetchone()
    if not row or not row[0]:
        return None
    val = row[0]
    if isinstance(val, dict):
        return val
    try:
        return json.loads(val)
    except Exception:
        return None


def grant_product_limits(db, account_id, pack_code):
    """Возвращает словарь результата. Никогда не бросает исключение наружу —
    начисление основного пакета не должно падать из-за дополнительных лимитов."""
    if pack_code not in PRODUCT_LIMITS:
        log.warning("реактивация: неизвестный пакет %r у аккаунта %s — лимит не начислен",
                    pack_code, account_id)
        return {"status": "unsupported_pack", "pack": pack_code, "account_id": account_id}

    extras = PRODUCT_LIMITS[pack_code]
    if not extras.get("reactivation"):
        return {"status": "no_reactivation_limit", "pack": pack_code}

    data = _billing_row(db, account_id)
    if data is None:
        log.error("реактивация: у аккаунта %s нет записи billing — лимит не начислен", account_id)
        return {"status": "no_billing", "pack": pack_code, "account_id": account_id}

    start, until = data.get("manager_period_start"), data.get("manager_paid_until")
    if not start or not until:
        log.error("реактивация: у аккаунта %s не установлен период пакета "
                  "(manager_period_start=%r, manager_paid_until=%r) — лимит не начислен",
                  account_id, start, until)
        return {"status": "no_period", "pack": pack_code, "account_id": account_id}

    qty = int(extras["reactivation"])
    data["reactivation_purchased"] = qty
    db.execute(text("UPDATE storage SET value=:v WHERE account_id=:a AND key='billing'"),
               {"v": json.dumps(data, ensure_ascii=False), "a": account_id})
    log.info("реактивация: аккаунту %s начислено %d касаний на период %s — %s",
             account_id, qty, start, until)
    return {"status": "granted", "pack": pack_code, "account_id": account_id,
            "reactivation": qty, "period_start": start, "paid_until": until}
