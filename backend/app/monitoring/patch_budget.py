import io, time, ast
P = "budget.py"
src = io.open(P, encoding="utf-8").read()
if "_key16" in src:
    print("Уже применён."); raise SystemExit(0)

HELPER = '''def _key16(key: str) -> str:
    """monitor_runs.mode — varchar(16), полный ключ туда не влезает.
    Пишем устойчивый отпечаток; период уже внутри key, месяцы не схлопнутся."""
    import hashlib
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


'''
A = "def already_notified(cur, key: str) -> bool:"
if src.count(A) != 1:
    print("СТОП: якорь найден", src.count(A), "раз."); raise SystemExit(1)
out = src.replace(A, HELPER + A, 1)

for old, new in (
    ("LIMIT 1\",\n        (key,))", "LIMIT 1\",\n        (_key16(key),))"),
    ('(store._uuid(), "budget", key, store._now()))',
     '(store._uuid(), "budget", _key16(key), store._now()))'),
):
    if out.count(old) != 1:
        print("СТОП: фрагмент найден", out.count(old), "раз, файл не тронут."); raise SystemExit(1)
    out = out.replace(old, new, 1)

ast.parse(out)
bak = P + ".bak_key16_%d" % int(time.time())
io.open(bak, "w", encoding="utf-8").write(src)
io.open(P, "w", encoding="utf-8").write(out)
print("Патч применён. Бэкап:", bak)
