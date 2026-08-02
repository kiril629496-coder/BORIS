"""
Воркер очереди писем BORIS AI. Запускается из cron раз в минуту.

Забирает из email_queue письма со статусом queued, у которых подошло время
следующей попытки, и пробует отправить. Захват через FOR UPDATE SKIP LOCKED,
поэтому два одновременных запуска не возьмут одно письмо.

В лог пишет только когда что-то произошло — иначе файл распухнет от пустых строк.
"""

import logging
import sys
from datetime import datetime

sys.path.insert(0, "/root/BORIS/backend")

from dotenv import load_dotenv

load_dotenv("/root/BORIS/backend/.env")

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s %(levelname)s %(message)s")


def main() -> int:
    from app.services import email_queue as queue
    queue.touch_heartbeat()
    result = queue.process_batch()
    if any(result.values()):
        print("%s очередь писем: %s" % (datetime.now().strftime("%d.%m %H:%M"), result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
