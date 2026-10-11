import ast, re, shutil, sys, time
p = "app/api/banners.py"
s0 = open(p, encoding="utf-8").read()
i = s0.find("def _template_background_query")
if i < 0:
    print("функция не найдена"); raise SystemExit(1)
seg = s0[i:i + 900]
m = re.search(r"\n([ \t]*)scene = (.+)\n", seg)
if not m:
    print("строка scene не найдена в функции"); raise SystemExit(1)
print("НАЙДЕНО:", m.group(0).strip()[:120])
if "(style or {}).get(\"scene\")" in m.group(2):
    print("уже пропатчено"); raise SystemExit(0)
ind = m.group(1)
new_line = "\n%sscene = (style or {}).get(\"scene\") or (%s)\n" % (ind, m.group(2))
s = s0[:i] + seg.replace(m.group(0), new_line, 1) + s0[i + 900:]
ast.parse(s)
print("НОВАЯ СТРОКА:", new_line.strip()[:140])
if "--apply" in sys.argv:
    shutil.copy(p, "%s.bak_scene_%d" % (p, int(time.time())))
    open(p, "w", encoding="utf-8").write(s)
    print("ЗАПИСАНО")
else:
    print("dry-run OK")
