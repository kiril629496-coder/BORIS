import ast, shutil, sys, time
p = "app/api/banners.py"
s0 = open(p, encoding="utf-8").read(); s = s0
a1 = '    exact_text: str = ""'
n1 = '    exact_text: str = ""\n    exact_subtitle: str = ""\n    exact_price: str = ""'
a2 = '        copy = {"title": req.exact_text, "subtitle": "", "price_text": "", "advantages": []}'
n2 = '        copy = {"title": req.exact_text, "subtitle": req.exact_subtitle, "price_text": req.exact_price, "advantages": []}'
print("якорь полей:", s.count(a1), "| якорь copy:", s.count(a2))
if "exact_subtitle" not in s:
    s = s.replace(a1, n1, 1)
s = s.replace(a2, n2, 1)
if s != s0:
    ast.parse(s)
    if "--apply" in sys.argv:
        shutil.copy(p, "%s.bak_exact_%d" % (p, int(time.time())))
        open(p, "w", encoding="utf-8").write(s)
        print("ЗАПИСАНО: подзаголовок и цена теперь передаются в OpenAI дословно")
    else:
        print("dry-run OK")
else:
    print("уже пропатчено")
