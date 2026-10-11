#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BORIS Stage 1: единый резолвер ниши. Фазы: before | apply | after."""
import os, sys, re, json, shutil, datetime, py_compile

BK   = "/root/BORIS/backend"
TS   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
SNAP = "/root/niche_stage1_before.json"
RBSQL= "/root/niche_stage1_rollback.sql"
MARK = "NICHE_RESOLVER_V1"
RES  = os.path.join(BK, "app/services/niche_resolver.py")
ONB  = os.path.join(BK, "app/onboarding.py")
SCEN = os.path.join(BK, "app/scenarios.py")
REPORT = []

RESOLVER_SRC = '''# -*- coding: utf-8 -*-
# NICHE_RESOLVER_V1
# Единственное место, где решается, что такое «ниша аккаунта».
# Приоритет: accounts.company_niche -> user_onboarding.form_data.company_niche
#            -> business_scenarios.input_data.direction
# Внутренности позже переедут в Client Config без изменения вызывающего кода.
import json
from sqlalchemy import text as _T
from app.db.session import SessionLocal

SOURCE_PROFILE  = "profile"
SOURCE_FORM     = "onboarding_form"
SOURCE_SCENARIO = "scenario_input"


def _norm(v):
    return " ".join(str(v or "").split()).strip()


def _key(v):
    return _norm(v).lower().replace("\\u0451", "\\u0435")


def _parse(txt):
    # form_data хранится ТЕКСТОМ и бывает python-repr, а не JSON. json.loads на нём падает.
    if not txt:
        return {}
    try:
        v = json.loads(txt)
        return v if isinstance(v, dict) else {}
    except Exception:
        pass
    try:
        import ast
        v = ast.literal_eval(txt)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def resolve_niche(account_id, db=None):
    own = db is None
    db = db or SessionLocal()
    try:
        prof = _norm(db.execute(_T("SELECT company_niche FROM accounts WHERE account_id=:a"),
                                {"a": account_id}).scalar())
        form = ""
        for (fd,) in db.execute(_T("""SELECT o.form_data::text FROM user_onboarding o
                                      JOIN users u ON u.id = o.user_id
                                      WHERE u.account_id = :a
                                      ORDER BY o.updated_at DESC NULLS LAST"""),
                                {"a": account_id}).fetchall():
            j = _parse(fd)
            v = _norm(j.get("company_niche") or j.get("niche"))
            if v:
                form = v
                break
        scen = ""
        for (idata,) in db.execute(_T("""SELECT input_data::text FROM business_scenarios
                                         WHERE account_id = :a
                                         ORDER BY updated_at DESC NULLS LAST, id DESC"""),
                                   {"a": account_id}).fetchall():
            j = _parse(idata)
            v = _norm(j.get("direction") or j.get("niche"))
            if v:
                scen = v
                break
        cands = {SOURCE_PROFILE: prof, SOURCE_FORM: form, SOURCE_SCENARIO: scen}
        conflict = bool(form and scen and _key(form) != _key(scen) and not prof)
        for s in (SOURCE_PROFILE, SOURCE_FORM, SOURCE_SCENARIO):
            if cands[s]:
                return {"niche": cands[s], "source": s, "conflict": conflict,
                        "candidates": cands, "synced": False}
        return {"niche": "", "source": "", "conflict": False, "candidates": cands, "synced": False}
    finally:
        if own:
            db.close()


def business_niche(account_id, db=None):
    """Только чтение. Для read-путей (stage_of и т.п.)."""
    return resolve_niche(account_id, db=db).get("niche") or ""


def ensure_profile_niche(account_id, db=None):
    """Перелив в профиль. Пишет ТОЛЬКО когда профиль пуст, ниша доказана и конфликта нет."""
    own = db is None
    db = db or SessionLocal()
    r = None
    try:
        r = resolve_niche(account_id, db=db)
        if r["source"] in ("", SOURCE_PROFILE) or not r["niche"] or r["conflict"]:
            return r
        db.execute(_T("""UPDATE accounts SET company_niche = :n
                         WHERE account_id = :a AND coalesce(company_niche, '') = ''"""),
                   {"n": r["niche"], "a": account_id})
        db.commit()
        r["synced"] = True
        return r
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return r or {"niche": "", "source": "", "conflict": False, "candidates": {}, "synced": False}
    finally:
        if own:
            db.close()
'''


def log(s):
    print(s)
    REPORT.append(s)


def db_open():
    sys.path.insert(0, BK)
    os.chdir(BK)
    from app.db.session import SessionLocal
    return SessionLocal()


