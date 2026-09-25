from __future__ import annotations

import json

from sqlalchemy import text

from app.models.storage import Storage


_LOCK_SEED = 731927411


def _lock_marker(account_id: str) -> str:
    return "posting_posts_lock:" + str(account_id)


def lock_posting_posts(db, account_id: str) -> None:
    """Serialize all read-modify-write operations for one account's posting_posts.

    Transaction-scoped advisory lock + row FOR UPDATE prevents the JSON blob
    from suffering last-writer-wins loss when API, scheduler and cleanup mutate
    different post records concurrently.
    """
    key = "posting_posts:" + str(account_id)
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, :seed))"),
        {"k": key, "seed": _LOCK_SEED},
    )
    db.info[_lock_marker(account_id)] = True


def posting_posts_lock_held(db, account_id: str) -> bool:
    return bool(db.info.get(_lock_marker(account_id)))


def load_posting_posts_for_update(db, account_id: str):
    lock_posting_posts(db, account_id)
    row = (
        db.query(Storage)
        .filter(Storage.account_id == account_id, Storage.key == "posting_posts")
        .with_for_update()
        .first()
    )
    try:
        posts = json.loads(row.value or "[]") if row else []
    except Exception:
        posts = []
    if not isinstance(posts, list):
        posts = []
    return row, posts


def save_posting_posts_locked(db, account_id: str, row, posts) -> None:
    if not posting_posts_lock_held(db, account_id):
        raise RuntimeError("POSTING_POSTS_WRITE_WITHOUT_LOCK")
    payload = json.dumps(posts if isinstance(posts, list) else [], ensure_ascii=False)
    if row:
        row.value = payload
    else:
        db.add(Storage(account_id=account_id, key="posting_posts", value=payload))
    db.commit()
    db.info.pop(_lock_marker(account_id), None)
