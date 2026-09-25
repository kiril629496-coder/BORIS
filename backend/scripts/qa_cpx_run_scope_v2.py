#!/usr/bin/env python3
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.db.session import SessionLocal

QA_ACCOUNT="planetazayavki_65985"

def main():
    db=SessionLocal()
    try:
        db.begin()

        mandate=db.execute(text("""
          SELECT id,max_actions_run,max_actions_day
          FROM money_mandates
          WHERE :a=ANY(account_scope)
            AND status='active' AND revoked_at IS NULL
            AND source='paid_tariff_kpi'
            AND 'cpx.raise_bid'=ANY(allowed_operations)
            AND (valid_from IS NULL OR valid_from<=now())
            AND (valid_until IS NULL OR valid_until>now())
          ORDER BY id DESC LIMIT 1
        """),{"a":QA_ACCOUNT}).mappings().first()
        assert mandate, "PLANETA active paid KPI mandate missing"
        assert int(mandate["max_actions_run"])>=5
        print("MANDATE=PASS",dict(mandate))

        # These synthetic receipts never leave this transaction and never call Avito.
        for i in range(1,6):
            db.execute(text("""
              INSERT INTO cpx_execution_receipts(
                account_id,request_key,item_id,action,source,status,old_bid_penny,intended_bid_penny
              ) VALUES(:a,:k,:item,'raise','new_feed_hourly_ramp','prepared',5000,5500)
            """),{"a":QA_ACCOUNT,"k":f"nf10:2099010112:{990000000+i}","item":990000000+i})
        print("HOUR_12_FIRST_5=PASS")

        # A new nf10 hour must have its own run capacity.
        for i in range(7,12):
            db.execute(text("""
              INSERT INTO cpx_execution_receipts(
                account_id,request_key,item_id,action,source,status,old_bid_penny,intended_bid_penny
              ) VALUES(:a,:k,:item,'raise','new_feed_hourly_ramp','prepared',5000,5500)
            """),{"a":QA_ACCOUNT,"k":f"nf10:2099010113:{990000000+i}","item":990000000+i})
        print("HOUR_13_NEXT_5=PASS")

        total=db.execute(text("""
          SELECT count(*) FROM cpx_execution_receipts
          WHERE account_id=:a AND request_key LIKE 'nf10:20990101%'
        """),{"a":QA_ACCOUNT}).scalar()
        assert total==10,total
        print("CROSS_HOUR_CAPACITY=PASS total=",total)

        # Only after proving the next hour is allowed, verify the sixth action in
        # the original hour is rejected. The transaction is expected to abort here.
        try:
            db.execute(text("""
              INSERT INTO cpx_execution_receipts(
                account_id,request_key,item_id,action,source,status,old_bid_penny,intended_bid_penny
              ) VALUES(:a,:k,:item,'raise','new_feed_hourly_ramp','prepared',5000,5500)
            """),{"a":QA_ACCOUNT,"k":"nf10:2099010112:990000006","item":990000006})
            raise AssertionError("6th action in same nf10 hour was not blocked")
        except DBAPIError as exc:
            msg=str(getattr(exc,"orig",exc))
            blocked="blocked_max_actions_run_atomic" in msg
            print("HOUR_12_SIXTH_BLOCKED",blocked,msg.splitlines()[0][:220])
            assert blocked

        print("CPX_RUN_SCOPE_V2_BEHAVIOR=PASS")
    finally:
        db.rollback()
        db.close()
        print("QA_ROLLBACK=PASS")

if __name__=="__main__":
    main()
