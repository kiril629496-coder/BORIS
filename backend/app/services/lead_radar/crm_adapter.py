import json
from sqlalchemy import text

class BorisCRMAdapter:
    """Пишет только в существующую CRM BORIS (`boris_crm_*`). Вторую CRM не создаёт."""
    def __init__(self, engine): self.engine=engine; self.pg=engine.dialect.name=='postgresql'
    def _now(self): return 'NOW()' if self.pg else 'CURRENT_TIMESTAMP'
    def _json_expr(self,param='m'): return f'CAST(:{param} AS jsonb)' if self.pg else f':{param}'
    def _pipeline(self,c,owner):
        active='true' if self.pg else '1'
        pid=c.execute(text(f"SELECT id FROM boris_crm_pipelines WHERE owner_user_id=:o AND is_active={active} ORDER BY is_default DESC,id LIMIT 1"),{'o':owner}).scalar()
        if not pid: raise RuntimeError('BORIS_CRM_PIPELINE_NOT_READY')
        sid=c.execute(text("SELECT id FROM boris_crm_stages WHERE pipeline_id=:p ORDER BY position,id LIMIT 1"),{'p':pid}).scalar()
        if not sid: raise RuntimeError('BORIS_CRM_STAGE_NOT_READY')
        return pid,sid
    def _company(self,c,q):
        if not (q.raw.company_name or q.raw.company_domain): return None
        row=c.execute(text("SELECT id FROM boris_crm_companies WHERE owner_user_id=:o AND ((:w<>'' AND website=:w) OR (:n<>'' AND name=:n)) ORDER BY id LIMIT 1"),{'o':q.raw.owner_user_id,'w':q.raw.company_domain,'n':q.raw.company_name}).scalar()
        if row: return row
        now=self._now()
        return c.execute(text(f"INSERT INTO boris_crm_companies(owner_user_id,name,website,source,source_ref,created_at,updated_at) VALUES(:o,:n,:w,'lead_radar',:r,{now},{now}) RETURNING id"),{'o':q.raw.owner_user_id,'n':q.raw.company_name or q.raw.company_domain,'w':q.raw.company_domain or None,'r':q.dedupe_key}).scalar_one()
    def _contact(self,c,q):
        hint=(q.raw.contact_hint or '').strip(); email=hint if '@' in hint else None; phone=hint if hint and not email else None
        if not hint and not q.raw.author: return None
        if email:
            row=c.execute(text("SELECT id FROM boris_crm_contacts WHERE owner_user_id=:o AND primary_email=:e LIMIT 1"),{'o':q.raw.owner_user_id,'e':email}).scalar()
        elif phone:
            row=c.execute(text("SELECT id FROM boris_crm_contacts WHERE owner_user_id=:o AND primary_phone=:p LIMIT 1"),{'o':q.raw.owner_user_id,'p':phone}).scalar()
        else: row=None
        if row: return row
        now=self._now()
        return c.execute(text(f"INSERT INTO boris_crm_contacts(owner_user_id,display_name,primary_phone,primary_email,company_name,source,source_ref,status,created_at,updated_at) VALUES(:o,:d,:p,:e,:cn,'lead_radar',:r,'active',{now},{now}) RETURNING id"),{'o':q.raw.owner_user_id,'d':q.raw.author or q.raw.company_name or 'Новый лид','p':phone,'e':email,'cn':q.raw.company_name or None,'r':q.dedupe_key}).scalar_one()
    def _activity(self,c,owner,deal_id,contact_id,source,title,body,source_ref,metadata):
        now=self._now(); j=self._json_expr('m')
        c.execute(text(f"INSERT INTO boris_crm_activities(owner_user_id,deal_id,contact_id,activity_type,channel,direction,title,body,source,source_ref,actor_type,metadata_json,created_at) VALUES(:o,:d,:c,'lead_discovered',:ch,'in',:t,:b,'lead_radar',:r,'system',{j},{now})"),
                  {'o':owner,'d':deal_id,'c':contact_id,'ch':source,'t':title,'b':body[:4000],'r':source_ref,'m':json.dumps(metadata,ensure_ascii=False)})
    def _lock_deal_key(self,c,owner_user_id,source_ref):
        """Serialize creation of one Lead Radar business deal across concurrent workers."""
        if self.pg:
            lock_key=f"lead_radar:{int(owner_user_id)}:{source_ref}"
            c.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k,0))"),{'k':lock_key})

    def ensure_deal(self,q):
        with self.engine.begin() as c:
            # API/manual/timer cycles may overlap in different processes. Lock the
            # business dedupe key before check+insert so two workers cannot create
            # two CRM deals for the same Lead Radar demand.
            self._lock_deal_key(c,q.raw.owner_user_id,q.dedupe_key)
            # Cross-source source_ref is the stable business dedupe key, not the first source fingerprint.
            existing=c.execute(text("SELECT id FROM boris_crm_deals WHERE owner_user_id=:o AND source='lead_radar' AND source_ref=:r ORDER BY id LIMIT 1"),{'o':q.raw.owner_user_id,'r':q.dedupe_key}).scalar()
            if existing: return existing
            pid,sid=self._pipeline(c,q.raw.owner_user_id); cid=self._contact(c,q); coid=self._company(c,q)
            amount=(q.budget_min_rub or 0)*100 or None; now=self._now()
            did=c.execute(text(f"INSERT INTO boris_crm_deals(owner_user_id,contact_id,company_id,pipeline_id,stage_id,title,amount_kopeks,currency,source,source_ref,status,next_action_at,last_activity_at,created_at,updated_at) VALUES(:o,:c,:co,:p,:s,:t,:a,'RUB','lead_radar',:r,'open',:na,{now},{now},{now}) RETURNING id"),{'o':q.raw.owner_user_id,'c':cid,'co':coid,'p':pid,'s':sid,'t':q.raw.title[:500],'a':amount,'r':q.dedupe_key,'na':q.next_followup_at}).scalar_one()
            self._activity(c,q.raw.owner_user_id,did,cid,q.raw.source,'Lead Radar: найден сигнал',q.raw.text,q.fingerprint,{'signal':q.signal_family.value,'score':q.score,'services':q.service_ids,'url':q.raw.url})
            return did
    def add_signal(self,deal_id,raw,signal_family=None,score=None,service_ids=None):
        """Второй источник не создаёт вторую сделку, а добавляет доказательство в timeline существующей."""
        with self.engine.begin() as c:
            row=c.execute(text("SELECT owner_user_id,contact_id FROM boris_crm_deals WHERE id=:d AND source='lead_radar' LIMIT 1"),{'d':deal_id}).first()
            if not row: return False
            self._activity(c,row[0],deal_id,row[1],raw.source,'Lead Radar: дополнительный сигнал',raw.text,
                           f"{raw.source}:{raw.external_id or raw.url}",{'signal':getattr(signal_family,'value',signal_family),'score':score,'services':service_ids or [],'url':raw.url})
            return True


    def reject_deal(self,deal_id,owner_user_id,lead_id,reason,quality_version):
        with self.engine.begin() as c:
            row=c.execute(text(
                "SELECT id,pipeline_id,contact_id,status,source FROM boris_crm_deals "
                "WHERE id=:d AND owner_user_id=:o LIMIT 1"
            ),{'d':deal_id,'o':owner_user_id}).mappings().first()
            if not row or row.get('source')!='lead_radar' or row.get('status')!='open':
                return False
            lost_stage=c.execute(text(
                "SELECT id FROM boris_crm_stages WHERE pipeline_id=:p AND semantic_type='lost' ORDER BY position,id LIMIT 1"
            ),{'p':row['pipeline_id']}).scalar()
            if not lost_stage:
                raise RuntimeError('BORIS_CRM_LOST_STAGE_NOT_READY')
            now=self._now()
            c.execute(text(
                f"UPDATE boris_crm_deals SET status='lost',stage_id=:s,lost_reason=:r,"
                f"next_action_at=NULL,last_activity_at={now},updated_at={now} WHERE id=:d"
            ),{'s':lost_stage,'r':str(reason)[:500],'d':deal_id})
            ref=f"quality:{lead_id}:{quality_version}"[:300]
            exists_row=c.execute(text(
                "SELECT 1 FROM boris_crm_activities WHERE deal_id=:d AND source='lead_radar' AND source_ref=:r LIMIT 1"
            ),{'d':deal_id,'r':ref}).first()
            if not exists_row:
                j=self._json_expr('m')
                c.execute(text(
                    f"INSERT INTO boris_crm_activities(owner_user_id,deal_id,contact_id,activity_type,channel,direction,"
                    f"title,body,source,source_ref,actor_type,metadata_json,created_at) VALUES("
                    f":o,:d,:c,'lead_requalified_rejected','lead_radar','in',:t,:b,'lead_radar',:r,'system',{j},{now})"
                ),{'o':owner_user_id,'d':deal_id,'c':row.get('contact_id'),
                   't':'Lead Radar: лид автоматически переоценён',
                   'b':str(reason)[:2000],'r':ref,
                   'm':json.dumps({'lead_id':lead_id,'quality_version':quality_version},ensure_ascii=False)})
            return True

    def restore_deal(self,deal_id,owner_user_id,lead_id,quality_version):
        """Re-open the same Lead Radar deal when a newer quality rule fixes a false negative."""
        with self.engine.begin() as c:
            row=c.execute(text(
                "SELECT id,pipeline_id,contact_id,status,source FROM boris_crm_deals "
                "WHERE id=:d AND owner_user_id=:o LIMIT 1"
            ),{'d':deal_id,'o':owner_user_id}).mappings().first()
            if not row or row.get('source')!='lead_radar':
                return False
            if row.get('status')=='open':
                return True
            first_stage=c.execute(text(
                "SELECT id FROM boris_crm_stages WHERE pipeline_id=:p ORDER BY position,id LIMIT 1"
            ),{'p':row['pipeline_id']}).scalar()
            if not first_stage:
                raise RuntimeError('BORIS_CRM_FIRST_STAGE_NOT_READY')
            now=self._now()
            c.execute(text(
                f"UPDATE boris_crm_deals SET status='open',stage_id=:s,lost_reason=NULL,"
                f"last_activity_at={now},updated_at={now} WHERE id=:d"
            ),{'s':first_stage,'d':deal_id})
            ref=f"quality_restore:{lead_id}:{quality_version}"[:300]
            exists_row=c.execute(text(
                "SELECT 1 FROM boris_crm_activities WHERE deal_id=:d AND source='lead_radar' AND source_ref=:r LIMIT 1"
            ),{'d':deal_id,'r':ref}).first()
            if not exists_row:
                j=self._json_expr('m')
                c.execute(text(
                    f"INSERT INTO boris_crm_activities(owner_user_id,deal_id,contact_id,activity_type,channel,direction,"
                    f"title,body,source,source_ref,actor_type,metadata_json,created_at) VALUES("
                    f":o,:d,:c,'lead_requalified_restored','lead_radar','in',:t,:b,'lead_radar',:r,'system',{j},{now})"
                ),{'o':owner_user_id,'d':deal_id,'c':row.get('contact_id'),
                   't':'Lead Radar: лид автоматически восстановлен',
                   'b':'Новая версия проверки качества подтвердила, что запрос снова соответствует каталогу клиента.',
                   'r':ref,'m':json.dumps({'lead_id':lead_id,'quality_version':quality_version},ensure_ascii=False)})
            return True
