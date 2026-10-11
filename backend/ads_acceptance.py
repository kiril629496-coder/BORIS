#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Слой ПРИЁМКИ сгенерированных заголовков объявлений.

Ничего не генерирует и никуда не публикует. Только проверяет результат:
  1) нет ли в заголовке числа, которого нет во входных данных объявления;
  2) не дублирует ли заголовок действующие объявления, ранее предложенные
     варианты или соседние предложения этого же прогона.

Используется из ads_autopilot.py ПЕРЕД сохранением предложения в ads_proposals.
"""

import re
import unicodedata

# ---------------------------------------------------------------- числа

# Разделители размеров: латинская x, кириллическая х, знак умножения.
_DIM_SEP = "xх×*"
# Единицы, которые не влияют на само число.
_UNITS = ("мм", "см", "кв.м", "кв м", "м2", "м²", "м3", "м³", "м", "шт", "кг",
          "т", "л", "ч", "мин", "%", "руб", "р.", "₽")

_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")

# Хвосты, которые в заголовке не являются самостоятельным фактом:
# они всегда идут вместе с числом и проверяются вместе с ним.
_PERCENT_RE = re.compile(r"-?\d+\s*%")


def _canon_num(raw):
    """'6,0' -> '6' ; '2.40' -> '2.4' ; '06' -> '6'."""
    s = raw.replace(",", ".").strip()
    try:
        v = float(s)
    except ValueError:
        return s
    if v == int(v):
        return str(int(v))
    return ("%g" % v)


def extract_numbers(text):
    """Все числа текста в канонической форме. Размеры 6х2,4 дают {6, 2.4}."""
    if not text:
        return set()
    t = unicodedata.normalize("NFKC", str(text)).lower()
    # разделители размеров -> пробел, чтобы 6х2,4 распалось на два числа
    for ch in _DIM_SEP:
        t = t.replace(ch, " ")
    # неразрывные пробелы и разделители тысяч внутри чисел
    t = t.replace("\u00a0", " ")
    t = re.sub(r"(?<=\d)[  ](?=\d{3}\b)", "", t)
    return {_canon_num(m.group(0)) for m in _NUM_RE.finditer(t)}


def collect_source_numbers(*sources):
    """Все числа, подтверждённые входными данными объявления."""
    out = set()
    for s in sources:
        if isinstance(s, dict):
            for v in s.values():
                out |= extract_numbers(v)
        elif isinstance(s, (list, tuple, set)):
            for v in s:
                out |= extract_numbers(v)
        else:
            out |= extract_numbers(s)
    return out


def check_numbers(new_title, source_numbers):
    """
    Возвращает список выдуманных чисел (в порядке появления в заголовке).
    Пустой список = все числа заголовка подтверждены данными объявления.
    """
    bad = []
    seen = set()
    for n in sorted(extract_numbers(new_title)):
        if n not in source_numbers and n not in seen:
            seen.add(n)
            bad.append(n)
    return bad


def numbers_reason(new_title, bad):
    """Человеческая формулировка для журнала."""
    if not bad:
        return ""
    marks = []
    for n in bad:
        m = re.search(r"[^\s,;]*%s[^\s,;]*" % re.escape(n.replace(".", "[.,]")), new_title)
        piece = m.group(0) if m else n
        if piece not in marks:
            marks.append(piece)
    return "в заголовке число, которого нет в данных объявления: " + ", ".join(marks)


# ---------------------------------------------------------------- дубли

_STOP = {"в", "во", "на", "с", "со", "и", "для", "от", "до", "по", "из", "под",
         "у", "к", "за", "о", "об", "при", "же", "а", "но", "или"}

_CITY_CANON = {
    "спб": "спб", "санкт": "спб", "санкт-петербург": "спб", "санкт-петербурге": "спб",
    "петербург": "спб", "петербурге": "спб", "питер": "спб", "питере": "спб",
    "ленобласть": "спб", "ленобласти": "спб",
    "мск": "мск", "москва": "мск", "москве": "мск", "подмосковье": "мск",
    "подмосковье;": "мск", "мо": "мск",
    "екб": "екб", "екатеринбург": "екб", "екатеринбурге": "екб",
    "нн": "нн", "нижний": "нн", "новгород": "нн", "новгороде": "нн",
}


def _tokens(title):
    t = unicodedata.normalize("NFKC", str(title or "")).lower()
    t = re.sub(r"[^0-9a-zа-яё]+", " ", t)
    out = []
    for w in t.split():
        if w in _STOP:
            continue
        out.append(_CITY_CANON.get(w, w))
    return out


def _norm_string(title):
    return " ".join(_tokens(title))


def _jaccard(a, b):
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / float(len(sa | sb))


def _trigrams(s):
    s = " " + s + " "
    return {s[i:i + 3] for i in range(len(s) - 2)}


def _dice(a, b):
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return 0.0
    return 2.0 * len(ta & tb) / float(len(ta) + len(tb))


def similarity(a, b):
    """0..1. Берём максимум словарной и посимвольной меры — морфология не мешает."""
    ja = _jaccard(_tokens(a), _tokens(b))
    di = _dice(_norm_string(a), _norm_string(b))
    return round(max(ja, di), 3)


DUP_THRESHOLD = 0.62

_KIND_RU = {
    "active": "действующее объявление",
    "proposal_old": "ранее сохранённое предложение",
    "run_current": "предложение текущего прогона",
}


def find_duplicate(new_title, candidates, threshold=DUP_THRESHOLD):
    """
    candidates: последовательность (kind, title) или (kind, title, ref).
    kind: active | proposal_old | run_current.
    Возвращает None либо словарь с полным описанием совпадения для журнала.
    """
    best = None
    for cand in candidates or ():
        kind, title = cand[0], cand[1]
        ref = cand[2] if len(cand) > 2 else None
        if not title:
            continue
        coef = similarity(new_title, title)
        if best is None or coef > best["coef"]:
            best = {"kind": kind, "kind_ru": _KIND_RU.get(kind, kind),
                    "title": title, "ref": ref, "coef": coef,
                    "method": "совпадение значимых слов и символьных сочетаний"}
    if best and best["coef"] >= threshold:
        best["threshold"] = threshold
        return best
    return None


def duplicate_reason(new_title, dup):
    """Строка для журнала: с чем именно совпал, оба заголовка и коэффициент."""
    if not dup:
        return ""
    ref = (" id=%s" % dup["ref"]) if dup.get("ref") else ""
    return ("дубль: «%s» совпадает с %s%s «%s», коэффициент %.2f при пороге %.2f"
            % (new_title, dup["kind_ru"], ref, dup["title"],
               dup["coef"], dup.get("threshold", DUP_THRESHOLD)))


# ---------------------------------------------------------------- приёмка

def accept(new_title, source_numbers, candidates, threshold=DUP_THRESHOLD):
    """
    Единая точка приёмки заголовка.
    Возвращает (ok, code, reason, detail):
      code: "" | "invented_number" | "duplicate"
      detail: список чисел либо описание совпадения — для журнала и отчёта.
    """
    bad = check_numbers(new_title, source_numbers)
    if bad:
        return False, "invented_number", numbers_reason(new_title, bad), bad
    dup = find_duplicate(new_title, candidates, threshold)
    if dup:
        return False, "duplicate", duplicate_reason(new_title, dup), dup
    return True, "", "", None
