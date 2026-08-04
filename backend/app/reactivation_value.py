# -*- coding: utf-8 -*-
"""Денежные величины диалога: сумма + назначение + уверенность + источник.

Правило продукта: пользователю показываем ТОЛЬКО confidence=high.
Лучше «цена не определена», чем выдуманная цифра — один показанный ИНН
вместо цены убивает доверие ко всем суммам в системе.

Регулярки здесь — быстрый разбор. Смысловое извлечение (что именно цена,
что предоплата, что доставка) в перспективе уходит к модели, эта структура
рассчитана на такую замену без переделки потребителей."""
import re

from sqlalchemy import text

CURRENCY = "RUB"
# Выше этой суммы в наших нишах цена не встречается — почти наверняка реквизит
# или страховая ответственность. Не выбрасываем, но понижаем до low.
MAX_PLAUSIBLE = 3000000

NUM_RE = re.compile(
    r"(?<![\d,.])(\d{1,3}(?:[ \u00a0]\d{3})+|\d{4,9}|\d{1,4})"
    r"\s*(тыс\w*|тр\b|т\.?\s?р\.?|₽|руб\w*)?", re.I)

# Реквизиты и контакты — приоритет выше любого поиска цены.
REQ_RE = re.compile(
    r"\bинн\b|\bкпп\b|\bбик\b|\bогрн\b|р/с|к/с|расч[её]тн|\bсч[её]т\b|\bкарт[аы]\b|"
    r"@|\.ru\b|\.com\b|\bтел\b|телефон|whatsapp|вайбер|viber", re.I)
# Не деньги по смыслу.
NOTMONEY_RE = re.compile(
    r"\d\s*[xх*]\s*\d|\bмм\b|\bсм\b|\bм2\b|\bкв\b|\bгод\b|\b20\d\d\s*г|"
    r"\bлет\b|\bдн\w*\b|\bчас\w*\b|ответственност|страхов|ущерб|штраф", re.I)
PHONE_RE = re.compile(r"\+?\d[\d\-\(\) ]{9,}")

KINDS = [
    ("deposit", r"залог|депозит|предоплат|п/оплат|аванс|задаток"),
    ("delivery", r"доставк|привоз|вывоз|подач"),
    ("installation", r"монтаж|установк|сборк"),
    ("rent", r"аренд|в месяц|помесячн|за сутки|в сутки"),
    ("price", r"цена|цену|стоимост|стоит|стоить|будет|выходит|итого|прайс|покупка|продаж|расч[её]т|наличн|б/н|безнал|с ндс|под ключ|вы[йи]дет|обойд[её]тся|скажем"),
]


def _amount(num, suf):
    """Тысячный множитель применяем ТОЛЬКО к числам меньше 1000.
    В прайсе бытовок написано «Аренда в месяц - 10 000 т.р.» — суффикс поставлен
    ошибочно к уже полной сумме, и умножение давало 10 000 000 вместо 10 000."""
    n = int(re.sub(r"\D", "", num))
    if suf and re.match(r"тыс|тр|т\.?\s?р", suf, re.I) and n < 1000:
        n *= 1000
    return n


def extract_amounts(body):
    """Возвращает список словарей: amount, currency, confidence, source, quote."""
    out = []
    s = re.sub(r"\s+", " ", body or "")
    for m in NUM_RE.finditer(s):
        num, suf = m.group(1), (m.group(2) or "")
        left = s[max(0, m.start() - 40): m.start()]
        right = s[m.end(): m.end() + 25]
        around = left + " " + m.group(0) + " " + right
        quote = s[max(0, m.start() - 35): m.end() + 15].strip()
        amount = _amount(num, suf)

        # 1. Реквизиты, почта, телефон — вообще не деньги.
        if REQ_RE.search(around) or (m.start() and s[m.start() - 1].isalpha()):
            out.append({"amount": amount, "currency": CURRENCY, "confidence": "low",
                        "source": "requisites", "quote": quote})
            continue
        if PHONE_RE.search(m.group(0)) or len(re.sub(r"\D", "", num)) > 9:
            out.append({"amount": amount, "currency": CURRENCY, "confidence": "low",
                        "source": "identifier", "quote": quote})
            continue
        # Явная денежная единица перебивает подозрения: «13 000 руб, 1 час»
        # это всё-таки цена. Размеры вида 1200х2100 отсекаются всегда.
        if re.search(r"\d\s*[xх*]\s*\d", around) or (NOTMONEY_RE.search(around) and not suf):
            out.append({"amount": amount, "currency": CURRENCY, "confidence": "low",
                        "source": "not_money", "quote": quote})
            continue
        if amount < 1000:
            continue

        # 2. Назначение суммы — по БЛИЖАЙШЕМУ ключевому слову, а не по порядку
        # списка: во фразе «аренда 7 000 в месяц, залог 10 000» слово «залог»
        # не должно перекрашивать сумму аренды.
        kind, best_dist = None, 999
        for name, rx in KINDS:
            for mm in re.finditer(rx, left, re.I):
                d = len(left) - mm.end()
                if d < best_dist and d <= 20:
                    kind, best_dist = name, d
            mm = re.search(rx, right, re.I)
            if mm and mm.start() < best_dist and mm.start() <= 10:
                kind, best_dist = name, mm.start()

        # 3. Уверенность.
        if amount > MAX_PLAUSIBLE:
            conf, source = "low", "implausible"
        elif suf and kind:
            conf, source = "high", kind
        elif suf or kind:
            conf, source = "high", kind or "price"
        else:
            conf, source = "medium", "bare_number"
        out.append({"amount": amount, "currency": CURRENCY, "confidence": conf,
                    "source": source, "quote": quote})
    return out


def dialog_money(db, account_id, avito_chat_id):
    """Суммы диалога по назначению. Складывать их между собой НЕЛЬЗЯ."""
    rows = db.execute(text(
        "SELECT id, direction, coalesce(text,'') FROM messenger_messages"
        " WHERE account_id=:a AND avito_chat_id=:c AND coalesce(msg_type,'') <> 'system'"
        "   AND btrim(coalesce(text,'')) <> ''"
        " ORDER BY avito_created_at, id"), {"a": account_id, "c": avito_chat_id}).fetchall()
    found, rejected = [], []
    for mid, direction, body in rows:
        for a in extract_amounts(body):
            a["message_id"] = mid
            a["from"] = "продавец" if str(direction).lower().startswith("out") else "клиент"
            (found if a["confidence"] == "high" else rejected).append(a)

    def best(kind):
        sel = [a for a in found if a["source"] == kind]
        return max(sel, key=lambda z: z["amount"]) if sel else None

    price = best("price") or best("rent")
    res = {"price": price, "deposit": best("deposit"), "delivery": best("delivery"),
           "installation": best("installation"), "rent": best("rent"),
           "all_high": found, "rejected": rejected}
    res["display"] = price["amount"] if price else None
    return res


def dialog_value(db, account_id, avito_chat_id):
    """Совместимость со старым qa_react_queue.py: та же новая логика,
    разложенная по прежним ключам. Показываем только confidence=high."""
    m = dialog_money(db, account_id, avito_chat_id)
    price = m.get("price")
    return {"max_price": price,
            "prepay": m.get("deposit"),
            "last_price": m.get("rent") or m.get("delivery") or price,
            "value": m.get("display"),
            "rejected": m.get("rejected", [])}
