#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тест аналитики должен пополнять пул: иначе проверяет не аналитику,
а отказ по исчерпанному лимиту (который и так проверен отдельно)."""
import io
import sys
import time

APPLY = "--apply" in sys.argv
QA = "qa_an.py"

OLD = '''    kind, oid = L.resolve_billing_owner(db, acc)
    print("аккаунт: %s" % acc)'''

NEW = '''    kind, oid = L.resolve_billing_owner(db, acc)
    from app.services import command_registry as R
    if not L.has_feature(db, kind, oid, R.FEATURE_BASE):
        L.set_feature(db, kind, oid, R.FEATURE_BASE, True)
    if L.pool_state(db, kind, oid)["left"] < 20:
        db.execute(text(
            "insert into product_limits (billing_owner_kind, billing_owner_id,"
            " resource, purchased, used, period_start, paid_until)"
            " values (:k,:o,'commands',200,0,current_date,current_date + 30)"
            " on conflict (billing_owner_kind, billing_owner_id, resource)"
            " do update set purchased=200, used=0, paid_until=current_date + 30"),
            {"k": kind, "o": oid})
        db.commit()
    print("аккаунт: %s | остаток пула: %d"
          % (acc, L.pool_state(db, kind, oid)["left"]))'''


def main():
    src = io.open(QA, encoding="utf-8").read()
    if "остаток пула" in src:
        print("УЖЕ ПРОПАТЧЕН")
        return
    if src.count(OLD) != 1:
        print("ОСТАНОВКА: якорь встречается %d раз" % src.count(OLD))
        sys.exit(2)
    out = src.replace(OLD, NEW, 1)
    print("строк %d -> %d" % (src.count("\n"), out.count("\n")))
    if not APPLY:
        print("DRY-RUN. Для записи: --apply")
        return
    bak = "%s.bak_pool_%d" % (QA, int(time.time()))
    io.open(bak, "w", encoding="utf-8").write(src)
    io.open(QA, "w", encoding="utf-8").write(out)
    print("записано, бэкап %s" % bak)


if __name__ == "__main__":
    main()
