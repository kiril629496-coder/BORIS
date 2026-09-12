"""Account-scoped client mailboxes for BORIS outreach.
Secrets are encrypted with existing Fernet. SMTP sends and IMAP reply polling only.
No second queue/scheduler: invoked by canonical email_queue runner.
"""
from __future__ import annotations
import email, html as html_lib, imaplib, os, re, smtplib
from datetime import datetime
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr
from sqlalchemy import text
from app.crypto_utils import encrypt_secret, decrypt_secret
from app.db.session import SessionLocal
from app.services.exception_observability import observe_suppressed

SCHEMA='SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'

def ensure_schema(db):
    ready=db.execute(text("""SELECT
      to_regclass('public.client_mailboxes') IS NOT NULL
      AND to_regclass('public.prospect_inbound_replies') IS NOT NULL
      AND to_regclass('public.prospect_reply_alerts') IS NOT NULL
      AND to_regclass('public.mailbox_sent_copy_queue') IS NOT NULL
      AND to_regclass('public.owner_mailbox_transport_drift_events') IS NOT NULL
      AND EXISTS(
        SELECT 1 FROM pg_constraint
        WHERE conrelid='public.client_mailboxes'::regclass
          AND conname='client_mailboxes_owner_transport_canonical_v1'
      )
      AND to_regclass('public.uq_client_mailboxes_owner_outreach_single_v1') IS NOT NULL
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='mailbox_sent_copy_queue' AND column_name='next_attempt_at')
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='prospect_inbound_replies' AND column_name='message_kind')
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='prospect_reply_alerts' AND column_name='attempted_at')
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='prospect_reply_alerts' AND column_name='email_queue_id')
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='client_mailboxes' AND column_name='smtp_last_checked_at')
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='client_mailboxes' AND column_name='smtp_last_error')
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='client_mailboxes' AND column_name='imap_last_checked_at')
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='client_mailboxes' AND column_name='imap_last_error')
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='manager_leads' AND column_name='lead_source')
    """)).scalar()
    if ready:
        return
    for stmt in [x.strip() for x in SCHEMA.split(';') if x.strip()]:
        db.execute(text(stmt))
    # Owner-outreach canonical routing is deliberately NOT authored by the
    # application role. The root-owned systemd preflight
    # scripts/owner_email_db_guard.sh installs the postgres-owned DB guards.
    # This keeps ordinary API/QA/deploy code from redefining transport policy.
    db.commit()

def _fernet_ready(): return bool(os.environ.get('FERNET_KEY'))


def _mailbox_password(row:dict)->str:
    """Reuse BORIS' working global SMTP secret for owner outreach without copying it into DB."""
    if str(row.get('account_id') or '')=='__owner_outreach__':
        env_user=(os.getenv('SMTP_USER') or '').strip().lower()
        env_host=(os.getenv('SMTP_HOST') or '').strip().lower()
        row_user=str(row.get('username') or '').strip().lower()
        row_host=str(row.get('smtp_host') or '').strip().lower()
        if env_user and env_host and row_user==env_user and row_host==env_host:
            pw=os.getenv('SMTP_PASS') or os.getenv('SMTP_PASSWORD') or ''
            if pw:
                return pw
    return decrypt_secret(row['secret_encrypted'])


def _mailbox_reply_to(row:dict, explicit=None):
    if explicit:
        return explicit
    if str(row.get('account_id') or '')=='__owner_outreach__':
        configured=(os.getenv('PROSPECT_OUTREACH_REPLY_TO') or '').strip()
        if configured:
            return configured
    return row.get('email_address')


def save_mailbox(owner_user_id:int, account_id:str, *, email_address:str, display_name:str|None,
                 smtp_host:str,smtp_port:int,imap_host:str,imap_port:int,username:str,password:str,
                 smtp_ssl:bool=True,imap_ssl:bool=True)->int:
    if account_id == '__owner_outreach__':
        raise ValueError('owner_outreach_transport_is_system_managed')
    if not _fernet_ready(): raise RuntimeError('FERNET_KEY is required for client mailbox secrets')
    db=SessionLocal(); ensure_schema(db)
    try:
        if account_id == '__owner_outreach__':
            owned=db.execute(text("SELECT 1 FROM users WHERE id=:o AND role='owner'"),{'o':owner_user_id}).first()
        else:
            owned=db.execute(text('SELECT 1 FROM accounts WHERE account_id=:a AND owner_user_id=:o'),{'a':account_id,'o':owner_user_id}).first()
        if not owned: raise ValueError('account not found')
        enc=encrypt_secret(password)
        mid=db.execute(text("""INSERT INTO client_mailboxes(owner_user_id,account_id,email_address,display_name,smtp_host,smtp_port,smtp_ssl,imap_host,imap_port,imap_ssl,username,secret_encrypted,status)
          VALUES(:o,:a,:e,:n,:sh,:sp,:ss,:ih,:ip,:is,:u,:p,'active')
          ON CONFLICT(owner_user_id,account_id,email_address) DO UPDATE SET
            display_name=EXCLUDED.display_name,
            smtp_host=EXCLUDED.smtp_host,smtp_port=EXCLUDED.smtp_port,smtp_ssl=EXCLUDED.smtp_ssl,
            imap_host=EXCLUDED.imap_host,imap_port=EXCLUDED.imap_port,imap_ssl=EXCLUDED.imap_ssl,
            username=EXCLUDED.username,secret_encrypted=EXCLUDED.secret_encrypted,status='active',
            smtp_last_checked_at=NULL,smtp_last_error=NULL,
            imap_last_checked_at=NULL,imap_last_error=NULL,
            last_checked_at=NULL,last_error=NULL,updated_at=NOW()
          RETURNING id"""),{'o':owner_user_id,'a':account_id,'e':email_address.strip().lower(),'n':display_name,'sh':smtp_host,'sp':smtp_port,'ss':smtp_ssl,'ih':imap_host,'ip':imap_port,'is':imap_ssl,'u':username,'p':enc}).scalar_one()
        db.commit(); return int(mid)
    finally: db.close()

def _row(mailbox_id:int):
    db=SessionLocal(); ensure_schema(db)
    try:
        r=db.execute(text('SELECT * FROM client_mailboxes WHERE id=:i AND status=\'active\''),{'i':mailbox_id}).mappings().first(); return dict(r) if r else None
    finally: db.close()

def _set_mailbox_channel_health(mailbox_id:int, channel:str, *, ok:bool, error:str|None=None)->None:
    """Persist SMTP and IMAP health independently so one channel cannot hide the other."""
    if channel not in ('smtp','imap'):
        raise ValueError('unsupported mailbox health channel')
    err=None if ok else str(error or 'unknown_error')[:255]
    db=SessionLocal(); ensure_schema(db)
    try:
        if channel=='smtp':
            db.execute(text("""UPDATE client_mailboxes
              SET smtp_last_checked_at=NOW(),smtp_last_error=:e,last_checked_at=NOW(),
                  last_error=NULLIF(concat_ws('; ',
                    CASE WHEN :e IS NOT NULL THEN 'SMTP: '||:e END,
                    CASE WHEN imap_last_error IS NOT NULL THEN 'IMAP: '||imap_last_error END
                  ),''),updated_at=NOW()
              WHERE id=:i"""),{'i':mailbox_id,'e':err})
        else:
            db.execute(text("""UPDATE client_mailboxes
              SET imap_last_checked_at=NOW(),imap_last_error=:e,last_checked_at=NOW(),
                  last_error=NULLIF(concat_ws('; ',
                    CASE WHEN smtp_last_error IS NOT NULL THEN 'SMTP: '||smtp_last_error END,
                    CASE WHEN :e IS NOT NULL THEN 'IMAP: '||:e END
                  ),''),updated_at=NOW()
              WHERE id=:i"""),{'i':mailbox_id,'e':err})
        db.commit()
    finally:
        db.close()

def _mailbox_exception_reason(exc:Exception)->str:
    name=type(exc).__name__
    detail=' '.join(str(exc or '').split())[:160]
    return f"{name}:{detail}" if detail and detail!=name else name


def mailbox_error_kind(reason:str|None)->str|None:
    """Stable diagnosis used by health/backoff without hiding provider detail."""
    low=str(reason or '').lower()
    if not low:
        return None
    if (
        ('application password is required' in low or 'application password required' in low)
        or 'parol prilozheniya' in low
        or 'парол' in low and 'прилож' in low
    ):
        return 'application_password_required'
    if 'authenticationfailed' in low or 'smtpauthenticationerror' in low:
        return 'authentication_failed'
    return None


