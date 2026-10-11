#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тест не должен требовать заполненного contact_name."""
import io
import sys
import time

APPLY = "--apply" in sys.argv
QA = "qa_note.py"

OLD = '''    lead = db.execute(text(
        "select id, contact_name, avito_chat_id, coalesce(note,'')"
        " from messenger_leads where account_id=:a"
        "   and coalesce(contact_name,'') <> ''"
        " order by last_msg_at desc nulls last limit 1"), {"a": ACC}).fetchone()
    if not lead:
        print("нет карточки с именем — тест невозможен")
        return
    lead_id, name, chat_id, note0 = lead[0], lead[1], lead[2], lead[3]'''

NEW = '''    fill = db.execute(text(
        "select count(*),"
        " count(*) filter (where coalesce(contact_name,'') <> ''),"
        " count(*) filter (where coalesce(company,'') <> ''),"
        " count(*) filter (where coalesce(item_title,'') <> '')"
        " from messenger_leads where account_id=:a"), {"a": ACC}).fetchone()
    print("карточек %s | с именем %s | с компанией %s | с объявлением %s"
          % tuple(fill))

    lead = db.execute(text(
        "select id, coalesce(contact_name,''), coalesce(company,''),"
        " coalesce(item_title,''), avito_chat_id, coalesce(note,'')"
        " from messenger_leads where account_id=:a"
        "   and (coalesce(contact_name,'') <> '' or coalesce(company,'') <> ''"
        "        or coalesce(item_title,'') <> '')"
        " order by last_msg_at desc nulls last limit 1"), {"a": ACC}).fetchone()
    if not lead:
        print("нет ни одной заполненной карточки — тест невозможен")
        return
    lead_id = lead[0]
    name = (lead[1] or lead[2] or lead[3])[:40]
    chat_id, note0 = lead[4], lead[5]'''


def main():
    src = io.open(QA, encoding="utf-8").read()
    if "нет ни одной заполненной карточки" in src:
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
    bak = "%s.bak_fill_%d" % (QA, int(time.time()))
    io.open(bak, "w", encoding="utf-8").write(src)
    io.open(QA, "w", encoding="utf-8").write(out)
    print("записано, бэкап %s" % bak)


if __name__ == "__main__":
    main()
