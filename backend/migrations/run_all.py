#!/usr/bin/env python3
"""BORIS canonical numeric SQL migration runner.

- Executes each SQL file as one PostgreSQL transaction via psql -1 (never naive `split(';')`).
- Serializes deploys with a PostgreSQL advisory lock.
- Records immutable sha256 checksums in boris_schema_migrations.
- Refuses duplicate numeric ordinals and modified already-applied migrations.
- `apply` requires an explicit numeric range and performs one DB backup first.
"""
from __future__ import annotations
import argparse, hashlib, os, re, subprocess, sys
from pathlib import Path
import psycopg2

ROOT=Path('/root/BORIS')
BACKEND=ROOT/'backend'
MIG=BACKEND/'migrations'
LOCK_KEY=0x424F5249534D  # BORISM
RX=re.compile(r'^(\d{3})_(.+)\.sql$')

# MIGRATION_IMMUTABILITY_EXCEPTION_LEDGER_V1:
# Historical debt discovered 2026-09-10: migration 035 was edited after it had
# already been applied. Never silently bless arbitrary edits. Only this exact
# old-ledger -> exact-current pair is acknowledged; any further byte change is
# still a hard verification failure. Later 041-043 supersede the DB function.
KNOWN_LEGACY_REWRITES={
    35:{
        'ledger':'7b8908aeac3e464dd530a680ce766604d7275f6ef4e818a8eda0c152107f2a73',
        'current':'9d2bf86dbf312c87b54ae372237c12769b78e6a565b11cf66679f50e2e9e5ef2',
        'reason':'historical 035 source rewrite; effective DB guard superseded by 041-043',
    },
}
# Historical SQL helpers that predate the canonical NNN_ migration contract.
# Their exact names are frozen; any NEW noncanonical SQL filename is rejected.
KNOWN_NONCANONICAL_SQL={
    '20260905_owner_outreach_daily_cap.sql',
    '20260905_owner_outreach_daily_cap_30.sql',
    'verify_001.sql',
}

# OWNER_EMAIL_MIGRATION_AUTHORITY_V1
# Versions through 057 are immutable history. Any later ordinary migration
# that tries to redefine the canonical owner-email transport is rejected by
# the runner before backup/SQL. A transport change requires an explicit
# postgres maintenance path; the application DB role cannot self-authorize it.
OWNER_EMAIL_AUTHORITY_VERSION=57
OWNER_EMAIL_PROTECTED_MARKERS=(
    'boris.owner_email_ddl_authorized',
    'client_mailboxes_owner_transport_canonical_v1',
    'prospect_outreach_bootstrap_owner_canonical_v1',
    'prospect_campaigns_owner_mailbox_canonical_v1',
    'uq_client_mailboxes_owner_outreach_single_v1',
)

def db_url()->str:
    url=os.getenv('DATABASE_URL','').strip()
    if not url:
        env=BACKEND/'.env'
        for line in env.read_text().splitlines():
            if line.startswith('DATABASE_URL='):
                url=line.split('=',1)[1].strip().strip('"').strip("'"); break
    return url.replace('+asyncpg','').replace('+psycopg2','')

def discover():
    by={}
    for p in MIG.glob('*.sql'):
        m=RX.match(p.name)
        if not m: continue
        n=int(m.group(1))
        if n in by: raise SystemExit(f'DUPLICATE_MIGRATION_ORDINAL {n:03d}: {by[n].name}, {p.name}')
        by[n]=p
    return dict(sorted(by.items()))

def sha(p:Path)->str: return hashlib.sha256(p.read_bytes()).hexdigest()

def owner_email_protected_change(version:int,p:Path)->bool:
    if int(version)<=OWNER_EMAIL_AUTHORITY_VERSION:
        return False
    body=p.read_text(errors='ignore').lower()
    return any(marker.lower() in body for marker in OWNER_EMAIL_PROTECTED_MARKERS)

