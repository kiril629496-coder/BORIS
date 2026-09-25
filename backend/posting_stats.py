# -*- coding: utf-8 -*-
"""Сбор статистики по опубликованным постам ВКонтакте.
Запуск: venv/bin/python3 posting_stats.py [--apply]
Без флага показывает, что собрал бы, ничего не записывая."""
import os, sys, json, datetime, requests

for _l in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")):
    _l = _l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        _k, _v = _l.split("=", 1)
        os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))

from sqlalchemy import text
from app.db.session import SessionLocal

APPLY = "--apply" in sys.argv
VK = "https://api.vk.com/method/"
MAX_AGE_DAYS = 30          # посты старше месяца уже не меняются, не опрашиваем


def _vk_stats(owner_id, post_ids):
    """Пачкой забирает метрики по списку post_id одной группы."""
    tok = os.environ.get("VK_TOKEN")
    if not tok or not post_ids:
        return {}
    posts = ",".join("%s_%s" % (owner_id, pid) for pid in post_ids)
    r = requests.get(VK + "wall.getById",
                     params={"posts": posts, "access_token": tok, "v": "5.199"}, timeout=30)
    d = r.json()
    if "error" in d:
        print("  ВК ошибка:", d["error"].get("error_msg"))
        return {}
    out = {}
    items = d.get("response") or []
    if isinstance(items, dict):
        items = items.get("items") or []
    for p in items:
        likes = (p.get("likes") or {}).get("count") or 0
        comments = (p.get("comments") or {}).get("count") or 0
        reposts = (p.get("reposts") or {}).get("count") or 0
        views = (p.get("views") or {}).get("count") or 0
        reactions = likes + comments + reposts
        out[p["id"]] = {
            "likes": likes, "comments": comments, "reposts": reposts, "views": views,
            "reactions": reactions,
            "er": round(reactions * 100.0 / views, 2) if views else None,
            "checked_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
    return out


def collect():
    db = SessionLocal()
    rows = db.execute(text("select id, account_id, value from storage where key='posting_posts'")).fetchall()
    today = datetime.date.today()
    total = updated = 0
    for rid, acc, raw in rows:
        posts = json.loads(raw) if raw else []
        by_owner = {}
        for p in posts:
            if p.get("status") != "published":
                continue
            vk = (p.get("ids") or {}).get("vk") or {}
            pid, owner = vk.get("post_id"), vk.get("owner_id")
            if not pid or not owner:
                continue
            created = (p.get("created_at") or "")[:10]
            try:
                age = (today - datetime.date.fromisoformat(created)).days
                if age > MAX_AGE_DAYS:
                    continue
            except Exception:
                pass
            by_owner.setdefault(str(owner), []).append((int(pid), p))
        for owner, pairs in by_owner.items():
            stats = _vk_stats(owner, [pid for pid, _ in pairs])
            for pid, p in pairs:
                s = stats.get(pid)
                total += 1
                if not s:
                    continue
                p["stats"] = s
                updated += 1
                print("  %-8s vk %-6s лайки %-4s комм %-3s репосты %-3s просмотры %-6s ER %-6s | %s"
                      % (acc, pid, s["likes"], s["comments"], s["reposts"], s["views"],
                         s["er"] if s["er"] is not None else "—",
                         (p.get("text") or "")[:38].replace("\n", " ")))
        if APPLY and posts:
            db.execute(text("update storage set value=:v where id=:i"),
                       {"v": json.dumps(posts, ensure_ascii=False), "i": rid})
    if APPLY:
        db.commit()
    db.close()
    print("\nпостов проверено: %s | метрик собрано: %s | %s"
          % (total, updated, "ЗАПИСАНО" if APPLY else "ПОКАЗ, ничего не записано"))


if __name__ == "__main__":
    collect()
