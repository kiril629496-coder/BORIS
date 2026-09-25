from pathlib import Path
import sys, tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal
from sqlalchemy import text
import app.services.avito_account_throttle as throttle
from app.services.reliability import dependency_call, ProviderDeferred, _provider_tenant_identity

checks=[]
def ck(name, ok, evidence=None):
    checks.append((name,bool(ok),evidence)); print('PASS' if ok else 'FAIL',name,evidence if evidence is not None else '')

def main():
    db=SessionLocal()
    try:
        dup=db.execute(text("""select avito_user_id,array_agg(account_id order by account_id) aliases
          from accounts where coalesce(avito_user_id,'')<>'' group by avito_user_id having count(*)>1 order by avito_user_id""")).mappings().all()
    finally: db.close()
    # Every duplicate provider UID must resolve all aliases to the same key-set.
    for row in dup:
        aliases=list(row['aliases'] or [])
        keysets=[set(throttle._tenant_keys(a)) for a in aliases]
        ck('ALIAS_KEYSET_'+str(row['avito_user_id']), bool(keysets) and all(k==keysets[0] for k in keysets), aliases)
        ids=[_provider_tenant_identity('avito.token',a) for a in aliases]
        ck('ALIAS_RATE_BUDGET_'+str(row['avito_user_id']), len(set(ids))==1 and ids[0]=='uid:'+str(row['avito_user_id']), ids)

    if dup:
        aliases=list(dup[0]['aliases'] or [])
        with tempfile.TemporaryDirectory() as d:
            old=(throttle._STATE_DIR,throttle._LEDGER,throttle._LOCK)
            try:
                throttle._STATE_DIR=Path(d); throttle._LEDGER=Path(d)/'ledger.json'; throttle._LOCK=Path(d)/'ledger.lock'
                rem=throttle.record_account_throttle(aliases[0],12,'qa_alias')
                ck('ALIAS_RETRY_AFTER_PROPAGATES', all(throttle.account_throttle_remaining(a)>=11 for a in aliases), rem)
                called={'n':0}
                def forbidden(*a,**k): called['n']+=1; raise AssertionError('external provider call occurred')
                try: dependency_call('avito.token',forbidden,account_id=aliases[-1])
                except ProviderDeferred: pass
                ck('DEPENDENCY_PRECHECK_ZERO_PROVIDER_IO', called['n']==0, called['n'])
                throttle._LEDGER.write_text('{}')
                class R:
                    status_code=429
                    headers={'Retry-After':'9'}
                try: dependency_call('avito.token',lambda *a,**k:R(),account_id=aliases[0])
                except ProviderDeferred: pass
                ck('DEPENDENCY_429_PUBLISHES_ALIAS', throttle.account_throttle_remaining(aliases[-1])>=8)
            finally:
                throttle._STATE_DIR,throttle._LEDGER,throttle._LOCK=old
    failed=[x for x in checks if not x[1]]
    print('QA_AVITO_PROVIDER_ALIAS_THROTTLE=', 'PASS' if not failed else 'FAIL','checks=',len(checks),'failed=',len(failed))
    raise SystemExit(1 if failed else 0)

if __name__=='__main__': main()
