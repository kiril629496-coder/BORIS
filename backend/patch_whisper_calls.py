import sys, time, py_compile, shutil, tempfile, os
CT = "/root/BORIS/backend/app/api/calltracking.py"
E = [
 ('_transcribe(audio, f"call_{call_id}.{ext}", _asr_hint(account_id))',
  '_transcribe(audio, f"call_{call_id}.{ext}", _asr_hint(account_id), account_id)'),
 ('_transcribe(audio, f"call_{cid}.{ext}", _asr_hint(account_id))',
  '_transcribe(audio, f"call_{cid}.{ext}", _asr_hint(account_id), account_id)'),
]
src = open(CT, encoding="utf-8").read()
bad = 0
for i, (o, n) in enumerate(E, 1):
    c = src.count(o)
    print("  вызов %d: якорь %d раз" % (i, c))
    if c != 1:
        bad += 1
if bad:
    print("STOP: якоря не однозначны"); sys.exit(1)
out = src
for o, n in E:
    out = out.replace(o, n)
fd, t = tempfile.mkstemp(suffix=".py"); os.close(fd)
open(t, "w", encoding="utf-8").write(out)
py_compile.compile(t, doraise=True)
bak = "%s.bak_wcalls_%d" % (CT, int(time.time()))
shutil.copy2(CT, bak); shutil.copy2(t, CT); os.unlink(t)
py_compile.compile(CT, doraise=True)
print("  бэкап: %s" % os.path.basename(bak))
print("ПРИМЕНЕНО: account_id прокинут в оба вызова")