def test_mailbox(mailbox_id:int)->dict:
    r=_row(mailbox_id)
    if not r: return {'smtp':False,'imap':False,'reason':'mailbox_not_found'}
    pw=_mailbox_password(r); out={'smtp':False,'imap':False}
    try:
        cls=smtplib.SMTP_SSL if r['smtp_ssl'] else smtplib.SMTP
        with cls(r['smtp_host'],r['smtp_port'],timeout=20) as s:
            if not r['smtp_ssl']: s.starttls()
            s.login(r['username'],pw); s.noop(); out['smtp']=True
    except Exception as e: out['smtp_error']=_smtp_failure_reason(e,'login')
    try:
        cls=imaplib.IMAP4_SSL if r['imap_ssl'] else imaplib.IMAP4
        m=cls(r['imap_host'],r['imap_port']); m.login(r['username'],pw); m.select('INBOX',readonly=True); m.logout(); out['imap']=True
    except Exception as e: out['imap_error']=_mailbox_exception_reason(e)
    _set_mailbox_channel_health(
        mailbox_id,'smtp',ok=bool(out.get('smtp')),error=out.get('smtp_error')
    )
    _set_mailbox_channel_health(
        mailbox_id,'imap',ok=bool(out.get('imap')),error=out.get('imap_error')
    )
    return out

def probe_smtp_health(mailbox_id:int)->dict:
    """SMTP auth/noop probe only. It never sends a message."""
    r=_row(mailbox_id)
    if not r:
        return {'ok':False,'reason':'mailbox_not_found'}
    try:
        pw=_mailbox_password(r)
        cls=smtplib.SMTP_SSL if r['smtp_ssl'] else smtplib.SMTP
        with cls(r['smtp_host'],r['smtp_port'],timeout=20) as smtp:
            if not r['smtp_ssl']:
                smtp.starttls()
            smtp.login(r['username'],pw)
            smtp.noop()
        _set_mailbox_channel_health(mailbox_id,'smtp',ok=True)
        return {'ok':True}
    except Exception as exc:
        reason=_smtp_failure_reason(exc,'login')
        _set_mailbox_channel_health(mailbox_id,'smtp',ok=False,error=reason)
        return {'ok':False,'reason':reason}

def _canonical_owner_transport()->dict|None:
    """Canonical owner-outreach transport backed by BORIS' working global mailbox secret."""
    host=(os.getenv('SMTP_HOST') or '').strip()
    user=(os.getenv('SMTP_USER') or '').strip()
    password=os.getenv('SMTP_PASS') or os.getenv('SMTP_PASSWORD') or ''
    address=(os.getenv('EMAIL_FROM_ADDRESS') or user).strip()
    imap_host=(os.getenv('PROSPECT_OWNER_IMAP_HOST') or '').strip()
    if not (host and user and password and address and imap_host):
        return None
    try:
        smtp_port=int(os.getenv('SMTP_PORT') or 465)
        imap_port=int(os.getenv('PROSPECT_OWNER_IMAP_PORT') or 993)
    except Exception:
        return None
    return {
        'email_address':address.lower(),
        'smtp_host':host.lower(),'smtp_port':smtp_port,'smtp_ssl':True,
        'imap_host':imap_host.lower(),'imap_port':imap_port,'imap_ssl':True,
        'username':user,
        'password':password,
    }


def _probe_transport_config(cfg:dict)->dict:
    """Verify replacement SMTP+IMAP before any production mailbox metadata is changed."""
    out={'smtp':False,'imap':False,'imap_cursor':0}
    try:
        with smtplib.SMTP_SSL(cfg['smtp_host'],int(cfg['smtp_port']),timeout=20) as smtp:
            smtp.login(cfg['username'],cfg['password']); smtp.noop()
        out['smtp']=True
    except Exception as exc:
        out['smtp_error']=_smtp_failure_reason(exc,'login')
    try:
        m=imaplib.IMAP4_SSL(cfg['imap_host'],int(cfg['imap_port']))
        m.login(cfg['username'],cfg['password'])
        st,data=m.status('INBOX','(UIDNEXT)')
        if st=='OK' and data:
            raw=(data[0].decode('utf-8','ignore') if isinstance(data[0],bytes) else str(data[0]))
            match=re.search(r'UIDNEXT\s+(\d+)',raw,re.I)
            if match:
                out['imap_cursor']=max(0,int(match.group(1))-1)
        m.logout(); out['imap']=True
    except Exception as exc:
        out['imap_error']=_mailbox_exception_reason(exc)
    return out


def ensure_owner_outreach_transport()->dict:
    """Self-heal a broken owner mailbox onto the verified canonical BORIS transport.

    Healthy alternate mailboxes are never replaced. A switch occurs only when
    the current owner-outreach transport is unhealthy AND the canonical SMTP
    and IMAP both pass a live authentication probe first.
    """
    cfg=_canonical_owner_transport()
    if not cfg:
        return {'checked':0,'switched':0,'status':'canonical_transport_not_configured'}
    db=SessionLocal(); ensure_schema(db)
    try:
        rows=[dict(x) for x in db.execute(text("""SELECT id,email_address,smtp_host,smtp_port,smtp_ssl,
          imap_host,imap_port,imap_ssl,username,smtp_last_error,imap_last_error
          FROM client_mailboxes
          WHERE account_id='__owner_outreach__' AND status='active'
          ORDER BY id""")).mappings().all()]
    finally:
        db.close()
    switched=0; results={}
    probe=None
    for r in rows:
        mailbox_id=int(r['id'])
        matches=(
            str(r.get('email_address') or '').lower()==cfg['email_address']
            and str(r.get('smtp_host') or '').lower()==cfg['smtp_host']
            and int(r.get('smtp_port') or 0)==int(cfg['smtp_port'])
            and bool(r.get('smtp_ssl'))
            and str(r.get('imap_host') or '').lower()==cfg['imap_host']
            and int(r.get('imap_port') or 0)==int(cfg['imap_port'])
            and bool(r.get('imap_ssl'))
            and str(r.get('username') or '').lower()==str(cfg['username']).lower()
        )
        if matches:
            results[mailbox_id]={'status':'canonical'}
            continue
        unhealthy=bool(r.get('smtp_last_error') or r.get('imap_last_error'))
        allow_failover=str(os.getenv('PROSPECT_OWNER_ALLOW_TRANSPORT_FAILOVER') or '').strip().lower() in {'1','true','yes','on'}
        if not allow_failover:
            if unhealthy:
                results[mailbox_id]={
                    'status':'alternate_unhealthy_failover_disabled',
                    'smtp_error':r.get('smtp_last_error'),'imap_error':r.get('imap_last_error'),
                }
            else:
                results[mailbox_id]={'status':'alternate_healthy_not_touched'}
            continue
        # Explicit owner opt-in makes the canonical BORIS transport authoritative.
        # A concurrent raw/API rewrite back to an old mailbox must not silently
        # win merely because it cleared health errors. Probe canonical first,
        # then restore it before bootstrap/feeder work in the same minute cycle.
        if probe is None:
            probe=_probe_transport_config(cfg)
        if not (probe.get('smtp') and probe.get('imap')):
            results[mailbox_id]={
                'status':'canonical_probe_failed',
                'smtp':bool(probe.get('smtp')),'imap':bool(probe.get('imap')),
                'smtp_error':probe.get('smtp_error'),'imap_error':probe.get('imap_error'),
            }
            continue
        wdb=SessionLocal(); ensure_schema(wdb)
        try:
            wdb.execute(text("""UPDATE client_mailboxes SET
              email_address=:e,smtp_host=:sh,smtp_port=:sp,smtp_ssl=TRUE,
              imap_host=:ih,imap_port=:ip,imap_ssl=TRUE,username=:u,
              last_imap_uid=:cursor,last_checked_at=NOW(),last_error=NULL,
              smtp_last_checked_at=NOW(),smtp_last_error=NULL,
              imap_last_checked_at=NOW(),imap_last_error=NULL,updated_at=NOW()
              WHERE id=:i AND status='active'"""),{
                'e':cfg['email_address'],'sh':cfg['smtp_host'],'sp':cfg['smtp_port'],
                'ih':cfg['imap_host'],'ip':cfg['imap_port'],'u':cfg['username'],
                'cursor':int(probe.get('imap_cursor') or 0),'i':mailbox_id,
            })
            wdb.commit(); switched+=1
            results[mailbox_id]={'status':'switched_to_canonical','imap_cursor':int(probe.get('imap_cursor') or 0)}
        except Exception:
            wdb.rollback(); raise
        finally:
            wdb.close()
    return {'checked':len(rows),'switched':switched,'results':results}


