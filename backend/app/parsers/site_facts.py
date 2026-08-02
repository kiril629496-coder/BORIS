"""Извлечение бизнес-фактов из уже собранных данных сайта.

Чистый модуль: не ходит в сеть, не пишет в БД, не вызывает модель,
не считает хеши и не определяет конфликты. Только извлечение
и нормализация кандидатов. Запись — задача ядра памяти.

Вход  : knowledge (из parse_site), products (оттуда же), base_url
Выход : (список кандидатов, статистика)
"""
import re

TYPE_MAP = {
    "phone":           ("kontakty",   "Телефон",             ""),
    "email":           ("kontakty",   "Email",               ""),
    "address":         ("kontakty",   "Адрес",               ""),
    "work_hours":      ("kontakty",   "График работы",       ""),
    "inn":             ("rekvizity",  "ИНН",                 ""),
    "ogrn":            ("rekvizity",  "ОГРН",                ""),
    "kpp":             ("rekvizity",  "КПП",                 ""),
    "bank_account":    ("rekvizity",  "Расчётный счёт",      ""),
    "delivery_zone":   ("geografiya", "Зона доставки",       ""),
    "delivery_cost":   ("usloviya",   "Стоимость доставки",  "руб"),
    "delivery_time":   ("usloviya",   "Срок доставки",       "дни"),
    "pickup":          ("usloviya",   "Самовывоз",           ""),
    "payment_methods": ("usloviya",   "Способы оплаты",      ""),
    "vat_prepay":      ("usloviya",   "Условия оплаты",      ""),
    "warranty_term":   ("garantiya",  "Гарантийный срок",    "мес"),
    "product_price":   ("cena",       "",                    "руб"),
    "faq_pair":        ("faq",        "",                    ""),
}

PAGE_CONF = {"contacts": 85, "delivery": 85, "payment": 85, "warranty": 85,
             "about": 80, "faq": 60, "main": 70}

RE_PHONE = re.compile(r"(?<!\d)(?:\+7|8|7)[\s\-\(]*\d{3}[\s\-\)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)")
RE_EMAIL = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
RE_INN = re.compile(r"ИНН[\s:№]*(\d{10}|\d{12})", re.I)
RE_OGRN = re.compile(r"ОГРН(?:ИП)?[\s:№]*(\d{13}|\d{15})", re.I)
RE_KPP = re.compile(r"КПП[\s:№]*(\d{9})", re.I)
RE_ACC = re.compile(r"(?:р[/\.]?с|расч[её]тный сч[её]т|счет)[\s:№]*(\d{20})", re.I)
RE_BIK = re.compile(r"БИК[\s:№]*(\d{9})", re.I)
RE_MONEY = re.compile(r"(\d[\d\s\u00a0]{2,})\s*(?:₽|руб|р\.)", re.I)
RE_DAYS = re.compile(r"(\d+)(?:\s*[-–]\s*(\d+))?\s*(рабоч\w*\s*)?(дн\w+|сут\w+|недел\w+)", re.I)
RE_MONTHS = re.compile(r"(\d+)\s*(год\w*|лет|мес\w+)", re.I)
RE_HOURS = re.compile(r"(?:с\s*)?(\d{1,2})[:.](\d{2})\s*(?:до|[-–—])\s*(\d{1,2})[:.](\d{2})")
RE_ADDR = re.compile(
    r"(?:г\.?\s*[А-ЯЁ][а-яё\-]+[,\s]+)?(?:ул\.|улица|проспект|пр-т|шоссе|ш\.|переулок|"
    r"пер\.|проезд)\s*[А-ЯЁа-яё0-9\s\-\.]{3,40}?,?\s*(?:д\.?\s*)?\d+[А-Яа-я]?"
    r"(?:\s*,?\s*(?:стр|корп|офис|оф)\.?\s*\d+)?", re.I)

PROMO = ("лучш", "№1", "номер один", "спешите", "успейте", "только сегодня",
         "выгодн", "уникальн", "идеальн", "гарантируем качество")

KW = {
    "delivery": ("доставк", "привез", "отгрузк"),
    "pickup": ("самовывоз", "забрать самост"),
    "warranty": ("гарант",),
    "zone": ("москв", "область", "регион", "по россии", "мкад"),
}


def _clean(s):
    return " ".join(str(s or "").split())


def _sentence(text, pos, span=0):
    """Цитата: предложение вокруг найденного, не длиннее 300 символов."""
    left = max(text.rfind(".", 0, pos), text.rfind("!", 0, pos),
               text.rfind("?", 0, pos), text.rfind("\n", 0, pos))
    rights = [text.find(c, pos + span) for c in ".!?\n"]
    rights = [r for r in rights if r > 0]
    right = min(rights) if rights else len(text)
    s = _clean(text[left + 1:right + 1])
    if len(s) > 300:
        s = _clean(text[max(0, pos - 120):pos + span + 120])
    return s or _clean(text[max(0, pos - 120):pos + span + 120])


