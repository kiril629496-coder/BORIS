"""P0-C — baseline не создаётся из недостоверного баланса. MISSING != REAL_ZERO."""
import sys, os, time, shutil, tempfile, py_compile

ROOT = "/root/BORIS/backend"
AV = os.path.join(ROOT, "app/api/avito.py")

E = [
    (AV, "1. curr_balance: MISSING != REAL_ZERO",
     '''        cost_per_lead = None
        if today_row:
            today_data = _json_kc.loads(today_row.value)
            curr_balance = float(
                (today_data.get("balance") or {}).get("real") or 0
            )
        else:
            today_data = {}
            curr_balance = 0.0''',
     '''        cost_per_lead = None
        # P0-C: отсутствие баланса и реальный ноль — РАЗНЫЕ состояния.
        balance_known = False
        curr_balance = 0.0
        if today_row:
            today_data = _json_kc.loads(today_row.value)
            _bal_raw = (today_data.get("balance") or {}).get("real")
            if _bal_raw is not None:
                try:
                    curr_balance = float(_bal_raw)
                    balance_known = True
                except (TypeError, ValueError):
                    balance_known = False
        else:
            today_data = {}'''),

    (AV, "2. baseline не создаётся без достоверного баланса",
     '''        else:
            baseline_balance = curr_balance

            baseline_payload = _json_kc.dumps(
                {
                    "date": today,
                    "balance": baseline_balance,
                    "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                },
                ensure_ascii=False,
            )

            db.add(
                Storage(
                    account_id=account_id,
                    key=baseline_key,
                    value=baseline_payload,
                )
            )
            db.commit()''',
     '''        elif not balance_known:
            # P0-C: достоверного снимка баланса нет, baseline НЕ создаём.
            baseline_balance = None
            print(f"[kpi_check] {account_id}: baseline_waiting_daily_stats")
        else:
            baseline_balance = curr_balance

            baseline_payload = _json_kc.dumps(
                {
                    "date": today,
                    "balance": baseline_balance,
                    "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                },
                ensure_ascii=False,
            )

            db.add(
                Storage(
                    account_id=account_id,
                    key=baseline_key,
                    value=baseline_payload,
                )
            )
            db.commit()
            print(f"[kpi_check] {account_id}: baseline_created={baseline_balance}")'''),

    (AV, "3. spent только при достоверных значениях",
     '''        spent = max(
            baseline_balance - curr_balance,
            0,
        )''',
     '''        # P0-C: расход считаем, только когда достоверны ОБА значения.
        if baseline_balance is not None and balance_known:
            spent = max(
                baseline_balance - curr_balance,
                0,
            )
        else:
            spent = 0
            print(f"[kpi_check] {account_id}: balance_unknown, spent не считается")'''),
]


def main():
    apply = "--apply" in sys.argv
    files = {}
    for path in {p for p, *_ in E}:
        files[path] = open(path, encoding="utf-8").read()

    for path, label, old, new in E:
        n = files[path].count(old)
        if n != 1:
            print(f"СТОП: якорь «{label}» найден {n} раз — файл изменён. Ничего не тронуто.")
            sys.exit(1)
        if new in files[path]:
            print(f"СТОП: «{label}» уже применён")
            sys.exit(1)
        files[path] = files[path].replace(old, new, 1)
        print(f"ok  {label}")

    tmp = {}
    for path, content in files.items():
        fd, t = tempfile.mkstemp(suffix=".py")
        os.close(fd)
        open(t, "w", encoding="utf-8").write(content)
        py_compile.compile(t, doraise=True)
        tmp[path] = t
    print("COMPILE PASS для будущей версии")

    if not apply:
        print("DRY-RUN PASS. Файлы не тронуты.")
        for t in tmp.values():
            os.unlink(t)
        return

    ts = int(time.time())
    for path, t in tmp.items():
        bak = f"{path}.bak_p0c_{ts}"
        shutil.copy2(path, bak)
        shutil.copy2(t, path)
        os.unlink(t)
        py_compile.compile(path, doraise=True)
        print(f"ПРИМЕНЕНО {path}  бэкап {bak}")
    print("Готово. Крон не включается. Нужен рестарт бэкенда, чтобы правка ожила.")


if __name__ == "__main__":
    main()
