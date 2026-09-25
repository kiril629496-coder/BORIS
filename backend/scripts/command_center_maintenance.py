#!/usr/bin/env python3
import json
from app.db.session import SessionLocal
from app.services.command_audit import reconcile_lifecycle, purge_texts

def main():
    db=SessionLocal()
    try:
        life=reconcile_lifecycle(db)
        retention=purge_texts(db, dry=False)
        out={"status":"PASS","lifecycle":life,"retention":retention}
        print(json.dumps(out,ensure_ascii=False,default=str))
        return 0
    finally:
        db.close()

if __name__ == '__main__':
    raise SystemExit(main())