def reconcile_owner_campaign_mailbox_health()->dict:
    """Pause owner-mailbox campaigns on transport failure and resume only these pauses after recovery."""
    db=SessionLocal(); ensure_schema(db)
    try:
        rows=[dict(x) for x in db.execute(text("""SELECT
          c.id,c.status,c.desired_status,c.status_reason,c.mailbox_id,
          m.status AS mailbox_status,m.smtp_last_error,m.imap_last_error
          FROM prospect_campaigns c
          JOIN client_mailboxes m ON m.id=c.mailbox_id
          WHERE m.account_id='__owner_outreach__'
            AND c.desired_status='active'
            AND (c.status='active' OR c.status_reason='mailbox_auth_blocked')
          ORDER BY c.id""")).mappings().all()]
        paused=[]; resumed=[]; blocked={}
        for r in rows:
            unhealthy=(
                str(r.get('mailbox_status') or '')!='active'
                or bool(r.get('smtp_last_error'))
                or bool(r.get('imap_last_error'))
            )
            cid=int(r['id'])
            if unhealthy:
                blocked[cid]={
                    'smtp':str(r.get('smtp_last_error') or '')[:160] or None,
                    'imap':str(r.get('imap_last_error') or '')[:160] or None,
                }
                if str(r.get('status') or '')=='active':
                    changed=db.execute(text("""UPDATE prospect_campaigns
                      SET status='paused',paused_at=COALESCE(paused_at,NOW()),
                          status_reason='mailbox_auth_blocked',status_source='mailbox_guard',updated_at=NOW()
                      WHERE id=:c AND desired_status='active' AND status='active'"""),{'c':cid}).rowcount or 0
                    if changed: paused.append(cid)
            elif str(r.get('status') or '')=='paused' and str(r.get('status_reason') or '')=='mailbox_auth_blocked':
                changed=db.execute(text("""UPDATE prospect_campaigns
                  SET status='active',paused_at=NULL,status_reason=NULL,
                      status_source='mailbox_guard_recovered',updated_at=NOW()
                  WHERE id=:c AND desired_status='active' AND status='paused'
                    AND status_reason='mailbox_auth_blocked'"""),{'c':cid}).rowcount or 0
                if changed: resumed.append(cid)
        db.commit()
        return {'checked':len(rows),'paused':paused,'resumed':resumed,'blocked':blocked}
    except Exception:
        db.rollback(); raise
    finally:
        db.close()


def recheck_unhealthy_smtp(limit:int=20,cooldown_minutes:int=15)->dict:
    """Retry only unhealthy SMTP auth/connectivity, rate-limited and send-free."""
    cap=max(1,min(int(limit),100))
    cooldown=max(5,min(int(cooldown_minutes),120))
    db=SessionLocal(); ensure_schema(db)
    try:
        ids=[int(x[0]) for x in db.execute(text("""SELECT id
          FROM client_mailboxes
          WHERE status='active' AND smtp_last_error IS NOT NULL
            AND (
              smtp_last_checked_at IS NULL
              OR smtp_last_checked_at <= NOW()-make_interval(mins => CASE
                   WHEN lower(COALESCE(smtp_last_error,'')) LIKE '%application password%'
                     OR lower(COALESCE(smtp_last_error,'')) LIKE '%parol prilozheniya%'
                   THEN 60 ELSE :m END)
            )
          ORDER BY COALESCE(smtp_last_checked_at,created_at),id
          LIMIT :lim"""),{'m':cooldown,'lim':cap}).all()]
    finally:
        db.close()
    checked=restored=failed=0
    results={}
    for mailbox_id in ids:
        result=probe_smtp_health(mailbox_id)
        checked+=1
        if result.get('ok'):
            restored+=1
        else:
            failed+=1
        results[mailbox_id]=result
    return {'checked':checked,'restored':restored,'failed':failed,'results':results}

def _sent_folder(m):
    typ,boxes=m.list(); sent_box=None
    for raw in (boxes or []):
        line=(raw.decode('utf-8','ignore') if isinstance(raw,bytes) else str(raw))
        if '\\Sent' in line:
            sent_box=line.rsplit(' ',1)[-1].strip().strip('"'); break
    if sent_box: return sent_box
    for candidate in ('Sent','INBOX.Sent','Отправленные'):
        try:
            st,_=m.status(candidate,'(MESSAGES)')
            if st=='OK': return candidate
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=476)
    return None

def _append_sent_copy(r, pw, raw_bytes:bytes)->tuple[bool,str]:
    try:
        cls=imaplib.IMAP4_SSL if r['imap_ssl'] else imaplib.IMAP4
        m=cls(r['imap_host'],r['imap_port']); m.login(r['username'],pw)
        box=_sent_folder(m)
        if not box:
            m.logout(); return False,'sent_folder_not_found'
        import time as _time
        st,_=m.append(box,r'\Seen',imaplib.Time2Internaldate(_time.time()),raw_bytes)
        m.logout()
        return (st=='OK', 'ok' if st=='OK' else 'sent_copy_append_failed')
    except Exception as e: return False,type(e).__name__

def _queue_sent_copy(mailbox_id:int,message_id:str,raw_bytes:bytes,error:str):
    db=SessionLocal(); ensure_schema(db)
    try:
        db.execute(text("""INSERT INTO mailbox_sent_copy_queue(mailbox_id,message_id,mime_bytes,status,last_error,next_attempt_at)
          VALUES(:m,:mid,:raw,'pending',:e,now())
          ON CONFLICT(mailbox_id,message_id) DO UPDATE
          SET mime_bytes=EXCLUDED.mime_bytes,status='pending',last_error=EXCLUDED.last_error,next_attempt_at=now(),updated_at=NOW()
          WHERE mailbox_sent_copy_queue.status<>'saved'"""),{'m':mailbox_id,'mid':message_id,'raw':raw_bytes,'e':error[:255]})
        db.commit()
    finally: db.close()

def flush_sent_copies(limit:int=20)->dict:
    """Crash-safe, multi-worker IMAP Sent-copy dispatcher with bounded retry."""
    cap=max(1,min(int(limit),100)); done=failed=dead=recovered=0
    # Recover only stale execution leases; a live worker owns `sending` briefly.
    db=SessionLocal(); ensure_schema(db)
    try:
        recovered=db.execute(text("""UPDATE mailbox_sent_copy_queue
          SET status='pending',next_attempt_at=now(),last_error=concat_ws('; ',NULLIF(last_error,''),'recovered stale sent-copy lease'),updated_at=now()
          WHERE status='sending' AND updated_at<now()-interval '5 minutes'""")).rowcount or 0
        db.commit()
    finally: db.close()
    for _ in range(cap):
        db=SessionLocal(); row=None
        try:
            row=db.execute(text("""SELECT id,mailbox_id,message_id,mime_bytes,attempts FROM mailbox_sent_copy_queue
              WHERE status='pending' AND (next_attempt_at IS NULL OR next_attempt_at<=now())
              ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED""")).mappings().first()
            if row:
                row=dict(row)
                db.execute(text("UPDATE mailbox_sent_copy_queue SET status='sending',updated_at=now() WHERE id=:i"),{'i':row['id']})
                db.commit()
            else: db.rollback()
        finally: db.close()
        if not row: break
        r=_row(int(row['mailbox_id']))
        if not r:
            db=SessionLocal()
            try:
                db.execute(text("UPDATE mailbox_sent_copy_queue SET status='dead',attempts=attempts+1,last_error='mailbox_not_found',next_attempt_at=NULL,updated_at=now() WHERE id=:i"),{'i':row['id']}); db.commit()
            finally: db.close()
            dead+=1; continue
        ok,reason=_append_sent_copy(r,_mailbox_password(r),bytes(row['mime_bytes']))
        attempt=int(row.get('attempts') or 0)+1
        terminal=attempt>=5
        db=SessionLocal()
        try:
            if ok:
                db.execute(text("UPDATE mailbox_sent_copy_queue SET status='saved',attempts=:a,last_error=NULL,next_attempt_at=NULL,updated_at=NOW() WHERE id=:i"),{'i':row['id'],'a':attempt}); done+=1
            elif terminal:
                db.execute(text("UPDATE mailbox_sent_copy_queue SET status='dead',attempts=:a,last_error=:e,next_attempt_at=NULL,updated_at=NOW() WHERE id=:i"),{'i':row['id'],'a':attempt,'e':str(reason)[:255]}); dead+=1
            else:
                wait=min(3600,60*(2**max(0,attempt-1)))
                db.execute(text("UPDATE mailbox_sent_copy_queue SET status='pending',attempts=:a,last_error=:e,next_attempt_at=now()+make_interval(secs=>:w),updated_at=NOW() WHERE id=:i"),{'i':row['id'],'a':attempt,'e':str(reason)[:255],'w':wait}); failed+=1
            db.commit()
        finally: db.close()
    return {'saved':done,'pending_failed':failed,'dead':dead,'recovered_sending':int(recovered)}

def sent_copy_saved(mailbox_id:int,message_id:str)->bool:
    """Return whether the Sent copy is durable.

    Successful direct IMAP append intentionally creates no queue row. A queue row
    exists only when the direct append failed and recovery is required. Therefore
    absence of a recovery row for a successfully sent message is the normal
    "saved directly" state, not "pending".
    """
    db=SessionLocal(); ensure_schema(db)
    try:
        row=db.execute(text("SELECT status FROM mailbox_sent_copy_queue WHERE mailbox_id=:m AND message_id=:mid"),{'m':mailbox_id,'mid':message_id}).scalar()
        return row is None or row == 'saved'
    finally: db.close()

