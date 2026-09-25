from datetime import datetime, timezone, timedelta
from app.usage import USD_TO_RUB
from .classifier import classify,service_budget
from .signals import detect_signal,parse_budget,urgency,decision_maker_hint
from .dedupe import fingerprint,dedupe_key
from .scoring import score,QUALITY_VERSION
from .policy import decide
from .outreach import build_reply
from .models import QualifiedLead,ContactMode,RawLead
from .source_policy import DEFAULT_SOURCE_POLICIES

TRUSTED_SERVICE_HINT_CONNECTORS={'google_news_search_rss'}

def _classify_raw(raw,catalog=None,profile=None):
    ids=set(classify(raw.title+' '+raw.text,catalog=catalog))
    meta=getattr(raw,'metadata',None) or {}
    if str((profile or {}).get('mode') or 'development').lower()=='development' and meta.get('connector') in TRUSTED_SERVICE_HINT_CONNECTORS:
        allowed={int(x.get('offering_code') or x.get('id') or 0) for x in (catalog or [])} if catalog is not None else set(range(1,36))
        for value in meta.get('suggested_service_ids') or []:
            try: value=int(value)
            except Exception: continue
            if value in allowed:
                ids.add(value)
    return sorted(ids)

def _metadata_budget_rub(raw):
    """Convert provider budget metadata to RUB without a network FX call.

    Freelancer exposes the project currency exchange_rate against USD. BORIS has
    one project-wide USD_TO_RUB rate already used for cost accounting. If either
    value is missing/invalid, fail closed and let text/catalog logic handle it.
    """
    meta=getattr(raw,'metadata',None) or {}
    budget=meta.get('budget') if isinstance(meta,dict) else None
    currency=meta.get('currency') if isinstance(meta,dict) else None
    if not isinstance(budget,dict) or not isinstance(currency,dict):
        return (None,None)
    def num(value):
        try:
            value=float(value)
        except Exception:
            return None
        if value<0 or value!=value or value in (float('inf'),float('-inf')):
            return None
        return value
    lo=num(budget.get('minimum')); hi=num(budget.get('maximum'))
    if lo is None and hi is None:
        return (None,None)
    code=str(currency.get('code') or '').strip().upper()
    if code=='RUB':
        factor=1.0
    else:
        exchange=num(currency.get('exchange_rate'))
        if exchange is None or exchange<=0:
            return (None,None)
        factor=exchange*float(USD_TO_RUB)
    def rub(value):
        return None if value is None else int(round(value*factor))
    lo_rub,hi_rub=rub(lo),rub(hi)
    if lo_rub is not None and hi_rub is not None and lo_rub>hi_rub:
        lo_rub,hi_rub=hi_rub,lo_rub
    return (lo_rub,hi_rub)

def _score_with_budget(raw,ids,signal,now,catalog,profile):
    text_value=raw.title+' '+raw.text
    provider_budget=_metadata_budget_rub(raw)
    text_budget=parse_budget(text_value)
    explicit_budget=provider_budget if any(x is not None for x in provider_budget) else text_budget
    svc_budget=service_budget(ids,catalog)
    budget=explicit_budget if any(x is not None for x in explicit_budget) else svc_budget
    s,reasons=score(raw,ids,signal,now,explicit_budget,profile=profile)
    if explicit_budget[1] is not None and svc_budget[0] is not None and explicit_budget[1] < svc_budget[0]:
        # Explicit customer budget below our catalog floor is a commercial
        # mismatch, not something freshness/keyword bonuses may override.
        s=min(s,max(0,int((profile or {}).get('min_score') or 45)-1))
        reasons.append('явный бюджет ниже минимальной цены подходящего направления')
    return s,reasons,budget

