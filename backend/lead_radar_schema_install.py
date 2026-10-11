"""Idempotent Lead Radar schema install/upgrade. Does not touch existing boris_crm_* tables."""
import os
from sqlalchemy import create_engine,inspect,text
from app.services.lead_radar.repository import SQLLeadRepository

def main():
    url=os.getenv('DATABASE_URL') or os.getenv('DB_URL') or os.getenv('POSTGRES_URL')
    if not url: raise SystemExit('DATABASE_URL_MISSING')
    e=create_engine(url,pool_pre_ping=True); SQLLeadRepository(e).install(); ins=inspect(e)
    if e.dialect.name=='postgresql':
        owner_raw=os.getenv('LEAD_RADAR_OWNER_USER_ID','').strip()
        with e.begin() as c:
            c.execute(text('ALTER TABLE lead_radar_sources ADD COLUMN IF NOT EXISTS owner_user_id BIGINT'))
            null_rows=c.execute(text('SELECT COUNT(*) FROM lead_radar_sources WHERE owner_user_id IS NULL')).scalar()
            if int(null_rows or 0)>0 and not owner_raw:
                raise SystemExit('LEAD_RADAR_OWNER_REQUIRED_FOR_V1_SOURCE_MIGRATION')
            if int(null_rows or 0)>0:
                c.execute(text('UPDATE lead_radar_sources SET owner_user_id=:o WHERE owner_user_id IS NULL'),{'o':int(owner_raw)})
            c.execute(text('ALTER TABLE lead_radar_sources ALTER COLUMN owner_user_id SET NOT NULL'))
            c.execute(text('ALTER TABLE lead_radar_sources ADD COLUMN IF NOT EXISTS last_failure_at TIMESTAMPTZ'))
            c.execute(text('ALTER TABLE lead_radar_sources ADD COLUMN IF NOT EXISTS recovery_strategy VARCHAR(80)'))
            c.execute(text('ALTER TABLE lead_radar_sources ADD COLUMN IF NOT EXISTS blocked_external BOOLEAN NOT NULL DEFAULT FALSE'))
            # v1 had global UNIQUE(key)/UNIQUE(fingerprint). Remove them before owner-scoped uniqueness.
            c.execute(text('ALTER TABLE lead_radar_sources DROP CONSTRAINT IF EXISTS lead_radar_sources_key_key'))
            c.execute(text('ALTER TABLE lead_radar_leads DROP CONSTRAINT IF EXISTS lead_radar_leads_fingerprint_key'))
            c.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_lead_radar_source_owner_key ON lead_radar_sources(owner_user_id,key)'))
            c.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_lead_radar_lead_owner_fingerprint ON lead_radar_leads(owner_user_id,fingerprint)'))
            c.execute(text('CREATE INDEX IF NOT EXISTS ix_lead_radar_sources_owner ON lead_radar_sources(owner_user_id)'))
    ins=inspect(e); tables=set(ins.get_table_names())
    expected={'lead_radar_sources','lead_radar_leads','lead_radar_events','lead_radar_outreach_attempts'}
    missing=expected-tables
    if missing: raise SystemExit('LEAD_RADAR_SCHEMA_MISSING:'+','.join(sorted(missing)))
    print('LEAD_RADAR_SCHEMA=PASS')
if __name__=='__main__': main()