def _drafts_folder(m):
    typ,boxes=m.list(); draft_box=None
    for raw in (boxes or []):
        line=(raw.decode('utf-8','ignore') if isinstance(raw,bytes) else str(raw))
        if '\\Drafts' in line:
            draft_box=line.rsplit(' ',1)[-1].strip().strip('"'); break
    if draft_box: return draft_box
    for candidate in ('Drafts','INBOX.Drafts','Черновики'):
        try:
            st,_=m.status(candidate,'(MESSAGES)')
            if st=='OK': return candidate
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=575)
    return None

def save_draft(mailbox_id:int,to,subject,body,html=None,reply_to=None,headers=None,attachments=None):
    """Create a real draft in the connected mailbox via IMAP. Nothing is sent."""
    r=_row(mailbox_id)
    if not r: return False,'mailbox_not_found',''
    from app.services.email_service import build_message
    msg=build_message(to,subject,body,html=html,from_address=r['email_address'],from_name=r.get('display_name'),reply_to=_mailbox_reply_to(r,reply_to),headers=headers,attachments=attachments)
    mid=msg.get('Message-ID',''); raw=msg.as_bytes()
    try:
        pw=_mailbox_password(r)
        cls=imaplib.IMAP4_SSL if r['imap_ssl'] else imaplib.IMAP4
        m=cls(r['imap_host'],r['imap_port']); m.login(r['username'],pw)
        box=_drafts_folder(m)
        if not box:
            m.logout(); return False,'drafts_folder_not_found',mid
        import time as _time
        st,_=m.append(box,'\\Draft',imaplib.Time2Internaldate(_time.time()),raw)
        m.logout()
        return (st=='OK', 'ok' if st=='OK' else 'draft_append_failed', mid)
    except Exception as e:
        return False,type(e).__name__,mid

def _smtp_failure_reason(exc:Exception, phase:str)->str:
    """Classify SMTP failures without risking duplicate delivery.

    SMTPRecipientsRefused is raised at RCPT TO before DATA is accepted, so
    delivery is definitely false and the queue can apply its permanent-recipient
    policy. Unknown transport errors during DATA remain delivery_unknown.
    """
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return 'SMTPRecipientsRefused'
    code=getattr(exc,'smtp_code',None)
    # SMTP reply text is provider-controlled and unstable ("try later",
    # localized wording, etc.). When a numeric SMTP code exists, keep the
    # durable classification key strictly Class:code so retries/analytics do
    # not split one failure class into many strings.
    if code is not None:
        return f'{type(exc).__name__}:{code}'
    reason=type(exc).__name__
    detail=getattr(exc,'smtp_error',None)
    if isinstance(detail,bytes):
        detail=detail.decode('utf-8','replace')
    detail=' '.join(str(detail or '').split())[:120]
    if detail:
        reason=f'{reason}:{detail}'
    if phase=='sending':
        return 'delivery_unknown:'+reason
    return reason


def send_outbound(mailbox_id:int,to,subject,body,html=None,reply_to=None,headers=None,attachments=None,from_name=None):
    r=_row(mailbox_id)
    if not r: return False,'mailbox_not_found',''
    from app.services.email_service import build_message, _smtp_transport_send, SMTPTransportFailure
    msg=build_message(to,subject,body,html=html,from_address=r['email_address'],from_name=(from_name if from_name is not None else r.get('display_name')),reply_to=_mailbox_reply_to(r,reply_to),headers=headers,attachments=attachments)
    mid=msg.get('Message-ID',''); raw=msg.as_bytes()
    pw=_mailbox_password(r)
    try:
        _smtp_transport_send(
            msg,
            tenant_scope=f"mailbox:{int(mailbox_id)}",
            host=r['smtp_host'],
            port=int(r['smtp_port']),
            username=r['username'],
            password=pw,
            use_ssl=bool(r['smtp_ssl']),
            starttls=not bool(r['smtp_ssl']),
            timeout=20,
        )
    except SMTPTransportFailure as wrapped:
        failure=_smtp_failure_reason(wrapped.original,wrapped.phase)
        try:
            _set_mailbox_channel_health(mailbox_id,'smtp',ok=False,error=failure)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=651)
        return False,failure,mid
    except Exception as e:
        failure=_smtp_failure_reason(e,'connect')
        try:
            _set_mailbox_channel_health(mailbox_id,'smtp',ok=False,error=failure)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=651)
        return False,failure,mid
    # Health bookkeeping must never turn an already accepted SMTP delivery into
    # an ambiguous queue result. Persist it best-effort after DATA acceptance.
    try:
        _set_mailbox_channel_health(mailbox_id,'smtp',ok=True)
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=658)
    ok,reason=_append_sent_copy(r,pw,raw)
    if not ok:
        _queue_sent_copy(mailbox_id,mid,raw,reason)
        return True,'ok_sent_copy_pending',mid
    return True,'ok',mid


def _decode(v):
    try: return str(make_header(decode_header(v or '')))
    except Exception: return v or ''

def _strip_quoted_history(value:str)->str:
    """Keep the new reply and drop common quoted-thread history."""
    text_value=str(value or '').replace('\r\n','\n').replace('\r','\n')
    out=[]
    for line in text_value.split('\n'):
        stripped=line.strip()
        low=stripped.lower().replace('ё','е')
        if stripped.startswith('>'):
            continue
        if out and (
            re.match(r'^on .+ wrote:$',low)
            or ' написал' in low and low.endswith(':')
            or ' wrote:' in low
            or re.match(r'^-{2,}\s*(original message|исходное сообщение)',low)
            or re.match(r'^(from|от):\s',low)
        ):
            break
        out.append(line)
    return '\n'.join(out).strip()


def _body_preview(msg):
    parts=[]; html_parts=[]
    if msg.is_multipart():
        for p in msg.walk():
            ctype=p.get_content_type()
            if 'attachment' in str(p.get('Content-Disposition','')).lower():
                continue
            if ctype not in ('text/plain','text/html'):
                continue
            try:
                decoded=p.get_payload(decode=True).decode(p.get_content_charset() or 'utf-8','replace')
            except Exception:
                continue
            if ctype=='text/plain':
                cleaned=_strip_quoted_history(decoded)
                if cleaned:
                    parts.append(cleaned)
            else:
                html_parts.append(decoded)
    else:
        try:
            decoded=msg.get_payload(decode=True).decode(msg.get_content_charset() or 'utf-8','replace')
            if msg.get_content_type()=='text/html':
                html_parts.append(decoded)
            else:
                cleaned=_strip_quoted_history(decoded)
                if cleaned:
                    parts.append(cleaned)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=720)
    # Some mail clients send reply text as HTML only. Falling back to stripped
    # HTML keeps opt-outs and sales intent visible instead of creating an empty
    # generic lead. Plain text remains authoritative when present.
    if not parts and html_parts:
        raw=' '.join(html_parts)
        raw=re.sub(r'(?is)<(script|style)\b[^>]*>.*?</\1>',' ',raw)
        raw=re.sub(r'(?is)<blockquote\b[^>]*>.*?</blockquote>',' ',raw)
        raw=re.sub(r'(?is)<div\b[^>]*class=["\'][^"\']*gmail_quote[^"\']*["\'][^>]*>.*',' ',raw)
        raw=re.sub(r'(?s)<[^>]+>','\n',raw)
        cleaned=_strip_quoted_history(html_lib.unescape(raw))
        if cleaned:
            parts.append(cleaned)
    v=re.sub(r'\s+',' ',' '.join(parts)).strip(); return v[:2000]

def _delivery_recipient(msg, preview:str='')->str|None:
    """Extract the original recipient from a DSN/bounce message."""
    candidates=[]
    try:
        for part in msg.walk() if msg.is_multipart() else [msg]:
            if str(part.get_content_type() or '').lower()=='message/delivery-status':
                payload=part.get_payload()
                blocks=payload if isinstance(payload,list) else [part]
                for block in blocks:
                    for h in ('Final-Recipient','Original-Recipient'):
                        raw=str(block.get(h) or '').strip()
                        if raw:
                            value=raw.split(';',1)[-1].strip()
                            addr=parseaddr(value)[1] or value
                            candidates.append(addr)
            elif str(part.get_content_type() or '').lower()=='message/rfc822':
                payload=part.get_payload()
                nested=payload[0] if isinstance(payload,list) and payload else payload
                if nested is not None:
                    for h in ('To','Delivered-To','Original-To'):
                        raw=str(nested.get(h) or '').strip()
                        if raw:
                            addr=parseaddr(raw)[1]
                            if addr:
                                candidates.append(addr)
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=761)
    blob=' '.join([
        str(msg.get('Final-Recipient') or ''),
        str(msg.get('Original-Recipient') or ''),
        str(preview or ''),
    ])
    for pat in (
        r'(?i)(?:final-recipient|original-recipient)\s*:\s*(?:rfc822\s*;\s*)?([^\s<>;,]+@[^\s<>;,]+)',
        r'(?i)(?:recipient|получатель|адрес)\s*[:=]\s*<?([^\s<>;,]+@[^\s<>;,]+)>?',
    ):
        m=re.search(pat,blob)
        if m:
            candidates.append(m.group(1))
    for value in candidates:
        addr=str(value or '').strip().strip('<>.,;').lower()
        if re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',addr):
            return addr
    return None


