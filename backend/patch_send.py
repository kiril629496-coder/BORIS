"""Доставка Executive Report в Telegram. Dry-run по умолчанию, отправка только с --send."""
import sys, os, time, shutil, tempfile, py_compile

F = "/root/BORIS/backend/exec_report_v4.py"

E = [
 ("1. флаги отправки",
  '''    ap.add_argument("--dry-run", action="store_true", default=True)''',
  '''    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--force-send", action="store_true")'''),

 ("2. функции маршрута, разбивки и метки",
  '''def main():''',
  '''TG_LIMIT = 4096


def route_for(cur, owner):
    r = q(cur, "SELECT chat_id, thread_id FROM tg_routes"
          " WHERE account_id=%s AND thread_key='executive'", (f"owner:{owner}",))
    return (r[0][0], r[0][1]) if r else (None, None)


def split_blocks(text, limit=TG_LIMIT):
    parts, cur_part = [], ""
    for block in text.split("\\n\\n"):
        if len(cur_part) + len(block) + 2 > limit and cur_part:
            parts.append(cur_part.rstrip())
            cur_part = ""
        cur_part += block + "\\n\\n"
    if cur_part.strip():
        parts.append(cur_part.rstrip())
    too_long = [p for p in parts if len(p) > limit]
    return parts, too_long


def marker_key(day):
    return f"executive_report_sent:{day}:evening"


def marker_get(cur, acc, day):
    return st(cur, acc, marker_key(day))


def marker_set(cur, acc, day, payload):
    import json as _j
    cur.execute("SELECT id FROM storage WHERE account_id=%s AND key=%s", (acc, marker_key(day)))
    row = cur.fetchone()
    val = _j.dumps(payload, ensure_ascii=False)
    if row:
        cur.execute("UPDATE storage SET value=%s WHERE id=%s", (val, row[0]))
    else:
        cur.execute("INSERT INTO storage (account_id, key, value) VALUES (%s,%s,%s)",
                    (acc, marker_key(day), val))


def main():'''),

 ("3. блок доставки вместо строки SAFETY",
  '''    print("\\nSAFETY: DB writes 0, Telegram 0, Avito 0, разрешён только AI usage log")''',
  '''    # ---------------------------------------------------------- доставка
    primary = list(accs.keys())[0]
    report_key = f"executive:{a.owner}:{a.date}:evening"
    chat_id, thread_id = route_for(cur, a.owner)
    parts, too_long = split_blocks(final)
    sent_before = marker_get(cur, primary, a.date)

    print("\\n" + "=" * 72 + "\\nPREFLIGHT")
    print(f"  owner: {a.owner}")
    print(f"  report_key: {report_key}")
    print(f"  chat_id: {chat_id or 'МАРШРУТ НЕ НАЙДЕН'}  thread_id: {thread_id}")
    print(f"  длина текста: {len(final)} символов, частей к отправке: {len(parts)}")
    print(f"  первая строка: {final.splitlines()[0] if final else ''}")
    print(f"  последняя строка: {final.splitlines()[-1] if final else ''}")
    print(f"  уже отправлялся: {'да, ' + str(sent_before.get('sent_at')) if sent_before else 'нет'}")
    print(f"  would_send: {bool(chat_id and not too_long)}   send: {bool(a.send)}")

    if too_long:
        print("  ⛔ есть блок длиннее лимита Telegram — отправка невозможна без ручной правки")

    if not a.send:
        print("\\nSAFETY: DB writes 0, Telegram 0, Avito 0, разрешён только AI usage log")
        cur.close(); conn.close(); return

    if not chat_id:
        print("\\nОТПРАВКА ОТМЕНЕНА: маршрут executive для этого владельца не найден")
        cur.close(); conn.close(); return
    if too_long:
        print("\\nОТПРАВКА ОТМЕНЕНА: блок превышает лимит Telegram")
        cur.close(); conn.close(); return
    if sent_before and not a.force_send:
        print(f"\\nalready_sent: отчёт за {a.date} уже отправлен "
              f"({sent_before.get('sent_at')}). Повтор только с --force-send")
        cur.close(); conn.close(); return

    from app.telegram_bot import send_telegram_message
    results = []
    for i, p in enumerate(parts, 1):
        r = send_telegram_message(chat_id, p)
        results.append(r)
        print(f"  часть {i}/{len(parts)}: отправлена, ответ {str(r)[:120]}")
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    marker_set(cur, primary, a.date, {"report_key": report_key, "chat_id": str(chat_id),
                                      "sent_at": ts, "parts": len(parts)})
    print("\\n" + "=" * 72 + "\\nSEND RESULT")
    print(f"  telegram: ok, частей {len(parts)}")
    print(f"  chat_id: {chat_id}")
    print(f"  timestamp: {ts}")
    print(f"  report_key: {report_key}")
    print("  Avito mutations: 0, CRM mutations: 0, бизнес-данные не менялись")
    print("  записана только метка отправки в storage")'''),
]


def main():
    apply = "--apply" in sys.argv
    src = open(F, encoding="utf-8").read()
    for label, old, new in E:
        n = src.count(old)
        if n != 1:
            print(f"СТОП: якорь «{label}» найден {n} раз")
            sys.exit(1)
        src = src.replace(old, new, 1)
        print(f"ok  {label}")
    fd, t = tempfile.mkstemp(suffix=".py"); os.close(fd)
    open(t, "w", encoding="utf-8").write(src)
    py_compile.compile(t, doraise=True)
    print("COMPILE PASS")
    if not apply:
        os.unlink(t); print("DRY-RUN PASS. Файл не тронут."); return
    bak = f"{F}.bak_send_{int(time.time())}"
    shutil.copy2(F, bak); shutil.copy2(t, F); os.unlink(t)
    py_compile.compile(F, doraise=True)
    print(f"ПРИМЕНЕНО {F}  бэкап {bak}")


if __name__ == "__main__":
    main()
