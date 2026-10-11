"""Синтетические тесты Autonomy Guard на живых мандатах. Только чтение."""
import sys
sys.path.insert(0, "/root/BORIS/backend")

from app.db.session import SessionLocal
from app.services.autonomy import can_execute_live_action, OPERATIONS
from sqlalchemy import text

SALE = "prodazha_bytovok_25677"
RENT = "3411770_94346"
OTHER = "garik_plitka_mo_58647"

CTX = {"actor": "boris_auto", "trigger": "cron",
       "source": "cpx_advisor.apply_one", "request_id": "qa-run-0001"}

res = []


def check(name, got, expect):
    res.append((name, got == expect, f"{got} (ждали {expect})"))


def main():
    db = SessionLocal()
    try:
        print("реестр операций:", ", ".join(sorted(OPERATIONS)))
        print()

        r = can_execute_live_action(db, SALE, "cpx.delete_everything", CTX)
        check("неизвестная операция", r["reason_code"], "blocked_unknown_operation")

        r = can_execute_live_action(db, SALE, "cpx.raise_bid", {"actor": "boris_auto"})
        check("неполный execution_context", r["reason_code"],
              "blocked_missing_execution_context")

        r = can_execute_live_action(db, SALE, "cpx.remove_promotion", CTX)
        check("снятие продвижения без подтверждения", r["reason_code"],
              "blocked_requires_confirmation")

        r = can_execute_live_action(db, OTHER, "cpx.raise_bid", CTX,
                                    bid_context={"item_id": "1", "old_bid_rub": 20,
                                                 "new_bid_rub": 22})
        check("аккаунт вне scope мандата", r["reason_code"], "blocked_no_mandate")

        ok_bid = {"item_id": "9001", "old_bid_rub": 20, "new_bid_rub": 23,
                  "avito_min_bid_rub": 3}
        r = can_execute_live_action(db, SALE, "cpx.raise_bid", CTX, bid_context=ok_bid)
        check("продажа: допустимый raise", r["reason_code"],
              "allowed_by_mandate_balance_unknown")
        check("продажа: mandate_id проставлен", bool(r["mandate_id"]), True)
        check("продажа: mandate_version проставлен", bool(r["mandate_version"]), True)
        check("бюджет помечен PARTIAL", r["limits_applied"].get("daily_budget_enforcement"),
              "PARTIAL")

        r = can_execute_live_action(db, SALE, "cpx.raise_bid", CTX,
                                    bid_context={"item_id": "9002", "old_bid_rub": 20,
                                                 "new_bid_rub": 25, "avito_min_bid_rub": 3})
        check("шаг 25% превышает 15%", r["reason_code"], "blocked_bid_delta_exceeded")

        r = can_execute_live_action(db, SALE, "cpx.raise_bid", CTX,
                                    bid_context={"item_id": "9003", "old_bid_rub": 60,
                                                 "new_bid_rub": 66, "avito_min_bid_rub": 3})
        check("ставка выше потолка 60", r["reason_code"], "blocked_bid_above_max")

        r = can_execute_live_action(db, SALE, "cpx.lower_bid", CTX,
                                    bid_context={"item_id": "9004", "old_bid_rub": 20,
                                                 "new_bid_rub": 18, "avito_min_bid_rub": 19})
        check("ниже минимума Avito", r["reason_code"], "blocked_bid_below_min")

        r = can_execute_live_action(db, RENT, "cpx.raise_bid", CTX,
                                    bid_context={"item_id": "9005", "old_bid_rub": 20,
                                                 "new_bid_rub": 22, "avito_min_bid_rub": 3})
        check("аренда: допустимый raise", r["reason_code"],
              "allowed_by_mandate_balance_unknown")
        check("аренда: лимит run = 2", r["limits_applied"]["max_actions_run"], 2)

        r = can_execute_live_action(db, SALE, "cpx.raise_bid", CTX, bid_context=ok_bid,
                                    balance={"status": "KNOWN", "value": 4807.96})
        check("баланс KNOWN без reserve", r["reason_code"], "allowed_by_mandate")

        r = can_execute_live_action(db, SALE, "cpx.raise_bid", CTX, bid_context=ok_bid,
                                    mass_count=10)
        check("массовое действие", r["reason_code"], "blocked_mass_action")

        db.execute(text("UPDATE money_mandates SET status='suspended'"
                        " WHERE :a = ANY(account_scope)"), {"a": SALE})
        r = can_execute_live_action(db, SALE, "cpx.raise_bid", CTX, bid_context=ok_bid)
        check("kill-switch: suspended", r["reason_code"], "blocked_mandate_suspended")
        db.rollback()

        r = can_execute_live_action(db, SALE, "cpx.raise_bid", CTX, bid_context=ok_bid)
        check("после отката мандат снова активен", r["reason_code"],
              "allowed_by_mandate_balance_unknown")

        print()
        for name, ok, got in res:
            print(f"  {'PASS' if ok else 'FAIL'}  {name:42s} {got}")
        p = sum(1 for _, o, _ in res if o)
        print(f"\nИТОГО: {p} PASS, {len(res) - p} FAIL")

        st = db.execute(text("SELECT status, count(*) FROM money_mandates"
                             " GROUP BY status")).fetchall()
        print("состояние мандатов после тестов:", [tuple(x) for x in st])
        n = db.execute(text("SELECT count(*) FROM autonomy_guard_events")).fetchone()
        print("записей в autonomy_guard_events:", n[0])
    finally:
        db.close()


if __name__ == "__main__":
    main()
