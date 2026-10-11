#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Этап 2, шаг 7: карточку ищем и по объявлению тоже."""
import io
import sys
import time

APPLY = "--apply" in sys.argv
OPS = "app/services/command_ops.py"

OLD_FIND = '''    if et == "dialog" and eid:
        row = db.execute(_t(
            "select id, coalesce(contact_name,''), coalesce(company,''),"
            " coalesce(note,'') from messenger_leads"
            " where account_id=:a and avito_chat_id=:c limit 1"),
            {"a": acc, "c": str(eid)}).fetchone()
        if row:
            return row
    if et == "lead" and eid:
        row = db.execute(_t(
            "select id, coalesce(contact_name,''), coalesce(company,''),"
            " coalesce(note,'') from messenger_leads"
            " where account_id=:a and id=:i limit 1"),
            {"a": acc, "i": int(eid)}).fetchone()
        if row:
            return row
    q = str((ctx.get("entities") or {}).get("client") or "").strip()
    if not q:
        return None
    return db.execute(_t(
        "select id, coalesce(contact_name,''), coalesce(company,''),"
        " coalesce(note,'') from messenger_leads"
        " where account_id=:a and (contact_name ilike :lk or company ilike :lk)"
        " order by last_msg_at desc nulls last limit 1"),
        {"a": acc, "lk": "%" + q + "%"}).fetchone()'''

NEW_FIND = '''    cols = ("select id, coalesce(contact_name,''), coalesce(company,''),"
            " coalesce(item_title,''), coalesce(note,'') from messenger_leads")
    if et == "dialog" and eid:
        row = db.execute(_t(
            cols + " where account_id=:a and avito_chat_id=:c limit 1"),
            {"a": acc, "c": str(eid)}).fetchone()
        if row:
            return row
    if et == "lead" and eid:
        row = db.execute(_t(
            cols + " where account_id=:a and id=:i limit 1"),
            {"a": acc, "i": int(eid)}).fetchone()
        if row:
            return row
    q = str((ctx.get("entities") or {}).get("client") or "").strip()
    if not q:
        return None
    return db.execute(_t(
        cols + " where account_id=:a and (contact_name ilike :lk"
        "   or company ilike :lk or item_title ilike :lk)"
        " order by last_msg_at desc nulls last limit 1"),
        {"a": acc, "lk": "%" + q + "%"}).fetchone()


def _lead_title(lead):
    """Подпись карточки: имя, компания, объявление — что заполнено."""
    return lead[1] or lead[2] or lead[3] or ("карточка #%s" % lead[0])'''

OLD_PREV = '''    who = (lead[1] or lead[2] or "карточка #%s" % lead[0]) if lead else \\
        (str((ctx.get("entities") or {}).get("client") or "") or "клиент не определён")
    s = "Заметка для «%s»: %s" % (who, txt or "текст не распознан")
    return {"summary": s,
            "changes": [{"entity": "messenger_leads", "field": "note",
                         "before": (lead[3][:80] if lead else None),'''
NEW_PREV = '''    who = _lead_title(lead) if lead else \\
        (str((ctx.get("entities") or {}).get("client") or "") or "клиент не определён")
    s = "Заметка для «%s»: %s" % (who[:60], txt or "текст не распознан")
    return {"summary": s,
            "changes": [{"entity": "messenger_leads", "field": "note",
                         "before": (lead[4][:80] if lead else None),'''

OLD_WHO = '''    who = lead[1] or lead[2] or ("карточка #%s" % lead[0])'''
NEW_WHO = '''    who = _lead_title(lead)[:60]'''


def main():
    src = io.open(OPS, encoding="utf-8").read()
    if "_lead_title" in src:
        print("УЖЕ ПРОПАТЧЕН")
        return
    out = src
    for old, new in ((OLD_FIND, NEW_FIND), (OLD_PREV, NEW_PREV), (OLD_WHO, NEW_WHO)):
        if out.count(old) != 1:
            print("ОСТАНОВКА: якорь встречается %d раз" % out.count(old))
            sys.exit(2)
        out = out.replace(old, new, 1)
    print("строк %d -> %d" % (src.count("\n"), out.count("\n")))
    if not APPLY:
        print("DRY-RUN. Для записи: --apply")
        return
    bak = "%s.bak_ops7_%d" % (OPS, int(time.time()))
    io.open(bak, "w", encoding="utf-8").write(src)
    io.open(OPS, "w", encoding="utf-8").write(out)
    print("записано, бэкап %s" % bak)


if __name__ == "__main__":
    main()
