import ast, shutil, sys, time
p = "app/api/avito.py"
s0 = open(p, encoding="utf-8").read()
old = "        xml_parts.append('    <ListingFee>PackageSingle</ListingFee>')"
new = ("        if \"ListingFee\" not in item.params:\n"
       "            xml_parts.append('    <ListingFee>PackageSingle</ListingFee>')")
print("якорь найден:", s0.count(old))
if "if \"ListingFee\" not in item.params" in s0:
    print("уже пропатчено"); raise SystemExit(0)
s = s0.replace(old, new, 1)
ast.parse(s)
print("ДО :", old.strip())
print("ПОСЛЕ:", new.strip().replace("\n", " | "))
if "--apply" in sys.argv:
    shutil.copy(p, "%s.bak_fee_%d" % (p, int(time.time())))
    open(p, "w", encoding="utf-8").write(s)
    print("ЗАПИСАНО")
else:
    print("dry-run OK")
