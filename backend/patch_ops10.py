#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Этап 2, шаг 10: категория факта для базы знаний."""
import io
import sys
import time

APPLY = "--apply" in sys.argv
PROMPT = "app/services/command_prompt.py"

OLD = ("- для правки карточки клиента (crm_create_or_update) клади отдельными\n"
       "  полями то, что прозвучало: contact_name, company, object_place,\n"
       "  rent_term, need_type, cost_rub. Ничего не выдумывай — только сказанное")
NEW = (OLD + "\n"
       "- для записи в базу знаний (knowledge_add) клади сам факт в text,\n"
       "  а в category одно из: cena (цены и залоги), usluga, tovar,\n"
       "  preimushchestvo, rule (правила работы, доставка, условия),\n"
       "  garantiya, stil, faq. Не уверен — ставь rule")


def main():
    src = io.open(PROMPT, encoding="utf-8").read()
    if "knowledge_add) клади" in src:
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
    bak = "%s.bak_ops10_%d" % (PROMPT, int(time.time()))
    io.open(bak, "w", encoding="utf-8").write(src)
    io.open(PROMPT, "w", encoding="utf-8").write(out)
    print("записано, бэкап %s" % bak)


if __name__ == "__main__":
    main()
