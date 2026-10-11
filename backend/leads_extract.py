# -*- coding: utf-8 -*-
"""Наполнение карточек клиентов (messenger_leads) из переписки.
Данные берутся ТОЛЬКО из сообщений покупателя. Холостой прогон по умолчанию."""
import json, os, sys, hashlib
sys.path.insert(0, "/root/BORIS/backend")
from sqlalchemy import text
from app.api.inbox_slots import SessionLocal

try:
    from app.api.calltracking import get_intl_requests_proxies
except Exception:
    def get_intl_requests_proxies():
        return None

MODEL = "gpt-5.4"
OPERATION = "карточка клиента"
FIELDS = ["company", "contact_name", "position", "object_place", "rent_term", "need_type", "cost_rub"]
MAXLEN = {}

def arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default

APPLY = "--apply" in sys.argv
ACCOUNT = arg("--account", "prodazha_bytovok_25677")
LIMIT = int(arg("--limit", "5"))

PROMPT = (
    "Ты разбираешь переписку продавца бытовок с покупателем на Авито и заполняешь карточку сделки.\n"
    "Верни ТОЛЬКО JSON без пояснений и без ```, строго с этими ключами:\n"
    '{"company":"", "contact_name":"", "position":"", "object_place":"",'
    ' "rent_term":"", "need_type":"", "cost_rub":""}\n\n'
    "ГЛАВНОЕ ПРАВИЛО: данные берутся ТОЛЬКО из реплик, помеченных 'Клиент:'.\n"
    "Строки 'Мы:' - это наши ответы и наш прайс, они НЕ являются данными о клиенте.\n"
    "Если сведений нет в репликах клиента - оставь пустую строку. Не додумывай.\n"
    "Пиши КОРОТКО, каждое поле не длиннее 100 символов, без перечисления всех деталей.\n\n"
    "company - организация клиента, если он сам её назвал\n"
    "contact_name - как клиент себя назвал\n"
    "position - его должность, если упомянул\n"
    "object_place - куда нужна бытовка: адрес, район, объект, город\n"
    "rent_term - срок аренды словами клиента: 'на месяц', 'до конца сентября'\n"
    "need_type - что именно нужно клиенту: размер, состояние, комплектация\n"
    "cost_rub - сумма, которую назвал или подтвердил САМ КЛИЕНТ: его бюджет либо явное\n"
    "  согласие на цену. Если цену называли только мы, а клиент её не подтвердил - ПУСТО.\n\n"
    "ПЕРЕПИСКА:\n"
)

def load_maxlen(db):
    for name, ln in db.execute(text(
        "SELECT column_name, character_maximum_length FROM information_schema.columns"
        " WHERE table_name = 'messenger_leads'")).fetchall():
        if ln:
            MAXLEN[name] = int(ln)
    print("длины колонок: %s" % {k: MAXLEN.get(k) for k in FIELDS if k in MAXLEN})

def dialog_text(db, chat_id):
    rows = db.execute(text(
        "SELECT direction, coalesce(text,'') FROM messenger_messages"
        " WHERE account_id = :a AND avito_chat_id = :c"
        " ORDER BY coalesce(avito_created_at, 0) ASC LIMIT 80"),
        {"a": ACCOUNT, "c": chat_id}).fetchall()
    out = []
    for d, t in rows:
        t = (t or "").strip()
        if not t:
            continue
        who = "Клиент" if (d or "") in ("in", "incoming") else "Мы"
        out.append("%s: %s" % (who, t[:600]))
    return "\n".join(out)[:9000]

def ask(prompt):
    """One durable paid intent through the canonical BORIS text contour."""
    try:
        from app.api.campaigns import _ff_guarded_openai_response
        digest=hashlib.sha256((str(ACCOUNT)+"|"+str(prompt)).encode("utf-8","ignore")).hexdigest()
        paid=_ff_guarded_openai_response(
            str(ACCOUNT), OPERATION, MODEL, str(prompt), 500,
            "lead-card:"+digest, timeout=120,
        )
        raw=str((paid or {}).get("text") or "").strip()
        raw=raw.replace("```json", "").replace("```", "").strip()
        if not raw:
            return None,"пустой ответ AI"
        return json.loads(raw),None
    except Exception as exc:
        return None,(type(exc).__name__+": "+str(exc))[:180]

def clean(v, key):
    v = ("" if v is None else str(v)).strip()
    if key == "cost_rub":
        d = "".join(c for c in v if c.isdigit())
        return int(d[:9]) if d else None
    lim = MAXLEN.get(key, 120)
    return v[:lim] or None

def main():
    db = SessionLocal()
    load_maxlen(db)
    leads = db.execute(text(
        "SELECT id, avito_chat_id, coalesce(item_title,''), coalesce(msg_count,0)"
        " FROM messenger_leads WHERE account_id = :a AND extracted_at IS NULL"
        " AND coalesce(msg_count,0) >= 2 ORDER BY last_msg_at DESC NULLS LAST"
        " LIMIT :n"), {"a": ACCOUNT, "n": LIMIT}).fetchall()
    print("аккаунт: %s   карточек к разбору: %d   режим: %s"
          % (ACCOUNT, len(leads), "ЗАПИСЬ" if APPLY else "холостой"))
    done = 0
    fail = 0
    for lid, chat, title, cnt in leads:
        body = dialog_text(db, chat)
        print("")
        print("--- карточка %s | %s | сообщений %s ---" % (lid, title[:50], cnt))
        if len(body) < 40:
            print("   пропуск: переписка пустая или слишком короткая")
            continue
        if not APPLY:
            print("   zero-paid dry-run: AI не вызывается; кандидат готов к явному --apply")
            continue
        res, err = ask(PROMPT + body)
        if err:
            print("   ОШИБКА: %s" % err)
            fail += 1
            continue
        vals = {k: clean(res.get(k), k) for k in FIELDS}
        for k in FIELDS:
            print("   %-13s %s" % (k, vals[k] if vals[k] is not None else "-"))
        sets = ", ".join("%s = :%s" % (k, k) for k in FIELDS)
        p = dict(vals)
        p["i"] = lid
        try:
            db.execute(text("UPDATE messenger_leads SET " + sets +
                            ", extracted_at = now(), updated_at = now() WHERE id = :i"), p)
            db.commit()
            done += 1
            print("   записано")
        except Exception as e:
            db.rollback()
            fail += 1
            print("   НЕ ЗАПИСАНО: %s" % repr(e)[:150])
    print("")
    print("записано: %d   не удалось: %d" % (done, fail))
    db.close()

main()
