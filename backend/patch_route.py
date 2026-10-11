import shutil, time, py_compile, sys, hashlib

P = "app/api/avito.py"
SHA_EXPECTED = "aef2ae4f4f8a0a5336ad7918f02d60bc912fb4c0e2a4beb89f2ce680d57e4bc1"
src = open(P, encoding="utf-8").read()
cur = hashlib.sha256(src.encode()).hexdigest()
print("SHA сейчас :", cur)
print("SHA ожидаем:", SHA_EXPECTED)
if cur != SHA_EXPECTED:
    print("СТОП: файл изменился с момента фиксации, патч не применён"); sys.exit(1)

A_OLD = '@router.post("/generate_feed")\n\ndef _normalize_category('
A_NEW = 'def _normalize_category('
B_OLD = '\ndef generate_feed(req: GenerateFeedRequest):'
B_NEW = '\n@router.post("/generate_feed")\ndef generate_feed(req: GenerateFeedRequest):'

if 'router.post("/generate_feed")\ndef generate_feed' in src:
    print("СТОП: патч уже применён"); sys.exit(0)

for name, old in (("декоратор перед helper", A_OLD), ("определение обработчика", B_OLD)):
    n = src.count(old)
    print("якорь %-26s x%s" % (name, n))
    if n != 1:
        print("СТОП: якорь не уникален"); sys.exit(1)

new_src = src.replace(A_OLD, A_NEW, 1).replace(B_OLD, B_NEW, 1)
print("строк было/стало:", len(src.splitlines()), "->", len(new_src.splitlines()))

bak = P + ".bak_route_" + str(int(time.time()))
shutil.copy2(P, bak)
open(P, "w", encoding="utf-8").write(new_src)
py_compile.compile(P, doraise=True)
print("\nПАТЧ МАРШРУТА ПРИМЕНЁН")
print("бэкап:", bak)
print("SHA после:", hashlib.sha256(new_src.encode()).hexdigest())
