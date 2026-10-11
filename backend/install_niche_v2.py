# -*- coding: utf-8 -*-
"""Stage 2: do_for для всех billing_mode + nudge остаётся в auto-контуре + fallback резолвера."""
import os, sys, re, json, shutil, datetime, py_compile, importlib
BK = "/root/BORIS/backend"; sys.path.insert(0, BK); os.chdir(BK)
RES = BK + "/app/services/niche_resolver.py"; NEW = RES + ".new"; ONB = BK + "/app/onboarding.py"
TS = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
MARKB = "DO_FOR_ALL_BILLING_V1"; MARKN = "NUDGE_BILLING_GATE_V1"
OLD_SQL = "SELECT account_id FROM accounts WHERE billing_mode='auto' OR billing_mode IS NULL"
NEW_SQL = "SELECT account_id FROM accounts"
R = []


def log(s):
    print(s); R.append(s)


def stop(m):
    print("STOP: %s" % m); sys.exit(1)


GATE_FN = '''

def _nudge_billing_ok(account_id: str) -> bool:  # NUDGE_BILLING_GATE_V1
    """Подсказки клиентам остаются в прежнем круге billing_mode. fail-closed."""
    db = SessionLocal()
    try:
        bm = db.execute(text("SELECT billing_mode FROM accounts WHERE account_id=:a"),
                        {"a": account_id}).scalar()
        return bm is None or bm == "auto"
    except Exception:
        return False
    finally:
        db.close()

'''


def elig(apply_billing, db):
    from sqlalchemy import text as T
    from app.onboarding import stage_of
    rows = db.execute(T("""SELECT a.account_id, coalesce(a.billing_mode,'NULL'),
        (SELECT round(extract(epoch from (now()-p.reached_at))/3600.0,1)
           FROM onboarding_progress p WHERE p.account_id=a.account_id)
        FROM accounts a ORDER BY a.account_id""")).fetchall()
    out = {}
    for a, bm, h in rows:
        st = (stage_of(a, db=db) or {}).get("\u0441\u0442\u0430\u0434\u0438\u044f")
        ok = ((not apply_billing) or bm in ("auto", "NULL")) and isinstance(st, int) \
            and st < 6 and h is not None and float(h) >= 24
        out[a] = {"billing": bm, "stage": st, "hours": h, "elig": bool(ok)}
    return out


def patch_onboarding():
    src = open(ONB, encoding="utf-8").read()
    ch = []
    if MARKB in src:
        ch.append("billing-фильтр уже снят")
    else:
        if src.count(OLD_SQL) != 1:
            stop("якорь billing-фильтра найден %d раз" % src.count(OLD_SQL))
        src = src.replace(OLD_SQL, NEW_SQL + "  -- " + MARKB, 1)
        ch.append("stuck_accounts: billing-фильтр снят")
    if MARKN in src:
        ch.append("гейт nudge уже стоит")
    else:
        m = re.search(r'\ndef nudge\([^)]*\)[^\n]*:\n(?P<ind>[ \t]+)(?P<doc>"""(?:.|\n)*?"""\n)?', src)
        if not m:
            stop("не найден def nudge")
        ind = m.group("ind")
        gate = ('%s# %s — расширение do_for не расширяет круг видимых клиенту действий\n'
                '%sif not _nudge_billing_ok(account_id):\n'
                '%s    return {"status": "skip", "reason": "подсказки отключены для billing_mode"}\n'
                ) % (ind, MARKN, ind, ind)
        src = src[:m.end()] + gate + src[m.end():]
        if src.count("\ndef nudge(") != 1:
            stop("def nudge встречается не один раз")
        src = src.replace("\ndef nudge(", GATE_FN + "\ndef nudge(", 1)
        ch.append("nudge: добавлен fail-closed гейт по billing_mode")
    return src, ch


