# -*- coding: utf-8 -*-
"""Разовый скрипт активации пакетов для Планеты мебели (dev_620).

Запуск:
    python3 backend/dev_620_activate_planeta_packages.py
"""
import os
import sys

ROOT = "/root/BORIS/backend"
sys.path.insert(0, ROOT)

# .env как в qa_platform_owner.py
for _l in open(f"{ROOT}/.env", encoding="utf-8"):
    _l = _l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        _k, _v = _l.split("=", 1)
        os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))

from app.api.billing import set_tier, add_ecs_package
from app.api.messenger import add_manager_package
from app.api.calltracking import add_rop_package

ACCOUNT_ID = "planetazayavki_65985"


def main():
    print(f"ACCOUNT={ACCOUNT_ID}")

    # Автопилот (billing tier)
    r1 = set_tier(ACCOUNT_ID, "tariff_2")
    print("AUTOPILOT:", r1)

    # МОП (пакет сообщений менеджера)
    r2 = add_manager_package(ACCOUNT_ID, 2000, days=365)
    print("MOP:", r2)

    # РОП (пакет минут)
    r3 = add_rop_package(ACCOUNT_ID, 3000, days=365)
    print("ROP:", r3)

    # ЕЦС (единый центр сообщений) — синхронно со сроком МОП/РОП
    r4 = add_ecs_package(ACCOUNT_ID, days=365)
    print("ECS:", r4)


if __name__ == "__main__":
    main()
