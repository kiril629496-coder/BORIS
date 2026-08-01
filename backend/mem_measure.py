"""Замер памяти: факты, покрытие, ответы без модели. Запуск: python3 /tmp/mem_measure.py before|after"""
import sys, json, datetime
from sqlalchemy import text
from app.db.session import SessionLocal
from app.api import client_memory as CM

LABEL = sys.argv[1] if len(sys.argv) > 1 else "before"
ACCS = ["otdushi", "evz_denis_aleksandra_evakuatory_i_pricepy_22264"]

# фиксированный набор — тот же, что гоняли раньше
QUESTIONS = {
 "otdushi": ["сколько стоит песня на заказ", "цена видеопоздравления",
             "какая гарантия", "книга для мамы", "сколько стоит стих на юбилей",
             "как быстро сделаете", "можно послушать пример", "как оплатить"],
 "evz_denis_aleksandra_evakuatory_i_pricepy_22264":
            ["цена переоборудования газель фермер", "это с установкой платформы?",
             "сколько стоит прицеп", "какая гарантия на переоборудование",
             "сколько стоит эвакуатор под ключ", "делаете ремонт автомобилей",
             "сроки изготовления", "работаете с юрлицами"],
}

def ask(db, acc, q):
    try:
        r = CM.find_answer(db, acc, q)
    except Exception as e:
        return {"q": q, "answered": False, "err": repr(e)[:120]}
    if not r:
        return {"q": q, "answered": False, "text": ""}
    if isinstance(r, dict):
        txt = r.get("answer") or r.get("text") or r.get("value") or ""
        fid = r.get("fact_id") or r.get("id")
    else:
        txt, fid = str(r), None
    return {"q": q, "answered": bool(txt), "text": str(txt)[:90], "fact_id": fid}

out = {"label": LABEL, "at": datetime.datetime.now().isoformat()[:19], "accounts": {}}
db = SessionLocal()
try:
    for acc in ACCS:
        st = {}
        st["facts"] = db.execute(text("SELECT count(*) FROM client_facts WHERE account_id=:a AND status<>'rejected'"), {"a": acc}).scalar() or 0
        st["confirmed"] = db.execute(text("SELECT count(*) FROM client_facts WHERE account_id=:a AND status='confirmed'"), {"a": acc}).scalar() or 0
        st["draft"] = db.execute(text("SELECT count(*) FROM client_facts WHERE account_id=:a AND status='draft'"), {"a": acc}).scalar() or 0
        st["conflict"] = db.execute(text("SELECT count(*) FROM client_facts WHERE account_id=:a AND status='conflict'"), {"a": acc}).scalar() or 0
        st["usable"] = db.execute(text("SELECT count(*) FROM client_facts WHERE account_id=:a AND (status='confirmed' OR (status='draft' AND confidence>=80))"), {"a": acc}).scalar() or 0
        res = [ask(db, acc, q) for q in QUESTIONS[acc]]
        st["answers"] = res
        st["answered"] = sum(1 for r in res if r["answered"])
        st["unclear"] = len(res) - st["answered"]
        st["coverage_pct"] = round(100 * st["answered"] / len(res)) if res else 0
        out["accounts"][acc] = st
finally:
    db.close()

path = f"/tmp/mem_{LABEL}.json"
json.dump(out, open(path, "w"), ensure_ascii=False, indent=1)

for acc, st in out["accounts"].items():
    print(f"\n=== {acc[:34]} ===")
    print(f"  фактов: {st['facts']} | подтверждено: {st['confirmed']} | черновиков: {st['draft']} | конфликтов: {st['conflict']} | в пуле ответов: {st['usable']}")
    print(f"  вопросов: {len(st['answers'])} | ответил из памяти: {st['answered']} | «уточню»: {st['unclear']} | покрытие: {st['coverage_pct']}%")
    for r in st["answers"]:
        mark = "✅" if r["answered"] else "· "
        print(f"    {mark} {r['q'][:42]:44} {r.get('text','')[:46]}")
print(f"\nзамер сохранён: {path}")