def tests(db):
    import app.services.niche_resolver as nr
    ok = True
    BYT_R, BYT_P, PBI = "3411770_94346", "prodazha_bytovok_25677", "pbi_artashes_plitka_mo_83993"

    def chk(name, cond, got):
        global_ok = "PASS" if cond else "FAIL"
        log("  %-4s %-52s %s" % (global_ok, name, got))
        return cond
    r = nr.resolve_niche(PBI, db=db)
    ok &= chk("PBI: ниша из accounts.company_niche", r["source"] == "profile",
              "source=%s niche=%r" % (r["source"], r["niche"][:40]))
    for a, exp in ((BYT_R, "\u0410\u0440\u0435\u043d\u0434\u0430 \u0431\u044b\u0442\u043e\u0432\u043e\u043a"),
                   (BYT_P, "\u041f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0441\u0442\u0432\u043e \u0438 \u043f\u0440\u043e\u0434\u0430\u0436\u0430 \u0431\u044b\u0442\u043e\u0432\u043e\u043a")):
        r = nr.resolve_niche(a, db=db)
        ok &= chk("Бытовки %s: ниша из accounts.name" % a[:18],
                  r["source"] == "account_name" and r["niche"] == exp,
                  "source=%s conf=%s niche=%r" % (r["source"], r["confidence"], r["niche"]))
    r = nr.resolve_niche("antonybatono_78732", db=db)
    ok &= chk("заполненный онбординг: из form_data", r["source"] == "onboarding_form",
              "source=%s niche=%r" % (r["source"], r["niche"][:40]))
    v, s = nr.pick_source({"profile": "", "onboarding_form": "", "scenario_input": "X", "account_name": "Y"})
    ok &= chk("scenario-only: из input_data.direction", s == "scenario_input", "source=%s" % s)
    r = nr.resolve_niche("qa_dry_moskva", db=db)
    ok &= chk("нет данных: empty/needs_input", not r["niche"] and r["status"] == "needs_input",
              "status=%s reason=%s" % (r["status"], r.get("reason", "")[:45]))
    d = nr.derive_niche_from_profile("\u041f\u0411\u0418 \u0410\u0440\u0442\u0430\u0448\u0435\u0441 \u041f\u043b\u0438\u0442\u043a\u0430 \u041c\u041e",
                                     "\u0417\u0430\u0432\u043e\u0434 \u043f\u043e \u0438\u0437\u0433\u043e\u0442\u043e\u0432\u043b\u0435\u043d\u0438\u044e \u0431\u0440\u0443\u0441\u0447\u0430\u0442\u043a\u0438")
    ok &= chk("person-label не проходит fallback", not d["value"], "reason=%s" % d.get("reason", "")[:50])
    src = open(ONB, encoding="utf-8").read()
    fn = src[src.index("def stuck_accounts"):src.index("def stuck_accounts") + 700]
    ok &= chk("do_for: billing_mode больше не фильтрует", "billing_mode" not in fn and MARKB in fn,
              "маркер %s" % ("есть" if MARKB in fn else "НЕТ"))
    import app.onboarding as o
    importlib.reload(o)
    from sqlalchemy import text as T
    b4 = db.execute(T("SELECT nudge_count, nudged_at FROM onboarding_progress WHERE account_id=:a"),
                    {"a": BYT_P}).fetchone()
    res = o.nudge(BYT_P, force=True)
    af = db.execute(T("SELECT nudge_count, nudged_at FROM onboarding_progress WHERE account_id=:a"),
                    {"a": BYT_P}).fetchone()
    ok &= chk("nudge: manual-аккаунт пропущен даже с force", res.get("status") == "skip",
              "%s" % str(res)[:60])
    ok &= chk("подсказок клиентам не отправлено", str(b4) == str(af), "счётчик %s -> %s" % (b4, af))
    ok &= chk("nudge: auto-контур не тронут", o._nudge_billing_ok("evgeniy_peregorodki_12447") is True, "eligible")
    return ok


def main():
    from app.db.session import SessionLocal
    db = SessionLocal()
    log("=== 1. ELIGIBILITY ДО/ПОСЛЕ (правило billing) ===")
    bef, aft = elig(True, db), elig(False, db)
    new = [a for a in aft if aft[a]["elig"] and not bef[a]["elig"]]
    log("  eligible было %d, станет %d, добавится %d: %s"
        % (sum(1 for v in bef.values() if v["elig"]), sum(1 for v in aft.values() if v["elig"]), len(new), new))
    for a in ("pbi_artashes_plitka_mo_83993", "3411770_94346", "prodazha_bytovok_25677"):
        log("  %-30s billing=%-7s стадия=%-3s часов=%-6s было=%-5s станет=%s"
            % (a[:30], aft[a]["billing"], aft[a]["stage"], aft[a]["hours"], bef[a]["elig"], aft[a]["elig"]))
    log("=== 2. ПАТЧ ===")
    src, ch = patch_onboarding()
    py_compile.compile(NEW, doraise=True)
    shutil.copy2(ONB, ONB + ".before_v2_" + TS); open(ONB, "w", encoding="utf-8").write(src)
    shutil.copy2(RES, RES + ".before_v2_" + TS); shutil.copy2(NEW, RES); os.remove(NEW)
    for c in ch:
        log("  " + c)
    log("  резолвер заменён, бэкапы *.before_v2_%s" % TS)
    log("=== 3. COMPILE ===")
    for f in (RES, ONB):
        py_compile.compile(f, doraise=True)
    log("  py_compile OK")
    log("=== 4. ТЕСТЫ ===")
    good = tests(db)
    log("=== ИТОГ: %s ===" % ("PASS" if good else "FAIL"))
    if not good:
        log("  откат: cp %s.before_v2_%s %s (то же для резолвера)" % (ONB, TS, ONB))
    db.close()
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