def collect():
    from sqlalchemy import text as T
    from app.services.niche_resolver import resolve_niche
    from app.onboarding import stage_of
    db = db_open()
    accs = [r[0] for r in db.execute(T("SELECT account_id FROM accounts ORDER BY account_id")).fetchall()]
    det = set(r[0] for r in db.execute(T("SELECT account_id FROM storage WHERE key='detected_category'")).fetchall())
    out = {}
    for a in accs:
        try:
            rn = resolve_niche(a, db=db)
        except Exception as e:
            rn = {"niche": "", "source": "ERR:" + str(e)[:50], "conflict": False}
        try:
            st = stage_of(a, db=db) or {}
            stage = st.get("\u0441\u0442\u0430\u0434\u0438\u044f", st.get("stage", "?"))
        except Exception as e:
            stage = "ERR:" + str(e)[:50]
        out[a] = {"niche": (rn.get("niche") or "")[:44], "source": rn.get("source") or "-",
                  "conflict": bool(rn.get("conflict")), "stage": stage, "detected": a in det}
    db.close()
    return out


def write_resolver():
    d = os.path.join(BK, "app/services")
    if not os.path.isdir(d):
        os.makedirs(d)
    ini = os.path.join(d, "__init__.py")
    if not os.path.exists(ini):
        open(ini, "w").close()
    cur = open(RES, encoding="utf-8").read() if os.path.exists(RES) else ""
    if cur.strip() == RESOLVER_SRC.strip():
        log("  resolver: уже актуален")
        return
    if cur:
        shutil.copy2(RES, RES + ".before_niche_" + TS)
    open(RES, "w", encoding="utf-8").write(RESOLVER_SRC)
    log("  resolver: записан %s" % RES)


def apply_edits():
    changed, skipped = [], []
    src = open(ONB, encoding="utf-8").read()
    if MARK in src:
        log("  onboarding.py: уже пропатчен, пропуск")
    else:
        shutil.copy2(ONB, ONB + ".before_niche_" + TS)
        # EDIT-1 auto_do
        old1 = '        niche = (acc[0] or "").strip() if acc else ""'
        if src.count(old1) != 1:
            log("  STOP-EDIT1: якорь auto_do найден %d раз" % src.count(old1))
            skipped.append("auto_do")
        else:
            src = src.replace(old1, old1 + "\n"
                "        if not niche:  # " + MARK + "\n"
                "            from app.services.niche_resolver import ensure_profile_niche as _epn\n"
                "            niche = (_epn(account_id, db=db) or {}).get(\"niche\") or \"\"")
            changed.append("auto_do -> ensure_profile_niche")
        # EDIT-2 stage_of
        rx = re.compile(r"\n([ \t]*)a = db\.execute\(text\(\"\"\"SELECT avito_client_id.*?\.fetchone\(\)\n", re.S)
        ms = rx.findall(src)
        if len(ms) != 1:
            log("  STOP-EDIT2: якорь stage_of найден %d раз" % len(ms))
            skipped.append("stage_of")
        else:
            ind = ms[0]
            ins = ("\n%sif a is not None and not str(a[2] or \"\").strip():  # %s\n"
                   "%s    from app.services.niche_resolver import business_niche as _bn\n"
                   "%s    _rn = _bn(account_id, db=db)\n"
                   "%s    if _rn:\n"
                   "%s        a = (a[0], a[1], _rn, a[3])\n") % (ind, MARK, ind, ind, ind, ind)
            src = rx.sub(lambda m: m.group(0) + ins.lstrip("\n"), src, count=1)
            changed.append("stage_of -> business_niche")
        open(ONB, "w", encoding="utf-8").write(src)
    # EDIT-3 scenarios.py
    s2 = open(SCEN, encoding="utf-8").read()
    if MARK in s2:
        log("  scenarios.py: уже пропатчен, пропуск")
    else:
        rx3 = re.compile(r"\n([ \t]*)f\[\"has_niche\"\]\s*=\s*([^\n]+)\n")
        m3 = rx3.findall(s2)
        sig = re.search(r"def collect_facts\(([^)]*)\)", s2)
        params = [p.strip().split(":")[0].split("=")[0].strip() for p in (sig.group(1).split(",") if sig else [])]
        avar = "account_id" if "account_id" in params else ("acc" if "acc" in params else "")
        if len(m3) != 1 or not avar:
            log("  SKIP-EDIT3: has_niche найден %d раз, account-переменная=%r" % (len(m3), avar))
            skipped.append("collect_facts")
        else:
            shutil.copy2(SCEN, SCEN + ".before_niche_" + TS)
            ind, orig = m3[0]
            dbarg = ", db=db" if "db" in params else ""
            new = ("\n%sf[\"has_niche\"] = %s\n"
                   "%sif not f[\"has_niche\"]:  # %s\n"
                   "%s    from app.services.niche_resolver import business_niche as _bn2\n"
                   "%s    f[\"has_niche\"] = bool(_bn2(%s%s))\n") % (ind, orig, ind, MARK, ind, ind, avar, dbarg)
            s2 = rx3.sub(new, s2, count=1)
            open(SCEN, "w", encoding="utf-8").write(s2)
            changed.append("collect_facts -> business_niche")
    return changed, skipped


