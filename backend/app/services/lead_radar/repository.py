from datetime import datetime, timezone, timedelta
import hashlib
import json, threading
from contextlib import contextmanager
from sqlalchemy import select,insert,update,and_,func,or_,exists,inspect,text
from sqlalchemy.exc import IntegrityError
from .db import metadata,leads,events,outreach,sources,profiles,offerings
from app.services.exception_observability import observe_suppressed

def _bounded_external_id(value,limit=300):
    value=str(value or '')
    if len(value)<=limit:
        return value
    digest=hashlib.sha256(value.encode('utf-8')).hexdigest()
    # Keep a small human-readable prefix while guaranteeing DB compatibility.
    prefix=value[:min(180,max(0,limit-72))]
    return (prefix+'#sha256:'+digest)[:limit]

def _required_owner(value):
    try: v=int(value or 0)
    except Exception: v=0
    if v<=0: raise RuntimeError('LEAD_RADAR_OWNER_REQUIRED_FAIL_CLOSED')
    return v

class SQLLeadRepository:
    _local_locks={}
    _local_locks_guard=threading.Lock()
    def __init__(self, engine): self.engine=engine

    @contextmanager
    def cycle_guard(self, owner_user_id):
        """Single-flight per owner. Prevents timer + manual scan from running together.
        PostgreSQL uses a session advisory lock; local/SQLite QA uses a process lock.
        """
        if self.engine.dialect.name=='postgresql':
            # Session-level advisory locks must not keep an SQL transaction open
            # for the whole external scan. AUTOCOMMIT keeps the connection/session
            # (and therefore the advisory lock) alive without idle-in-transaction.
            conn=self.engine.connect().execution_options(isolation_level='AUTOCOMMIT')
            key=673900000+int(owner_user_id)
            try:
                ok=bool(conn.execute(select(func.pg_try_advisory_lock(key))).scalar())
                yield ok
            finally:
                if 'ok' in locals() and ok:
                    try: conn.execute(select(func.pg_advisory_unlock(key)))
                    except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=46)
                conn.close()
            return
        lock_key=(id(self.engine),int(owner_user_id))
        with self._local_locks_guard:
            lock=self._local_locks.setdefault(lock_key,threading.Lock())
        ok=lock.acquire(blocking=False)
        try: yield ok
        finally:
            if ok: lock.release()
    def install(self):
        if not inspect(self.engine).has_table('lead_radar_leads'):
            raise RuntimeError('LEAD_RADAR_SCHEMA_MISSING_MIGRATION_049')
        cols={x.get('name') for x in inspect(self.engine).get_columns('lead_radar_leads')}
        if 'quality_version' not in cols:
            with self.engine.begin() as c:
                c.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
        # Production invariant: one Lead Radar business key may own only one
        # BORIS CRM deal. This is the DB-level backstop for concurrent workers;
        # crm_adapter also serializes check+insert with an advisory transaction lock.
        if self.engine.dialect.name=='postgresql' and inspect(self.engine).has_table('boris_crm_deals'):
            with self.engine.begin() as c:
                c.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))

    # ---------- tenant product/service profile ----------
    def get_profile(self,owner_user_id):
        owner_user_id=_required_owner(owner_user_id)
        with self.engine.begin() as c:
            row=c.execute(select(profiles).where(profiles.c.owner_user_id==owner_user_id)).mappings().first()
        return dict(row) if row else None

    def get_offerings(self,owner_user_id,enabled_only=True):
        owner_user_id=_required_owner(owner_user_id)
        q=select(offerings).where(offerings.c.owner_user_id==owner_user_id).order_by(offerings.c.offering_code)
        if enabled_only:
            q=q.where(offerings.c.enabled==True)
        with self.engine.begin() as c:
            return [dict(x) for x in c.execute(q).mappings().all()]

    def require_catalog(self,owner_user_id):
        profile=self.get_profile(owner_user_id)
        if not profile or not profile.get('enabled'):
            raise RuntimeError('LEAD_RADAR_PROFILE_REQUIRED_FAIL_CLOSED')
        catalog=self.get_offerings(owner_user_id,enabled_only=True)
        if not catalog:
            raise RuntimeError('LEAD_RADAR_OFFERINGS_REQUIRED_FAIL_CLOSED')
        return profile,catalog

    def upsert_profile(self,owner_user_id,*,name,business_type='services',mode='generic',
                       geography_json=None,intent_keywords=None,negative_keywords=None,
                       min_score=45,currency='RUB',enabled=True,settings_json=None):
        owner_user_id=_required_owner(owner_user_id)
        vals=dict(owner_user_id=owner_user_id,name=str(name or '').strip() or f'Client {owner_user_id}',
                  business_type=str(business_type or 'services'),mode=str(mode or 'generic'),
                  geography_json=list(geography_json or []),intent_keywords=list(intent_keywords or []),
                  negative_keywords=list(negative_keywords or []),min_score=max(1,min(100,int(min_score or 45))),
                  currency=str(currency or 'RUB')[:10],enabled=bool(enabled),settings_json=dict(settings_json or {}),
                  updated_at=datetime.now(timezone.utc))
        with self.engine.begin() as c:
            rid=c.execute(select(profiles.c.id).where(profiles.c.owner_user_id==owner_user_id)).scalar()
            if rid:
                c.execute(update(profiles).where(profiles.c.id==rid).values(**vals))
                return rid
            return c.execute(insert(profiles).values(**vals).returning(profiles.c.id)).scalar_one()

    def replace_offerings(self,owner_user_id,rows):
        owner_user_id=_required_owner(owner_user_id)
        clean=[]
        seen_codes=set(); seen_keys=set()
        for pos,row in enumerate(rows or [],1):
            code=int(row.get('offering_code') or pos)
            key=str(row.get('external_key') or f'offering_{code}').strip()
            name=str(row.get('name') or '').strip()
            if code<=0 or code in seen_codes: raise RuntimeError('LEAD_RADAR_OFFERING_CODE_INVALID')
            if not key or key in seen_keys: raise RuntimeError('LEAD_RADAR_OFFERING_KEY_INVALID')
            if not name: raise RuntimeError('LEAD_RADAR_OFFERING_NAME_REQUIRED')
            kws=[str(x).strip() for x in row.get('keywords') or [] if str(x).strip()]
            if not kws: raise RuntimeError(f'LEAD_RADAR_OFFERING_KEYWORDS_REQUIRED:{key}')
            seen_codes.add(code); seen_keys.add(key)
            clean.append(dict(owner_user_id=owner_user_id,offering_code=code,external_key=key,
                              kind=str(row.get('kind') or 'service')[:20],name=name,keywords=kws,
                              negative_keywords=[str(x).strip() for x in row.get('negative_keywords') or [] if str(x).strip()],
                              price_min_rub=row.get('price_min_rub'),price_max_rub=row.get('price_max_rub'),
                              currency=str(row.get('currency') or 'RUB')[:10],enabled=bool(row.get('enabled',True)),
                              metadata_json=dict(row.get('metadata_json') or {}),updated_at=datetime.now(timezone.utc)))
        if not clean: raise RuntimeError('LEAD_RADAR_OFFERINGS_REQUIRED_FAIL_CLOSED')
        with self.engine.begin() as c:
            c.execute(offerings.delete().where(offerings.c.owner_user_id==owner_user_id))
            for vals in clean:
                c.execute(insert(offerings).values(**vals))
        return len(clean)

    # ---------- leads / dedupe ----------
    def by_fingerprint(self, owner_user_id, fp):
        with self.engine.begin() as c:
            return c.execute(select(leads).where(and_(leads.c.owner_user_id==owner_user_id,leads.c.fingerprint==fp))).mappings().first()
    def recent_duplicate(self, owner_user_id, dkey, published_at, days=14):
        since=published_at-timedelta(days=days)
        with self.engine.begin() as c:
            return c.execute(select(leads).where(and_(leads.c.owner_user_id==owner_user_id,leads.c.dedupe_key==dkey,leads.c.published_at>=since)).order_by(leads.c.id.desc())).mappings().first()
    def save(self, q):
        vals=dict(owner_user_id=q.raw.owner_user_id,fingerprint=q.fingerprint,dedupe_key=q.dedupe_key,source_key=q.raw.source,
          external_id=_bounded_external_id(q.raw.external_id),url=q.raw.url,title=q.raw.title,body=q.raw.text,author=q.raw.author,company_name=q.raw.company_name,
          company_domain=q.raw.company_domain,contact_hint=q.raw.contact_hint,location=q.raw.location,published_at=q.raw.published_at,
          signal_family=q.signal_family.value,service_ids=q.service_ids,score=q.score,reasons=q.reasons,budget_min_rub=q.budget_min_rub,
          budget_max_rub=q.budget_max_rub,urgency=q.urgency,decision_maker_hint=q.decision_maker_hint,status=q.status,
          contact_mode=q.contact_mode.value,contact_reason=q.contact_reason,reply_text=q.reply,crm_deal_id=q.crm_deal_id,
          next_followup_at=q.next_followup_at,metadata_json=q.raw.metadata,quality_version=q.quality_version)
        with self.engine.begin() as c:
            row=c.execute(select(leads.c.id).where(and_(leads.c.owner_user_id==q.raw.owner_user_id,leads.c.fingerprint==q.fingerprint))).first()
            if row:
                c.execute(update(leads).where(leads.c.id==row[0]).values(**vals,updated_at=datetime.now(timezone.utc))); lid=row[0]
            else:
                lid=c.execute(insert(leads).values(**vals).returning(leads.c.id)).scalar_one()
        return lid
    def add_event(self, lead_id,event,payload=None):
        with self.engine.begin() as c: c.execute(insert(events).values(lead_id=lead_id,event=event,payload_json=payload or {}))
    def add_duplicate_signal(self, existing_id, raw):
        self.add_event(existing_id,'duplicate_signal',{'source':raw.source,'external_id':raw.external_id,'url':raw.url,'title':raw.title})
    def get_lead(self,lead_id):
        with self.engine.begin() as c: return c.execute(select(leads).where(leads.c.id==lead_id)).mappings().first()

    def active_unsent_for_quality(self,owner_user_id,quality_version,limit=1000,families=None,published_since=None):
        owner_user_id=_required_owner(owner_user_id)
        no_outreach=~exists(select(outreach.c.id).where(outreach.c.lead_id==leads.c.id))
        # Self-heal must cover every registered source family. It also rechecks
        # recent rejected leads after a quality-rule change so false negatives can
        # recover automatically. Old rejected demand stays closed and is not revived.
        family_filter=sources.c.family.in_(tuple(families)) if families else sources.c.id.is_not(None)
        active_status=leads.c.status.in_(('qualified','draft_ready','ready_to_contact'))
        rejected_status=leads.c.status=='rejected'
        if published_since is not None:
            rejected_status=and_(rejected_status,leads.c.published_at>=published_since)
        q=(select(leads,sources.c.family.label('source_family'))
           .select_from(leads.outerjoin(sources,and_(sources.c.owner_user_id==leads.c.owner_user_id,sources.c.key==leads.c.source_key)))
           .where(and_(
               leads.c.owner_user_id==owner_user_id,
               leads.c.first_contact_at.is_(None),
               or_(active_status,rejected_status),
               func.coalesce(leads.c.quality_version,'')!=str(quality_version),
               family_filter,
               no_outreach,
           ))
           .order_by(leads.c.id)
           .limit(int(limit)))
        with self.engine.begin() as c:
            return [dict(x) for x in c.execute(q).mappings().all()]

    def update_quality_result(self,lead_id,quality_version,service_ids,signal_family,score,reasons,reject=False,budget=None):
        vals=dict(
            quality_version=str(quality_version),service_ids=list(service_ids or []),
            signal_family=getattr(signal_family,'value',signal_family),score=int(score or 0),
            reasons=list(reasons or []),updated_at=datetime.now(timezone.utc),
        )
        if budget is not None:
            vals.update(budget_min_rub=budget[0],budget_max_rub=budget[1])
        if reject:
            vals.update(status='rejected',contact_mode='none',contact_reason='quality_self_heal_rejected',
                        reply_text=None,next_followup_at=None)
        with self.engine.begin() as c:
            c.execute(update(leads).where(leads.c.id==lead_id).values(**vals))

    # ---------- outreach / follow-up ----------
    def mark_contacted(self, lead_id, when=None, followup_hours=72):
        when=when or datetime.now(timezone.utc)
        with self.engine.begin() as c:
            c.execute(update(leads).where(leads.c.id==lead_id).values(
                first_contact_at=func.coalesce(leads.c.first_contact_at,when),last_contact_at=when,
                next_followup_at=when+timedelta(hours=followup_hours),status='contacted'))
    def outreach_attempt_count(self,lead_id,mode=None):
        q=select(func.count()).select_from(outreach).where(outreach.c.lead_id==lead_id)
        if mode: q=q.where(outreach.c.mode==mode)
        with self.engine.begin() as c: return c.execute(q).scalar_one()
    def create_outreach(self, lead_id,mode,message,channel='source',attempt=1,next_attempt_at=None):
        key=f'{lead_id}:{mode}:{attempt}'
        with self.engine.begin() as c:
            row=c.execute(select(outreach.c.id).where(outreach.c.idempotency_key==key)).first()
            if row: return row[0],False
            oid=c.execute(insert(outreach).values(lead_id=lead_id,channel=channel,mode=mode,idempotency_key=key,status='queued',attempt=attempt,message_text=message,next_attempt_at=next_attempt_at).returning(outreach.c.id)).scalar_one()
        return oid,True
    def complete_outreach(self, oid, ok, external_message_id=None,error=None,next_attempt_at=None):
        vals=dict(status='sent' if ok else 'failed',external_message_id=external_message_id,error=(error or '')[:1000],completed_at=datetime.now(timezone.utc))
        if not ok: vals['next_attempt_at']=next_attempt_at
        with self.engine.begin() as c:
            c.execute(update(outreach).where(outreach.c.id==oid).values(**vals))
    def due_outreach_retries(self,now=None,limit=None,owner_user_id=None):
        now=now or datetime.now(timezone.utc)
        stale=now-timedelta(minutes=15)
        q=select(outreach,leads).select_from(outreach.join(leads,outreach.c.lead_id==leads.c.id)).where(or_(
            and_(outreach.c.status=='failed',outreach.c.next_attempt_at.is_not(None),outreach.c.next_attempt_at<=now),
            and_(outreach.c.status=='queued',outreach.c.created_at<=stale)))
        if owner_user_id is not None: q=q.where(leads.c.owner_user_id==owner_user_id)
        
        q=q.order_by(outreach.c.next_attempt_at)
        if limit is not None: q=q.limit(int(limit))
        with self.engine.begin() as c: return [dict(x) for x in c.execute(q).mappings().all()]
    def spawn_outreach_retry(self,parent_oid,lead_id,mode,message,channel,attempt,now=None,stale_after_minutes=15):
        """Atomically create the next attempt and retire its parent.

        Allowed parents:
        - failed and due now;
        - queued but stale/ambiguous (worker may have died around external send).

        The previous row becomes terminal only in the same transaction that creates
        the next attempt. A crash before commit leaves the parent eligible for recovery.
        """
        now=now or datetime.now(timezone.utc); key=f'{lead_id}:{mode}:{attempt}'
        try:
            with self.engine.begin() as c:
                row=c.execute(select(outreach.c.status,outreach.c.created_at,outreach.c.next_attempt_at).where(outreach.c.id==parent_oid)).first()
                if not row: return None,False
                status,created_at,next_attempt_at=row
                if getattr(created_at,'tzinfo',None) is None and created_at is not None: created_at=created_at.replace(tzinfo=timezone.utc)
                if getattr(next_attempt_at,'tzinfo',None) is None and next_attempt_at is not None: next_attempt_at=next_attempt_at.replace(tzinfo=timezone.utc)
                due_failed=(status=='failed' and next_attempt_at is not None and next_attempt_at<=now)
                stale_queued=(status=='queued' and created_at is not None and created_at<=now-timedelta(minutes=stale_after_minutes))
                if not (due_failed or stale_queued): return None,False
                existing=c.execute(select(outreach.c.id).where(outreach.c.idempotency_key==key)).scalar()
                if existing:
                    c.execute(update(outreach).where(outreach.c.id==parent_oid).values(status='superseded',next_attempt_at=None,completed_at=func.coalesce(outreach.c.completed_at,now)))
                    return existing,False
                oid=c.execute(insert(outreach).values(lead_id=lead_id,channel=channel,mode=mode,idempotency_key=key,status='queued',attempt=attempt,message_text=message).returning(outreach.c.id)).scalar_one()
                c.execute(update(outreach).where(outreach.c.id==parent_oid).values(status='superseded',next_attempt_at=None,completed_at=func.coalesce(outreach.c.completed_at,now)))
                return oid,True
        except IntegrityError:
            # Concurrent worker won the unique idempotency-key race. Do not fail the cycle.
            with self.engine.begin() as c:
                existing=c.execute(select(outreach.c.id).where(outreach.c.idempotency_key==key)).scalar()
                if existing:
                    c.execute(update(outreach).where(outreach.c.id==parent_oid).values(status='superseded',next_attempt_at=None,completed_at=func.coalesce(outreach.c.completed_at,now)))
                    return existing,False
            raise

    def set_lead_status(self,lead_id,status):
        with self.engine.begin() as c: c.execute(update(leads).where(leads.c.id==lead_id).values(status=status,updated_at=datetime.now(timezone.utc)))
    def due_followups(self,now=None,limit=None,owner_user_id=None):
        now=now or datetime.now(timezone.utc)
        with self.engine.begin() as c:
            q=select(leads).where(and_(leads.c.status=='contacted',leads.c.next_followup_at.is_not(None),leads.c.next_followup_at<=now))
            if owner_user_id is not None: q=q.where(leads.c.owner_user_id==owner_user_id)
            q=q.order_by(leads.c.next_followup_at)
            if limit is not None: q=q.limit(int(limit))
            rows=c.execute(q).mappings().all()
            return [dict(x) for x in rows]
    def mark_followup_sent(self,lead_id,when=None):
        when=when or datetime.now(timezone.utc)
        with self.engine.begin() as c:
            c.execute(update(leads).where(leads.c.id==lead_id).values(last_contact_at=when,next_followup_at=None,status='followup_sent',updated_at=when))

    # ---------- source state / self-heal ----------
    def ensure_source(self,key,family,policy=None,owner_user_id=None):
        owner_user_id=_required_owner(owner_user_id)
        now=datetime.now(timezone.utc); pj={}
        if policy:
            pj={k:getattr(policy,k) for k in ('allows_business_contact','allows_automation','applicant_channel','min_interval_seconds','followup_max','enabled')}
        with self.engine.begin() as c:
            row=c.execute(select(sources.c.id).where(and_(sources.c.owner_user_id==owner_user_id,sources.c.key==key))).scalar()
            if row:
                c.execute(update(sources).where(sources.c.id==row).values(family=family,enabled=pj.get('enabled',True),policy_json=pj,updated_at=now)); return row
            return c.execute(insert(sources).values(owner_user_id=owner_user_id,key=key,family=family,enabled=pj.get('enabled',True),policy_json=pj,consecutive_failures=0,blocked_external=False).returning(sources.c.id)).scalar_one()
    @staticmethod
    def _recovery_plan(failures:int,blocked_external=False):
        if blocked_external: return 'external_dependency', timedelta(hours=6)
        if failures<=1: return 'retry_fast', timedelta(minutes=5)
        if failures==2: return 'retry_backoff', timedelta(minutes=20)
        if failures==3: return 'alternate_strategy_or_cache', timedelta(hours=1)
        if failures<=5: return 'degraded_backoff', timedelta(hours=3)
        return 'external_dependency', timedelta(hours=6)
    def source_scan_start(self,key,now=None,owner_user_id=None):
        owner_user_id=_required_owner(owner_user_id)
        now=now or datetime.now(timezone.utc)
        with self.engine.begin() as c: c.execute(update(sources).where(and_(sources.c.owner_user_id==owner_user_id,sources.c.key==key)).values(last_scan_at=now,updated_at=now))
    def source_last_success(self,key,owner_user_id=None):
        owner_user_id=_required_owner(owner_user_id)
        with self.engine.begin() as c:
            value=c.execute(select(sources.c.last_success_at).where(and_(sources.c.owner_user_id==owner_user_id,sources.c.key==key))).scalar()
        if value is not None and getattr(value,'tzinfo',None) is None:
            value=value.replace(tzinfo=timezone.utc)
        return value
    def source_success(self,key,now=None,owner_user_id=None):
        owner_user_id=_required_owner(owner_user_id)
        now=now or datetime.now(timezone.utc)
        with self.engine.begin() as c:
            row=c.execute(select(sources.c.policy_json).where(and_(sources.c.owner_user_id==owner_user_id,sources.c.key==key))).scalar() or {}
            interval=max(60,int((row or {}).get('min_interval_seconds') or 300))
            c.execute(update(sources).where(and_(sources.c.owner_user_id==owner_user_id,sources.c.key==key)).values(last_scan_at=now,last_success_at=now,last_error=None,consecutive_failures=0,next_scan_at=now+timedelta(seconds=interval),recovery_strategy='normal',blocked_external=False,updated_at=now))
    def source_failure(self,key,error,now=None,blocked_external=False,owner_user_id=None):
        owner_user_id=_required_owner(owner_user_id)
        now=now or datetime.now(timezone.utc)
        with self.engine.begin() as c:
            current=c.execute(select(sources.c.consecutive_failures).where(and_(sources.c.owner_user_id==owner_user_id,sources.c.key==key))).scalar()
            failures=int(current or 0)+1
            # Temporary provider throttling/timeouts must self-heal with backoff and must not
            # turn the owner into an operator even after many repeats.
            err_text=str(error).lower()
            transient=any(x in err_text for x in ('rate limit','too many requests','http error 429','temporarily unavailable','service unavailable','timeout','timed out','connection reset'))
            external=bool(blocked_external or (failures>=6 and not transient))
            if transient:
                if failures<=1: strategy,delay='provider_retry_fast',timedelta(minutes=5)
                elif failures==2: strategy,delay='provider_retry_backoff',timedelta(minutes=20)
                elif failures==3: strategy,delay='provider_backoff_1h',timedelta(hours=1)
                elif failures<=5: strategy,delay='provider_backoff_3h',timedelta(hours=3)
                else: strategy,delay='provider_backoff_6h',timedelta(hours=6)
                external=False
            else:
                strategy,delay=self._recovery_plan(failures,external)
            c.execute(update(sources).where(and_(sources.c.owner_user_id==owner_user_id,sources.c.key==key)).values(last_scan_at=now,last_failure_at=now,last_error=str(error)[:1000],consecutive_failures=failures,next_scan_at=now+delay,recovery_strategy=strategy,blocked_external=external,updated_at=now))
        return {'failures':failures,'strategy':strategy,'next_scan_at':now+delay,'blocked_external':external}
    def source_due(self,key,now=None,owner_user_id=None):
        owner_user_id=_required_owner(owner_user_id)
        now=now or datetime.now(timezone.utc)
        with self.engine.begin() as c:
            row=c.execute(select(sources.c.enabled,sources.c.next_scan_at).where(and_(sources.c.owner_user_id==owner_user_id,sources.c.key==key))).first()
        if not row or not row[0]: return False
        due=row[1]
        if due is None: return True
        # SQLite test adapter can drop tzinfo; production Postgres preserves it. Normalize safely.
        if getattr(due,'tzinfo',None) is None: due=due.replace(tzinfo=timezone.utc)
        if getattr(now,'tzinfo',None) is None: now=now.replace(tzinfo=timezone.utc)
        return due<=now

    # ---------- read API ----------
    def list_leads(self,limit=100,status=None,owner_user_id=None):
        q=select(leads).order_by(leads.c.score.desc(),leads.c.published_at.desc()).limit(limit)
        if status: q=q.where(leads.c.status==status)
        if owner_user_id is not None: q=q.where(leads.c.owner_user_id==owner_user_id)
        with self.engine.begin() as c: return [dict(x) for x in c.execute(q).mappings().all()]
    def stats(self,owner_user_id=None):
        with self.engine.begin() as c:
            base=select(func.count()).select_from(leads)
            grouped=select(leads.c.status,func.count()).group_by(leads.c.status)
            if owner_user_id is not None:
                base=base.where(leads.c.owner_user_id==owner_user_id); grouped=grouped.where(leads.c.owner_user_id==owner_user_id)
            total=c.execute(base).scalar_one(); by_status=dict(c.execute(grouped).all())
            sq=select(sources).order_by(sources.c.key)
            if owner_user_id is not None: sq=sq.where(sources.c.owner_user_id==owner_user_id)
            src=[dict(x) for x in c.execute(sq).mappings().all()]
            return {'total':total,'by_status':by_status,'sources':src}