class LeadRadar:
    def __init__(self,repo,crm=None,sender=None,max_age_hours=168,source_policies=None,enforce_profile=False):
        self.repo=repo; self.crm=crm; self.sender=sender; self.max_age=timedelta(hours=max_age_hours); self.policies=dict(DEFAULT_SOURCE_POLICIES); self.policies.update(source_policies or {})
        self.enforce_profile=bool(enforce_profile); self._tenant_cache={}

    def _tenant(self,owner_user_id):
        owner_user_id=int(owner_user_id)
        if owner_user_id in self._tenant_cache:
            return self._tenant_cache[owner_user_id]
        profile=self.repo.get_profile(owner_user_id) if hasattr(self.repo,'get_profile') else None
        catalog=self.repo.get_offerings(owner_user_id) if profile and hasattr(self.repo,'get_offerings') else None
        if profile and profile.get('enabled') and catalog:
            value=(profile,catalog)
        elif self.enforce_profile:
            raise RuntimeError('LEAD_RADAR_PROFILE_OR_OFFERINGS_REQUIRED_FAIL_CLOSED')
        else:
            value=({'mode':'development','business_type':'services','min_score':45,'intent_keywords':[],'negative_keywords':[]},None)
        self._tenant_cache[owner_user_id]=value
        return value

    def process(self,raw,now=None):
        if int(getattr(raw,'owner_user_id',0) or 0)<=0:
            raise RuntimeError('LEAD_RADAR_OWNER_REQUIRED_FAIL_CLOSED')
        now=now or datetime.now(timezone.utc); profile,catalog=self._tenant(raw.owner_user_id); fp=fingerprint(raw.source,raw.external_id,raw.url,raw.title,raw.text)
        if self.repo.by_fingerprint(raw.owner_user_id,fp): return None
        ids=_classify_raw(raw,catalog,profile); signal=detect_signal(raw.source_family,raw.title+' '+raw.text,profile=profile,matched=bool(ids)); dkey=dedupe_key(raw,ids)
        existing=self.repo.recent_duplicate(raw.owner_user_id,dkey,raw.published_at)
        if existing:
            self.repo.add_duplicate_signal(existing['id'],raw)
            if self.crm and existing.get('crm_deal_id') and hasattr(self.crm,'add_signal'):
                try: self.crm.add_signal(existing['crm_deal_id'],raw,signal_family=signal,score=existing.get('score'),service_ids=existing.get('service_ids'))
                except Exception as e: self.repo.add_event(existing['id'],'crm_duplicate_signal_error',{'error':str(e)[:500]})
            return {'duplicate_of':existing['id'],'fingerprint':fp}
        s,reasons,budget=_score_with_budget(raw,ids,signal,now,catalog,profile)
        q=QualifiedLead(raw=raw,service_ids=ids,signal_family=signal,score=s,reasons=reasons,fingerprint=fp,dedupe_key=dkey,
          quality_version=QUALITY_VERSION,budget_min_rub=budget[0],budget_max_rub=budget[1],
          urgency=urgency(raw.title+' '+raw.text),decision_maker_hint=decision_maker_hint(signal))
        if now-raw.published_at>self.max_age: q.status='stale'
        elif not ids or s<int(profile.get('min_score') or 45): q.status='rejected'
        else:
            q.reply=build_reply(q,catalog=catalog,profile=profile); policy=self.policies.get(raw.source) or self.policies.get(raw.source_family)
            d=decide(raw,s,signal,policy); q.contact_mode=d.mode; q.contact_reason=d.reason
            q.status='qualified' if d.mode==ContactMode.CRM_ONLY else ('draft_ready' if d.mode==ContactMode.DRAFT else ('ready_to_contact' if d.mode==ContactMode.AUTO else 'qualified'))
        lid=self.repo.save(q); self.repo.add_event(lid,'qualified',{'score':s,'signal':signal.value,'services':ids,'status':q.status})
        if q.status not in ('stale','rejected') and self.crm:
            try:
                q.crm_deal_id=self.crm.ensure_deal(q); self.repo.add_event(lid,'crm_deal_linked',{'deal_id':q.crm_deal_id}); self.repo.save(q)
            except Exception as e:
                q.status='crm_error'; self.repo.add_event(lid,'crm_error',{'error':str(e)[:500]}); self.repo.save(q); return q
        if q.contact_mode==ContactMode.AUTO and self.sender:
            # No BORIS daily volume cap for Lead Radar. Source/API throttling is handled
            # by connector scheduling; duplicate/idempotency guards are per lead/message.
            oid,created=self.repo.create_outreach(lid,'auto',q.reply,raw.source)
            if created:
                try:
                    ext=self.sender.send(raw,q.reply); self.repo.complete_outreach(oid,True,str(ext or '')); self.repo.mark_contacted(lid); q.status='contacted'; q.next_followup_at=datetime.now(timezone.utc)+timedelta(hours=72); self.repo.add_event(lid,'outreach_sent',{'mode':'auto'})
                except Exception as e:
                    self.repo.complete_outreach(oid,False,error=str(e),next_attempt_at=datetime.now(timezone.utc)+timedelta(minutes=5)); q.status='outreach_retrying'; self.repo.add_event(lid,'outreach_retry_scheduled',{'attempt':1,'error':str(e)[:500]})
                self.repo.save(q)
        return q

    def requalify_active_unsent(self,owner_user_id,now=None,limit=1000):
        now=now or datetime.now(timezone.utc)
        out={'checked':0,'kept':0,'rejected':0,'revived':0,'crm_closed':0,'crm_opened':0,'crm_errors':0}
        profile,catalog=self._tenant(owner_user_id)
        rows=self.repo.active_unsent_for_quality(
            owner_user_id,QUALITY_VERSION,limit=limit,published_since=now-self.max_age
        )
        for row in rows:
            fam=row.get('source_family') or ''
            raw=RawLead(
                source=row['source_key'],external_id=row.get('external_id') or '',url=row.get('url') or '',
                title=row.get('title') or '',text=row.get('body') or '',published_at=row['published_at'],
                source_family=fam,author=row.get('author') or '',company_name=row.get('company_name') or '',
                company_domain=row.get('company_domain') or '',location=row.get('location') or '',
                contact_hint=row.get('contact_hint') or '',owner_user_id=row['owner_user_id'],
                metadata=row.get('metadata_json') or {}
            )
            tv=raw.title+' '+raw.text
            ids=_classify_raw(raw,catalog,profile); signal=detect_signal(fam,tv,profile=profile,matched=bool(ids))
            s,reasons,budget=_score_with_budget(raw,ids,signal,now,catalog,profile)
            reject=not ids or s<int(profile.get('min_score') or 45)
            was_rejected=row.get('status')=='rejected'
            out['checked']+=1
            if reject:
                self.repo.update_quality_result(row['id'],QUALITY_VERSION,ids,signal,s,reasons,reject=True,budget=budget)
                out['rejected']+=1
                self.repo.add_event(row['id'],'quality_self_heal_rejected',
                                    {'quality_version':QUALITY_VERSION,'score':s,'services':ids,'signal':signal.value})
                if self.crm and row.get('crm_deal_id') and hasattr(self.crm,'reject_deal'):
                    try:
                        if self.crm.reject_deal(
                            row['crm_deal_id'],row['owner_user_id'],row['id'],
                            'Lead Radar: текущая проверка качества показала, что это нецелевой запрос для каталога клиента',
                            QUALITY_VERSION
                        ):
                            out['crm_closed']+=1
                    except Exception as e:
                        self.repo.add_event(row['id'],'quality_self_heal_crm_error',{'error':str(e)[:500]})
                continue

            policy=self.policies.get(raw.source) or self.policies.get(raw.source_family)
            d=decide(raw,s,signal,policy)
            status='qualified' if d.mode==ContactMode.CRM_ONLY else ('draft_ready' if d.mode==ContactMode.DRAFT else ('ready_to_contact' if d.mode==ContactMode.AUTO else 'qualified'))
            q=QualifiedLead(
                raw=raw,service_ids=ids,signal_family=signal,score=s,reasons=reasons,status=status,
                quality_version=QUALITY_VERSION,fingerprint=row.get('fingerprint') or '',
                dedupe_key=row.get('dedupe_key') or '',budget_min_rub=budget[0],budget_max_rub=budget[1],
                urgency=urgency(tv),decision_maker_hint=decision_maker_hint(signal),
                crm_deal_id=row.get('crm_deal_id'),next_followup_at=row.get('next_followup_at')
            )
            q.contact_mode=d.mode; q.contact_reason=d.reason; q.reply=build_reply(q,catalog=catalog,profile=profile)

            # Revival is fail-safe: CRM must be available before the rejected row is
            # promoted to the current quality version, otherwise the next cycle retries.
            if self.crm and (was_rejected or not q.crm_deal_id):
                try:
                    q.crm_deal_id=self.crm.ensure_deal(q)
                    if was_rejected and hasattr(self.crm,'restore_deal'):
                        if not self.crm.restore_deal(q.crm_deal_id,row['owner_user_id'],row['id'],QUALITY_VERSION):
                            raise RuntimeError('BORIS_CRM_RESTORE_FAILED')
                    out['crm_opened']+=1
                except Exception as e:
                    out['crm_errors']+=1
                    self.repo.add_event(row['id'],'quality_self_heal_crm_error',{'error':str(e)[:500],'revival':was_rejected})
                    continue

            self.repo.save(q)
            out['kept']+=1
            if was_rejected:
                out['revived']+=1
                self.repo.add_event(row['id'],'quality_self_heal_revived',
                                    {'quality_version':QUALITY_VERSION,'score':s,'services':ids,'signal':signal.value,'status':status,'crm_deal_id':q.crm_deal_id})
            else:
                self.repo.add_event(row['id'],'quality_self_heal_checked',
                                    {'quality_version':QUALITY_VERSION,'score':s,'services':ids,'signal':signal.value})
        return out

    def process_due_followups(self,now=None,limit=None,owner_user_id=None):
        """Авто-follow-up только для лидов, где первый контакт был разрешён в AUTO.
        Никогда не превращает DRAFT/CRM_ONLY в скрытую автоматическую рассылку.
        """
        now=now or datetime.now(timezone.utc); results=[]
        if not self.sender: return results
        for row in self.repo.due_followups(now,limit,owner_user_id=owner_user_id):
            policy=self.policies.get(row['source_key'])
            if not policy or not policy.allows_automation or policy.followup_max<=0:
                self.repo.add_event(row['id'],'followup_skipped_policy',{}); continue
            already=self.repo.outreach_attempt_count(row['id'],'followup')
            if already>=policy.followup_max:
                self.repo.add_event(row['id'],'followup_limit_reached',{'max':policy.followup_max}); continue
            attempt=already+1; text=('Добрый день! Возвращаюсь к вашему запросу «%s». '
                'Если задача ещё актуальна, можем быстро уточнить объём и дать оценку по срокам и стоимости.' % (row['title'][:180]))
            oid,created=self.repo.create_outreach(row['id'],'followup',text,row['source_key'],attempt)
            if not created: continue
            try:
                # Sender может получить либо RawLead, либо dict; боевой adapter обязан поддержать lead row.
                raw=RawLead(source=row['source_key'],external_id=row.get('external_id') or '',url=row.get('url') or '',title=row['title'],text=row.get('body') or '',published_at=row['published_at'],source_family=row.get('signal_family') or '',author=row.get('author') or '',company_name=row.get('company_name') or '',company_domain=row.get('company_domain') or '',location=row.get('location') or '',contact_hint=row.get('contact_hint') or '',owner_user_id=row['owner_user_id'],metadata=row.get('metadata_json') or {})
                ext=self.sender.send(raw,text); self.repo.complete_outreach(oid,True,str(ext or '')); self.repo.mark_followup_sent(row['id'],now)
                self.repo.add_event(row['id'],'followup_sent',{'attempt':attempt}); results.append({'lead_id':row['id'],'status':'sent'})
            except Exception as e:
                self.repo.complete_outreach(oid,False,error=str(e),next_attempt_at=now+timedelta(minutes=5)); self.repo.add_event(row['id'],'followup_retry_scheduled',{'attempt':attempt,'error':str(e)[:500]}); results.append({'lead_id':row['id'],'status':'retrying'})
        return results


    def process_outreach_retries(self,now=None,limit=None,owner_user_id=None):
        """Retry failed sends only through an idempotent sender. Max 3 total attempts.
        The external idempotency key is stable across attempts, so a timeout after provider acceptance
        must not create a second client message on a conforming gateway.
        """
        now=now or datetime.now(timezone.utc); results=[]
        if not self.sender: return results
        for row in self.repo.due_outreach_retries(now,limit,owner_user_id=owner_user_id):
            source=row['source_key']; retry_safe=getattr(self.sender,'retry_safe',lambda _s:False)(source)
            if not retry_safe:
                # Non-idempotent transport cannot be retried safely after an ambiguous send.
                self.repo.complete_outreach(row['id'],False,error=row.get('error') or 'non-idempotent retry blocked',next_attempt_at=None)
                self.repo.set_lead_status(row['lead_id'],'outreach_blocked')
                self.repo.add_event(row['lead_id'],'outreach_retry_blocked_non_idempotent',{}); results.append({'lead_id':row['lead_id'],'status':'blocked'}); continue
            prev_attempt=int(row['attempt'] or 1); attempt=prev_attempt+1
            raw=RawLead(source=source,external_id=row.get('external_id') or '',url=row.get('url') or '',title=row['title'],text=row.get('body') or '',published_at=row['published_at'],source_family=row.get('signal_family') or '',author=row.get('author') or '',company_name=row.get('company_name') or '',company_domain=row.get('company_domain') or '',location=row.get('location') or '',contact_hint=row.get('contact_hint') or '',owner_user_id=row['owner_user_id'],metadata=row.get('metadata_json') or {})
            oid,created=self.repo.spawn_outreach_retry(row['id'],row['lead_id'],row['mode'],row['message_text'],source,attempt,now=now)
            if not created: continue
            try:
                ext=self.sender.send(raw,row['message_text']); self.repo.complete_outreach(oid,True,str(ext or ''))
                if row['mode']=='followup': self.repo.mark_followup_sent(row['lead_id'],now)
                else: self.repo.mark_contacted(row['lead_id'],now)
                self.repo.add_event(row['lead_id'],'outreach_retry_sent',{'attempt':attempt}); results.append({'lead_id':row['lead_id'],'status':'sent','attempt':attempt})
            except Exception as e:
                if attempt>=3:
                    self.repo.complete_outreach(oid,False,error=str(e),next_attempt_at=None); self.repo.set_lead_status(row['lead_id'],'outreach_blocked')
                    self.repo.add_event(row['lead_id'],'outreach_self_heal_exhausted',{'attempt':attempt,'error':str(e)[:500]}); results.append({'lead_id':row['lead_id'],'status':'blocked','attempt':attempt})
                else:
                    delay=20 if attempt==2 else 60
                    self.repo.complete_outreach(oid,False,error=str(e),next_attempt_at=now+timedelta(minutes=delay)); self.repo.set_lead_status(row['lead_id'],'outreach_retrying')
                    self.repo.add_event(row['lead_id'],'outreach_retry_scheduled',{'attempt':attempt,'delay_minutes':delay,'error':str(e)[:500]}); results.append({'lead_id':row['lead_id'],'status':'retrying','attempt':attempt})
        return results
