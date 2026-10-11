import ast, re, shutil, sys, time
p = "app/api/accounts.py"
s0 = open(p, encoding="utf-8").read()
if re.search(r"^from typing import .*\bOptional\b", s0, re.M) or "import typing" in s0:
    print("Optional уже импортирован"); raise SystemExit(0)
m = re.search(r"^from typing import (.+)$", s0, re.M)
if m:
    s = s0[:m.start()] + "from typing import Optional, " + m.group(1) + s0[m.end():]
    how = "дополнил существующий import typing"
else:
    lines = s0.split("\n")
    idx = 0
    for i, l in enumerate(lines[:30]):
        if l.startswith("import ") or l.startswith("from "):
            idx = i + 1
    lines.insert(idx, "from typing import Optional")
    s = "\n".join(lines)
    how = "вставил новую строку импорта после строки %d" % idx
ast.parse(s)
print("ЧТО ДЕЛАЮ:", how)
if "--apply" in sys.argv:
    shutil.copy(p, "%s.bak_optional_%d" % (p, int(time.time())))
    open(p, "w", encoding="utf-8").write(s)
    print("ЗАПИСАНО")
else:
    print("dry-run OK")
