#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 1 close: fail-closed фильтр мусорных ниш + откат otdushi."""
import os, sys, json, shutil, datetime, py_compile
BK = "/root/BORIS/backend"; sys.path.insert(0, BK); os.chdir(BK)
RES = os.path.join(BK, "app/services/niche_resolver.py")
TS = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
SNAP = "/root/niche_prejunk.json"
MARK = "NICHE_JUNK_FILTER_V1"

FILTER_SRC = '''
_JUNK_PREFIX = ("\\u0442\\u0435\\u0441\\u0442", "test", "\\u0434\\u0435\\u043c\\u043e", "demo",
                "\\u043f\\u0440\\u0438\\u043c\\u0435\\u0440", "sample")
_JUNK_EXACT = set(["qa", "qwe", "asd", "asdf", "xxx", "\\u0445\\u0445\\u0445", "n/a", "na",
                   "null", "none", "-", "\\u043d\\u0438\\u0448\\u0430",
                   "\\u043f\\u0440\\u043e\\u0432\\u0435\\u0440\\u043a\\u0430", "check"])


def is_junk(v):
    """fail-closed: непригодное значение НЕ является валидным источником ниши."""
    import re as _re
    s = _key(v)
    if not s or len(s) < 3:
        return True
    toks = [t for t in _re.split(r"[^0-9a-z\\u0430-\\u044f]+", s) if t]
    if not toks:
        return True
    for t in toks:
        if t in _JUNK_EXACT or t.isdigit():
            continue
        if any(t.startswith(p) for p in _JUNK_PREFIX):
            continue
        return False
    return True

'''

EDITS = [
 ("def _parse(txt):", FILTER_SRC.lstrip("\n") + "\ndef _parse(txt):"),
 ('        cands = {SOURCE_PROFILE: prof, SOURCE_FORM: form, SOURCE_SCENARIO: scen}\n',
  '        cands = {SOURCE_PROFILE: prof, SOURCE_FORM: form, SOURCE_SCENARIO: scen}\n'
  '        cands = dict((k, ("" if is_junk(v) else v)) for k, v in cands.items())  # ' + MARK + '\n'
  '        prof, form, scen = cands[SOURCE_PROFILE], cands[SOURCE_FORM], cands[SOURCE_SCENARIO]\n'),
 ('        if r["source"] in ("", SOURCE_PROFILE) or not r["niche"] or r["conflict"]:',
  '        if (r["source"] in ("", SOURCE_PROFILE) or not r["niche"]\n'
  '                or r["conflict"] or is_junk(r["niche"])):  # ' + MARK),
]


def snapshot(path):
    from patch_niche_stage1 import collect
    json.dump(collect(), open(path, "w"), ensure_ascii=False)


def patch():
    src = open(RES, encoding="utf-8").read()
    if MARK in src:
        print("  фильтр уже стоит, патч пропущен"); return True
    for old, new in EDITS:
        if src.count(old) != 1:
            print("  STOP: якорь встречается %d раз: %r" % (src.count(old), old[:50])); return False
        src = src.replace(old, new, 1)
    shutil.copy2(RES, RES + ".before_junk_" + TS)
    open(RES, "w", encoding="utf-8").write(src)
    py_compile.compile(RES, doraise=True)
    print("  патч применён, бэкап %s" % (RES + ".before_junk_" + TS)); return True


def rollback_otdushi():
    from sqlalchemy import text as T
    from app.db.session import SessionLocal
    db = SessionLocal()
    was = db.execute(T("SELECT coalesce(company_niche,'') FROM accounts WHERE account_id='otdushi'")).scalar()
    db.execute(T("UPDATE accounts SET company_niche='' WHERE account_id='otdushi' AND company_niche='%s'" % (was or "")))
    db.commit()
    now = db.execute(T("SELECT coalesce(company_niche,'') FROM accounts WHERE account_id='otdushi'")).scalar()
    db.close()
    print("  otdushi.company_niche: %r -> %r" % (was, now))
    return now == ""


