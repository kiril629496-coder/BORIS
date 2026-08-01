"""
Часовой раннер Бориса-Советника по ставкам.
Находит аккаунты с заданным KPI и дёргает эндпоинт Советника (вся работа с БД — внутри бэкенда).
БД читаем напрямую через psycopg2 с DSN из .env (НЕ через app.db.session — у него другие креды при прямом запуске).
НИЧЕГО НЕ МЕНЯЕТ. Запуск: cron раз в час.
"""
import json, httpx, psycopg2
from datetime import datetime


def _dsn():
    for l in open("/root/BORIS/backend/.env"):
        if l.startswith("DATABASE_URL"):
            return l.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL не найден в .env")


def main():
    conn = psycopg2.connect(_dsn())
    cur = conn.cursor()
    try:
        cur.execute("SELECT account_id, value FROM storage WHERE key = 'kpi_settings';")
        accounts = []
        for account_id, value in cur.fetchall():
            try:
                kpi = json.loads(value)
                if kpi.get("max_cost_per_lead_rub"):
                    accounts.append(account_id)
            except Exception:
                pass
        print(f"[{datetime.now().isoformat()}] Советник: аккаунтов с KPI = {len(accounts)}: {accounts}")
        # заодно узнаём, у кого включён автопилот ставок
        auto_accounts = set()
        cur.execute("SELECT account_id, value FROM storage WHERE key = 'kpi_settings';")
        for aid, val in cur.fetchall():
            try:
                if json.loads(val).get("bid_autopilot"):
                    auto_accounts.add(aid)
            except Exception:
                pass

        for acc in accounts:
            try:
                resp = httpx.get(f"http://127.0.0.1:8000/api/cpx_advisor/run?account_id={acc}", timeout=120)
                data = resp.json()
                advice = data.get("advice", {})
                summary = advice.get("summary", data.get("message", "нет данных"))
                print(f"  {acc}: {data.get('status')} — {summary}")

                # АВТОПИЛОТ: если включён — применяем рекомендации (с предохранителями внутри apply_one)
                if acc in auto_accounts and advice:
                    recs = advice.get("recommendations", {})
                    applied = 0
                    for action_key, act in [("raise", "raise"), ("lower_or_archive", "archive")]:
                        for it in recs.get(action_key, []):
                            try:
                                ar = httpx.post("http://127.0.0.1:8000/api/cpx_advisor/apply_one",
                                    json={"account_id": acc, "item_id": it["id"], "action": act}, timeout=30)
                                if ar.json().get("status") == "ok":
                                    applied += 1
                            except Exception as e:
                                print(f"    apply {it.get('id')}: ошибка {str(e)[:80]}")
                    if applied:
                        print(f"    🤖 АВТОПИЛОТ: применено действий = {applied}")
            except Exception as e:
                print(f"  {acc}: ОШИБКА {str(e)[:150]}")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
