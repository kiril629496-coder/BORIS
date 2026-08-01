import shutil, time, sys, py_compile

PATH = "app/api/avito.py"
bak = PATH + ".before_catfeed_%s" % int(time.time())
shutil.copy(PATH, bak)
print("бэкап:", bak)

s = open(PATH, encoding="utf-8").read()
ANCHOR = "        xml_parts.append(f'    <Category>{html.escape(item.category)}</Category>')"
n = s.count(ANCHOR)
print("якорь <Category> найден раз:", n)
assert n == 1, "якорь не уникален — СТОП, ничего не менял"
assert "def _cat_for_feed(" not in s, "helper уже есть — СТОП"

NEW = (
    "        try:\n"
    "            _cat_feed = _cat_for_feed(item) or item.category\n"
    "        except Exception:\n"
    "            _cat_feed = item.category\n"
    "        xml_parts.append(f'    <Category>{html.escape(_cat_feed)}</Category>')"
)
s = s.replace(ANCHOR, NEW)

HELPER = '''


def _cat_for_feed(item):
    """Валидное для Avito имя тега <Category> — 2-й сегмент пути дерева."""
    raw = (getattr(item, "category", "") or "").strip()
    niche = (getattr(item, "category_id", "") or "").strip()
    tid = getattr(item, "template_id", None)
    try:
        got = _normalize_category(raw, niche or None, tid)
        if got:
            return got
    except Exception:
        pass
    key = niche or raw
    if not key:
        return raw
    _SL = globals().get("SessionLocal")
    if _SL is None:
        for _mod in ("app.db.session", "app.database", "app.db"):
            try:
                _SL = getattr(__import__(_mod, fromlist=["SessionLocal"]), "SessionLocal")
                break
            except Exception:
                continue
    if _SL is None:
        return raw
    try:
        from sqlalchemy import text as _t
        db = _SL()
        try:
            r = db.execute(_t(
                "select coalesce(nullif(category_path, \\'\\'), category_name) "
                "from category_templates "
                "where category_id ilike :k or category_name ilike :k "
                "order by (case when coalesce(category_path, category_name) like :g "
                "then 0 else 1 end) limit 1"), {"k": "%" + key + "%", "g": "%>%"}).fetchone()
        finally:
            db.close()
        if r and r[0] and ">" in r[0]:
            parts = [p.strip() for p in r[0].split(">")]
            if len(parts) >= 2 and parts[1]:
                return parts[1]
    except Exception:
        pass
    return raw
'''

open(PATH, "w", encoding="utf-8").write(s.rstrip("\n") + "\n" + HELPER)
print("врезка сделана, helper дописан в конец файла")
py_compile.compile(PATH, doraise=True)
print("СИНТАКСИС OK")