def _machine_reply_kind(msg, sender:str, subject:str, preview:str)->str|None:
    """Classify automated mail so it never becomes a sales lead."""
    sender_low=str(sender or '').strip().lower()
    subject_low=str(subject or '').strip().lower().replace('ё','е')
    preview_low=str(preview or '').strip().lower().replace('ё','е')
    auto_submitted=str(msg.get('Auto-Submitted') or '').strip().lower()
    precedence=str(msg.get('Precedence') or '').strip().lower()
    ctype=str(msg.get_content_type() or '').lower()
    raw_ct=str(msg.get('Content-Type') or '').lower()
    is_delivery=(
        sender_low.startswith('mailer-daemon@')
        or sender_low.startswith('postmaster@')
        or ctype=='message/delivery-status'
        or 'report-type=delivery-status' in raw_ct
        or any(x in subject_low for x in (
            'delivery status notification','undelivered mail','delivery failure',
            'failure notice','returned mail','mail delivery failed',
            'не доставлено','ошибка доставки','не удалось доставить',
        ))
    )
    if is_delivery:
        delivery_meta=[]
        try:
            for part in msg.walk() if msg.is_multipart() else [msg]:
                if str(part.get_content_type() or '').lower()!='message/delivery-status':
                    continue
                payload=part.get_payload()
                blocks=payload if isinstance(payload,list) else [part]
                for block in blocks:
                    for h in ('Status','Action','Diagnostic-Code','Final-Recipient','Original-Recipient'):
                        v=str(block.get(h) or '').strip()
                        if v:
                            delivery_meta.append(v)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=815)
        delivery_blob=' '.join([subject_low,preview_low,' '.join(delivery_meta).lower()])
        permanent=bool(re.search(r'(^|\s)5\.\d+\.\d+(\s|$)',delivery_blob)) or any(x in delivery_blob for x in (
            '5.1.1','5.0.0','550 ','user unknown','unknown user',
            'no such user','mailbox unavailable','recipient address rejected',
            'address not found','recipient not found','does not exist',
            'адрес не существует','ящик не существует','получатель не найден',
        ))
        return 'permanent_bounce' if permanent else 'bounce'
    if auto_submitted and auto_submitted!='no':
        return 'auto_reply'
    if any(msg.get(h) for h in ('X-Autoreply','X-Autorespond','X-Auto-Response-Suppress')):
        return 'auto_reply'
    if precedence in {'bulk','junk','list'}:
        return 'auto_reply'
    if any(x in subject_low for x in (
        'automatic reply','auto reply','out of office','autoreply',
        'автоматический ответ','автоответ','вне офиса','отсутствую',
    )):
        return 'auto_reply'
    return None


def _is_opt_out(text_value:str)->bool:
    low=re.sub(r'\s+',' ',str(text_value or '')).strip().lower().replace('ё','е')
    # Explicit do-not-contact instructions are unconditional.
    if any(x in low for x in (
        'не пишите','больше не пишите','удалите мой адрес','удалите из рассылки','отпис',
        'прошу не писать','не присылайте','не звоните','больше не звоните',
    )):
        return True
    # If the same reply contains an explicit continuation signal, fail closed:
    # preserve the conversation instead of suppressing an ambiguous lead.
    positive=bool(re.search(r'(давайте.{0,25}(?:созвон|встрет|обсуд)|(?:созвон|встреч|видеозвон)|сколько.{0,20}(?:стоит|стоимост)|какая.{0,20}(?:цена|стоимост)|расскажите|пришлите|хочу попробовать|готов.{0,15}обсуд)',low))
    if positive:
        return False
    # Soft refusal phrases count only when they are short/standalone or clearly
    # refer to our offer. This avoids false opt-outs inside a nuanced answer.
    # Normalize trailing punctuation so ordinary replies such as
    # "Не интересно, спасибо." are not accidentally kept as sales leads.
    soft=low.strip(' \t\r\n.,!;:')
    if re.fullmatch(r'(?:нет[, ]*)?(?:мне |нам )?(?:не интересно|неинтересно|не актуально|неактуально)(?:[,!. ]*(?:спасибо)?)?',soft):
        return True
    if re.fullmatch(r'(?:нет[, ]*)?(?:спасибо)(?:[!. ]*)?',soft) or re.fullmatch(r'нет[, ]*спасибо[!. ]*',soft):
        return True
    if re.search(r'(?:ваше|ваши|это|предложен|услуг|рассылк).{0,35}(?:не интерес|не актуаль)',low):
        return True
    # "не надо / не нужно" are opt-out only when they clearly refer to our
    # offer/contact. They must not suppress a hot reply like
    # "не надо менять объявления, давайте созвонимся".
    if re.fullmatch(r'(?:нет[, ]*)?(?:мне |нам )?(?:не нужно|не надо)(?:[,!. ]*(?:спасибо)?)?',soft):
        return True
    if re.search(r'(?:мне |нам )?(?:не нужно|не надо).{0,35}(?:ваше|ваши|это|предложен|услуг|рассылк|писать|звонить|связываться)',low):
        return True
    if re.search(r'(?:ваше|ваши|предложен|услуг|рассылк).{0,35}(?:не нужны|не нужно|не надо)',low):
        return True
    return False

def _reply_sales_stage(text_value:str)->str:
    """Classify obvious inbound sales intent without a paid AI call."""
    low=re.sub(r'\s+',' ',str(text_value or '')).strip().lower().replace('ё','е')
    if any(x in low for x in (
        'давайте созвонимся','давайте созвон','можем созвониться','можно созвониться','созвонимся',
        'назначим встречу','назначить встречу','давайте встретимся','видеозвон','видео созвон',
        'когда удобно созвониться','во сколько созвон','пришлите ссылку на встречу',
    )):
        return 'целевое действие'
    if any(x in low for x in (
        'сколько стоит','какая стоимость','стоимость услуги','какая цена','цена услуги','тариф',
        'интересно','готов обсудить','готовы обсудить','расскажите подробнее','пришлите подробнее',
        'как работает','что входит','есть кейсы','покажите кейс','можете показать','хочу попробовать',
        'актуально','да, давайте','да давайте','да, интересно','да интересно',
    )):
        return 'квалифицирован'
    return 'новый'

def _merge_sales_stage(current:str|None, desired:str)->str:
    rank={'отказ':-1,'новый':0,'в работе':1,'квалифицирован':2,'целевое действие':3,'пробный':4,'оплатил':5,'сделка':6}
    cur=str(current or 'новый')
    return cur if rank.get(cur,0) >= rank.get(desired,0) else desired

def _reply_funnel_label(db, campaign_id):
    row=db.execute(text("SELECT name,niche FROM prospect_campaigns WHERE id=:i"),{'i':int(campaign_id)}).mappings().first() or {}
    blob=(str(row.get('name') or '')+' '+str(row.get('niche') or '')).lower().replace('ё','е')
    if 'разработ' in blob and any(x in blob for x in ('saas','мобильн','заказн','программ')):
        return 'разработка ПО'
    return 'BORIS'


def _match_member(db, mailbox_id, sender, in_reply_to):
    if in_reply_to:
        r=db.execute(text("""SELECT m.id member_id,m.campaign_id,m.company_id,m.email recipient_email,c.account_id FROM prospect_campaign_members m JOIN prospect_campaigns c ON c.id=m.campaign_id JOIN email_queue q ON q.id=m.email_queue_id WHERE c.mailbox_id=:mb AND q.provider_message_id=:mid ORDER BY m.id DESC LIMIT 1"""),{'mb':mailbox_id,'mid':in_reply_to.strip()}).mappings().first()
        if r: return r
    return db.execute(text("""SELECT m.id member_id,m.campaign_id,m.company_id,m.email recipient_email,c.account_id FROM prospect_campaign_members m JOIN prospect_campaigns c ON c.id=m.campaign_id WHERE c.mailbox_id=:mb AND lower(m.email)=lower(:e) AND m.status IN ('queued','sent','replied') ORDER BY m.sent_at DESC NULLS LAST,m.id DESC LIMIT 1"""),{'mb':mailbox_id,'e':sender}).mappings().first()

