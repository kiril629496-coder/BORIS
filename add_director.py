path = "/root/BORIS/backend/app/api/avito.py"
with open(path, encoding="utf-8") as f:
    src = f.read()

addition = '''

@router.get("/director_overview")
def director_overview():
    """Сводка по всем клиентам для Бориса-директора: последний снимок статистики каждого."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    from app.models.account import Account
    import json as _json
    from datetime import date as _date, timedelta as _timedelta

    db = SessionLocal()
    try:
        accounts = db.query(Account).all()
        overview = []
        for acc in accounts:
            latest_snapshot = None
            for i in range(7):
                d = (_date.today() - _timedelta(days=i)).isoformat()
                row = db.query(Storage).filter(Storage.account_id == acc.account_id, Storage.key == f"daily_stats:{d}").first()
                if row:
                    latest_snapshot = _json.loads(row.value)
                    break
            has_avito_keys = bool(acc.avito_client_id and acc.avito_client_secret)
            overview.append({
                "account_id": acc.account_id,
                "name": acc.name,
                "has_avito_keys": has_avito_keys,
                "latest_stats_date": latest_snapshot.get("date") if latest_snapshot else None,
                "balance": latest_snapshot.get("balance") if latest_snapshot else None,
                "items_count": latest_snapshot.get("items_count") if latest_snapshot else None
            })
    finally:
        db.close()

    return {"status": "ok", "accounts": overview}
'''

if "director_overview" not in src:
    src = src + addition
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print("OK: обзор для директора добавлен")
else:
    print("УЖЕ ЕСТЬ")
