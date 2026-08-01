"""Фоновый обход: кто замер на стадии онбординга — тому подсказка.
Запускать раз в час: nudge сам не отправит чаще раза в сутки и больше 3 раз на стадию.
Запуск: cd /root/BORIS/backend && venv/bin/python3 onboarding_nudge_runner.py
"""
import os, sys, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env):
    for line in open(_env, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from app.onboarding import stuck_accounts, nudge, do_for

def main():
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    stuck = stuck_accounts(min_hours=24)
    print("[%s] замерших: %d" % (ts, len(stuck)))
    sent = 0
    did = 0
    for s in stuck:
        acc = s["account_id"]
        # сначала пробуем сделать работу за клиента, подсказка — только если не вышло
        d = do_for(acc)
        if d.get("status") == "ok":
            print("  %-44s стадия %s → СДЕЛАЛ: %s" % (acc, s["стадия"], ", ".join(d["сделано"])))
            did += 1
            continue
        why = d.get("reason") or d.get("message") or "-"
        r = nudge(acc)
        mark = "→ подсказка" if r.get("status") == "ok" else "пропуск: %s" % r.get("reason")
        print("  %-44s стадия %s, %sч  сам не смог (%s)  %s" % (acc, s["стадия"], s["часов_на_стадии"], why, mark))
        if r.get("status") == "ok":
            sent += 1
    print("[%s] сделано за клиентов: %d" % (ts, did))
    print("[%s] отправлено подсказок: %d" % (ts, sent))

if __name__ == "__main__":
    main()
