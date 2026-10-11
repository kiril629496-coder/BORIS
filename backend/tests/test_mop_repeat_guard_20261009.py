from unittest.mock import patch

from app.api import messenger


class _Rows:
    def __init__(self, rows):
        self._rows = rows
    def fetchall(self):
        return self._rows


class _Db:
    def __init__(self, rows):
        self.rows = rows
    def execute(self, *args, **kwargs):
        return _Rows(self.rows)
    def close(self):
        pass


def _norm(value):
    import re
    s = str(value or "").lower().replace("ё", "е")
    s = re.sub(r"^\s*(здравствуйте|добрый\s+(день|вечер)|доброе\s+утро|привет)\b[!,.—:\s-]*", "", s)
    return " ".join(re.findall(r"[а-яa-z0-9]+", s))


def test_price_fallback_avoids_recent_exact_safe_phrase():
    base = "Актуальную стоимость уточнит менеджер. Передам ваш запрос."
    already = "Не буду придумывать цену без расчёта. Передам ваши параметры менеджеру для точного ответа."
    with patch("app.db.session.SessionLocal", return_value=_Db([(already,)])):
        out = messenger._mop_safe_fallback_variant("planetazayavki_65985", "chat-1", base)
    assert out != base
    assert _norm(out) != _norm(already)
    assert "цен" in out.lower() or "стоим" in out.lower() or "расч" in out.lower()


def test_attachment_fallback_is_varied_but_keeps_handoff_truth():
    base = "Фото получил. Передам коллеге, чтобы он посмотрел и уточнил детали."
    with patch("app.db.session.SessionLocal", return_value=_Db([])):
        a = messenger._mop_safe_fallback_variant("planetazayavki_65985", "chat-A", base)
        b = messenger._mop_safe_fallback_variant("planetazayavki_65985", "chat-B", base)
    assert a != base
    assert b != base
    assert "передам" in a.lower()
    assert "передам" in b.lower()


def test_normal_reply_is_unchanged():
    normal = "Да, присылайте размеры и город — сориентируем по следующему шагу."
    with patch("app.db.session.SessionLocal", return_value=_Db([])):
        out = messenger._mop_safe_fallback_variant("planetazayavki_65985", "chat-C", normal)
    assert out == normal
