# -*- coding: utf-8 -*-
"""Переносит block_email_verification ВЫШЕ словаря BLOCKS.
add_qa_block.py вставлял его перед def main(), а BLOCKS объявлен раньше —
Python не видел функцию на момент сборки словаря."""
import io, re, sys, time

PATH = sys.argv[1] if len(sys.argv) > 1 else "/root/BORIS/backend/qa_suite.py"
s = io.open(PATH, encoding="utf-8").read()

start = s.find("\ndef block_email_verification():")
assert start != -1, "тело блока не найдено"
end = s.find("\ndef main():", start)
assert end != -1, "def main() после блока не найден"
body = s[start:end]
assert "block_email_verification" in body

m = re.search(r"\nBLOCKS\s*=\s*\{", s)
assert m, "словарь BLOCKS не найден"
assert m.start() < start, "BLOCKS уже ниже блока — перенос не нужен"

s2 = s[:start] + s[end:]
m2 = re.search(r"\nBLOCKS\s*=\s*\{", s2)
assert m2, "BLOCKS потерялся после выреза"
s2 = s2[:m2.start()] + body + s2[m2.start():]

io.open(PATH + ".before_order_%d" % int(time.time()), "w", encoding="utf-8").write(s)
io.open(PATH, "w", encoding="utf-8").write(s2)

i_body = s2.find("def block_email_verification():")
i_blocks = s2.find("BLOCKS = {")
print("  ok: блок перенесён. позиция тела %d, позиция BLOCKS %d" % (i_body, i_blocks))
assert i_body < i_blocks, "порядок всё ещё неверный"
print("ЗАПИСАНО:", PATH)
