import re

class SizeError(ValueError):
    pass

_M = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*м\s*$")
_MM = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*мм\s*$")
_BARE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*$")

def _fmt(metres):
    s = ("%g" % round(metres, 3)).replace(".", ",")
    return s + " м"

def normalize_size(value, assume_unit=None):
    """Приводит размер к формату контракта 90576: строка в метрах, '6 м' / '2,4 м'.
    assume_unit: 'mm' или 'm' — единица голого числа. Без неё голое число НЕ трактуется."""
    if value is None or str(value).strip() == "":
        raise SizeError("пустое значение")
    s = str(value).replace("\u00a0", " ").strip()
    m = _MM.match(s)
    if m:
        return _fmt(float(m.group(1).replace(",", ".")) / 1000.0)
    m = _M.match(s)
    if m:
        return _fmt(float(m.group(1).replace(",", ".")))
    m = _BARE.match(s)
    if m:
        if assume_unit == "mm":
            return _fmt(float(m.group(1).replace(",", ".")) / 1000.0)
        if assume_unit == "m":
            return _fmt(float(m.group(1).replace(",", ".")))
        raise SizeError("голое число без указания единиц: %r" % s)
    raise SizeError("единицы не определяются: %r" % s)
