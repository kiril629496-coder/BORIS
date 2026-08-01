"""Ежедневный сбор статистики по всем аккаунтам. Запускать через cron раз в сутки."""
import httpx
import psycopg2

def _load_dsn():
    for _l in open(".env"):
        if _l.startswith("DATABASE_URL"):
            return _l.split("=", 1)[1].strip().strip('"').strip("'")
    return None

DB_DSN = _load_dsn()
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
            r = httpx.get(f"{API_BASE}/api/avito/collect_stats", params={"account_id": account_id}, timeout=60)
            data = r.json()
            if data.get("status") == "ok":
                snap = data.get("snapshot", {})
                print(f"[{account_id}] OK — объявлений: {snap.get('items_count')}, баланс: {snap.get('balance')}")
            else:
                print(f"[{account_id}] ОШИБКА: {data.get('message')}")
        except Exception as e:
            print(f"[{account_id}] ИСКЛЮЧЕНИЕ: {e}")

if __name__ == "__main__":
    main()
