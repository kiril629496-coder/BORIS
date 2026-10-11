import os, shutil, sys, time, py_compile
sys.path.insert(0, os.getcwd())
P = "app/api/client_memory.py"
src = open(P, encoding="utf-8").read()
OLD = '        "   AND f.category <> \'rule\'"\n'
NEW = '        "   AND f.category NOT IN (\'rule\', \'keys\')"\n'
if "NOT IN ('rule', 'keys')" in src:
    print("УЖЕ ПРИМЕНЁН"); sys.exit(0)
n = src.count(OLD)
if n != 1:
    print("СТОП: якорь найден %d раз (нужно 1)" % n); sys.exit(1)
new = src.replace(OLD, NEW, 1)
try:
    compile(new, P, "exec")
except SyntaxError as e:
    print("СТОП: синтаксис ДО записи —", e); sys.exit(1)
bak = "%s.bak_keys_%d" % (P, int(time.time()))
shutil.copy2(P, bak)
open(P, "w", encoding="utf-8").write(new)
open("/tmp/keys_bak.txt", "w").write(bak)
try:
    py_compile.compile(P, doraise=True)
    import importlib
    importlib.import_module("app.main")
    print("ФИЛЬТР ПРИМЕНЁН, py_compile OK, import app.main OK")
    print("бэкап:", bak)
except Exception as e:
    shutil.copy2(bak, P)
    print("ОТКАТ:", str(e)[:180]); sys.exit(1)
