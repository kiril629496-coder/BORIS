import io, time, ast
P = "monitor_preflight.py"
src = io.open(P, encoding="utf-8").read()
if "ANY(%s::uuid[])" in src:
    print("Уже применён.")
    raise SystemExit(0)
out, changed = [], []
for i, line in enumerate(src.split("\n"), 1):
    if "ANY(%s)" in line and any(t in line for t in ("monitor_messages", "monitor_sources", "source_id")):
        line = line.replace("ANY(%s)", "ANY(%s::uuid[])")
        changed.append((i, line.strip()))
    out.append(line)
new = "\n".join(out)
ast.parse(new)
if not changed:
    print("СТОП: подходящих строк не найдено, файл не тронут.")
    raise SystemExit(1)
bak = P + ".bak_uuid_%d" % int(time.time())
io.open(bak, "w", encoding="utf-8").write(src)
io.open(P, "w", encoding="utf-8").write(new)
print("Изменено строк:", len(changed), "| бэкап:", bak)
for n, t in changed:
    print("  %d: %s" % (n, t[:100]))
