"""Ежедневный автозапуск KPI-логики по всем аккаунтам с автопилотом 'always_auto'.
Запускать через cron раз в сутки, ПОСЛЕ daily_stats_collector.py (нужны свежие данные)."""
import httpx
import psycopg2
import os
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env")

DB_DSN = os.getenv("DATABASE_URL")
API_BASE = "http://localhost:8000"

def get_all_account_ids():
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute("SELECT account_id FROM accounts")
    ids = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()
    return ids

def main():
    account_ids = get_all_account_ids()
    print(f"Найдено аккаунтов: {len(account_ids)}")
    for account_id in account_ids:
        try:
            r = httpx.post(f"{API_BASE}/api/avito/kpi_autopilot_run", params={"account_id": account_id}, timeout=300)
            data = r.json()
            status = data.get("status")
            if status == "skipped":
                print(f"[{account_id}] пропущен: {data.get('reason')}")
            elif status == "ok":
                print(f"[{account_id}] OK — действие: {data.get('suggested_action')}, выполнено шагов: {len(data.get('actions_taken', []))}")
            else:
                print(f"[{account_id}] статус: {status}, {data}")
        except Exception as e:
            print(f"[{account_id}] ИСКЛЮЧЕНИЕ: {e}")

if __name__ == "__main__":
    main()
