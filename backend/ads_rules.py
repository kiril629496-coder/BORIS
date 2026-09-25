# -*- coding: utf-8 -*-
"""Правила заголовка объявления и самооценка. Отдельный файл: правила
должны проверяться кодом, а не доверием к модели.
"""
import re
import unicodedata
MAX_LEN = 50
BAD_CHARS = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")
SPINTAX = re.compile(r"[{}|]")
HTML = re.compile(r"<[^>]+>")
INTENT_RENT = ("аренда", "аренд", "прокат")   # «аренд» ловит аренды/аренду/арендовать
INTENT_BUY = ("купить", "куп", "прода", "цена")   # «прода» ловит продажа/продам/продать


# Города пишут по-разному: «Санкт-Петербург», «СПб», «Питер». Проверять
# по одной форме нельзя — иначе живой заголовок получит ложное нарушение.
CITY_ALIASES = {
    "санкт-петербург": ("санкт-петербург", "спб", "питер", "с-петербург", "ленобласт"),
    "москва": ("москва", "мск", "московск", "подмосков"),
    "екатеринбург": ("екатеринбург", "екб"),
    "нижний новгород": ("нижний новгород", "нижнем новгороде", "нн"),
}


def _city_forms(city):
    c = (city or "").strip().lower()
    if not c:
        return ()
    for base, forms in CITY_ALIASES.items():
        if c.startswith(base[:6]):
            return forms
    return (c[:6],)


def has_city(title, city):
    t = (title or "").lower()
    return any(f in t for f in _city_forms(city))


def check(title, city="", kind="rent", old_title=""):
    """Проверка по жёстким правилам. Возвращает список нарушений."""
    t = (title or "").strip()
    bad = []
    if not t:
        return ["пустой заголовок"]
    if len(t) > MAX_LEN:
        bad.append("длина %d, допустимо %d" % (len(t), MAX_LEN))
    if BAD_CHARS.search(t):
        bad.append("эмодзи в заголовке")
    if SPINTAX.search(t):
        bad.append("остался спинтакс")
    if HTML.search(t):
        bad.append("html-разметка")
    letters = [c for c in t if c.isalpha()]
    if letters and sum(1 for c in letters if c.isupper()) / len(letters) > 0.5:
        bad.append("капс")
    if city and not has_city(t, city):
        bad.append("нет города")
    words = INTENT_RENT if kind == "rent" else INTENT_BUY
    if not any(w in t.lower() for w in words):
        bad.append("нет слова о намерении (%s)" % words[0])
    if old_title and t.strip().lower() == old_title.strip().lower():
        bad.append("совпадает со старым")
    return bad


def score(title, city="", kind="rent"):
    """Самооценка по пяти показателям, 0-10. Порог — 7."""
    t = (title or "").strip()
    n = len(t)
    s = {}
    s["длина"] = 10 if 25 <= n <= MAX_LEN else (7 if n <= MAX_LEN else 3)
    hits = 0
    if city and has_city(t, city):
        hits += 1
    words = INTENT_RENT if kind == "rent" else INTENT_BUY
    if any(w in t.lower() for w in words):
        hits += 1
    if re.search(r"\d", t):
        hits += 1
    s["SEO"] = {0: 3, 1: 6, 2: 8, 3: 10}[min(hits, 3)]
    s["читаемость"] = 10 if 3 <= len(t.split()) <= 8 else 6
    s["УТП"] = 9 if re.search(r"\d|доставк|под ключ|утепл|нов", t.lower()) else 5
    s["модерация"] = 3 if (BAD_CHARS.search(t) or SPINTAX.search(t) or HTML.search(t)) else 10
    s["итого"] = round(sum(v for k, v in s.items()) / 5, 1)
    return s


def explain(old_title, views, city="", kind="rent"):
    """Почему меняем — фактами, а не оценками."""
    why = ["было %d просмотров за неделю" % views]
    t = (old_title or "").lower()
    if city and not has_city(t, city):
        why.append("в заголовке нет города")
    words = INTENT_RENT if kind == "rent" else INTENT_BUY
    if not any(w in t for w in words):
        why.append("нет слова «%s»" % words[0])
    if len(old_title.split()) <= 3:
        why.append("заголовок слишком общий")
    if not re.search(r"\d", old_title or ""):
        why.append("нет конкретики: размера или количества")
    return why


def _canon_num(raw):
    """'6,0' -> '6' ; '2.40' -> '2.4' ; '06' -> '6'."""
    s = str(raw).replace(",", ".").strip()
    try:
        v = float(s)
    except ValueError:
        return s
    return str(int(v)) if v == int(v) else ("%g" % v)


