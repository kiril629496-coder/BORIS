# -*- coding: utf-8 -*-
"""QA-скрипты в учёт расхода. Без --apply только план."""
import sys, os, time, shutil, tempfile, py_compile

APPLY = "--apply" in sys.argv
ROOT = "/root/BORIS/backend"

TPL = '''        if r.status_code==200:
            _d = r.json()
            try:
                sys.path.insert(0, "/root/BORIS/backend")
                from app.usage import log_usage as _lg
                _u = _d.get("usage") or {}
                _lg("qa_internal", "openai", "gpt-4o-mini", "%s",
                    _u.get("prompt_tokens", 0), _u.get("completion_tokens", 0),
                    request_id=r.headers.get("x-request-id"))
            except Exception as _ue:
                print("[usage qa]", str(_ue)[:100], flush=True)
            return _d["choices"][0]["message"]["content"].strip()
        return "vision статус "+str(r.status_code)
    except Exception as e:
        return "%s: "+str(e)[:80]'''

OLD_TPL = '''        if r.status_code==200:
            return r.json()["choices"][0]["message"]["content"].strip()
        return "vision статус "+str(r.status_code)
    except Exception as e:
        return "%s: "+str(e)[:80]'''

E = [
    (os.path.join(ROOT, "qa_tester.py"), "UX-разбор не удался", "qa_ux_review"),
    (os.path.join(ROOT, "qa_tester.py"), "Лендинг-разбор не удался", "qa_landing_review"),
    (os.path.join(ROOT, "qa_tester_client.py"), "UX-разбор не удался", "qa_ux_review_client"),
]

print("=== ЯКОРЯ ===")
files, bad = {}, 0
for path, tail, op in E:
    if path not in files:
        files[path] = open(path, encoding="utf-8").read()
    old = OLD_TPL % tail
    c = files[path].count(old)
    print("  %-24s %-28s %d раз" % (os.path.basename(path), tail, c))
    if c != 1:
        bad += 1
if bad:
    print("STOP: %d якорей не однозначны, ничего не тронуто" % bad)
    sys.exit(1)

outs = dict(files)
for path, tail, op in E:
    outs[path] = outs[path].replace(OLD_TPL % tail, TPL % (op, tail))

print("=== IMPORT SYS ===")
for path in outs:
    if "\nimport sys" not in outs[path] and not outs[path].startswith("import sys"):
        outs[path] = outs[path].replace("import ", "import sys\nimport ", 1)
        print("  %-24s добавлен import sys" % os.path.basename(path))
    else:
        print("  %-24s import sys уже есть" % os.path.basename(path))

print("=== COMPILE-GATE ===")
tmp = {}
for path, out in outs.items():
    fd, t = tempfile.mkstemp(suffix=".py"); os.close(fd)
    open(t, "w", encoding="utf-8").write(out)
    py_compile.compile(t, doraise=True)
    tmp[path] = t
    print("  %-24s %d -> %d строк  OK" % (os.path.basename(path),
                                          files[path].count("\n"), out.count("\n")))

if not APPLY:
    for t in tmp.values():
        os.unlink(t)
    print("режим: СУХОЙ ПРОГОН, оригиналы не тронуты")
    sys.exit(0)

ts = int(time.time())
for path, t in tmp.items():
    bak = "%s.bak_qa_%d" % (path, ts)
    shutil.copy2(path, bak)
    shutil.copy2(t, path)
    os.unlink(t)
    py_compile.compile(path, doraise=True)
    print("  применено %-24s бэкап %s" % (os.path.basename(path), os.path.basename(bak)))
print("ПРИМЕНЕНО, файлов: %d, точек учёта: %d" % (len(tmp), len(E)))
