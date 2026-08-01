#!/root/BORIS/backend/venv/bin/python3
"""Скрипт для cron - проверяет прокси раз в час, шлёт алерт если мёртв."""
import sys, os
sys.path.insert(0, "/root/BORIS/backend")
os.chdir("/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv()
from app.api.parser import check_proxy_and_alert

if __name__ == "__main__":
    result = check_proxy_and_alert()
    print(f"[{__import__('datetime').datetime.now()}] Проверка прокси: {result}")