def ensure_ledger(conn):
    with conn.cursor() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS boris_schema_migrations(
          version integer PRIMARY KEY, filename text NOT NULL, sha256 text NOT NULL,
          applied_at timestamptz NOT NULL DEFAULT now())''')
    conn.commit()

def ledger(conn):
    ensure_ledger(conn)
    with conn.cursor() as c:
        c.execute('SELECT version,filename,sha256,applied_at FROM boris_schema_migrations ORDER BY version')
        return {int(r[0]):r for r in c.fetchall()}


def record_applied_fresh(url: str, version: int, filename: str, digest: str) -> None:
    """Record a successfully executed migration through a fresh DB session.

    The SQL file is executed by an external psql process. The coordinator
    connection may be killed by PostgreSQL/network policy while psql is busy;
    reusing it here can leave a migration live but missing from the ledger.
    A fresh connection makes the post-DDL receipt durable, and the exact
    filename/hash verification keeps the immutable-ledger contract fail-closed.
    """
    fresh = psycopg2.connect(url)
    try:
        ensure_ledger(fresh)
        with fresh.cursor() as c:
            c.execute(
                'SELECT filename,sha256 FROM boris_schema_migrations WHERE version=%s',
                (version,),
            )
            row = c.fetchone()
            if row is None:
                c.execute(
                    'INSERT INTO boris_schema_migrations(version,filename,sha256) VALUES(%s,%s,%s)',
                    (version, filename, digest),
                )
            elif row[0] != filename or row[1] != digest:
                raise RuntimeError(f'IMMUTABLE_MIGRATION_CHANGED {version:03d}')
        fresh.commit()
    finally:
        fresh.close()


def _has_columns(conn, table: str, columns: set[str]) -> bool:
    with conn.cursor() as c:
        c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",(table,))
        have={r[0] for r in c.fetchall()}
    return columns <= have

def _regclass(conn, name: str) -> bool:
    with conn.cursor() as c:
        c.execute("SELECT to_regclass(%s) IS NOT NULL",('public.'+name,)); return bool(c.fetchone()[0])

def _function_contains(conn, signature: str, markers: tuple[str,...]) -> bool:
    with conn.cursor() as c:
        c.execute("SELECT pg_get_functiondef(%s::regprocedure)",(signature,))
        row=c.fetchone(); body=str(row[0] if row else '')
    return all(marker in body for marker in markers)

def _legacy_rewrite_allowed(version:int, ledger_sha:str, current_sha:str)->bool:
    item=KNOWN_LEGACY_REWRITES.get(int(version)) or {}
    return bool(item and item.get('ledger')==ledger_sha and item.get('current')==current_sha)

def baseline_probe(conn, version: int) -> tuple[bool,str]:
    """Prove historical schema before adopting an already-live pre-ledger migration."""
    probes={
      1: lambda: (_has_columns(conn,'users',{'email_verified','email_verified_at','status','email_normalized','trial_started_at'}) and _regclass(conn,'email_verifications') and _regclass(conn,'auth_rate_events') and _regclass(conn,'auth_blocks'), 'email verification schema'),
      2: lambda: (_has_columns(conn,'email_queue',{'status','attempts','max_attempts','next_attempt_at','provider_message_id'}) and _regclass(conn,'email_delivery_events'), 'email queue schema'),
      3: lambda: (_has_columns(conn,'email_queue',{'ref_type','ref_id','expires_at'}), 'email queue ref schema'),
      4: lambda: (_has_columns(conn,'campaign_items',{'feed_identity','avito_item_id','identity_status'}), 'campaign identity schema'),
      5: lambda: (_varchar_len_at_least(conn,'ext_a2a_messages','kind',64), 'A2A kind width'),
      6: lambda: (_has_columns(conn,'boris_sales_hot_leads',{'contact_peer_id','followup_sent_at','followup_message_id','followup_error'}) and _has_columns(conn,'boris_sales_tg_replies',{'manager_lead_id','auto_reply_text','auto_reply_message_id','auto_replied_at','auto_reply_policy'}), 'sales hot lead lifecycle'),
      7: lambda: (_has_columns(conn,'telephony_afterhours_settings',{'autoanswer_mode','no_answer_seconds','collect_phone','collect_topic','create_crm_task','voicemail_text'}), 'telephony autoanswer policy'),
      8: lambda: (_regclass(conn,'telephony_voice_agent_settings') and _regclass(conn,'telephony_voice_agent_sessions') and _has_columns(conn,'telephony_voice_agent_settings',{'disclosure_required','disclosure_text'}), 'telephony voice-agent policy'),
      40: lambda: (
          _regclass(conn,'email_open_trackers')
          and _regclass(conn,'email_open_events')
          and _regclass(conn,'email_tracking_policy')
          and _regclass(conn,'ix_email_open_events_tracker_opened')
          and _regclass(conn,'ix_email_open_trackers_first_opened')
          and _regclass(conn,'ix_prospect_replies_member_human_received'),
          'email open tracking schema already live',
      ),
      41: lambda: (
          _function_contains(conn,'boris_cpx_receipt_atomic_capacity_guard()',(
              'CPX_ATOMIC_RUN_SCOPE_V3','budget_resume')),
          'CPX run-scope V3 already live or superseded by later guard',
      ),
      42: lambda: (
          _function_contains(conn,'boris_cpx_receipt_atomic_capacity_guard()',(
              'CPX_FEED20_EXACTLY_ONE_PER_HOUR_V1',)),
          'feed20 hourly run-scope already live',
      ),
      43: lambda: (
          _function_contains(conn,'boris_cpx_receipt_atomic_capacity_guard()',(
              'CPX_AUTONOMOUS_FIRST_BID_CROSS_SOURCE_HOURLY_V1',
              'blocked_first_bid_cross_source_hourly_atomic')),
          'cross-source first-bid atomic guard already live',
      ),
    }
    fn=probes.get(version)
    if not fn: return False,'no historical baseline probe defined'
    try:
        ok,label=fn(); return bool(ok),label
    except Exception as exc:
        conn.rollback(); return False,f'probe error: {type(exc).__name__}: {exc}'

def _varchar_len_at_least(conn, table: str, column: str, minimum: int) -> bool:
    with conn.cursor() as c:
        c.execute("SELECT character_maximum_length FROM information_schema.columns WHERE table_schema='public' AND table_name=%s AND column_name=%s",(table,column))
        r=c.fetchone(); return bool(r and r[0] is not None and int(r[0])>=minimum)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('command',choices=['status','apply','apply-pending','verify','adopt'])
    ap.add_argument('--from',dest='start',type=int)
    ap.add_argument('--to',dest='end',type=int)
    a=ap.parse_args()
    files=discover()
    unexpected_sql=sorted(
        p.name for p in MIG.glob('*.sql')
        if not RX.match(p.name) and p.name not in KNOWN_NONCANONICAL_SQL
    )
    if unexpected_sql:
        print('NONCANONICAL_MIGRATION_FILES ' + ','.join(unexpected_sql), file=sys.stderr)
        if a.command!='status':
            raise SystemExit(1)
    url=db_url()
    if not url: raise SystemExit('DATABASE_URL missing')
    conn=psycopg2.connect(url,application_name='boris-migration-runner')
    conn.autocommit=False
    try:
        with conn.cursor() as c:
            c.execute('SELECT pg_advisory_lock(%s)',(LOCK_KEY,))
        applied=ledger(conn)
        if a.command in {'status','verify'}:
            bad=0
            for n,p in files.items():
                r=applied.get(n); digest=sha(p)
                if not r:
                    state='PENDING'
                    if a.command=='verify': bad+=1
                elif r[2]!=digest and _legacy_rewrite_allowed(n,r[2],digest):
                    state='APPLIED_LEGACY_REWRITE_ACK'
                elif r[2]!=digest: state='CHECKSUM_MISMATCH'; bad+=1
                elif r[1]!=p.name: state='FILENAME_MISMATCH'; bad+=1
                else: state='APPLIED'
                print(f'{n:03d} {state} {p.name}')
            ghosts=sorted(set(applied)-set(files))
            for n in ghosts:
                print(f'{n:03d} APPLIED_FILE_MISSING {applied[n][1]}'); bad+=1
            if a.command=='verify':
                if bad: raise SystemExit(1)
                print('MIGRATION_LEDGER_VERIFY=PASS')
            return
        if a.command=='adopt':
            if a.start is None or a.end is None:
                raise SystemExit('adopt requires --from N --to N')
            chosen=[(n,p) for n,p in files.items() if a.start<=n<=a.end]
            if not chosen: raise SystemExit('no migrations in selected range')
            for n,p in chosen:
                if n in applied:
                    digest=sha(p)
                    if applied[n][1]!=p.name:
                        raise SystemExit(f'IMMUTABLE_MIGRATION_CHANGED {n:03d}')
                    if applied[n][2]!=digest and not _legacy_rewrite_allowed(n,applied[n][2],digest):
                        raise SystemExit(f'IMMUTABLE_MIGRATION_CHANGED {n:03d}')
                    state='SKIP_APPLIED_LEGACY_REWRITE_ACK' if applied[n][2]!=digest else 'SKIP_APPLIED'
                    print(f'{n:03d} {state} {p.name}'); continue
                ok,detail=baseline_probe(conn,n)
                if not ok: raise SystemExit(f'BASELINE_PROBE_FAILED {n:03d} {p.name}: {detail}')
                with conn.cursor() as c:
                    c.execute('INSERT INTO boris_schema_migrations(version,filename,sha256) VALUES(%s,%s,%s)',(n,p.name,sha(p)))
                conn.commit(); print(f'{n:03d} ADOPTED {p.name} [{detail}]')
            print('MIGRATION_BASELINE_ADOPT=PASS'); return
        if a.command=='apply-pending':
            chosen=[(n,p) for n,p in files.items() if n not in applied]
        else:
            if a.start is None or a.end is None:
                raise SystemExit('apply requires --from N --to N')
            chosen=[(n,p) for n,p in files.items() if a.start<=n<=a.end]
            if not chosen: raise SystemExit('no migrations in selected range')
        # Before any schema mutation validate EVERY already-applied source file,
        # not only the chosen range. A corrupted historical migration blocks a
        # new deploy even when that historical version is outside the range.
        for n,p in files.items():
            r=applied.get(n)
            if r:
                digest=sha(p)
                if r[1]!=p.name or (r[2]!=digest and not _legacy_rewrite_allowed(n,r[2],digest)):
                    raise SystemExit(f'IMMUTABLE_MIGRATION_CHANGED {n:03d}')
        if not chosen:
            print('MIGRATION_APPLY_PENDING=NONE' if a.command=='apply-pending' else 'MIGRATION_APPLY=NOTHING')
            return
        for n,p in chosen:
            if n not in applied and owner_email_protected_change(n,p):
                raise SystemExit(
                    f'OWNER_EMAIL_MIGRATION_REQUIRES_POSTGRES_MAINTENANCE {n:03d} {p.name}'
                )
        print('MIGRATION_BACKUP_START',flush=True)
        subprocess.run(['bash',str(BACKEND/'scripts'/'backup_db_safe.sh')],check=True,cwd=str(BACKEND))
        print('MIGRATION_BACKUP=PASS',flush=True)
        for n,p in chosen:
            if n in applied:
                print(f'{n:03d} SKIP_APPLIED {p.name}',flush=True); continue
            print(f'{n:03d} APPLY {p.name}',flush=True)
            # Execute the file itself. psql owns SQL parsing, including DO $$...$$,
            # comments and semicolons inside procedural bodies.
            subprocess.run(['psql',url,'-X','-1','-v','ON_ERROR_STOP=1','-f',str(p)],check=True,cwd=str(BACKEND),stdout=subprocess.DEVNULL)
            digest=sha(p)
            # MIGRATION_FRESH_LEDGER_RECEIPT_V1: psql may outlive the coordinator
            # DB session. Never trust that pre-psql SSL connection for the
            # authoritative applied receipt.
            record_applied_fresh(url,n,p.name,digest)
            print(f'{n:03d} APPLIED',flush=True)
        print('MIGRATION_APPLY=PASS')
    finally:
        try:
            with conn.cursor() as c: c.execute('SELECT pg_advisory_unlock(%s)',(LOCK_KEY,))
            conn.commit()
        except Exception: pass
        conn.close()
if __name__=='__main__': main()