def backfill():
    from sqlalchemy import text as T
    from app.services.niche_resolver import ensure_profile_niche, resolve_niche
    db = db_open()
    accs = [r[0] for r in db.execute(T("SELECT account_id FROM accounts ORDER BY account_id")).fetchall()]
    done, confl, rb = [], [], []
    for a in accs:
        before = resolve_niche(a, db=db)
        if before["conflict"]:
            confl.append((a, before["candidates"]))
            continue
        r = ensure_profile_niche(a, db=db)
        if r.get("synced"):
            done.append((a, r["niche"], r["source"]))
            rb.append("UPDATE accounts SET company_niche='' WHERE account_id='%s';" % a)
    db.close()
    if rb:
        open(RBSQL, "w", encoding="utf-8").write("\n".join(rb) + "\n")
    return done, confl


def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else ""
    if phase == "before":
        log("=== PREFLIGHT ===")
        for f in (ONB, SCEN):
            log("  %s %s" % (os.path.basename(f), "OK" if os.path.exists(f) else "НЕТ ФАЙЛА"))
        write_resolver()
        log("=== ПРЯМЫЕ ЧТЕНИЯ company_niche В КОДЕ ===")
        import subprocess
        g = subprocess.run(["grep", "-rn", "--include=*.py", "company_niche", os.path.join(BK, "app")],
                           capture_output=True, text=True).stdout.strip().splitlines()
        for l in g:
            log("  " + l[len(BK) + 1:][:150])
        log("  всего вхождений: %d" % len(g))
        snap = collect()
        json.dump(snap, open(SNAP, "w"), ensure_ascii=False)
        log("=== BEFORE: %d аккаунтов, снимок в %s ===" % (len(snap), SNAP))
        low = [a for a, v in snap.items() if str(v["stage"]).isdigit() and int(v["stage"]) < 3]
        log("  ниже стадии 3: %d" % len(low))
    elif phase == "apply":
        log("=== PATCH ===")
        changed, skipped = apply_edits()
        log("  применено: %s" % (changed or "ничего"))
        log("  пропущено: %s" % (skipped or "ничего"))
        log("=== COMPILE-GATE ===")
        for f in (RES, ONB, SCEN):
            py_compile.compile(f, doraise=True)
        log("  py_compile OK")
        sys.path.insert(0, BK); os.chdir(BK)
        import app.main  # noqa
        log("  import app.main OK")
        log("=== SMOKE: stage_of на живых аккаунтах ===")
        sm = collect()
        bad = [a for a, v in sm.items() if str(v["stage"]).startswith("ERR")]
        if bad:
            log("  FAIL: stage_of падает на %s -> ОТКАТ" % bad[:3])
            for f in (ONB, SCEN):
                b = f + ".before_niche_" + TS
                if os.path.exists(b):
                    shutil.copy2(b, f)
            sys.exit(1)
        log("  stage_of отработал на всех %d без исключений" % len(sm))
        log("=== SELFTEST РЕЗОЛВЕРА ===")
        from app.services.niche_resolver import _parse, _key
        assert _parse("{'a': 1}") == {"a": 1}, "python-repr не разобран"
        assert _parse('{"a": 1}') == {"a": 1}, "json не разобран"
        assert _key(" \u0410 \u0411 ") == "\u0430 \u0431", "нормализация"
        log("  PASS: python-repr, json, нормализация")
        log("=== BACKFILL ===")
        done, confl = backfill()
        for a, n, s in done:
            log("  синхронизирован %-46s <- %-16s %s" % (a[:46], s, n[:50]))
        for a, c in confl:
            log("  CONFLICT %-46s %s" % (a[:46], c))
        log("  итого: синхронизировано %d, конфликтов %d" % (len(done), len(confl)))
        log("  откат бэкфилла: %s" % (RBSQL if done else "не требуется"))
    elif phase == "after":
        before = json.load(open(SNAP))
        after = collect()
        log("=== ТАБЛИЦА ПО %d АККАУНТАМ ===" % len(after))
        log("  %-44s %-15s %-5s %-5s %-4s %s" % ("account_id", "источник", "was", "now", "cat", "ниша"))
        moved = 0
        for a in sorted(after):
            b, n = before.get(a, {}), after[a]
            if str(b.get("stage")) != str(n["stage"]):
                moved += 1
            log("  %-44s %-15s %-5s %-5s %-4s %s%s" % (
                a[:44], n["source"], b.get("stage", "?"), n["stage"],
                "да" if n["detected"] else "-", n["niche"],
                "  <<< CONFLICT" if n["conflict"] else ""))
        low = [a for a, v in after.items() if str(v["stage"]).isdigit() and int(v["stage"]) < 3]
        withniche = [a for a in low if after[a]["niche"]]
        log("=== ИТОГ ===")
        log("  ниша резолвится у: %d из %d" % (len([1 for v in after.values() if v["niche"]]), len(after)))
        log("  сменили стадию: %d" % moved)
        log("  ниже стадии 3: %d, из них с известной нишей (кандидаты на этап 2): %d" % (len(low), len(withniche)))
        for a in withniche:
            log("    %s" % a)
        log("  PASS" if not [1 for v in after.values() if str(v["stage"]).startswith("ERR")] else "  FAIL")


if __name__ == "__main__":
    main()
