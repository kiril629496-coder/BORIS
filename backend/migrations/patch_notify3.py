import os, py_compile, shutil, time

BASE = os.environ.get("BORIS_BASE", "/root/BORIS/backend")
AU = os.path.join(BASE, "app/api/auth.py")
stamp = str(int(time.time()))
MARK = "уведомление владельцу отправляется ПОСЛЕ"

lines = open(AU, encoding="utf-8").read().split("\n")

if any(MARK in ln for ln in lines):
    print("  патч уже применён, пропуск"); raise SystemExit

tg_start = tg_end = None
for i, ln in enumerate(lines):
    if tg_start is None and "уведомление владельцу в Telegram о новом клиенте" in ln:
        tg_start = i
    elif tg_start is not None and 'print("telegram notify failed:", _e)' in ln:
        tg_end = i
        break

code_start = code_end = None
for i, ln in enumerate(lines):
    if i > (tg_end or 0) and code_start is None and "Код подтверждения. В базе только хеш" in ln:
        code_start = i
    elif code_start is not None and "register: QA-регистрация, письмо не отправляется" in ln:
        code_end = i
        break

print("  блок Telegram: строки %s-%s" % (tg_start, tg_end))
print("  блок кода:     строки %s-%s" % (code_start, code_end))

if None in (tg_start, tg_end, code_start, code_end) or not (tg_end < code_start):
    print("  ГРАНИЦЫ НЕ НАЙДЕНЫ ИЛИ ПОРЯДОК ИНОЙ — файл не тронут"); raise SystemExit

tg_block = lines[tg_start:tg_end + 1]
between = lines[tg_end + 1:code_start]
code_block = lines[code_start:code_end + 1]

new = (lines[:tg_start]
       + code_block
       + [""]
       + ["        # ВАЖНО: уведомление владельцу отправляется ПОСЛЕ письма с кодом.",
          "        # При недоступном Telegram клиент всё равно получает код вовремя."]
       + tg_block
       + between
       + lines[code_end + 1:])

shutil.copy2(AU, AU + ".before_notify3_" + stamp)
open(AU, "w", encoding="utf-8").write("\n".join(new))
try:
    py_compile.compile(AU, doraise=True)
    print("  auth.py ok, строк было %d стало %d, бэкап .before_notify3_%s"
          % (len(lines), len(new), stamp))
except Exception as exc:
    shutil.copy2(AU + ".before_notify3_" + stamp, AU)
    print("  auth.py СЛОМАН, ОТКАЧЕН (%s)" % type(exc).__name__)