def extract_numbers(text):
    """Числа текста в единой форме. Размеры 6х2,4 / 6 x 2.4 м / 6×2,4 дают {6, 2.4}."""
    if not text:
        return set()
    t = unicodedata.normalize("NFKC", str(text)).lower()
    for ch in "xх\u00d7*":          # латинская x, кириллическая х, знак умножения
        t = t.replace(ch, " ")
    t = t.replace("\u00a0", " ")
    t = re.sub(r"(?<=\d)[ ](?=\d{3}\b)", "", t)   # разделитель тысяч
    return set(_canon_num(m.group(0)) for m in re.finditer(r"\d+(?:[.,]\d+)?", t))


def numbers_ok(title, source_blob):
    """Все числа заголовка должны быть подтверждены данными объявления.
    Сравниваются НОРМАЛИЗОВАННЫЕ числа, а не подстроки: «6х2,4» в фиде
    подтверждает «6 x 2.4 м» в заголовке. Маркетинговое число, которого
    во входных данных нет (№1, 24/7, 10 лет, -20%), не проходит.
    """
    src = extract_numbers(source_blob)
    for n in sorted(extract_numbers(title)):
        if n not in src:
            return False, n
    return True, ""


def _norm(text):
    """Слова без пунктуации и окончаний: «Санкт-Петербург» и «Санкт-Петербурге»
    для Avito один и тот же дубль, а посимвольное сравнение их различает."""
    words = re.findall(r"[а-яёa-z0-9]+", (text or "").lower())
    return set(w[:6] for w in words if len(w) > 1)


# Оценочные вставки: информации о товаре не несут, различать по ним нельзя.
_MARKETING_WORDS = ("дёшево", "дешево", "недорого", "выгодно", "срочно", "акция",
                    "скидка", "лучшие", "лучший", "качественные", "быстро",
                    "надёжно", "надежно", "супер", "оптимально", "хит")


def _marketing_tokens():
    out = set()
    for w in _MARKETING_WORDS:
        out |= _norm(w)
    return out


def _city_tokens(city=None):
    """Токены города из существующего CITY_ALIASES плюс город аккаунта."""
    src = []
    try:
        al = CITY_ALIASES
    except NameError:
        al = None
    if isinstance(al, dict):
        for k, v in al.items():
            src.append(k)
            if isinstance(v, (list, tuple, set)):
                src.extend(v)
            elif isinstance(v, str):
                src.append(v)
    elif isinstance(al, (list, tuple, set)):
        src.extend(al)
    if city:
        src.append(city)
    out = set()
    for s in src:
        out |= _norm(s)
    return out


def _significant(tokens, city_tokens):
    """Оставляем только слова, которые реально различают заголовки."""
    junk = city_tokens | _marketing_tokens()
    return set(t for t in tokens if t not in junk)


def too_similar(title, others, limit=0.7, city=None):
    """Схожесть с уже известными заголовками: одинаковые Avito считает дублями.

    Сравнение идёт по РАЗЛИЧАЮЩИМ словам: город обязателен в каждом заголовке,
    а «недорого» и «срочно» информации не добавляют — оба вида слов из сравнения
    исключаются. Если после очистки одно из множеств пустое, пара сравнивается
    по исходным токенам.

    others: строки либо пары (вид источника, заголовок) — вид попадает в журнал.
    Возвращает (дубль, заголовок, коэффициент, вид источника).
    """
    a_all = _norm(title)
    if not a_all:
        return True, "", 1.0, "пустой заголовок"
    ct = _city_tokens(city)
    a_sig = _significant(a_all, ct)
    best_title, best_coef, best_kind = "", 0.0, ""
    for o in others or []:
        if isinstance(o, (list, tuple)):
            kind, other = (o[0], o[1]) if len(o) > 1 else ("", o[0])
        else:
            kind, other = "", o
        b_all = _norm(other)
        if not b_all:
            continue
        b_sig = _significant(b_all, ct)
        if a_sig and b_sig:
            x, y = a_sig, b_sig
        else:
            x, y = a_all, b_all          # откат: сравниваем как раньше
        coef = len(x & y) / float(max(len(x), len(y)))
        if coef > best_coef:
            best_coef, best_title, best_kind = coef, other, kind
    return (best_coef >= limit), best_title, round(best_coef, 3), best_kind


# --- родовое слово ниши -------------------------------------------------
def _generic_stems(generics):
    """Основы родовых слов: «бытовка» -> «бытов» (ловит бытовок/бытовки/бытовке)."""
    out = []
    for g in generics or []:
        for part in re.split(r"[^0-9a-zA-Zа-яА-ЯёЁ]+", str(g).lower()):
            if len(part) >= 5:
                out.append(part[:5])
    return out


def generic_ok(title, generics):
    """В заголовке должно быть слово, которым товар называют в поиске.

    Нишевый термин («распашонка», «прорабская») его не заменяет: по такому
    слову люди не ищут. Пустой список родовых слов = правило не применяется.
    Возвращает (ок, чего не хватает).
    """
    if not generics:
        return True, ""
    t = (title or "").lower()
    for stem in _generic_stems(generics):
        if stem in t:
            return True, ""
    return False, " или ".join(str(g) for g in generics)