def selftest():
    import importlib, app.services.niche_resolver as nr
    importlib.reload(nr)
    cases = [("\u0442\u0435\u0441\u0442", True), ("Test", True), ("QA", True), ("demo", True),
             ("\u0434\u0435\u043c\u043e", True), ("   ", True), ("", True),
             ("\u0422\u0435\u0441\u0442\u043e\u0432\u0430\u044f \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430", True),
             ("\u0422\u0440\u043e\u0442\u0443\u0430\u0440\u043d\u0430\u044f \u043f\u043b\u0438\u0442\u043a\u0430", False),
             ("\u0430\u0440\u0435\u043d\u0434\u044b \u0431\u044b\u0442\u043e\u0432\u043e\u043a", False),
             ("\u043c\u0435\u0445\u0430\u043d\u0438\u0437\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u0430\u044f \u0448\u0442\u0443\u043a\u0430\u0442\u0443\u0440\u043a\u0430", False)]
    bad = [(v, nr.is_junk(v), exp) for v, exp in cases if nr.is_junk(v) != exp]
    print("  SELFTEST: %d кейсов, расхождений %d %s" % (len(cases), len(bad), bad or ""))
    return not bad


def verify():
    from patch_niche_stage1 import collect
    before = json.load(open(SNAP))
    after = collect()
    ok = True
    print("\n=== 1. otdushi ===")
    o = after.get("otdushi", {})
    print("   ниша=%r источник=%r стадия=%s (было %r)" % (o.get("niche"), o.get("source"),
          o.get("stage"), before.get("otdushi", {}).get("niche")))
    ok &= (not o.get("niche"))
    print("   %s" % ("PASS: больше не резолвится как «тест»" if not o.get("niche") else "FAIL"))
    print("\n=== 2. qa_feed_test ===")
    q = after.get("qa_feed_test", {})
    keep = "\u043f\u043b\u0438\u0442\u043a\u0430" in (q.get("niche") or "").lower()
    print("   ниша=%r источник=%r" % (q.get("niche"), q.get("source")))
    print("   %s" % ("PASS: сохранена" if keep else "FAIL: потеряна"))
    ok &= keep
    print("\n=== 3. ОСТАЛЬНЫЕ РЕЗОЛВЫ ===")
    diff = [(a, before.get(a, {}).get("niche"), v.get("niche")) for a, v in after.items()
            if before.get(a, {}).get("niche") != v.get("niche")]
    for a, b, n in diff:
        print("   ИЗМЕНИЛОСЬ %-44s %r -> %r" % (a[:44], b, n))
    nb = len([1 for v in before.values() if v.get("niche")])
    na = len([1 for v in after.values() if v.get("niche")])
    print("   ниш было %d, стало %d, изменений %d (ожидаю ровно 1 — otdushi)" % (nb, na, len(diff)))
    ok &= (len(diff) == 1 and diff[0][0] == "otdushi")
    print("   %s" % ("PASS" if len(diff) == 1 and diff[0][0] == "otdushi" else "FAIL"))
    print("\n=== 4. РЕГРЕССИЯ stage_of / auto_do / collect_facts ===")
    err = [a for a, v in after.items() if str(v.get("stage")).startswith("ERR")]
    moved = [(a, before.get(a, {}).get("stage"), v.get("stage")) for a, v in after.items()
             if str(before.get(a, {}).get("stage")) != str(v.get("stage"))]
    print("   stage_of: %d аккаунтов, исключений %d, смен стадии %d %s" % (len(after), len(err), len(moved), moved or ""))
    try:
        from app.scenarios import collect_facts  # noqa
        from app.onboarding import auto_do  # noqa
        print("   импорт collect_facts и auto_do: OK")
    except Exception as e:
        print("   импорт FAIL: %s" % str(e)[:120]); ok = False
    ok &= not err and not moved
    print("\n=== ИТОГ: %s ===" % ("PASS" if ok else "FAIL"))
    return ok


if __name__ == "__main__":
    if sys.argv[1:] == ["snap"]:
        snapshot(SNAP); print("  снимок до патча сохранён")
    elif sys.argv[1:] == ["run"]:
        print("=== ПАТЧ ==="); p = patch()
        print("=== ОТКАТ otdushi ==="); r = rollback_otdushi()
        print("=== SELFTEST ==="); s = selftest()
        if not (p and r and s):
            print("STOP: патч/откат/селфтест не прошли — проверка не запускалась"); sys.exit(1)
        import app.main  # noqa
        print("  import app.main OK")
        sys.exit(0 if verify() else 1)
