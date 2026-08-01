"""Статистика Директора по снимкам daily_stats: период + динамика к предыдущему."""
import json as _json
from datetime import date as _date, timedelta as _timedelta
from fastapi import APIRouter, Depends
from app.api.auth import require_owner

from app.db.session import SessionLocal
from app.models.storage import Storage

router = APIRouter(prefix="/api/avito", tags=["director"])


def _delta(cur, prev):
    if not prev:
        return None
    return round((cur - prev) / prev * 100, 1)


def _collect(db, keys, account_id=None):
    q = db.query(Storage).filter(Storage.key.in_(keys))
    if account_id:
        q = q.filter(Storage.account_id == account_id)
    by_day, by_acc = {}, {}
    for r in q.all():
        day = r.key.split(":", 1)[1]
        try:
            snap = _json.loads(r.value)
        except Exception:
            continue
        items = snap.get("items") or []
        v = sum(int(i.get("views") or 0) for i in items)
        c = sum(int(i.get("contacts") or 0) for i in items)
        d = by_day.setdefault(day, {"date": day, "views": 0, "contacts": 0})
        d["views"] += v
        d["contacts"] += c
        a = by_acc.setdefault(r.account_id, {"views": 0, "contacts": 0})
        a["views"] += v
        a["contacts"] += c
    return by_day, by_acc


def _conv(v, c):
    return round(c / v * 100, 2) if v else 0


@router.get("/director_stats")
def director_stats(days: int = 30, date_from: str = None, date_to: str = None, account_id: str = None, _=Depends(require_owner)):
    today = _date.today()
    if date_from and date_to:
        try:
            d1 = _date.fromisoformat(date_from)
            d2 = _date.fromisoformat(date_to)
        except ValueError:
            return {"status": "error", "message": "Даты в формате ГГГГ-ММ-ДД"}
        if d2 < d1:
            d1, d2 = d2, d1
        days = (d2 - d1).days + 1
        start = d1
    else:
        days = max(1, min(int(days or 30), 365))
        start = today - _timedelta(days=days - 1)
    days = max(1, min(days, 365))
    cur_keys = [f"daily_stats:{(start + _timedelta(days=i)).isoformat()}" for i in range(days)]
    prev_start = start - _timedelta(days=days)
    prev_keys = [f"daily_stats:{(prev_start + _timedelta(days=i)).isoformat()}" for i in range(days)]

    db = SessionLocal()
    try:
        cur_day, cur_acc = _collect(db, cur_keys, account_id)
        prev_day, prev_acc = _collect(db, prev_keys, account_id)

        for d in cur_day.values():
            d["conversion"] = _conv(d["views"], d["contacts"])
        series = [cur_day[k] for k in sorted(cur_day)]

        cv = sum(x["views"] for x in cur_day.values())
        cc = sum(x["contacts"] for x in cur_day.values())
        pv = sum(x["views"] for x in prev_day.values())
        pc = sum(x["contacts"] for x in prev_day.values())
        cconv, pconv = _conv(cv, cc), _conv(pv, pc)

        accounts = []
        for acc_id, a in cur_acc.items():
            p = prev_acc.get(acc_id, {"views": 0, "contacts": 0})
            accounts.append({
                "account_id": acc_id,
                "views": a["views"], "contacts": a["contacts"],
                "conversion": _conv(a["views"], a["contacts"]),
                "prev_views": p["views"], "prev_contacts": p["contacts"],
                "d_views": _delta(a["views"], p["views"]),
                "d_contacts": _delta(a["contacts"], p["contacts"]),
                "d_conversion": _delta(_conv(a["views"], a["contacts"]), _conv(p["views"], p["contacts"])),
            })
        accounts.sort(key=lambda x: -x["views"])

        oldest = db.query(Storage.key).filter(Storage.key.like("daily_stats:%")).order_by(Storage.key.asc()).first()

        return {
            "status": "ok",
            "days_requested": days,
            "period_from": start.isoformat(),
            "period_to": (start + _timedelta(days=days-1)).isoformat(),
            "prev_from": prev_start.isoformat(),
            "prev_to": (prev_start + _timedelta(days=days-1)).isoformat(),
            "days_with_data": len(series),
            "prev_days_with_data": len(prev_day),
            "data_since": oldest[0].split(":", 1)[1] if oldest else None,
            "totals": {"views": cv, "contacts": cc, "conversion": cconv},
            "prev_totals": {"views": pv, "contacts": pc, "conversion": pconv},
            "deltas": {
                "views": _delta(cv, pv),
                "contacts": _delta(cc, pc),
                "conversion": _delta(cconv, pconv),
            },
            "series": series,
            "by_account": accounts,
        }
    finally:
        db.close()
