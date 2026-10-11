"""Read-only production preflight for Lead Radar. No writes."""
import argparse,os
from sqlalchemy import create_engine,inspect,text
from app.services.lead_radar.brain_seed import capture

REQUIRED={
 'boris_crm_contacts':{'id','owner_user_id','display_name','primary_phone','primary_email','company_name','source','source_ref','status'},
 'boris_crm_companies':{'id','owner_user_id','name','website','source','source_ref'},
 'boris_crm_pipelines':{'id','owner_user_id','is_default','is_active'},
 'boris_crm_stages':{'id','pipeline_id','position'},
 'boris_crm_deals':{'id','owner_user_id','contact_id','company_id','pipeline_id','stage_id','title','amount_kopeks','currency','source','source_ref','status','next_action_at','last_activity_at'},
 'boris_crm_activities':{'owner_user_id','deal_id','contact_id','activity_type','channel','direction','title','body','source','source_ref','actor_type','metadata_json'},
}

def run(engine,owner=None,printer=print):
    ins=inspect(engine); tables=set(ins.get_table_names()); missing_tables=sorted(set(REQUIRED)-tables)
    if missing_tables: raise RuntimeError('CRM_TABLES_MISSING_FAIL_CLOSED:'+','.join(missing_tables))
    for table,need in REQUIRED.items():
        have={x['name'] for x in ins.get_columns(table)}; missing=sorted(need-have)
        if missing: raise RuntimeError(f'CRM_SCHEMA_MISMATCH:{table}:'+','.join(missing))
    # Existing System Brain schema must accept this rule before production files are touched.
    capture(engine,apply=False,printer=lambda *_:None)
    if owner is not None:
        with engine.begin() as c:
            pid=c.execute(text('SELECT id FROM boris_crm_pipelines WHERE owner_user_id=:o AND is_active=true ORDER BY is_default DESC,id LIMIT 1'),{'o':int(owner)}).scalar()
            if not pid: raise RuntimeError('BORIS_CRM_PIPELINE_NOT_READY_FOR_OWNER:'+str(owner))
            sid=c.execute(text('SELECT id FROM boris_crm_stages WHERE pipeline_id=:p ORDER BY position,id LIMIT 1'),{'p':pid}).scalar()
            if not sid: raise RuntimeError('BORIS_CRM_STAGE_NOT_READY_FOR_OWNER:'+str(owner))
    printer('LEAD_RADAR_PREFLIGHT=PASS'+((' owner='+str(owner)) if owner is not None else ''))
    return True

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--owner',type=int); args=ap.parse_args()
    url=os.getenv('DATABASE_URL') or os.getenv('DB_URL') or os.getenv('POSTGRES_URL')
    if not url: raise SystemExit('DATABASE_URL_MISSING')
    try: run(create_engine(url,pool_pre_ping=True),args.owner)
    except RuntimeError as e: raise SystemExit(str(e))
if __name__=='__main__': main()
