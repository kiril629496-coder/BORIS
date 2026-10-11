"""Идемпотентный патч изоляции rule_marketing от контура ответов покупателю."""
import re, shutil, time, py_compile

SRC = "/root/BORIS/backend/app/api/client_memory.py"
OLD = "NOT IN ('rule', 'keys')"
NEW = "NOT IN ('rule', 'keys', 'rule_marketing')"

body = open(SRC, encoding="utf-8").read()
if NEW in body:
    print("патч уже применён, повторно ничего не делаю")
else:
    n = body.count(OLD)
    print("найдено вхождений %r: %s" % (OLD, n))
    if n == 0:
        raise SystemExit("ОСТАНОВ: шаблон не найден, файл изменился — патч не применяю")
    bak = SRC + ".bak_rulemkt_%s" % int(time.time())
    shutil.copy2(SRC, bak)
    print("бэкап:", bak)
    open(SRC, "w", encoding="utf-8").write(body.replace(OLD, NEW))
    py_compile.compile(SRC, doraise=True)
    print("применено вхождений: %s, компиляция OK" % n)

check = open(SRC, encoding="utf-8").read()
print("\n--- контрольные строки ---")
for i, line in enumerate(check.splitlines(), 1):
    if "rule_marketing" in line or "NOT IN ('rule'" in line:
        print("%s: %s" % (i, line.strip()))
