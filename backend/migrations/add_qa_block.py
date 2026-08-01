# -*- coding: utf-8 -*-
"""Добавляет блок email_verification в qa_suite.py.
Тело блока читается из соседнего файла qa_block_body.py."""
import io, os, re, sys, time

PATH = sys.argv[1] if len(sys.argv) > 1 else "/root/BORIS/backend/qa_suite.py"
BODY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qa_block_body.py")

s = io.open(PATH, encoding="utf-8").read()
if "block_email_verification" in s:
    print("УЖЕ ПРИМЕНЁН — выхожу, файл не тронут")
    sys.exit(0)

BLOCK = io.open(BODY, encoding="utf-8").read()

anchor = "\ndef main():"
assert s.count(anchor) == 1, "якорь def main() найден %d раз" % s.count(anchor)
s = s.replace(anchor, BLOCK + anchor, 1)
print("  ok: тело блока вставлено")

m = re.search(r"BLOCKS\s*=\s*\{", s)
assert m, "не найден словарь BLOCKS"
end = s.index("}", m.end())
seg = s[m.end():end].rstrip()
sep = "" if seg.endswith(",") or not seg else ","
s = s[:end] + sep + '\n    "email_verification": block_email_verification,\n' + s[end:]
print("  ok: блок зарегистрирован в BLOCKS")

old = 'for name in ["API", "UI", "Security", "Roles", "Business", "Performance", "Regression", "UX"]:'
assert s.count(old) == 1, "не найден список имён в сводке"
s = s.replace(old, 'for name in ["API", "UI", "Security", "Roles", "Business", "Performance", "Regression", "UX", "EmailVerify"]:', 1)
print("  ok: EmailVerify добавлен в итоговую таблицу")

io.open(PATH + ".before_emailverify_%d" % int(time.time()), "w", encoding="utf-8").write(
    io.open(PATH, encoding="utf-8").read())
io.open(PATH, "w", encoding="utf-8").write(s)
print("ЗАПИСАНО:", PATH)
