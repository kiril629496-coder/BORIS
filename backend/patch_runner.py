# -*- coding: utf-8 -*-
"""Автономный контур: отбор по мандату + две ветки (разгон/экономия)."""
import sys, time, py_compile, os
F = os.environ.get("P_F", "cpx_advisor_runner.py")
APPLY = "--apply" in sys.argv
E = []

E.append(("1. отбор аккаунтов по мандату",
'''        auto_accounts = set()
        cur.execute("SELECT account_id, value FROM storage WHERE key = 'kpi_settings';")
        for aid, val in cur.fetchall():
            try:
                if json.loads(val).get("bid_autopilot"):
                    auto_accounts.add(aid)
            except Exception:
                pass''',
'''        # Разрешение на автономные действия даёт МАНДАТ, а не флаг bid_autopilot:
        # у флага нет ни срока, ни лимитов, ни имени того, кто его выдал.
        auto_accounts = set()
        cur.execute(
            "SELECT DISTINCT unnest(account_scope) FROM money_mandates"
            " WHERE status='active' AND revoked_at IS NULL"
            "   AND (valid_until IS NULL OR valid_until > now())"
            "   AND (valid_from IS NULL OR valid_from <= now());")
        for row in cur.fetchall():
            auto_accounts.add(row[0])
        print(f"  с активным мандатом: {len(auto_accounts)}: {sorted(auto_accounts)}")'''))

E.append(("2. план действий вместо фиксированного raise",
'''                    considered = would_apply = applied = skipped = blocked = failed = 0
                    budget = MAX_ITEMS_PER_CYCLE
                    for action_key, act in AUTO_EXECUTABLE:
                        for it in recs.get(action_key, []):
                            considered += 1''',
'''                    considered = would_apply = applied = skipped = blocked = failed = 0
                    plan, mode = _build_plan(acc, recs)
                    budget = len(plan)
                    print(f"    режим: {mode}, к исполнению {len(plan)}")
                    for it, act in plan:
                            considered += 1'''))

E.append(("3. функция плана",
'''def main():''',
'''def _build_plan(acc, recs):
    """Что делать сегодня: догонять или экономить.

    Отстаём  -> поднимаем ставки у сильных объявлений (intraday-отбор).
    С запасом-> снижаем там, где переплачиваем (советник).
    Ровно    -> ничего, рано судить.
    Темп задаёт мандат: max_actions_run за прогон, мелкими шагами.
    """
    try:
        sys.path.insert(0, "/root/BORIS/backend")
        from app.db.session import SessionLocal
        from app.services.intraday import select_intraday_raise_candidates
        db = SessionLocal()
        try:
            r = select_intraday_raise_candidates(db, acc)
        finally:
            db.close()
    except Exception as e:
        print(f"    intraday недоступен: {str(e)[:90]}")
        return [], "ошибка"
    pace = (r.get("kpi_pace") or {}).get("state")
    cap = int((r.get("mandate") or {}).get("max_actions_run") or MAX_ITEMS_PER_CYCLE)
    if r.get("decision") == "RAISE_CANDIDATES":
        top = [c for c in (r.get("top") or []) if c.get("status") == "eligible"]
        n = int(r.get("selected_for_execution") or 0) or cap
        return ([({"id": int(c["item_id"])}, "raise") for c in top[:n]],
                f"разгон, отстаём ({pace})")
    if pace == "AHEAD":
        low = [it for it in (recs.get("lower") or []) if it.get("id")]
        return ([({"id": int(it["id"])}, "lower") for it in low[:cap]],
                "экономия, идём с запасом")
    return [], f"пауза ({pace})"


def main():'''))

src = open(F, encoding="utf-8").read()
bad = 0
for name, o, n in E:
    c = src.count(o)
    print("%-45s якорь %d раз" % (name, c))
    if c != 1:
        bad += 1
if bad:
    print("ОСТАНОВ: %d якорей не однозначны" % bad); sys.exit(1)
out = src
for name, o, n in E:
    out = out.replace(o, n)
print("строк было: %d, стало: %d" % (src.count("\n"), out.count("\n")))
if not APPLY:
    print("режим: СУХОЙ ПРОГОН"); sys.exit(0)
bak = "%s.bak_auto_%d" % (F, int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(F, "w", encoding="utf-8").write(out)
py_compile.compile(F, doraise=True)
print("бэкап: %s" % bak); print("правок: %d | ПРИМЕНЕНО" % len(E))
