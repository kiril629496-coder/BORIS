import io, time, ast
P = "cpx_advisor_runner.py"
src = io.open(P, encoding="utf-8").read()
if "_rid(" in src:
    print("Уже применён."); raise SystemExit(0)

A1 = "RUN_ID = uuid.uuid4().hex[:12]"
A2 = '"request_id": RUN_ID}'
for a, name in ((A1, "RUN_ID"), (A2, "вызов apply_one")):
    if src.count(a) != 1:
        print("СТОП: якорь %s найден %d раз. Файл не тронут." % (name, src.count(a)))
        raise SystemExit(1)

HELPER = A1 + '''


def _rid(account_id: str) -> str:
    """request_id уникален для пары прогон+аккаунт.
    Общий RUN_ID приводил к тому, что count_actions_run считал действия
    всех аккаунтов вместе, и max_actions_run мандата работал как одна
    квота на прогон: кто раньше в списке — тот и забирал её."""
    import hashlib
    return RUN_ID[:6] + hashlib.md5(str(account_id).encode("utf-8")).hexdigest()[:6]'''

out = src.replace(A1, HELPER, 1).replace(A2, '"request_id": _rid(acc)}', 1)
ast.parse(out)
bak = P + ".bak_rid_%d" % int(time.time())
io.open(bak, "w", encoding="utf-8").write(src)
io.open(P, "w", encoding="utf-8").write(out)
print("Патч применён. Бэкап:", bak)
print("Строк было %d, стало %d" % (len(src.splitlines()), len(out.splitlines())))
