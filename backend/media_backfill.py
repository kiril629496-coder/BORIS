"""Регистрация существующих файлов images/ в медиатеке.

ИНВАРИАНТ: ни одного move, rename или remove. Скрипт только читает диск и
пишет строки в media_assets. Публичные /images/... остаются byte-for-byte.

Fail-closed по владельцу: аккаунт без owner_user_id пропускается целиком и
попадает в отчёт — назначать владельца «по умолчанию» нельзя, за этим тянутся
права, лимиты AI и биллинг.

Запуск:  venv/bin/python3 media_backfill.py            (сухой прогон)
         venv/bin/python3 media_backfill.py --apply    (боевой)
"""
import os
import sys
import time

from sqlalchemy import text as _t

from app.db.session import SessionLocal
from app.services import media_service as M
from app.services import media_storage as st

BATCH = 500


def _owner_map(db):
    rows = db.execute(_t("SELECT account_id, owner_user_id FROM accounts")).fetchall()
    return {r[0]: r[1] for r in rows}


def run_backfill(db, dry_run=True, created_by=None, limit=0):
    t0 = time.time()
    owners = _owner_map(db)
    known = set(r[0] for r in db.execute(_t("SELECT storage_key FROM media_assets")).fetchall())
    folders = {}
    rep = {"seen": 0, "registered": 0, "already": 0, "skipped_prefix": 0,
           "skipped_service": 0, "skipped_no_owner": 0, "skipped_unknown_acc": 0,
           "by_type": {}, "no_owner_accounts": {}, "skipped_conflict": 0,
           "dry_run": bool(dry_run)}
    pending = 0

    for root, dirs, files in os.walk(st.IMAGES_DIR):
        rel_dir = os.path.relpath(root, st.IMAGES_DIR)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        if rel_dir and st.is_service_path(rel_dir):
            dirs[:] = []
            rep["skipped_service"] += len(files)
            continue
        parts = rel_dir.split("/") if rel_dir else []
        account_id = parts[0] if parts else ""
        folder_rel = "/".join(parts[1:]) if len(parts) > 1 else "общая"

        for fname in files:
            rep["seen"] += 1
            key = ("%s/%s" % (rel_dir, fname)) if rel_dir else fname
            mtype, stype = M.classify(fname)
            if mtype is None:
                rep["skipped_prefix"] += 1
                continue
            if not account_id or account_id not in owners:
                rep["skipped_unknown_acc"] += 1
                continue
            owner = owners.get(account_id)
            if owner is None:
                rep["skipped_no_owner"] += 1
                rep["no_owner_accounts"][account_id] = \
                    rep["no_owner_accounts"].get(account_id, 0) + 1
                continue
            if key in known:
                rep["already"] += 1
                continue
            rep["by_type"][mtype] = rep["by_type"].get(mtype, 0) + 1
            rep["registered"] += 1
            if dry_run:
                continue

            fkey = (owner, account_id, folder_rel)
            folder = folders.get(fkey)
            if folder is None:
                parent = None
                for piece in [p for p in folder_rel.split("/") if p]:
                    parent = M.ensure_folder(db, owner, account_id, piece,
                                             parent.id if parent else None,
                                             created_by=created_by)
                folder = parent
                folders[fkey] = folder
            try:
                size = st.size_of(key)
                sha = st.sha256_of(key) if size and size < 80 * 1024 * 1024 else None
            except OSError:
                continue
            # Savepoint на каждый файл: конфликт по любому ограничению не должен
            # убивать прогон из 11 000 записей.
            try:
                with db.begin_nested():
                    M.register_asset(db, owner, account_id, key, fname,
                                     media_type=mtype, source_type=stype,
                                     folder_id=folder.id if folder else None,
                                     sha256=sha, file_size=size,
                                     created_by=created_by, status="approved")
            except Exception as e:
                rep["skipped_conflict"] = rep.get("skipped_conflict", 0) + 1
                rep["registered"] -= 1
                rep["by_type"][mtype] = rep["by_type"].get(mtype, 1) - 1
                if rep["skipped_conflict"] <= 3:
                    print("конфликт на %s: %s" % (key, repr(e)[:140]), flush=True)
                continue
            known.add(key)
            pending += 1
            if pending >= BATCH:
                db.commit()
                pending = 0
            if limit and rep["registered"] >= limit:
                break
    if not dry_run:
        db.commit()
    rep["seconds"] = round(time.time() - t0, 1)
    return rep


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    db = SessionLocal()
    try:
        r = run_backfill(db, dry_run=not apply)
    finally:
        db.close()
    print("РЕЖИМ:", "БОЕВОЙ" if apply else "СУХОЙ ПРОГОН")
    for k in ("seen", "registered", "already", "skipped_prefix", "skipped_service",
              "skipped_no_owner", "skipped_unknown_acc", "skipped_conflict",
              "seconds"):
        print("  %-20s %s" % (k, r[k]))
    print("  по типам:", r["by_type"])
    if r["no_owner_accounts"]:
        print("  ПРОПУЩЕНЫ (нет владельца):", r["no_owner_accounts"])
