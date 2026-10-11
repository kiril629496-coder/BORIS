import os, shutil, sys, time, py_compile
sys.path.insert(0, os.getcwd())
P = "app/api/client_memory.py"
src = open(P, encoding="utf-8").read()
if '"self": account_id' in src:
    print("УЖЕ ПРИМЕНЁН"); sys.exit(0)

A1 = '        "   AND f.category NOT IN (\'rule\', \'keys\')"\n'
A2 = ('        " GROUP BY f.id"\n'
      '    ).bindparams(bindparam("accs", expanding=True)), {"accs": accs}).all()\n')
for f, n in ((A1, "строка category"), (A2, "GROUP BY + bindparams")):
    c = src.count(f)
    if c != 1:
        print("СТОП: якорь «%s» найден %d раз (нужно 1)" % (n, c)); sys.exit(1)

N1 = A1 + ('        "   AND (COALESCE(f.scope, \'business\') = \'business\'"\n'
           '        "        OR f.account_id = :self)"\n')
N2 = ('        " GROUP BY f.id"\n'
      '    ).bindparams(bindparam("accs", expanding=True)),\n'
      '        {"accs": accs, "self": account_id}).all()\n')
new = src.replace(A1, N1, 1).replace(A2, N2, 1)
try:
    compile(new, P, "exec")
except SyntaxError as e:
    print("СТОП: синтаксис ДО записи —", e); sys.exit(1)
bak = "%s.bak_scope_%d" % (P, int(time.time()))
shutil.copy2(P, bak)
open(P, "w", encoding="utf-8").write(new)
open("/tmp/scope_bak.txt", "w").write(bak)
try:
    py_compile.compile(P, doraise=True)
    import importlib
    importlib.import_module("app.main")
    print("ПРАВКА ПРИМЕНЕНА, py_compile OK, import app.main OK")
    print("бэкап:", bak)
except Exception as e:
    shutil.copy2(bak, P)
    print("ОТКАТ:", str(e)[:200]); sys.exit(1)
