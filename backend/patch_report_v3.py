"""Патч v3 для exec_report.py — девять поправок. Dry-run по умолчанию, --apply."""
import sys, os, time, shutil, tempfile, py_compile

F = "/root/BORIS/backend/exec_report.py"

TS = ("to_timestamp(avito_created_at / CASE WHEN avito_created_at > 100000000000"
      " THEN 1000 ELSE 1 END)::date=%s")

E = [
    ("1. sales: unix timestamp вместо каста integer",
     '''            " AND avito_created_at::date=%s AND direction='in'", (ids, day))[0][0],''',
     '''            " AND ''' + TS + ''' AND direction='in'", (ids, day))[0][0],'''),

    ("2. sales: диалоги, тот же фикс",
     '''            " WHERE account_id = ANY(%s) AND avito_created_at::date=%s", (ids, day))[0][0],''',
     '''            " WHERE account_id = ANY(%s) AND ''' + TS + '''", (ids, day))[0][0],'''),

    ("3. белый список баланса -> пусто (fail closed)",
     '''BALANCE_KNOWN_ACCOUNTS = {"prodazha_bytovok_25677", "3411770_94346"}''',
     '''BALANCE_KNOWN_ACCOUNTS = set()'''),

    ("4. техническое значение баланса прячем от AI",
     '''                   "reason": "тариф Avito не подтверждён; число от API может не отражать"''',
     '''                   "_raw_diagnostic": raw_bal,
                   "reason": "тариф Avito не подтверждён; число от API может не отражать"'''),

    ("5. соцсети: лидер только при ненулевом вовлечении",
     '''    ws = [p for p in posts if isinstance(p, dict) and (p.get("stats") or {}).get("views")]''',
     '''    ws = [p for p in posts if isinstance(p, dict)
          and ((p.get("stats") or {}).get("er") or 0) > 0]'''),

    ("6. соцсети: честная строка при отсутствии лидера",
     '''    if social:
        f["social"] = social''',
     '''    if today_posts and not ws:
        social["vovlechenie"] = "явного лидера по вовлечённости сегодня нет"
    if social:
        f["social"] = social'''),

    ("7. CRM: пустой результат становится явным пробелом",
     '''        (ids,))), W_NOW, "crm_tasks"), None)''',
     '''        (ids,))), W_NOW, "crm_tasks"), None)
    if not f.get("crm") or not (f["crm"] or {}).get("value"):
        gap("CRM", "задачи по этим аккаунтам не найдены либо таблица недоступна")'''),

    ("8. новый промпт со скелетом + очистка facts от диагностики",
     '''def summarize(facts, account_id):''',
     '''def strip_diag(o):
    """Убираем из payload всё, что начинается с подчёркивания: AI это не видит."""
    if isinstance(o, dict):
        return {k: strip_diag(v) for k, v in o.items() if not str(k).startswith("_")}
    if isinstance(o, list):
        return [strip_diag(v) for v in o]
    return o


PROMPT2 = """Ты — коммерческий директор. Вечером докладываешь владельцу бизнеса итоги дня.
Текст пойдёт в Telegram: простые строки, короткие заголовки, допустимы эмодзи.
Запрещены markdown-разметка, решётки, звёздочки и таблицы.

СТРУКТУРА ОБЯЗАТЕЛЬНА. Разделы 1-5, 8, 9, 10 выводить ВСЕГДА, даже если данных мало —
в этом случае честно писать, каких данных не хватает. Разделы 6 и 7 — только при наличии данных.
1. ИТОГ ДНЯ
2. AVITO
3. ПРОДАЖИ
4. ПРОДВИЖЕНИЕ
5. ЧТО СДЕЛАЛ BORIS
6. ЧТО СДЕЛАЛА КОМАНДА
7. СОЦСЕТИ
8. ТРЕБУЕТ РЕШЕНИЯ
9. ВЫВОД BORIS
10. ПЛАН НА ЗАВТРА

ЖЁСТКИЕ ПРАВИЛА:
- Только числа из фактов. Ни одной цифры сверх них.
- У каждой метрики есть period. Показатели с РАЗНЫМИ period не сравнивать между собой.
  Сегодняшние обращения и анализ продвижения за 7 полных дней — разные окна.
- Баланс: если balance.status не KNOWN, сумму НЕ называть вообще. Достаточно строки,
  что достоверное значение через API сейчас недоступно. Не советовать пополнение.
- Расход и цена обращения: по КАЖДОМУ аккаунту отдельной строкой — название аккаунта,
  состояние и причина СЛОВАМИ ИЗ ПОЛЯ reason. Свою причину не придумывать.
  Не объединять аккаунты с разными состояниями в одну фразу.
- Раздел ПРОДВИЖЕНИЕ обязателен, если есть cpx-данные. Указывать окно:
  предыдущие 7 полных дней, без сегодняшнего. Про объявления, требующие внимания,
  писать так: за анализируемое окно были просмотры, но не было обращений,
  поэтому они требуют дополнительной проверки. НЕ предлагать отключать продвижение.
- Причинность не приписывать: не доказано замером — «наблюдаем», «возможно»,
  «нужно накопить данные». Не писать «BORIS увеличил лиды».
- Если attribution UNRESOLVED или confidence low — не называть ни человека, ни BORIS:
  «выполнено через BORIS, исполнитель уточняется».
- Писать МОП русскими буквами. Не использовать технические имена, ключи и названия таблиц.

ФАКТЫ:
"""


def summarize(facts, account_id):'''),

    ("9. AI получает очищенные факты и новый промпт",
     '''    payload = PROMPT + json.dumps(facts, ensure_ascii=False)''',
     '''    payload = PROMPT2 + json.dumps(strip_diag(facts), ensure_ascii=False)'''),
]


def main():
    apply = "--apply" in sys.argv
    src = open(F, encoding="utf-8").read()
    for label, old, new in E:
        n = src.count(old)
        if n != 1:
            print(f"СТОП: якорь «{label}» найден {n} раз. Ничего не тронуто.")
            sys.exit(1)
        if new in src:
            print(f"СТОП: «{label}» уже применён")
            sys.exit(1)
        src = src.replace(old, new, 1)
        print(f"ok  {label}")

    fd, t = tempfile.mkstemp(suffix=".py")
    os.close(fd)
    open(t, "w", encoding="utf-8").write(src)
    py_compile.compile(t, doraise=True)
    print("COMPILE PASS для будущей версии")

    if not apply:
        os.unlink(t)
        print("DRY-RUN PASS. Файл не тронут.")
        return

    bak = f"{F}.bak_v3_{int(time.time())}"
    shutil.copy2(F, bak)
    shutil.copy2(t, F)
    os.unlink(t)
    py_compile.compile(F, doraise=True)
    print(f"ПРИМЕНЕНО {F}  бэкап {bak}")


if __name__ == "__main__":
    main()
