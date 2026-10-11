from size_norm import normalize_size, SizeError

ok = fail = 0
def check(name, fn, expect):
    global ok, fail
    try:
        got = fn()
    except SizeError as e:
        got = "БЛОК: %s" % e
    good = (got == expect) if expect != "БЛОК" else str(got).startswith("БЛОК")
    print(("PASS " if good else "FAIL ") + "%-32s -> %s" % (name, got))
    ok, fail = (ok + 1, fail) if good else (ok, fail + 1)

check("6000 мм", lambda: normalize_size("6000 мм"), "6 м")
check("2400 мм", lambda: normalize_size("2400 мм"), "2,4 м")
check("6000 при единицах mm", lambda: normalize_size("6000", "mm"), "6 м")
check("6 м", lambda: normalize_size("6 м"), "6 м")
check("2.4 м", lambda: normalize_size("2.4 м"), "2,4 м")
check("пустое", lambda: normalize_size(""), "БЛОК")
check("текст без единиц", lambda: normalize_size("шесть метров"), "БЛОК")
check("голое 6 без подсказки", lambda: normalize_size("6"), "БЛОК")
check("6 м при единицах mm", lambda: normalize_size("6 м", "mm"), "6 м")
check("неразрывный пробел", lambda: normalize_size("2,4\u00a0м"), "2,4 м")
print("ИТОГО: PASS %d / FAIL %d" % (ok, fail))
