# -*- coding: utf-8 -*-
"""Детерминированный поиск отказов во входящих сообщениях.
Независим от модели: проверяет её вывод, а не заменяет его.

ДВА РАЗНЫХ ВЕСА:
  HARD — явный запрет писать. Только он может привести к do_not_contact.
  WARN — «уже купил», «передумал» и подобное. НИКОГДА не блокирует кандидата,
         показывается человеку как предупреждение. Проверка 04.08 показала,
         что широкие шаблоны давали только ложные срабатывания: «Авито доставки
         пока нет», «Нет мне нужно просто поздравление», «нет необходимости в дверях»."""
import re

from sqlalchemy import text

# Явный запрет дальнейшего общения. Формулировки однозначные.
HARD = [
    (r"не\s+пишите", "не пишите"),
    (r"больше\s+не\s+(пиш|беспоко|звон)", "больше не пишите"),
    (r"прекрат\w*\s+(пис|звон|беспоко)", "прекратите писать"),
    (r"не\s+беспоко", "не беспокойте"),
    (r"удалит\w*\s+(мой\s+)?(номер|телефон|контакт)", "удалите номер"),
    (r"отстань|отвяж", "отстаньте"),
    (r"пожалуюсь|жалоб\w*\s+в\s+авито|заблокир", "угроза жалобы или блокировки"),
    (r"это\s+спам|спам\w*\s+рассылк|хватит\s+спам", "назвал спамом"),
]

# Признаки закрытия или отказа сейчас. Только предупреждение человеку.
WARN = [
    (r"уже\s+(куп|приобре|заказ|взял|нашл)", "уже купил или нашёл"),
    (r"(куп|наш)\w*\s+в\s+другом\s+месте", "купил в другом месте"),
    (r"наш[её]?л\w*\s+(друг\w+\s+)?(исполнител|поставщик|мастер|дешевл)", "нашёл другого"),
    (r"передума", "передумал"),
    (r"(уже\s+)?не\s*актуальн", "не актуально"),
    (r"больше\s+не\s+интересу|не\s+интересует\s+больше", "не интересует"),
]


def classify(body):
    """Возвращает (класс, метка) или (None, None). Класс: do_not_contact | warning."""
    s = re.sub(r"\s+", " ", (body or "")).lower().replace("ё", "е")
    for rx, label in HARD:
        if re.search(rx.replace("ё", "е"), s):
            return "do_not_contact", label
    for rx, label in WARN:
        if re.search(rx.replace("ё", "е"), s):
            return "warning", label
    return None, None


def find_refusals(db, account_id, avito_chat_id, limit=60):
    """Ищет только во ВХОДЯЩИХ непустых сообщениях этого диалога."""
    rows = db.execute(text(
        "SELECT id, to_timestamp(avito_created_at) AS ts, coalesce(text,'')"
        " FROM messenger_messages WHERE account_id=:a AND avito_chat_id=:c"
        "   AND lower(direction) IN ('in','incoming') AND coalesce(msg_type,'') <> 'system'"
        "   AND btrim(coalesce(text,'')) <> ''"
        " ORDER BY avito_created_at DESC, id DESC LIMIT :n"),
        {"a": account_id, "c": avito_chat_id, "n": limit}).fetchall()
    found = []
    for mid, ts, body in rows:
        kind, label = classify(body)
        if kind:
            found.append({"message_id": mid, "at": ts, "kind": kind, "label": label,
                          "snippet": re.sub(r"\s+", " ", body).strip()[:120]})
    return list(reversed(found))


def strongest(found):
    """Блокировать может ТОЛЬКО явный запрет. Остальное — предупреждение."""
    if any(f["kind"] == "do_not_contact" for f in found):
        return "do_not_contact"
    if found:
        return "warning"
    return None
