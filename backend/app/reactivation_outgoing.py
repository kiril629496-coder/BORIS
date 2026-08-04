# -*- coding: utf-8 -*-
"""Роль владельца в диалоге и тип последнего исходящего сообщения.
Всё детерминированно, без обращения к модели.

Зачем: нельзя реактивировать чат, где владелец аккаунта сам был покупателем,
и нельзя писать поверх собственной рассылки, теста или прошлого касания."""
import logging
import re

from sqlalchemy import text

log = logging.getLogger(__name__)

ROLE_SOURCES = ("item_owner_id", "owned_item", "foreign_item", "first_meaningful_message",
                "conflict_owner_vs_item", "conflict_owned_and_foreign", "insufficient_data")

TYPES = ("normal_reply", "seller_followup", "template_reply", "marketing_campaign",
         "previous_reactivation", "test_message", "unknown")

# Тип, после которого новое касание автоматически недопустимо.
COOLDOWN_TYPES = ("marketing_campaign", "previous_reactivation", "test_message")

TEST_RE = re.compile(
    r"(^|\s)(тест|test|проверка\s+связи|проверка\s+отправки|qa[_\- ]?test|"
    r"тест\s*отправки|ping)\b", re.I)

MARKETING_RE = [
    (r"только\s+до\s+\d", "срок акции"),
    (r"\bакци[яию]\b|акционн", "слово акция"),
    (r"скидк\w*\s*\d+\s*%|\d+\s*%\s*скидк", "скидка в процентах"),
    (r"\d[\d\s]{2,}\s*₽\s*вместо|вместо\s+\d[\d\s]{2,}\s*₽", "цена вместо цены"),
    (r"успей|успевайте|не\s+упустите|последний\s+день", "призыв поспешить"),
    (r"🔥|🎉|🎁|💥", "рекламные эмодзи"),
    (r"что\s+входит:\s*\n?\s*[•\-\*]", "маркированный оффер"),
]

# Короткое подталкивание без нового содержания. Список закрытый: короткий, но
# предметный вопрос («Куда нужна доставка?») — это нормальный ответ, а не подталкивание.
FOLLOWUP_RE = re.compile(
    r"^\s*(\?+|ау|алло|добрый\s+день|доброе\s+утро|добрый\s+вечер|здравствуйте|привет|"
    r"вы\s+тут|вы\s+здесь|ответьте|актуально(\s+ещ[её])?|ещ[её]\s+актуально|"
    r"напомню|напоминаю|подскажите|ок|окей|хорошо|да|нет|понял|поняла|спасибо|"
    r"ждём|ждем|жду\s+ответа)\s*[.,!?)]*\s*$", re.I)

DUP_CHATS_FOR_CAMPAIGN = 3
DUP_WINDOW_DAYS = 45


def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _accounts_uid_column(db):
    row = db.execute(text(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_name='accounts' AND column_name ~ 'avito.*user|user.*id'"
        " ORDER BY column_name LIMIT 1")).fetchone()
    return row[0] if row else None


def own_avito_uid(db, account_id):
    """Сначала accounts, затем account_slots. Без uid роль диалога определить нельзя,
    поэтому пустой результат — это повод заполнить данные, а не блокировать аккаунт."""
    col = _accounts_uid_column(db)
    if col:
        row = db.execute(text(
            "SELECT %s FROM accounts WHERE account_id=:a" % col), {"a": account_id}).fetchone()
        if row and row[0]:
            return str(row[0])
    try:
        row = db.execute(text(
            "SELECT avito_user_id FROM account_slots WHERE account_id=:a"
            "   AND avito_user_id IS NOT NULL LIMIT 1"), {"a": account_id}).fetchone()
        if row and row[0]:
            return str(row[0])
    except Exception:
        pass
    return None


def _first_meaningful(db, account_id, avito_chat_id):
    """Первое содержательное сообщение диалога: без системных, пустых и тестовых."""
    rows = db.execute(text(
        "SELECT id, direction, avito_created_at, coalesce(text,'')"
        " FROM messenger_messages WHERE account_id=:a AND avito_chat_id=:c"
        "   AND coalesce(msg_type,'') <> 'system'"
        "   AND btrim(coalesce(text,'')) <> ''"
        " ORDER BY avito_created_at, id LIMIT 12"),
        {"a": account_id, "c": avito_chat_id}).fetchall()
    for mid, direction, created, body in rows:
        if TEST_RE.search(body or ""):
            continue
        return {"message_id": mid, "direction": str(direction or "").lower(),
                "avito_created_at": created, "text": _norm(body)[:80]}
    return None


