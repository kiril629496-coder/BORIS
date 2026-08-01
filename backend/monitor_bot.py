#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Рассылка карточек мониторинга — БЕЗ собственного поллинга.

Нажатия кнопок обрабатывает существующий бот проекта (app.telegram_bot,
его поллинг getUpdates + _handle_callback_query -> alerts_handler).
Второй поллер на том же токене дал бы 409 Conflict, поэтому здесь его нет.

Этот скрипт делает ОДНО: берёт из БД непосланные находки и отправляет
карточки через app.telegram_bot. Запускается по крону.

Запуск:
    venv/bin/python3 monitor_bot.py            # разослать очередь и выйти
    venv/bin/python3 monitor_bot.py --loop     # цикл рассылки (если нужен демон)

Автоматических сообщений найденным людям НЕ отправляет: «Подготовить ответ»
лишь присылает черновик в тему, менеджер пишет человеку сам.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# .env в окружение ДО импорта app.* — иначе app/db/session.py возьмёт дефолт
# postgres:postgres и запись упадёт на аутентификации.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:  # noqa: BLE001
    pass

from app.monitoring import alerts, store                      # noqa: E402

LOG_PATH = os.getenv("MONITOR_BOT_LOG", "/root/BORIS/backend/logs/monitor_bot.log")
BATCH = int(os.getenv("MONITOR_BOT_BATCH", "10"))
MIN_SCORE = int(os.getenv("MONITOR_ALERT_MIN_SCORE", "40"))
LOOP_EVERY = int(os.getenv("MONITOR_BOT_SEND_EVERY", "60"))

log = logging.getLogger("monitor.bot")


def setup_logging() -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)])


def flush_queue(limit: int = BATCH) -> int:
    """
    Берёт непосланные находки и шлёт карточки. Возвращает число отправленных.
    Карточку, которую не удалось отправить, НЕ помечает — уйдёт следующим заходом.
    """
    sent = 0
    con = store.connect()
    try:
        with con, con.cursor() as cur:
            rows = store.pending_alerts(cur, limit=limit, min_score=MIN_SCORE)
            for row in rows:
                if alerts.send_card(row):
                    store.mark_sent(cur, row["id"])
                    sent += 1
                else:
                    log.warning("карточка %s не ушла — оставляю в очереди", row["id"])
                    break
    finally:
        con.close()
    if sent:
        log.info("отправлено карточек: %d", sent)
    return sent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true",
                    help="цикл рассылки вместо однократного прохода")
    args = ap.parse_args()
    setup_logging()

    if not args.loop:
        print(f"отправлено: {flush_queue()}")
        return 0

    log.info("цикл рассылки, интервал %d сек", LOOP_EVERY)
    while True:
        try:
            flush_queue()
        except Exception:  # noqa: BLE001
            log.exception("рассылка не удалась")
        time.sleep(LOOP_EVERY)


if __name__ == "__main__":
    raise SystemExit(main())