def _is_promo(s):
    low = (s or "").lower()
    return any(p in low for p in PROMO)


def _norm_phone(raw):
    d = re.sub(r"\D", "", raw or "")
    if len(d) == 11 and d[0] == "8":
        d = "7" + d[1:]
    if len(d) == 10:
        d = "7" + d
    return d if len(d) == 11 and d[0] == "7" else ""


def _inn_ok(v):
    def c(digits, coef):
        return sum(int(d) * k for d, k in zip(digits, coef)) % 11 % 10
    if len(v) == 10:
        return c(v[:9], (2, 4, 10, 3, 5, 9, 4, 6, 8)) == int(v[9])
    if len(v) == 12:
        return (c(v[:10], (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)) == int(v[10])
                and c(v[:11], (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)) == int(v[11]))
    return False


def _ogrn_ok(v):
    if len(v) == 13:
        return int(v[:12]) % 11 % 10 == int(v[12])
    if len(v) == 15:
        return int(v[:14]) % 13 % 10 == int(v[14])
    return False


def _money(raw):
    d = re.sub(r"\D", "", raw or "")
    return d if d and 10 <= int(d) <= 100000000 else ""


def _to_days(m):
    n = int(m.group(1))
    if (m.group(4) or "").lower().startswith("недел"):
        n *= 7
    return str(n)


def _to_months(m):
    n = int(m.group(1))
    unit = (m.group(2) or "").lower()
    return str(n * 12 if unit.startswith(("год", "лет")) else n)


def _cand(ftype, value, url, page_type, snippet, rule_id, raw, conf, name=None):
    cat, key, unit = TYPE_MAP[ftype]
    if not value or not snippet or not rule_id:
        return None
    return {"fact_type": ftype, "category": cat, "name": name or key,
            "value": str(value), "unit": unit, "confidence": int(conf),
            "source_ref": url, "page_type": page_type,
            "snippet": snippet[:400], "rule_id": rule_id, "raw": _clean(raw)[:120]}


def _scan_page(text, url, page_type, conf, out, stats):
    def add(c):
        if c:
            out.append(c)
        else:
            stats["dropped_empty"] += 1

    for m in RE_PHONE.finditer(text):
        v = _norm_phone(m.group(0))
        if v:
            add(_cand("phone", v, url, page_type,
                      _sentence(text, m.start(), len(m.group(0))),
                      "phone_mask", m.group(0), conf))
    for m in RE_EMAIL.finditer(text):
        add(_cand("email", m.group(0).lower(), url, page_type,
                  _sentence(text, m.start(), len(m.group(0))),
                  "email_mask", m.group(0), conf))
    for m in RE_ADDR.finditer(text):
        add(_cand("address", _clean(m.group(0)), url, page_type,
                  _sentence(text, m.start(), len(m.group(0))),
                  "address_street", m.group(0), conf))
    for m in RE_HOURS.finditer(text):
        v = "%s:%s-%s:%s" % (m.group(1).zfill(2), m.group(2), m.group(3).zfill(2), m.group(4))
        add(_cand("work_hours", v, url, page_type,
                  _sentence(text, m.start(), len(m.group(0))),
                  "hours_range", m.group(0), conf))

    for ftype, rx, checker, rule in (
            ("inn", RE_INN, _inn_ok, "inn_checksum"),
            ("ogrn", RE_OGRN, _ogrn_ok, "ogrn_checksum"),
            ("kpp", RE_KPP, None, "kpp_structure")):
        for m in rx.finditer(text):
            v = m.group(1)
            ok = checker(v) if checker else None
            c = 95 if ok else (85 if ok is None else 60)
            add(_cand(ftype, v, url, page_type,
                      _sentence(text, m.start(), len(m.group(0))),
                      rule + ("_ok" if ok else ("" if ok is None else "_bad")),
                      m.group(0), c))

    has_bik = bool(RE_BIK.search(text))
    for m in RE_ACC.finditer(text):
        add(_cand("bank_account", m.group(1), url, page_type,
                  _sentence(text, m.start(), len(m.group(0))),
                  "account_with_bik" if has_bik else "account_no_bik",
                  m.group(0), 95 if has_bik else 85))

    low = text.lower()
    if page_type in ("delivery", "main"):
        for m in RE_MONEY.finditer(text):
            s = _sentence(text, m.start(), len(m.group(0)))
            if any(k in s.lower() for k in KW["delivery"]) and not _is_promo(s):
                v = _money(m.group(1))
                if v:
                    add(_cand("delivery_cost", v, url, page_type, s,
                              "money_near_delivery", m.group(0), conf))
        for m in RE_DAYS.finditer(text):
            s = _sentence(text, m.start(), len(m.group(0)))
            if any(k in s.lower() for k in KW["delivery"]):
                add(_cand("delivery_time", _to_days(m), url, page_type, s,
                          "days_near_delivery", m.group(0), conf))
        for m in re.finditer("|".join(KW["zone"]), low):
            s = _sentence(text, m.start(), len(m.group(0)))
            if any(k in s.lower() for k in KW["delivery"]) and not _is_promo(s):
                add(_cand("delivery_zone", _clean(s)[:180], url, page_type, s,
                          "zone_near_delivery", m.group(0), conf))
                break

    if any(k in low for k in KW["pickup"]):
        i = min(low.find(k) for k in KW["pickup"] if low.find(k) >= 0)
        add(_cand("pickup", "есть", url, page_type, _sentence(text, i),
                  "pickup_word", "самовывоз", conf))

    if page_type in ("payment", "main", "contacts"):
        found = [w for w in ("наличными", "картой", "безналичный", "переводом", "онлайн")
                 if w[:6] in low]
        if found:
            i = low.find(found[0][:6])
            add(_cand("payment_methods", ", ".join(found), url, page_type,
                      _sentence(text, i), "payment_words", ", ".join(found), conf))
        for m in re.finditer(r"(?:с|без)\s+НДС|предоплат\w+|аванс\w*", text, re.I):
            add(_cand("vat_prepay", _clean(m.group(0)), url, page_type,
                      _sentence(text, m.start(), len(m.group(0))),
                      "vat_prepay_words", m.group(0), conf))

    if page_type in ("warranty", "about", "main", "delivery"):
        for m in RE_MONTHS.finditer(text):
            s = _sentence(text, m.start(), len(m.group(0)))
            if any(k in s.lower() for k in KW["warranty"]) and not _is_promo(s):
                add(_cand("warranty_term", _to_months(m), url, page_type, s,
                          "term_near_warranty", m.group(0), conf))


