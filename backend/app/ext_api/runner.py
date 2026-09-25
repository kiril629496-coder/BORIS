# -*- coding: utf-8 -*-
"""Раннер очереди Job.

Внутри бэкенда работает лёгкий поток (см. start_inline_runner), он берёт только
задачи классов READ_ONLY и SAFE_MUTATION. Тяжёлое и платное должен забирать
отдельный процесс — этот модуль:

    venv/bin/python3 -m app.ext_api.runner --once
    venv/bin/python3 -m app.ext_api.runner --loop --interval 5
"""
import argparse, time
from . import orchestrator


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--limit", type=int, default=25)
    a = ap.parse_args()

    if a.loop:
        print("раннер запущен, Ctrl+C чтобы остановить")
        while True:
            n = orchestrator.drain(a.limit)
            if n:
                print(f"  обработано задач: {n}")
            time.sleep(a.interval)
    else:
        print(f"  обработано задач: {orchestrator.drain(a.limit)}")


if __name__ == "__main__":
    main()