def account_item_sets(db, account_id, own_uid=None):
    """Объявления, принадлежность которых аккаунту установлена достоверно.
    Считается один раз на аккаунт и передаётся в dialog_role_full."""
    uid = own_uid if own_uid is not None else own_avito_uid(db, account_id)
    owned, foreign = set(), set()

    # 1. Активные объявления аккаунта (кэш профиля).
    try:
        from app.reactivation_profile import profile_of
        ids, _ = profile_of(db, account_id)
        if ids:
            owned |= {str(x) for x in ids}
    except Exception as e:
        log.warning("роли: активные объявления %s недоступны: %s", account_id, e)

    # 2. Снятые и архивные, если Avito их отдаёт.
    owned |= _fetch_inactive_item_ids(account_id)

    # 3. Прежние сообщения с тем же item_id и заполненным владельцем.
    for item_id, owner in db.execute(text(
            "SELECT DISTINCT item_id, item_owner_id FROM messenger_messages"
            " WHERE account_id=:a AND item_id IS NOT NULL AND item_owner_id IS NOT NULL"),
            {"a": account_id}):
        if uid and str(owner) == str(uid):
            owned.add(str(item_id))
        elif uid:
            foreign.add(str(item_id))
    return owned, foreign


def _fetch_inactive_item_ids(account_id):
    """Снятые и архивные объявления. Если Avito не поддерживает статус — пусто."""
    out = set()
    try:
        from app.api.messenger import _get_user_id_and_token
        import httpx
        uid, tok = _get_user_id_and_token(account_id)
        for status in ("old", "removed"):
            page = 1
            while page <= 10:
                r = httpx.get("https://api.avito.ru/core/v1/items",
                              params={"per_page": 100, "page": page, "status": status},
                              headers={"Authorization": "Bearer %s" % tok}, timeout=40)
                if r.status_code != 200:
                    break
                batch = (r.json() or {}).get("resources") or []
                if not batch:
                    break
                out |= {str(x.get("id")) for x in batch if x.get("id")}
                page += 1
    except Exception as e:
        log.info("роли: неактивные объявления %s не получены: %s", account_id, e)
    return out


def dialog_role_full(db, account_id, avito_chat_id, own_uid=None, item_sets=None):
    """Лестница признаков. Всегда возвращает объяснение решения."""
    uid = own_uid if own_uid is not None else own_avito_uid(db, account_id)
    owned, foreign = item_sets if item_sets is not None else account_item_sets(db, account_id, uid)

    row = db.execute(text(
        "SELECT DISTINCT item_id, item_owner_id FROM messenger_messages"
        " WHERE account_id=:a AND avito_chat_id=:c AND item_id IS NOT NULL"
        " LIMIT 1"), {"a": account_id, "c": avito_chat_id}).fetchone()
    item_id = str(row[0]) if row and row[0] else None
    owners = [str(r[0]) for r in db.execute(text(
        "SELECT DISTINCT item_owner_id FROM messenger_messages"
        " WHERE account_id=:a AND avito_chat_id=:c AND item_owner_id IS NOT NULL"),
        {"a": account_id, "c": avito_chat_id})]

    def out(role, source, conf, evidence):
        return {"dialog_role": role, "role_source": source, "role_confidence": conf,
                "role_evidence": evidence, "item_id": item_id,
                "item_owned": bool(item_id and item_id in owned),
                "item_foreign": bool(item_id and item_id in foreign)}

    # Уровень 1 — владелец объявления из самого сообщения.
    if uid and owners:
        role = "seller" if str(uid) in owners else "buyer"
        # Конфликт: владелец говорит одно, принадлежность объявления — другое.
        if role == "buyer" and item_id and item_id in owned:
            return out("unknown", "conflict_owner_vs_item", "low",
                       {"item_owner_id": owners, "account_uid": uid, "item_id": item_id})
        return out(role, "item_owner_id", "high",
                   {"item_owner_id": owners, "account_uid": uid})

    # Уровень 2 — принадлежность объявления аккаунту.
    if item_id and item_id in owned and item_id not in foreign:
        return out("seller", "owned_item", "high", {"item_id": item_id})
    if item_id and item_id in foreign and item_id not in owned:
        return out("buyer", "foreign_item", "high", {"item_id": item_id})
    if item_id and item_id in owned and item_id in foreign:
        return out("unknown", "conflict_owned_and_foreign", "low", {"item_id": item_id})

    # Уровень 3 — первое содержательное сообщение.
    first = _first_meaningful(db, account_id, avito_chat_id)
    if first:
        role = "seller" if first["direction"].startswith("in") else "buyer"
        return out(role, "first_meaningful_message", "medium", first)

    # Уровень 4 — данных нет.
    return out("unknown", "insufficient_data", "low", {})


