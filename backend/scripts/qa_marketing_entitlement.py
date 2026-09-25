#!/root/BORIS/backend/venv/bin/python
import sys
sys.path.insert(0, '/root/BORIS/backend')
from app.db.session import SessionLocal
from app.models.storage import Storage
from app.services.control_plane_adapters_ext import marketing_service_entitlement

db=SessionLocal()
try:
    ids=[str(x[0]) for x in db.query(Storage.account_id).filter(Storage.key=='kpi_settings').distinct().all() if x and x[0] and not str(x[0]).startswith(('qa','user:'))]
    rows=[]
    for aid in sorted(ids):
        e=marketing_service_entitlement(db,aid)
        rows.append((aid,e['state'],e['source'],e['until']))
    print('MARKETING_ENTITLEMENT_ROWS',len(rows))
    for row in rows: print(*row,sep=' | ')
    active=[r for r in rows if r[1]=='active']
    expired=[r for r in rows if r[1]=='expired']
    unknown=[r for r in rows if r[1]=='unknown']
    print('SUMMARY active=%d expired=%d unknown=%d' % (len(active),len(expired),len(unknown)))
    assert any(r[0]=='andrey_launzh_mebel_akkaunt_penza_82435' and r[1]=='expired' for r in rows)
    assert any(r[0]=='andrey_mebel_launzh_moskva_69737' and r[1]=='expired' for r in rows)
    assert any(r[0]=='planetazayavki_65985' and r[1]=='active' for r in rows)
    assert any(r[0]=='mbroker_27859' and r[1]=='active' for r in rows)
    print('QA_MARKETING_ENTITLEMENT=PASS')
finally:
    db.close()
