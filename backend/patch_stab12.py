#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Стабилизация 1 и 2.

1) Результат успешного исполнителя сохраняется в boris_commands.result —
   очищенным через redact_payload и с ограничением размера.
2) Ранние отказы (до обращения к модели) пишутся в boris_command_events
   с нормализованным error_code. Секретов в payload нет.
"""
import io
import sys
import time

APPLY = "--apply" in sys.argv
FLOW = "app/services/command_flow.py"

OLD_IMP = "import datetime\n"
NEW_IMP = "import datetime\nimport json\n"

OLD_IMP2 = "from app.services import command_store as store"
NEW_IMP2 = ("from app.services import command_store as store\n"
            "from app.services.command_redact import redact_payload")

OLD_PAY = '''def _finish(db, user, row, command_id):'''
NEW_PAY = '''def _result_json(res):
    """Снимок результата для boris_commands.result — очищенный и обрезанный."""
    data = _payload(res)
    if not data:
        return None
    try:
        return json.dumps(redact_payload(data), ensure_ascii=False)[:8000]
    except Exception:
        return None


def _finish(db, user, row, command_id):'''

OLD_CHARGE = '''    charged = limits.charge_units_once(
        db, command_id, int(row.get("units") or 0),
        row.get("billing_owner_kind"), row.get("billing_owner_id"),
        duration_ms=ms)'''
NEW_CHARGE = '''    charged = limits.charge_units_once(
        db, command_id, int(row.get("units") or 0),
        row.get("billing_owner_kind"), row.get("billing_owner_id"),
        duration_ms=ms, result_json=_result_json(res))'''

OLD_REJ = '''    owner, err = policy.precheck(db, user, inp.account_id, inp.source)
    if err and not owner:
        return _out(command_id, "failed", error_code=err)
    kind, oid = owner
    if err:
        store.create(db, command_id, uid, kind, oid, inp.account_id, inp.source,
                     raw_text=inp.text, transcript=inp.transcript)
        store.set_status(db, command_id, "failed", err)
        return _out(command_id, "failed", error_code=err)'''

NEW_REJ = '''    owner, err = policy.precheck(db, user, inp.account_id, inp.source)
    if err and not owner:
        # Отказ ДО разбора тоже должен быть в журнале: для вопроса «что
        # Борис делал сегодня» отказ не менее важен успеха. Секретов нет —
        # только нормализованный код, аккаунт, источник и стадия.
        audit.log_event(db, command_id, "rejected", actor=uid,
                        payload={"error_code": err, "stage": "precheck",
                                 "account_id": inp.account_id,
                                 "source": inp.source})
        return _out(command_id, "failed", error_code=err)
    kind, oid = owner
    if err:
        store.create(db, command_id, uid, kind, oid, inp.account_id, inp.source,
                     raw_text=inp.text, transcript=inp.transcript)
        store.set_status(db, command_id, "failed", err)
        audit.log_event(db, command_id, "rejected", actor=uid,
                        payload={"error_code": err, "stage": "precheck",
                                 "account_id": inp.account_id,
                                 "source": inp.source})
        return _out(command_id, "failed", error_code=err)'''


def main():
    src = io.open(FLOW, encoding="utf-8").read()
    if "_result_json" in src:
        print("УЖЕ ПРОПАТЧЕН")
        return
    out = src
    for old, new in ((OLD_IMP, NEW_IMP), (OLD_IMP2, NEW_IMP2), (OLD_PAY, NEW_PAY),
                     (OLD_CHARGE, NEW_CHARGE), (OLD_REJ, NEW_REJ)):
        if out.count(old) != 1:
            print("ОСТАНОВКА: якорь встречается %d раз: %s"
                  % (out.count(old), old.strip().splitlines()[0][:50]))
            sys.exit(2)
        out = out.replace(old, new, 1)
    print("строк %d -> %d" % (src.count("\n"), out.count("\n")))
    if not APPLY:
        print("DRY-RUN. Для записи: --apply")
        return
    bak = "%s.bak_stab12_%d" % (FLOW, int(time.time()))
    io.open(bak, "w", encoding="utf-8").write(src)
    io.open(FLOW, "w", encoding="utf-8").write(out)
    print("записано, бэкап %s" % bak)


if __name__ == "__main__":
    main()
