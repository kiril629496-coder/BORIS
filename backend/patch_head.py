"""Заголовки разделов 9 и 10 печатает код, модель отдаёт только тело. Dry-run по умолчанию."""
import sys, os, time, shutil, tempfile, py_compile

F = "/root/BORIS/backend/exec_report_v4.py"

E = [
 ("1. промпт: без заголовков, два блока через ---",
  '''\n9. ВЫВОД BORIS — 3-5 предложений: что сегодня действительно важно.\n10. ПЛАН НА ЗАВТРА — 3-5 пунктов.\n''',
  '''\nВерни РОВНО два блока, разделённых отдельной строкой из трёх дефисов ---\nПервый блок: вывод, 3-5 предложений о том, что сегодня действительно важно.\nВторой блок: план на завтра, 3-5 пунктов, КАЖДЫЙ С НОВОЙ СТРОКИ.\nЗаголовки разделов и нумерацию не пиши — их добавит система.\n'''),

 ("2. сборка разделов 9 и 10 кодом",
  '''\n    ai = demarkdown(ai or "")\n''',
  '''\n    ai = demarkdown(ai or "")
    _parts = [x.strip() for x in ai.split("---", 1)]
    _concl = _parts[0] if _parts else ""
    _plan_raw = _parts[1] if len(_parts) > 1 else ""
    _plan_lines = [l.strip(" -*•0123456789.)\\t") for l in _plan_raw.splitlines()
                   if l.strip(" -*•0123456789.)\\t")]
    ai = "9. ВЫВОД BORIS\\n" + _concl
    if _plan_lines:
        ai += "\\n\\n10. ПЛАН НА ЗАВТРА\\n" + "\\n".join(
            f"{i}. {l}" for i, l in enumerate(_plan_lines, 1))
'''),
]


def main():
    apply = "--apply" in sys.argv
    src = open(F, encoding="utf-8").read()
    for label, old, new in E:
        n = src.count(old)
        if n != 1:
            print(f"СТОП: якорь «{label}» найден {n} раз")
            sys.exit(1)
        if new in src:
            print(f"СТОП: «{label}» уже применён")
            sys.exit(1)
        src = src.replace(old, new, 1)
        print(f"ok  {label}")
    fd, t = tempfile.mkstemp(suffix=".py"); os.close(fd)
    open(t, "w", encoding="utf-8").write(src)
    py_compile.compile(t, doraise=True)
    print("COMPILE PASS")
    if not apply:
        os.unlink(t); print("DRY-RUN PASS. Файл не тронут."); return
    bak = f"{F}.bak_head_{int(time.time())}"
    shutil.copy2(F, bak); shutil.copy2(t, F); os.unlink(t)
    py_compile.compile(F, doraise=True)
    print(f"ПРИМЕНЕНО {F}  бэкап {bak}")


if __name__ == "__main__":
    main()