def role_allows_auto(info):
    """Автоматически допускается только продавец с высокой уверенностью."""
    return info.get("dialog_role") == "seller" and info.get("role_confidence") == "high"


def dialog_role(db, account_id, avito_chat_id, own_uid=None, item_sets=None):
    """Совместимость со старым кодом: возвращает только строку роли."""
    return dialog_role_full(db, account_id, avito_chat_id, own_uid, item_sets)["dialog_role"]


def last_outgoing(db, account_id, avito_chat_id, skip_tests=False):
    rows = db.execute(text(
        "SELECT id, to_timestamp(avito_created_at) AS ts, coalesce(text,'')"
        " FROM messenger_messages WHERE account_id=:a AND avito_chat_id=:c"
        "   AND lower(direction) IN ('out','outgoing') AND coalesce(msg_type,'') <> 'system'"
        " ORDER BY avito_created_at DESC, id DESC LIMIT 10"),
        {"a": account_id, "c": avito_chat_id}).fetchall()
    for mid, ts, body in rows:
        if skip_tests and TEST_RE.search(body or ""):
            continue
        if not (body or "").strip():
            continue
        return {"id": mid, "at": ts, "text": body}
    return None


def _sent_to_many_chats(db, account_id, body):
    """Один и тот же текст в нескольких РАЗНЫХ чатах = рассылка."""
    n = db.execute(text(
        "SELECT count(DISTINCT avito_chat_id) FROM messenger_messages"
        " WHERE account_id=:a AND lower(direction) IN ('out','outgoing')"
        "   AND regexp_replace(lower(btrim(coalesce(text,''))), '\\s+', ' ', 'g') = :t"
        "   AND to_timestamp(avito_created_at) > now() - make_interval(days => :d)"),
        {"a": account_id, "t": _norm(body), "d": DUP_WINDOW_DAYS}).scalar()
    return int(n or 0)


def _is_ours(db, account_id, body):
    """Совпадение с нашей же реактивационной отправкой."""
    try:
        from app.reactivation_core import text_hash
    except Exception:
        return False
    h = text_hash(body)
    n = db.execute(text(
        "SELECT count(*) FROM reactivation_messages WHERE account_id=:a AND text_hash=:h"),
        {"a": account_id, "h": h}).scalar()
    return bool(n)


def classify_outgoing(db, account_id, avito_chat_id):
    """Возвращает словарь: тип последнего исходящего, доказательства, нужен ли cooldown."""
    last = last_outgoing(db, account_id, avito_chat_id)
    if not last:
        return {"last_outgoing_type": "unknown", "reason": "исходящих нет",
                "cooldown": False, "message_id": None}
    body = last["text"]
    res = {"message_id": last["id"], "at": last["at"], "snippet": _norm(body)[:110]}

    if TEST_RE.search(body):
        deep = last_outgoing(db, account_id, avito_chat_id, skip_tests=True)
        res.update({"last_outgoing_type": "test_message", "reason": "техническое сообщение",
                    "cooldown": True,
                    "meaningful_message_id": (deep or {}).get("id"),
                    "meaningful_snippet": _norm((deep or {}).get("text"))[:110]})
        return res

    if _is_ours(db, account_id, body):
        res.update({"last_outgoing_type": "previous_reactivation",
                    "reason": "совпало с нашей отправкой", "cooldown": True})
        return res

    chats = _sent_to_many_chats(db, account_id, body)
    res["chats_with_same_text"] = chats
    low = _norm(body)
    hits = [label for rx, label in MARKETING_RE if re.search(rx, body, re.I)]

    # Акция определяется СОДЕРЖАНИЕМ, а не повторяемостью. Повтор без оффера —
    # это шаблон менеджера (контакты, адрес базы, прайс), а не маркетинговое касание.
    if hits:
        res.update({"last_outgoing_type": "marketing_campaign",
                    "reason": "оффер: " + ", ".join(hits[:3])
                              + (" | текст в %d чатах" % chats if chats > 1 else ""),
                    "cooldown": True})
        return res

    if chats >= DUP_CHATS_FOR_CAMPAIGN:
        res.update({"last_outgoing_type": "template_reply",
                    "reason": "шаблонный текст без оффера, встречается в %d чатах" % chats,
                    "cooldown": False})
        return res

    if FOLLOWUP_RE.match(low):
        res.update({"last_outgoing_type": "seller_followup",
                    "reason": "короткое подталкивание без нового содержания",
                    "cooldown": False})
        return res

    res.update({"last_outgoing_type": "normal_reply",
                "reason": "предметный ответ", "cooldown": False})
    return res


def previous_reactivation_detected(info):
    return bool(info.get("last_outgoing_type") in COOLDOWN_TYPES)