def _crm_and_alert(db, reply_id, match, sender, subject, preview):
    if not match: return None
    company=db.execute(text('SELECT name,website FROM prospect_companies WHERE id=:i'),{'i':match['company_id']}).mappings().first() or {}
    campaign=db.execute(text('SELECT owner_id,account_id,niche,name FROM prospect_campaigns WHERE id=:i'),{'i':match['campaign_id']}).mappings().first() or {}
    owner=campaign.get('owner_id')
    manager_email=db.execute(text('SELECT email FROM users WHERE id=:i'),{'i':owner}).scalar()
    funnel_label=_reply_funnel_label(db,match['campaign_id'])
    if funnel_label=='разработка ПО':
        lead=db.execute(text("""SELECT id,status FROM manager_leads
          WHERE manager_email=:me AND lower(email)=lower(:e)
            AND account_id IS NULL AND lower(coalesce(niche,'')) LIKE '%разработ%'
          ORDER BY id DESC LIMIT 1"""),{'me':manager_email,'e':sender}).mappings().first()
    else:
        campaign_account=campaign.get('account_id')
        lead=db.execute(text("""SELECT id,status FROM manager_leads
          WHERE manager_email=:me AND lower(email)=lower(:e)
            AND ((:a IS NULL AND account_id IS NULL) OR account_id=:a)
          ORDER BY id DESC LIMIT 1"""),{'me':manager_email,'e':sender,'a':campaign_account}).mappings().first()
    comment=f"Ответ на email-рассылку: {funnel_label}. Тема: {subject}. Ответ: {preview[:700]}"
    lead_source_value=('email_outreach_development' if funnel_label=='разработка ПО' else 'email_outreach_boris')

    if _is_opt_out(preview):
        # Explicit refusal is a global do-not-contact signal. Apply suppression in
        # the same transaction, including not-yet-sent canonical email_queue rows.
        from app.services.prospect_campaigns import suppress_in_db
        suppress_in_db(db,'email',sender,'recipient_opt_out')
        db.execute(text("UPDATE prospect_campaign_members SET status='suppressed',reply_status='opt_out',replied_at=NOW(),skip_reason='recipient_opt_out',updated_at=NOW() WHERE id=:i"),{'i':match['member_id']})
        if lead:
            db.execute(text("UPDATE manager_leads SET status=CASE WHEN status IN ('оплатил','сделка') THEN status ELSE 'отказ' END,comment=:c,lead_source=coalesce(lead_source,:ls),updated_at=NOW() WHERE id=:i"),{'c':comment,'i':lead['id'],'ls':lead_source_value})
            lead_id=int(lead['id'])
            db.execute(text("UPDATE manager_notes SET done=TRUE WHERE manager_email=:me AND lead_id=:l AND kind='reminder' AND COALESCE(done,FALSE)=FALSE"),{'me':manager_email,'l':lead_id})
            db.execute(text("INSERT INTO manager_notes(manager_email,lead_id,client_account_id,kind,text) VALUES(:me,:l,:a,'note',:t)"),{'me':manager_email,'l':lead_id,'a':match['account_id'],'t':'Email: клиент отказался от дальнейших сообщений. Адрес добавлен в стоп-лист BORIS.'})
        else:
            lead_id=None
        # Opt-out is not a hot sales lead: no reminder and no hot-lead alert.
        return lead_id

    desired_stage=_reply_sales_stage(preview)
    previous_stage=str((lead or {}).get('status') or '')
    if lead:
        final_stage=_merge_sales_stage(previous_stage,desired_stage)
        db.execute(text("UPDATE manager_leads SET status=:st,comment=:c,lead_source=coalesce(lead_source,:ls),updated_at=NOW() WHERE id=:i"),{'st':final_stage,'c':comment,'i':lead['id'],'ls':lead_source_value}); lead_id=int(lead['id'])
    else:
        final_stage=desired_stage
        lead_id=int(db.execute(text("""INSERT INTO manager_leads(manager_email,email,company,site,niche,comment,status,account_id,lead_source) SELECT :me,:e,pc.name,pc.website,ca.niche,:c,:st,ca.account_id,:ls FROM prospect_companies pc JOIN prospect_campaigns ca ON ca.id=:campaign WHERE pc.id=:company RETURNING id"""),{'me':manager_email,'e':sender,'c':comment,'st':final_stage,'campaign':match['campaign_id'],'company':match['company_id'],'ls':lead_source_value}).scalar_one())
    reminder_text='Клиент готов к созвону / встрече — согласовать время сегодня' if final_stage=='целевое действие' else ('Квалифицированный ответ на email — связаться сегодня' if final_stage=='квалифицирован' else 'Новый ответ на email-рассылку — связаться с лидом сегодня')
    # Keep exactly one active email follow-up reminder per lead. A hotter reply
    # upgrades the existing task instead of creating another open reminder.
    updated=db.execute(text("""UPDATE manager_notes SET text=:t,remind_at=NOW()
      WHERE id=(SELECT id FROM manager_notes WHERE manager_email=:me AND lead_id=:l AND kind='reminder'
        AND COALESCE(done,FALSE)=FALSE AND text IN ('Новый ответ на email-рассылку — связаться с лидом сегодня','Квалифицированный ответ на email — связаться сегодня','Клиент готов к созвону / встрече — согласовать время сегодня')
        ORDER BY id DESC LIMIT 1)"""),{'me':manager_email,'l':lead_id,'t':reminder_text}).rowcount
    if not updated:
        db.execute(text("INSERT INTO manager_notes(manager_email,lead_id,client_account_id,kind,text,remind_at) VALUES(:me,:l,:a,'reminder',:t,NOW())"),{'me':manager_email,'l':lead_id,'a':match['account_id'],'t':reminder_text})
    db.execute(text("UPDATE prospect_campaign_members SET status='replied',reply_status='replied',replied_at=NOW(),updated_at=NOW() WHERE id=:i"),{'i':match['member_id']})
    tg=db.execute(text('SELECT telegram_chat_id FROM accounts WHERE account_id=:a'),{'a':match['account_id']}).scalar() if match.get('account_id') else None
    if int(owner or 0)==int(os.getenv('PROSPECT_AUTOSTART_OWNER_ID','2') or 2):
        tg=os.getenv('PROSPECT_REPORT_TG_CHAT_ID','-1003952038222')
    # Alert on the first reply or when the conversation reaches a higher sales
    # stage. Routine follow-up messages still update CRM but do not spam owner TG.
    if not lead or final_stage!=previous_stage:
        db.execute(text("INSERT INTO prospect_reply_alerts(reply_id,account_id,telegram_chat_id,status) VALUES(:r,:a,:tg,'pending') ON CONFLICT(reply_id) DO NOTHING"),{'r':reply_id,'a':match['account_id'],'tg':tg})
    return lead_id

def _reply_alert_flush_lock():
    """Serialize alert dispatch without holding a PostgreSQL transaction."""
    from contextlib import contextmanager
    from app.db.session import engine
    @contextmanager
    def _lock():
        conn=engine.connect().execution_options(isolation_level='AUTOCOMMIT')
        got=False
        try:
            got=bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"),{'k':884422923}).scalar())
            yield got
        finally:
            if got:
                try: conn.execute(text("SELECT pg_advisory_unlock(:k)"),{'k':884422923})
                except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=988)
            conn.close()
    return _lock()


def _reply_alert_emails(row:dict)->list[str]:
    configured=(os.getenv('PROSPECT_REPLY_ALERT_EMAILS') or '').strip()
    raw=re.split(r'[,;\s]+',configured) if configured else []
    if not raw:
        raw=[str(row.get('owner_email') or '').strip()]
    out=[]
    for value in raw:
        email=str(value or '').strip().lower()
        if email and '@' in email and email not in out:
            out.append(email)
    return out


def _queue_reply_alert_fallback(row:dict, alert_text:str)->dict|None:
    """Queue one idempotent owner-email fallback after ambiguous Telegram delivery."""
    recipients=_reply_alert_emails(row)
    if not recipients:
        return None
    from app.services.email_queue import enqueue_email
    q=enqueue_email(
        recipients,
        ('Новый лид: разработка ПО' if 'разработка ПО' in alert_text else 'Новый лид из email-рассылки BORIS'),
        alert_text,
        source='prospect_reply_alert_fallback',
        idempotency_key=f"prospect_reply_alert_fallback:{row['reply_id']}",
        send_now=False,
    )
    return q if q.get('id') else None


def recover_stale_reply_alert_leases(stale_minutes:int=10)->dict:
    """Recover crashed reply-alert executions without duplicate notifications.

    Email-only alerts are safe to retry because enqueueing is idempotent.
    Telegram delivery can be ambiguous after a crash, so it is never retried;
    instead the row is routed to one idempotent fallback email.
    """
    mins=max(5,min(int(stale_minutes),120))
    db=SessionLocal(); ensure_schema(db)
    try:
        email_rows=db.execute(text("""UPDATE prospect_reply_alerts
          SET status='pending',
              error=concat_ws('; ',NULLIF(error,''),'stale email-alert lease recovered safely')
          WHERE status='sending' AND telegram_chat_id IS NULL
            AND attempted_at IS NOT NULL
            AND attempted_at<now()-(:m||' minutes')::interval
          RETURNING id"""),{'m':mins}).fetchall()
        tg_rows=db.execute(text("""UPDATE prospect_reply_alerts
          SET status='pending_email_fallback',
              error=concat_ws('; ',NULLIF(error,''),'stale Telegram lease; using email fallback without Telegram retry')
          WHERE status='sending' AND telegram_chat_id IS NOT NULL
            AND attempted_at IS NOT NULL
            AND attempted_at<now()-(:m||' minutes')::interval
          RETURNING id"""),{'m':mins}).fetchall()
        db.commit()
        return {'email_retry':len(email_rows),'telegram_fallback':len(tg_rows)}
    finally:
        db.close()


