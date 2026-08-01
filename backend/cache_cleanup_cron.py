#!/root/BORIS/backend/venv/bin/python3
"""Ежедневная чистка мусора в кэше city_analysis - удаляет пустые результаты
(0 объявлений = провал парсинга) и записи старше 7 дней."""
import sys, os
sys.path.insert(0, "/root/BORIS/backend")
os.chdir("/root/BORIS/backend")
import psycopg2, json
from datetime import datetime, timedelta

url = None
for l in open(".env"):
    if l.startswith("DATABASE_URL"):
        url = l.split("=", 1)[1].strip().strip('"').strip("'")
        break

c = psycopg2.connect(url)
cur = c.cursor()
cur.execute("SELECT key, value FROM storage WHERE key LIKE 'city_analysis:%';")
rows = cur.fetchall()
deleted = 0
cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

for key, value in rows:
    # ключ формата city_analysis:{city}:{query}:{date}
    date_part = key.split(":")[-1]
    is_old = date_part < cutoff
    is_empty = False
    try:
        data = json.loads(value)
        is_empty = not data.get("top5") and not data.get("avg_price")
    except Exception:
        is_empty = True
    if is_old or is_empty:
        cur.execute("DELETE FROM storage WHERE key=%s;", (key,))
        deleted += 1

c.commit()
print(f"[{datetime.now()}] Проверено: {len(rows)}, удалено (пустых/старых): {deleted}")
cur.close()
c.close()
