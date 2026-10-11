"""Врезка Autonomy Guard в apply_one. Dry-run по умолчанию, применение --apply."""
import sys, os, time, shutil, tempfile, py_compile

F = "/root/BORIS/backend/app/api/cpx_advisor.py"

GUARD_HEAD = '''            from app.services.autonomy import can_execute_live_action, log_guard_block
            _ctx = {"actor": body.actor_type or "", "trigger": body.trigger or "",
                    "source": body.source or "cpx_advisor.apply_one",
                    "request_id": body.request_id or ""}
'''

E = [
 ("1. trigger в модели запроса",
  '''    request_id: str = ""''',
  '''    request_id: str = ""
    trigger: str = ""'''),

 ("2. guard в ветке archive",
  '''        if body.action == "archive":''',
  '''        if body.action == "archive":
''' + GUARD_HEAD + '''            _g = can_execute_live_action(db, body.account_id, "cpx.remove_promotion",
                                         _ctx, bid_context={"item_id": body.item_id},
                                         balance={"status": "UNKNOWN", "value": None})
            if not _g["allowed"]:
                log_guard_block(db, body.account_id, "cpx.remove_promotion", "blocked",
                                _g["reason_code"], _ctx, object_id=body.item_id,
                                mandate={"id": _g.get("mandate_id"),
                                         "version": _g.get("mandate_version")},
                                balance_status="UNKNOWN",
                                limits=_g.get("limits_applied"), evidence=_g.get("evidence"))
                return {"status": "blocked", "reason_code": _g["reason_code"],
                        "action": body.action, "item_id": body.item_id,
                        "changed_avito": False}'''),

 ("3. guard в ветке ставок",
  '''        payload = {"actionTypeID": action_type, "bidPenny": int(new_bid), "itemID": body.item_id}''',
  '''        from app.services.autonomy import can_execute_live_action, log_guard_block
        _ctx = {"actor": body.actor_type or "", "trigger": body.trigger or "",
                "source": body.source or "cpx_advisor.apply_one",
                "request_id": body.request_id or ""}
        _op = "cpx.raise_bid" if body.action == "raise" else "cpx.lower_bid"
        _g = can_execute_live_action(
            db, body.account_id, _op, _ctx,
            bid_context={"item_id": body.item_id,
                         "old_bid_rub": cur_bid / 100.0,
                         "new_bid_rub": new_bid / 100.0,
                         "avito_min_bid_rub": (min_bid / 100.0) if min_bid else None},
            balance={"status": "UNKNOWN", "value": None})
        if not _g["allowed"]:
            log_guard_block(db, body.account_id, _op, "blocked", _g["reason_code"], _ctx,
                            object_id=body.item_id,
                            mandate={"id": _g.get("mandate_id"),
                                     "version": _g.get("mandate_version")},
                            balance_status="UNKNOWN",
                            limits=_g.get("limits_applied"), evidence=_g.get("evidence"))
            return {"status": "blocked", "reason_code": _g["reason_code"],
                    "action": body.action, "item_id": body.item_id,
                    "changed_avito": False}

        payload = {"actionTypeID": action_type, "bidPenny": int(new_bid), "itemID": body.item_id}'''),

 ("4. доказательность в журнале archive",
  '''                           actor=(body.actor_type or "user"),
                           source=(body.source or "cpx_advisor.apply_one"))''',
  '''                           actor=(body.actor_type or "user"),
                           source=(body.source or "cpx_advisor.apply_one"),
                           trigger=(body.trigger or None),
                           mandate_id=_g.get("mandate_id"),
                           mandate_version=_g.get("mandate_version"),
                           request_id=(body.request_id or None),
                           balance_status="UNKNOWN",
                           guard_reason=_g.get("reason_code"))'''),

 ("5. доказательность в журнале ставок",
  '''                       actor=(body.actor_type or "user"),
                       source=(body.source or "cpx_advisor.apply_one"))''',
  '''                       actor=(body.actor_type or "user"),
                       source=(body.source or "cpx_advisor.apply_one"),
                       trigger=(body.trigger or None),
                       mandate_id=_g.get("mandate_id"),
                       mandate_version=_g.get("mandate_version"),
                       request_id=(body.request_id or None),
                       balance_status="UNKNOWN",
                       guard_reason=_g.get("reason_code"))'''),
]


def main():
    apply = "--apply" in sys.argv
    src = open(F, encoding="utf-8").read()
    for label, old, new in E:
        n = src.count(old)
        if n != 1:
            print(f"СТОП: якорь «{label}» найден {n} раз")
            sys.exit(1)
        if new in src:
            print(f"СТОП: «{label}» уже применён")
            sys.exit(1)
        src = src.replace(old, new, 1)
        print(f"ok  {label}")
    fd, t = tempfile.mkstemp(suffix=".py"); os.close(fd)
    open(t, "w", encoding="utf-8").write(src)
    py_compile.compile(t, doraise=True)
    print("COMPILE PASS")
    if not apply:
        os.unlink(t); print("DRY-RUN PASS. Файл не тронут."); return
    bak = f"{F}.bak_guard_{int(time.time())}"
    shutil.copy2(F, bak); shutil.copy2(t, F); os.unlink(t)
    py_compile.compile(F, doraise=True)
    print(f"ПРИМЕНЕНО {F}  бэкап {bak}")


if __name__ == "__main__":
    main()
