"""Структурный контракт ответа модели: conclusion + tomorrow[]. Заголовки 9/10 рендерит код."""
import sys, os, time, shutil, tempfile, py_compile

F = "/root/BORIS/backend/exec_report_v4.py"

E = [
 ("1. промпт: строгий JSON вместо свободного текста",
  '''\n9. ВЫВОД BORIS — 3-5 предложений: что сегодня действительно важно.\n10. ПЛАН НА ЗАВТРА — 3-5 пунктов.\n''',
  '''\nВерни СТРОГО один JSON-объект и ничего кроме него:\n{"conclusion": "текст 3-5 предложений", "tomorrow": ["пункт", "пункт", "пункт"]}\nconclusion — что сегодня действительно важно, 3-5 предложений.\ntomorrow — от 3 до 5 пунктов, каждый отдельной строкой массива.\nЗаголовки, нумерацию, markdown и пояснения не добавляй — их подставит система.\n'''),

 ("2. разбор JSON и сборка разделов кодом",
  '''\n    ai = demarkdown(ai or "")\n''',
  '''\n    _raw = demarkdown(ai or "")
    _ai_ok, _concl, _plan = False, "", []
    try:
        _txt = re.sub(r"^[^{]*", "", _raw, count=1)
        _txt = re.sub(r"[^}]*$", "", _txt, count=1)
        _d = json.loads(_txt)
        _concl = str(_d.get("conclusion") or "").strip()
        _plan = [str(x).strip(" -*\\u2022\\t") for x in (_d.get("tomorrow") or [])
                 if str(x).strip(" -*\\u2022\\t")]
        _ai_ok = bool(_concl) and 3 <= len(_plan) <= 5
    except Exception as _e:
        print(f"  [AI CONTRACT] не разобрал JSON: {str(_e)[:80]}")
    if not _ai_ok:
        print("  [AI CONTRACT] FAIL: нет conclusion или tomorrow не 3-5 пунктов")
    ai = "\\U0001F9E0 ВЫВОД BORIS\\n" + (_concl or _raw.strip())
    if _plan:
        ai += "\\n\\n\\U0001F4CC ПЛАН НА ЗАВТРА\\n" + "\\n".join("\\u2022 " + p for p in _plan)
'''),

 ("3. не отправлять, если контракт модели не выполнен",
  '''\n    from app.telegram_bot import send_telegram_message\n''',
  '''\n    if not _ai_ok:
        print("\\nОТПРАВКА ОТМЕНЕНА: ответ модели не прошёл контракт"
              " (нужны conclusion и 3-5 пунктов tomorrow)")
        cur.close(); conn.close(); return
    from app.telegram_bot import send_telegram_message
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
    bak = f"{F}.bak_json_{int(time.time())}"
    shutil.copy2(F, bak); shutil.copy2(t, F); os.unlink(t)
    py_compile.compile(F, doraise=True)
    print(f"ПРИМЕНЕНО {F}  бэкап {bak}")


if __name__ == "__main__":
    main()