def _flush_alerts(limit=20):
    """Dispatch owner reply alerts with no DB transaction across Telegram/network I/O.

    Each row is first claimed as `sending` in a short transaction. If the process
    dies after an ambiguous Telegram attempt, the row is intentionally not
    auto-retried; duplicate hot-lead alerts are worse than an operator-visible
    `delivery_unknown` state.
    """
    done=0
    with _reply_alert_flush_lock() as locked:
        if not locked:
            return 0
        cap=max(1,min(int(limit),100))
        # Recover expired execution leases before claiming new work.
        # Email-only rows retry safely via idempotency. Telegram is never retried
        # after an ambiguous crash; it moves to a cross-channel email fallback.
        recover_stale_reply_alert_leases(stale_minutes=10)
        for _ in range(cap):
            db=SessionLocal(); row=None
            try:
                raw=db.execute(text("""SELECT a.id,a.reply_id,a.telegram_chat_id,a.status alert_status,
                  r.from_email,r.subject,r.crm_lead_id,l.status crm_status,
                  ca.owner_id,ca.name campaign_name,ca.niche campaign_niche,u.email owner_email
                  FROM prospect_reply_alerts a
                  JOIN prospect_inbound_replies r ON r.id=a.reply_id
                  JOIN prospect_campaigns ca ON ca.id=r.campaign_id
                  LEFT JOIN users u ON u.id=ca.owner_id
                  LEFT JOIN manager_leads l ON l.id=r.crm_lead_id
                  WHERE a.status IN ('pending','pending_email_fallback')
                  ORDER BY a.id LIMIT 1 FOR UPDATE OF a SKIP LOCKED""")).mappings().first()
                if raw:
                    row=dict(raw)
                    db.execute(text("UPDATE prospect_reply_alerts SET status='sending',attempted_at=now(),error=NULL WHERE id=:i"),{'i':row['id']})
                    db.commit()
                else:
                    db.rollback()
            finally:
                db.close()
            if not row:
                break

            stage=str(row.get('crm_status') or 'новый')
            stage_label='Целевое действие' if stage=='целевое действие' else ('Квалифицированный лид' if stage=='квалифицирован' else 'Новый лид')
            campaign_blob=(str(row.get('campaign_name') or '')+' '+str(row.get('campaign_niche') or '')).lower().replace('ё','е')
            funnel_label='разработка ПО' if ('разработ' in campaign_blob and any(x in campaign_blob for x in ('saas','мобильн','заказн','программ'))) else 'BORIS'
            alert_text=f"{stage_label} из email-рассылки: {funnel_label}. Ответил: {row['from_email']}. Тема: {row['subject'] or '—'}. CRM lead: #{row['crm_lead_id']}. Нужно связаться сегодня."
            channel=None; error=None; ambiguous=False; email_queue_id=None
            fallback_mode=(str(row.get('alert_status') or '')=='pending_email_fallback')
            try:
                if fallback_mode:
                    fallback=_queue_reply_alert_fallback(row,alert_text)
                    if not fallback:
                        raise RuntimeError('fallback email alert enqueue failed')
                    channel='email_fallback_queued'
                    email_queue_id=int(fallback['id'])
                    error='stale Telegram delivery ambiguity routed to email fallback'
                elif row['telegram_chat_id']:
                    from app.telegram_bot import send_telegram_message
                    thread_id=int(os.getenv('PROSPECT_REPORT_TG_THREAD_ID','1747') or 1747) if str(row['telegram_chat_id'])==str(os.getenv('PROSPECT_REPORT_TG_CHAT_ID','-1003952038222')) else None
                    # No SQLAlchemy SessionLocal is alive during this network call.
                    resp=send_telegram_message(str(row['telegram_chat_id']),f"🔥 <b>{stage_label}: {funnel_label}</b>\n\nОтветил: {row['from_email']}\nТема: {row['subject'] or '—'}\nCRM lead: #{row['crm_lead_id']}\n\nНужно связаться сегодня.",thread_id=thread_id)
                    if not (isinstance(resp,dict) and resp.get('ok')):
                        raise RuntimeError('telegram_alert_failed')
                    channel='telegram'
                    duplicate_recipients=_reply_alert_emails(row)
                    if duplicate_recipients:
                        from app.services.email_queue import enqueue_email
                        enqueue_email(
                            duplicate_recipients,
                            ('Новый лид: разработка ПО' if 'разработка ПО' in alert_text else 'Новый лид из email-рассылки BORIS'),
                            alert_text,
                            source='prospect_reply_alert_copy',
                            idempotency_key=f"prospect_reply_alert_copy:{row['reply_id']}",
                            send_now=False,
                        )
                else:
                    recipients=_reply_alert_emails(row)
                    if not recipients: raise RuntimeError('no notification channel')
                    from app.services.email_queue import enqueue_email
                    q=enqueue_email(recipients,'Новый лид из email-рассылки BORIS',alert_text,source='prospect_reply_alert',idempotency_key=f"prospect_reply_alert:{row['reply_id']}",send_now=False)
                    if not q.get('id'): raise RuntimeError('email alert enqueue failed')
                    channel='email_queued'
                    email_queue_id=int(q['id'])
            except Exception as e:
                error=type(e).__name__
                ambiguous=bool(row.get('telegram_chat_id'))
                # A fresh Telegram attempt can be ambiguous. Do not resend it;
                # route to an idempotent email fallback. A stale Telegram lease is
                # already in fallback mode, so a fallback failure escalates directly.
                if ambiguous and not fallback_mode:
                    try:
                        fallback=_queue_reply_alert_fallback(row,alert_text)
                        if fallback:
                            channel='email_fallback_queued'
                            email_queue_id=int(fallback['id'])
                            ambiguous=False
                            error='telegram_delivery_ambiguous_fallback_email_queued'
                    except Exception as fallback_exc:
                        error=f"{error};fallback:{type(fallback_exc).__name__}"

            wdb=SessionLocal()
            try:
                if channel=='telegram':
                    wdb.execute(text("UPDATE prospect_reply_alerts SET status='sent_telegram',sent_at=NOW(),attempted_at=COALESCE(attempted_at,now()),email_queue_id=NULL,error=NULL WHERE id=:i"),{'i':row['id']}); done+=1
                elif channel in ('email_queued','email_fallback_queued'):
                    status='queued_email' if channel=='email_queued' else 'queued_email_fallback'
                    wdb.execute(text("UPDATE prospect_reply_alerts SET status=:s,email_queue_id=:q,sent_at=NULL,attempted_at=COALESCE(attempted_at,now()),error=:e WHERE id=:i"),
                               {'s':status,'q':email_queue_id,'e':error,'i':row['id']}); done+=1
                elif ambiguous:
                    wdb.execute(text("UPDATE prospect_reply_alerts SET status='delivery_unknown',error=:e WHERE id=:i"),{'e':error,'i':row['id']})
                else:
                    # Local enqueue/no-channel failures are explicit and safe to retry later.
                    wdb.execute(text("UPDATE prospect_reply_alerts SET status='pending',error=:e WHERE id=:i"),{'e':error,'i':row['id']})
                wdb.commit()
            finally:
                wdb.close()
    return done


def reconcile_reply_alert_emails(limit:int=200)->dict:
    """Project canonical email_queue outcomes back onto reply-alert state.

    Email alerts are only "sent" after the queue itself confirms SMTP success.
    This prevents the owner UI from claiming delivery when an alert was merely
    enqueued, and makes dead/ambiguous fallback delivery visible to health checks.
    """
    cap=max(1,min(int(limit),1000))
    db=SessionLocal(); ensure_schema(db)
    changed=sent=dead=unknown=0
    try:
        rows=db.execute(text("""SELECT a.id,a.status alert_status,q.status queue_status,
          q.sent_at,q.last_error
          FROM prospect_reply_alerts a
          LEFT JOIN email_queue q ON q.id=a.email_queue_id
          WHERE a.status IN ('queued_email','queued_email_fallback')
          ORDER BY a.id LIMIT :l"""),{'l':cap}).mappings().all()
        for raw in rows:
            r=dict(raw); aid=int(r['id']); ast=str(r['alert_status']); qst=r.get('queue_status')
            fallback=(ast=='queued_email_fallback')
            if qst=='sent':
                status='sent_email_fallback' if fallback else 'sent_email'
                db.execute(text("""UPDATE prospect_reply_alerts
                  SET status=:s,sent_at=COALESCE(:sent_at,NOW()),error=NULL
                  WHERE id=:i"""),{'s':status,'sent_at':r.get('sent_at'),'i':aid})
                changed+=1; sent+=1
            elif qst=='dead':
                status='dead_email_fallback' if fallback else 'dead_email'
                db.execute(text("""UPDATE prospect_reply_alerts
                  SET status=:s,error=:e WHERE id=:i"""),
                  {'s':status,'e':str(r.get('last_error') or 'email alert delivery failed')[:1000],'i':aid})
                changed+=1; dead+=1
            elif qst=='delivery_unknown':
                db.execute(text("""UPDATE prospect_reply_alerts
                  SET status='delivery_unknown',
                      error=concat_ws('; ',NULLIF(error,''),:e)
                  WHERE id=:i"""),
                  {'e':('fallback email delivery outcome unknown' if fallback else 'email alert delivery outcome unknown'),'i':aid})
                changed+=1; unknown+=1
            elif qst is None:
                status='dead_email_fallback' if fallback else 'dead_email'
                db.execute(text("""UPDATE prospect_reply_alerts
                  SET status=:s,error='linked email queue row is missing' WHERE id=:i"""),
                  {'s':status,'i':aid})
                changed+=1; dead+=1
        db.commit()
        return {'changed':changed,'sent':sent,'dead':dead,'delivery_unknown':unknown}
    finally:
        db.close()