def _scan_faq(text, url, conf, out):
    """Только явные пары: вопрос с «?» и следующий блок от 40 символов."""
    parts = re.split(r"(?<=\?)\s+", text)
    for i in range(len(parts) - 1):
        q = _clean(parts[i])
        a = _clean(parts[i + 1]).split("?")[0].strip()
        if not q.endswith("?") or len(q) < 12 or len(q) > 200 or len(a) < 40:
            continue
        out.append({"fact_type": "faq_pair", "category": "faq", "name": q[:200],
                    "value": a[:900], "unit": "", "confidence": conf,
                    "source_ref": url, "page_type": "faq",
                    "snippet": (q + " — " + a)[:400], "rule_id": "faq_qmark_pair",
                    "raw": q[:120]})


def extract(knowledge, products, base_url, main_text=""):
    """Кандидаты из уже собранных данных. Ничего не пишет и не качает."""
    out, stats = [], {"dropped_empty": 0, "dedup": 0, "pages": 0}

    for pg in (knowledge or {}).get("pages") or []:
        stats["pages"] += 1
        ptype = pg.get("type") or "main"
        conf = PAGE_CONF.get(ptype, 70)
        text = pg.get("text") or ""
        _scan_page(text, pg.get("url") or base_url, ptype, conf, out, stats)
        if ptype == "faq":
            _scan_faq(text, pg.get("url") or base_url, PAGE_CONF["faq"], out)

    if main_text:
        _scan_page(main_text, base_url, "main", PAGE_CONF["main"], out, stats)

    for p in products or []:
        title, price = _clean(p.get("title")), p.get("price")
        v = _money(str(price or ""))
        if title and v:
            out.append({"fact_type": "product_price", "category": "cena",
                        "name": title[:250], "value": v, "unit": "руб",
                        "confidence": 85,
                        "source_ref": p.get("product_url") or base_url,
                        "page_type": "product",
                        "snippet": "%s — %s" % (title, price),
                        "rule_id": "price_from_products", "raw": str(price)[:120]})

    seen, uniq = {}, []
    for c in out:
        key = (c["fact_type"], c["name"].lower(), c["value"].lower())
        if key in seen:
            stats["dedup"] += 1
            prev = uniq[seen[key]]
            if c["page_type"] not in prev["snippet"]:
                prev["snippet"] = (prev["snippet"] + " | также: " + c["page_type"])[:400]
            if c["confidence"] > prev["confidence"]:
                prev["confidence"] = c["confidence"]
            continue
        seen[key] = len(uniq)
        uniq.append(c)

    return uniq, stats
