#!/usr/bin/env python3
"""Backfill account-private customer identities from Avito chat metadata."""
from app.api.messenger import fetch_chats
from app.db.session import SessionLocal
from app.services.mop_customer_brain import upsert_identity


def backfill(account_id: str, limit: int = 100) -> dict:
    result=fetch_chats(account_id, unread_only=False)
    if result.get('status')!='ok':
        return {'status':'error','account_id':account_id,'message':str(result.get('message') or '')[:200]}
    chats=(result.get('chats') or [])[:max(1,min(int(limit),100))]
    db=SessionLocal()
    stable=fallback=0
    try:
        for chat in chats:
            ident=upsert_identity(db, account_id, chat)
            if ident.get('source')=='avito_user_id': stable+=1
            elif ident: fallback+=1
        db.commit()
    except Exception:
        db.rollback(); raise
    finally:
        db.close()
    return {'status':'ok','account_id':account_id,'chats':len(chats),'stable':stable,'fallback':fallback}

if __name__=='__main__':
    import argparse,json
    ap=argparse.ArgumentParser()
    ap.add_argument('account_id')
    ap.add_argument('--limit',type=int,default=100)
    args=ap.parse_args()
    print(json.dumps(backfill(args.account_id,args.limit),ensure_ascii=False))