def _oldest_unseen_uids(raw_uids,max_messages:int)->list:
    """Bound an IMAP backlog without skipping older unseen messages.

    Processing the newest N first and then advancing last_imap_uid permanently
    loses older replies after an outage. Always drain the backlog from oldest
    to newest; the next minute continues from the next UID.
    """
    cap=max(1,min(int(max_messages),500))
    return list(raw_uids or [])[:cap]


def _effective_imap_cursor(stored_uid:int, uidnext:int|None)->int:
    """Recover a cursor that belongs to another IMAP UID space.

    UID values are mailbox-local. If a provider switch leaves a cursor greater
    than or equal to the current server's UIDNEXT, replay this inbox from UID 1.
    Reply reconciliation is idempotent, so replay is safer than skipping future
    replies until the new provider eventually reaches an impossible old UID.
    """
    stored=max(0,int(stored_uid or 0))
    if uidnext is None:
        return stored
    nxt=max(1,int(uidnext))
    return 0 if stored >= nxt else stored


def poll_mailbox(mailbox_id:int,max_messages:int=50)->dict:
    """Fetch IMAP bytes first, then reconcile each reply in short DB transactions."""
    r=_row(mailbox_id)
    if not r: return {'checked':0,'replies':0,'reason':'not_found'}
    pw=_mailbox_password(r); checked=replies=0
    max_uid=int(r.get('last_imap_uid') or 0); fetched=[]
    try:
        # Phase 1: external IMAP only. No SessionLocal transaction is alive here.
        cls=imaplib.IMAP4_SSL if r['imap_ssl'] else imaplib.IMAP4
        m=cls(r['imap_host'],r['imap_port']); m.login(r['username'],pw); m.select('INBOX')
        uidnext=None
        try:
            st_status,status_data=m.status('INBOX','(UIDNEXT)')
            if st_status=='OK' and status_data:
                raw_status=(status_data[0].decode('utf-8','ignore') if isinstance(status_data[0],bytes) else str(status_data[0]))
                match_uidnext=re.search(r'UIDNEXT\s+(\d+)',raw_status,re.I)
                if match_uidnext:
                    uidnext=int(match_uidnext.group(1))
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=1216)
        max_uid=_effective_imap_cursor(max_uid,uidnext)
        start=max(1,max_uid+1); typ,data=m.uid('search',None,f'UID {start}:*')
        uids=_oldest_unseen_uids((data[0].split() if typ=='OK' and data and data[0] else []),max_messages)
        cursor_uid=max_uid
        for ub in uids:
            uid=int(ub)
            typ,msgdata=m.uid('fetch',ub,'(RFC822)')
            # Fail closed on a transient fetch gap. Advancing the cursor past a
            # failed older UID would permanently lose that reply once newer UIDs
            # are processed. The next minute retries from this same UID.
            if typ!='OK' or not msgdata or not msgdata[0]:
                break
            raw=msgdata[0][1]
            if not raw:
                break
            fetched.append((uid,bytes(raw)))
            cursor_uid=uid
        max_uid=cursor_uid
        m.logout()

        # Phase 2: parse/reconcile DB one message at a time. No IMAP socket I/O.
        for uid,raw in fetched:
            msg=email.message_from_bytes(raw); checked+=1
            sender=parseaddr(msg.get('From',''))[1].strip().lower(); in_reply=(msg.get('In-Reply-To') or '').strip(); subject=_decode(msg.get('Subject'))
            preview=_body_preview(msg)
            message_kind=_machine_reply_kind(msg,sender,subject,preview) or 'human'
            delivery_recipient=_delivery_recipient(msg,preview) if message_kind in ('bounce','permanent_bounce') else None
            match_email=delivery_recipient or sender
            db=SessionLocal(); ensure_schema(db)
            try:
                match=_match_member(db,mailbox_id,match_email,in_reply)
                if not match:
                    db.rollback(); continue
                rid=db.execute(text("""INSERT INTO prospect_inbound_replies(mailbox_id,campaign_id,member_id,message_uid,message_id,in_reply_to,from_email,subject,body_preview,message_kind,received_at)
                  VALUES(:mb,:ca,:me,:u,:mid,:irt,:f,:s,:b,:kind,NOW()) ON CONFLICT(mailbox_id,message_uid) DO NOTHING RETURNING id"""),
                  {'mb':mailbox_id,'ca':match['campaign_id'],'me':match['member_id'],'u':uid,'mid':msg.get('Message-ID'),'irt':in_reply,'f':sender,'s':subject,'b':preview,'kind':message_kind}).scalar()
                if rid and message_kind=='permanent_bounce':
                    recipient=str(match.get('recipient_email') or '').strip().lower()
                    if recipient:
                        from app.services.prospect_campaigns import suppress_in_db
                        suppress_in_db(db,'email',recipient,'async_permanent_bounce')
                    db.execute(text("""UPDATE prospect_campaign_members
                      SET status=CASE WHEN status IN ('queued','sent') THEN 'suppressed' ELSE status END,
                          reply_status=CASE WHEN status IN ('queued','sent') THEN 'bounce' ELSE reply_status END,
                          skip_reason=CASE WHEN status IN ('queued','sent') THEN 'async_permanent_bounce' ELSE skip_reason END,
                          updated_at=NOW() WHERE id=:i"""),{'i':match['member_id']})
                elif rid and message_kind=='human':
                    lead_id=_crm_and_alert(db,int(rid),match,sender,subject,preview)
                    db.execute(text('UPDATE prospect_inbound_replies SET crm_lead_id=:l WHERE id=:i'),{'l':lead_id,'i':rid}); replies+=1
                db.commit()
            except Exception:
                db.rollback(); raise
            finally:
                db.close()

        # Phase 3: update IMAP health only. A successful inbox poll must never
        # erase a still-unresolved SMTP failure from the outbound channel.
        db=SessionLocal(); ensure_schema(db)
        try:
            db.execute(text("""UPDATE client_mailboxes
              SET last_imap_uid=:u,imap_last_checked_at=NOW(),imap_last_error=NULL,
                  last_checked_at=NOW(),
                  last_error=CASE WHEN smtp_last_error IS NULL THEN NULL ELSE 'SMTP: '||smtp_last_error END,
                  updated_at=NOW()
              WHERE id=:i"""),{'u':max_uid,'i':mailbox_id}); db.commit()
        finally: db.close()
        _flush_alerts()
        return {'checked':checked,'replies':replies,'last_uid':max_uid}
    except Exception as e:
        db=SessionLocal(); ensure_schema(db)
        try:
            err=_mailbox_exception_reason(e)
            db.execute(text("""UPDATE client_mailboxes
              SET imap_last_checked_at=NOW(),imap_last_error=:e,last_checked_at=NOW(),
                  last_error=concat_ws('; ',
                    CASE WHEN smtp_last_error IS NOT NULL THEN 'SMTP: '||smtp_last_error END,
                    'IMAP: '||:e
                  ),updated_at=NOW()
              WHERE id=:i"""),{'e':err,'i':mailbox_id}); db.commit()
        finally: db.close()
        return {'checked':checked,'replies':replies,'error':_mailbox_exception_reason(e)}

def poll_all(limit=50):
    """Poll healthy IMAP every minute; back off failed auth without hiding it."""
    db=SessionLocal(); ensure_schema(db)
    try:
        ids=[int(x[0]) for x in db.execute(text("""SELECT id
          FROM client_mailboxes
          WHERE status='active'
            AND (
              imap_last_error IS NULL
              OR imap_last_checked_at IS NULL
              OR imap_last_checked_at <= NOW()-make_interval(mins => CASE
                   WHEN lower(COALESCE(imap_last_error,'')) LIKE '%application password%'
                     OR lower(COALESCE(imap_last_error,'')) LIKE '%parol prilozheniya%'
                   THEN 60 ELSE 5 END)
            )
          ORDER BY id LIMIT :l"""),{'l':limit}).all()]
    finally:
        db.close()
    return {i:poll_mailbox(i) for i in ids}
