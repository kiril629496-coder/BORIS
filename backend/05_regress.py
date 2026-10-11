import os, json, sys
for _l in open('.env'):
    _l = _l.strip()
    if _l and not _l.startswith('#') and '=' in _l:
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))
sys.path.insert(0, '/root/BORIS/backend')
from app.db.session import SessionLocal
from app.api import client_memory as CM
from app.services import marketing_rules as MR

base = json.load(open('baseline.json', encoding='utf-8'))
QUESTIONS = {a: list(v.keys()) for a, v in base['answers'].items()}
db = SessionLocal()
bad = 0

print("=== 1. Сверка ответов ДО и ПОСЛЕ ===")
for acc, qs in QUESTIONS.items():
    for q in qs:
        was = base["answers"].get(acc, {}).get(q, {})
        try:
            r = CM.find_answer(db, acc, q)
            now = {"found": bool(r.get("found")),
                   "value": str(r.get("value") or r.get("answer") or "")[:120],
                   "id": r.get("id"), "name": str(r.get("name") or "")[:80]}
        except Exception as e:
            now = {"error": str(e)[:120]}
        if was != now:
            bad += 1
            print("  РАСХОЖДЕНИЕ %s | %s\n    было: %s\n    стало: %s" % (acc, q, was, now))
print("  расхождений: %s" % bad)

print("\n=== 2. Правило не отвечает покупателю ===")
rule = MR.get_optimization_rule(db, "cpx_raise")
if not rule:
    print("  ПРОПУСК: активного правила нет")
else:
    probes = [rule["name"], rule["text"][:90],
              "масштабирование объявлений с высокой конверсией",
              "при какой конверсии повышать ставку",
              "на сколько увеличить ставку"]
    for acc in QUESTIONS:
        for p in probes:
            r = CM.find_answer(db, acc, p)
            leak = bool(r.get("found")) and (
                "конверси" in str(r.get("value") or "").lower()
                or "ставк" in str(r.get("value") or "").lower())
            mark = "УТЕЧКА" if leak else "ok"
            if leak:
                bad += 1
            print("  %s | %s | %s | found=%s %s"
                  % (mark, acc, p[:42], r.get("found"), str(r.get("value") or "")[:50]))

print("\n=== 3. rules_for без новых правил ===")
for acc in QUESTIONS:
    now = len(CM.rules_for(db, acc))
    was = base["rules"].get(acc)
    ok = (now == was)
    if not ok:
        bad += 1
    print("  %s %s: было %s, стало %s" % ("ok" if ok else "ОТЛИЧАЕТСЯ", acc, was, now))
    for it in CM.rules_for(db, acc):
        if str(it.get("account_id")) == MR.PLATFORM_ACCOUNT or it.get("scope") == "platform":
            bad += 1
            print("    УТЕЧКА ПЛАТФОРМЕННОГО ПРАВИЛА:", it)

print("\n=== 4. Носитель __platform__ изолирован ===")
for acc in QUESTIONS:
    accs = CM.scope_accounts(db, acc)
    leak = MR.PLATFORM_ACCOUNT in accs
    if leak:
        bad += 1
    print("  %s %s -> %s" % ("УТЕЧКА" if leak else "ok", acc, accs))
own = CM.scope_accounts(db, MR.PLATFORM_ACCOUNT)
print("  scope_accounts('__platform__') -> %s (ожидается только он сам)" % own)
if [a for a in own if a != MR.PLATFORM_ACCOUNT]:
    bad += 1
    print("  УТЕЧКА: носитель состоит в группе")

print("\n=== 5. Fail-closed ===")
print("  несуществующая точка применения ->", MR.get_optimization_rule(db, "нет_такой"))
print("  matches(None,...) ->", MR.matches(None, 1000, 500))
print("  proposed_bid(None,...) ->", MR.proposed_bid(None, 12))
if rule:
    print("  мало просмотров ->", MR.matches(rule, 5, 5))
    print("  низкая конверсия ->", MR.matches(rule, 1000, 20))
    print("  подходит ->", MR.matches(rule, 714, 38))
    print("  ставка 12 ->", MR.proposed_bid(rule, 12))

print("\n=== ИТОГ: %s ===" % ("КРАСНЫЙ, проблем: %s" % bad if bad else "ЗЕЛЁНЫЙ"))
