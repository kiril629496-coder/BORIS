from typing import List, Optional
import json
from pathlib import Path
from fastapi import APIRouter,Depends,HTTPException,Query,Request,Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel,Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.api.auth import get_current_user
from app.api.accounts import _assert_account_access
from app.db.session import SessionLocal
from app.services import prospect_campaigns as svc
from app.services import client_mailboxes as mailboxes
from app.services import email_tracking as open_tracking
from app.services.exception_observability import observe_suppressed

router=APIRouter(prefix="/api/prospecting/scale",tags=["prospecting-scale"])
public_router=APIRouter(prefix="/api/prospecting/scale",tags=["prospecting-scale-public"])
def get_db():
 db=SessionLocal()
 try: yield db
 finally: db.close()
def uid(user):
 v=getattr(user,"id",None) or (user.get("id") if isinstance(user,dict) else None) or (user.get("user_id") if isinstance(user,dict) else None)
 if v is None: raise HTTPException(401,"user id unavailable")
 return int(v)

def account_scope(db:Session,user,account_id:Optional[str])->str:
 a=(account_id or getattr(user,"account_id",None) or "").strip()
 if not a: raise HTTPException(400,"account_id required")
 if a=="__owner_outreach__":
  if getattr(user,"role","")!="owner": raise HTTPException(403,"owner outreach access denied")
  return a
 _assert_account_access(db,a,user)
 return a


def current_copy_decision(current:dict|None, history:list|None)->dict:
 clean=dict(current or {})
 sent=int(clean.get("sent") or 0)
 positive=int(clean.get("positive_replied") or 0)
 opt_out=int(clean.get("opt_out") or 0)
 sample_target=20
 if sent < sample_target:
  return {
   "state":"collecting","sent":sent,"positive_replies":positive,"opt_outs":opt_out,
   "sample_target":sample_target,"sample_remaining":max(0,sample_target-sent),
   "owner_action_required":False,"auto_action":"none",
   "recommendation":f"Новый текст не трогаем: набираем минимум {sample_target} отправок. Осталось {max(0,sample_target-sent)}."
  }
 positive_rate=(positive/sent if sent else 0.0)
 opt_out_rate=(opt_out/sent if sent else 0.0)
 previous=[x for x in (history or []) if str((x or {}).get("version") or "") not in {"",str(clean.get("version") or "")}]
 previous_sent=sum(int((x or {}).get("sent") or 0) for x in previous)
 previous_positive=sum(int((x or {}).get("positive_replied") or 0) for x in previous)
 previous_rate=(previous_positive/previous_sent if previous_sent else 0.0)
 if positive>=2 and (previous_rate<=0 or positive_rate>=previous_rate*1.25):
  return {
   "state":"promising","sent":sent,"positive_replies":positive,"opt_outs":opt_out,
   "positive_rate_pct":round(positive_rate*100,1),"opt_out_rate_pct":round(opt_out_rate*100,1),
   "previous_sent":previous_sent,"previous_positive_replies":previous_positive,
   "owner_action_required":False,"auto_action":"keep_current",
   "recommendation":"Новый текст показывает полезные ответы. BORIS сохраняет его и продолжает набор статистики."
  }
 if sent>=30 and positive==0:
  return {
   "state":"needs_revision","sent":sent,"positive_replies":0,"opt_outs":opt_out,
   "positive_rate_pct":0.0,"opt_out_rate_pct":round(opt_out_rate*100,1),
   "previous_sent":previous_sent,"previous_positive_replies":previous_positive,
   "owner_action_required":False,"auto_action":"keep_current_until_safe_replacement",
   "recommendation":"После 30 отправок полезных ответов нет. BORIS помечает текст на замену, но не останавливает кампанию и не включает непроверенный текст автоматически."
  }
 return {
  "state":"observing","sent":sent,"positive_replies":positive,"opt_outs":opt_out,
  "positive_rate_pct":round(positive_rate*100,1),"opt_out_rate_pct":round(opt_out_rate*100,1),
  "previous_sent":previous_sent,"previous_positive_replies":previous_positive,
  "owner_action_required":False,"auto_action":"keep_collecting",
  "recommendation":"Выборка уже есть, но данных для уверенного решения мало. BORIS продолжает наблюдение без смены текста."
 }


def ab_decision(stats)->dict:
 clean=[]
 for raw in (stats or []):
  try:
   variant=str(raw.get("variant") or "")
   if variant not in {"A","B","C","D","E"}: continue
   sent=int(raw.get("sent") or 0)
   positive=int(raw.get("positive_replied") or 0)
   opt_out=int(raw.get("opt_out") or 0)
   clean.append({"variant":variant,"sent":sent,"positive":positive,"opt_out":opt_out,
                 "positive_rate":(positive/sent if sent else 0.0),
                 "opt_out_rate":(opt_out/sent if sent else 0.0)})
  except Exception:
   continue
 clean.sort(key=lambda x:x["variant"])
 total_sent=sum(x["sent"] for x in clean)
 total_positive=sum(x["positive"] for x in clean)
 total_opt_out=sum(x["opt_out"] for x in clean)
 active=[x for x in clean if x["sent"]>0]
 min_sent=min((x["sent"] for x in active),default=0)
 base={"total_sent":total_sent,"positive_replies":total_positive,"opt_outs":total_opt_out,
       "min_sent_per_variant":min_sent,"leader":None,"weak_variants":[],"auto_action":"none"}
 if len(active)<2:
  return {**base,"state":"collecting","recommendation":"Работаем текущим утверждённым письмом. A/B включим только после появления второго утверждённого варианта; действие владельца не требуется."}
 if min_sent<20 or total_sent<20*len(active):
  return {**base,"state":"collecting","recommendation":f"Набираем данные: нужно минимум 20 отправок на каждый активный вариант. Сейчас минимум {min_sent}."}
 ranked=sorted(active,key=lambda x:(x["positive_rate"],-x["opt_out_rate"],x["positive"]),reverse=True)
 leader=ranked[0]; runner=ranked[1]
 base["leader"]=leader["variant"]
 base["leader_rate"]=round(leader["positive_rate"]*100,1)
 base["runner_rate"]=round(runner["positive_rate"]*100,1)
 base["leader_opt_out_rate"]=round(leader["opt_out_rate"]*100,1)
 if leader["positive"]<3:
  return {**base,"state":"collecting","recommendation":"Выборка по отправкам уже есть, но полезных ответов пока мало. Продолжаем равномерный тест."}
 absolute_gap=leader["positive_rate"]-runner["positive_rate"]
 relative_gap=(leader["positive_rate"]/runner["positive_rate"]) if runner["positive_rate"]>0 else (99.0 if leader["positive_rate"]>0 else 1.0)
 weak=[x["variant"] for x in active
       if x["variant"]!=leader["variant"] and x["sent"]>=20
       and leader["positive_rate"]>0
       and x["positive_rate"]<=leader["positive_rate"]*0.5
       and (leader["positive_rate"]-x["positive_rate"])>=0.015]
 base["weak_variants"]=weak
 if absolute_gap>=0.015 and relative_gap>=1.5:
  return {**base,"state":"leader","recommendation":f"Лидер {leader['variant']}: {leader['positive_rate']*100:.1f}% полезных ответов. Пока сохраняем тест и набираем подтверждение; слабые варианты: {', '.join(weak) if weak else 'не выявлены'}."}
 return {**base,"state":"close","recommendation":"Варианты идут близко. Не перераспределяем трафик до более уверенного разрыва."}

def campaign_scope(db:Session,user,campaign_id:int):
 row=db.execute(text("SELECT owner_id,account_id FROM prospect_campaigns WHERE id=:c"),{"c":campaign_id}).mappings().first()
 if not row: raise HTTPException(404,"campaign not found")
 a=account_scope(db,user,row.get("account_id"))
 return int(row["owner_id"]),a

def mailbox_scope(db:Session,user,mailbox_id:int)->str:
 row=db.execute(text("SELECT account_id FROM client_mailboxes WHERE id=:i"),{"i":mailbox_id}).mappings().first()
 if not row: raise HTTPException(404,"mailbox not found")
 return account_scope(db,user,row.get("account_id"))
class DiscoverBody(BaseModel):
 niche:str=Field(min_length=2,max_length=300); regions:List[str]=Field(default_factory=lambda:["Россия"],max_length=100); target:int=Field(default=100,ge=1,le=1000)
class ParseBody(BaseModel): company_ids:List[int]=Field(min_length=1,max_length=1000); max_pages:int=Field(default=8,ge=1,le=20); max_companies:int=Field(default=100,ge=1,le=500)
class AbVariantBody(BaseModel):
 label:str=Field(min_length=1,max_length=8)
 subject:str=Field(min_length=2,max_length=500)
 body:str=Field(min_length=20,max_length=20000)
 banner_path:Optional[str]=Field(default=None,max_length=1000)

class CampaignBody(BaseModel):
 name:str=Field(min_length=2,max_length=300); niche:str=Field(min_length=2,max_length=300); regions:List[str]=Field(default_factory=list,max_length=100)
 subject_template:str=Field(min_length=2,max_length=500); body_template:str=Field(min_length=20,max_length=20000); ab_variants:List[AbVariantBody]=Field(default_factory=list,max_length=5); attachment_path:Optional[str]=None
 daily_limit:int=Field(default=50,ge=1,le=1000); per_domain_daily_limit:int=Field(default=1,ge=1,le=10); min_quality_score:int=Field(default=55,ge=0,le=100); account_id:Optional[str]=None; mailbox_id:Optional[int]=None
class StatusBody(BaseModel): status:str
class SuppressBody(BaseModel): kind:str="email"; value:str; reason:str="opt_out"
class ReplyBody(BaseModel): member_id:int; status:str

@public_router.get("/open/{token}.gif",include_in_schema=False)
def open_pixel(token:str,request:Request):
 try:
  open_tracking.record_open(token,request.headers.get("user-agent",""))
 except Exception as _suppressed_exc:
  # Pixel must remain non-blocking for the recipient even if analytics storage
  # has a temporary problem.
  observe_suppressed(__name__, _suppressed_exc, line=156)
 return Response(
  content=open_tracking.PIXEL_GIF,
  media_type="image/gif",
  headers={
   "Cache-Control":"no-store, no-cache, must-revalidate, max-age=0",
   "Pragma":"no-cache",
   "Expires":"0",
   "X-Robots-Tag":"noindex, nofollow, noarchive",
  },
 )

@public_router.get("/click/{token}",include_in_schema=False)
def email_click(token:str,request:Request):
 try:
  target=open_tracking.record_click(token,request.headers.get("user-agent",""))
 except Exception as _suppressed_exc:
  observe_suppressed(__name__,_suppressed_exc,line=172)
  target=None
 return RedirectResponse(
  url=target or "https://boris-ai.pro/",
  status_code=302,
  headers={
   "Cache-Control":"no-store, no-cache, must-revalidate, max-age=0",
   "Pragma":"no-cache",
   "X-Robots-Tag":"noindex, nofollow, noarchive",
  },
 )

@router.get("/health")
def health(user=Depends(get_current_user)):
 return {"ok":True,"universal_niches":True,"bulk_discovery":True,"quality_gate":True,"suppression":True,"daily_throttle":True,"per_domain_throttle":True,"canonical_email_queue":True,"mandatory_open_tracking":True,"open_reply_funnel":True,"second_scheduler":False}
@router.post("/discover")
def discover(body:DiscoverBody,user=Depends(get_current_user)):
 try:return {"ok":True,**svc.discover_niche(uid(user),body.niche,body.regions,body.target)}
 except Exception as e: raise HTTPException(502,f"{type(e).__name__}: {e}")
@router.post("/parse")
def parse(body:ParseBody,user=Depends(get_current_user)):
 return {"ok":True,**svc.parse_batch(uid(user),body.company_ids,body.max_pages,body.max_companies)}
@router.post("/campaigns")
def create_campaign(body:CampaignBody,db:Session=Depends(get_db),user=Depends(get_current_user)):
 try:
  a=account_scope(db,user,body.account_id)
  payload=body.model_dump(); payload["account_id"]=a
  cid=svc.create_campaign(uid(user),**payload); return {"ok":True,"campaign_id":cid}
 except HTTPException: raise
 except Exception as e: raise HTTPException(400,str(e))
@router.get("/campaigns")
def campaigns(account_id:str=Query(...),db:Session=Depends(get_db),user=Depends(get_current_user),limit:int=Query(100,ge=1,le=500)):
 svc.ensure_schema(db)
 a=account_scope(db,user,account_id)
 owner_rows=db.execute(text("SELECT DISTINCT owner_id FROM prospect_campaigns WHERE account_id=:a"),{"a":a}).all()
 owner=int(owner_rows[0][0]) if owner_rows else uid(user)
 daily=svc._owner_daily_state(db,owner)
 _,gate=svc._owner_allowance(db,owner,1000)
 rows=db.execute(text("""SELECT c.id,c.name,c.niche,c.regions,c.status,c.daily_limit,c.per_domain_daily_limit,
   c.min_quality_score,c.account_id,c.mailbox_id,c.subject_template,c.body_template,c.ab_variants,c.created_at,c.activated_at,
   c.copy_revision_status,c.copy_revision_version,c.copy_revision_needed_at,
   count(m.id) FILTER (WHERE m.status='ready') ready_count,
   count(m.id) FILTER (WHERE timezone(:tz,m.sent_at)::date=:d) sent_today,
   count(m.id) FILTER (WHERE timezone(:tz,m.queued_at)::date=:d) queued_today,
   (SELECT cm.last_checked_at FROM client_mailboxes cm WHERE cm.id=c.mailbox_id) mailbox_last_checked_at,
   (SELECT cm.last_error FROM client_mailboxes cm WHERE cm.id=c.mailbox_id) mailbox_last_error,
   (SELECT count(*) FROM email_queue q JOIN prospect_campaign_members qm ON qm.email_queue_id=q.id WHERE qm.campaign_id=c.id AND q.status='delivery_unknown') delivery_unknown_count,
   (SELECT count(*) FROM email_queue q JOIN prospect_campaign_members qm ON qm.email_queue_id=q.id WHERE qm.campaign_id=c.id AND q.status='queued') queue_pending_count,
   (SELECT count(*) FROM email_queue q JOIN prospect_campaign_members qm ON qm.email_queue_id=q.id WHERE qm.campaign_id=c.id AND q.status IN ('queued','sending') AND q.created_at<now()-interval '30 minutes') queue_stuck_count,
   (SELECT coalesce(max(q.attempts),0) FROM email_queue q JOIN prospect_campaign_members qm ON qm.email_queue_id=q.id WHERE qm.campaign_id=c.id AND q.status='queued' AND q.last_error LIKE 'SMTPAuthenticationError%') auth_backoff_max_attempts,
   (SELECT count(*) FROM email_queue q JOIN prospect_campaign_members qm ON qm.email_queue_id=q.id WHERE qm.campaign_id=c.id AND q.status='dead' AND timezone(:tz,q.updated_at)::date=:d) dead_today,
   (SELECT count(*) FROM prospect_inbound_replies pr WHERE pr.campaign_id=c.id AND COALESCE(pr.message_kind,'human')='human' AND timezone(:tz,pr.received_at)::date=:d) replies_today,
   (SELECT count(*) FROM prospect_reply_alerts pa JOIN prospect_inbound_replies pr2 ON pr2.id=pa.reply_id WHERE pr2.campaign_id=c.id AND pa.status IN ('pending','sending','pending_email_fallback','queued_email','queued_email_fallback')) reply_alert_pending_count,
   (SELECT count(*) FROM prospect_reply_alerts pa JOIN prospect_inbound_replies pr2 ON pr2.id=pa.reply_id WHERE pr2.campaign_id=c.id AND pa.status='delivery_unknown') reply_alert_unknown_count,
   (SELECT count(*) FROM prospect_reply_alerts pa
      JOIN prospect_inbound_replies pr2 ON pr2.id=pa.reply_id
      LEFT JOIN email_queue aq ON aq.id=pa.email_queue_id
      WHERE pr2.campaign_id=c.id AND (
        (pa.status IN ('pending','sending','pending_email_fallback') AND COALESCE(pa.attempted_at,pa.created_at)<now()-interval '10 minutes')
        OR (pa.status IN ('queued_email','queued_email_fallback') AND aq.status IN ('queued','sending') AND aq.updated_at<now()-interval '10 minutes')
      )) reply_alert_stuck_count,
   (SELECT count(*) FROM email_queue q2
      JOIN prospect_inbound_replies pr3 ON q2.idempotency_key IN (
        'prospect_reply_alert:'||pr3.id::text,
        'prospect_reply_alert_fallback:'||pr3.id::text
      )
      WHERE pr3.campaign_id=c.id AND q2.status='dead') reply_alert_email_dead_count,
   (SELECT COALESCE(jsonb_agg(jsonb_build_object(
      'variant',s.variant,'total',s.total,'queued',s.queued,'sent',s.sent,
      'replied',s.replied,'positive_replied',s.positive_replied,'opt_out',s.opt_out
    ) ORDER BY s.variant),'[]'::jsonb)
    FROM (
      SELECT COALESCE(am.ab_variant,'—') variant,
        count(*)::int total,
        count(*) FILTER (WHERE am.queued_at IS NOT NULL)::int queued,
        count(*) FILTER (WHERE am.sent_at IS NOT NULL)::int sent,
        count(*) FILTER (WHERE am.reply_status IN ('replied','opt_out'))::int replied,
        count(*) FILTER (WHERE am.reply_status='replied' AND EXISTS(SELECT 1 FROM prospect_inbound_replies ar JOIN manager_leads al ON al.id=ar.crm_lead_id WHERE ar.member_id=am.id AND COALESCE(ar.message_kind,'human')='human' AND al.status IN ('квалифицирован','целевое действие','пробный','оплатил','сделка')))::int positive_replied,
        count(*) FILTER (WHERE am.reply_status='opt_out')::int opt_out
      FROM prospect_campaign_members am
      WHERE am.campaign_id=c.id
      GROUP BY COALESCE(am.ab_variant,'—')
    ) s) ab_stats,
   (SELECT COALESCE(jsonb_agg(jsonb_build_object(
      'version',cs.version,'total',cs.total,'queued',cs.queued,'sent',cs.sent,
      'replied',cs.replied,'positive_replied',cs.positive_replied,'opt_out',cs.opt_out
    ) ORDER BY cs.version),'[]'::jsonb)
    FROM (
      SELECT COALESCE(cm.copy_version,'legacy') version,
        count(*)::int total,
        count(*) FILTER (WHERE cm.queued_at IS NOT NULL)::int queued,
        count(*) FILTER (WHERE cm.sent_at IS NOT NULL)::int sent,
        count(*) FILTER (WHERE cm.reply_status IN ('replied','opt_out'))::int replied,
        count(*) FILTER (WHERE cm.reply_status='replied' AND EXISTS(SELECT 1 FROM prospect_inbound_replies cr JOIN manager_leads cl ON cl.id=cr.crm_lead_id WHERE cr.member_id=cm.id AND COALESCE(cr.message_kind,'human')='human' AND cl.status IN ('квалифицирован','целевое действие','пробный','оплатил','сделка')))::int positive_replied,
        count(*) FILTER (WHERE cm.reply_status='opt_out')::int opt_out
      FROM prospect_campaign_members cm
      WHERE cm.campaign_id=c.id
      GROUP BY COALESCE(cm.copy_version,'legacy')
    ) cs) copy_stats
   FROM prospect_campaigns c LEFT JOIN prospect_campaign_members m ON m.campaign_id=c.id
   WHERE c.account_id=:a GROUP BY c.id ORDER BY c.id DESC LIMIT :l"""),
   {"a":a,"l":limit,"tz":daily["timezone"],"d":daily["today"]}).mappings().all()
 items=[]
 for row in rows:
  item=dict(row)
  delivery_unknown=int(item.pop("delivery_unknown_count") or 0)
  queue_pending=int(item.pop("queue_pending_count") or 0)
  queue_stuck=int(item.pop("queue_stuck_count") or 0)
  auth_attempts=int(item.pop("auth_backoff_max_attempts") or 0)
  dead_today=int(item.pop("dead_today") or 0)
  replies_today=int(item.pop("replies_today") or 0)
  reply_alert_pending=int(item.pop("reply_alert_pending_count") or 0)
  reply_alert_unknown=int(item.pop("reply_alert_unknown_count") or 0)
  reply_alert_stuck=int(item.pop("reply_alert_stuck_count") or 0)
  reply_alert_email_dead=int(item.pop("reply_alert_email_dead_count") or 0)
  mailbox_error=str(item.pop("mailbox_last_error") or "").strip()
  mailbox_checked=item.pop("mailbox_last_checked_at")
  deliverability=svc.campaign_deliverability(db,int(item["id"]))
  item["tracking"]=open_tracking.campaign_tracking_stats(db,int(item["id"]),include_subjects=True)
  tracking_gap=int(item["tracking"].get("untracked_sent_after_policy") or 0)
  owner_action=bool(delivery_unknown>0 or reply_alert_unknown>0 or reply_alert_email_dead>0 or auth_attempts>=3 or tracking_gap>0 or deliverability.get("owner_action_required"))
  recovering=bool(mailbox_error or auth_attempts>0 or queue_stuck>0 or reply_alert_stuck>0 or deliverability.get("state")=="throttled")
  health_state="needs_owner" if owner_action else ("recovering" if recovering else "ok")
  item["health"]={"state":health_state,"owner_action_required":owner_action,
    "mailbox_last_checked_at":mailbox_checked,"mailbox_error":mailbox_error or None,
    "delivery_unknown":delivery_unknown,"queue_pending":queue_pending,"queue_stuck":queue_stuck,"auth_backoff_max_attempts":auth_attempts,
    "dead_today":dead_today,"replies_today":replies_today,"reply_alert_pending":reply_alert_pending,
    "reply_alert_delivery_unknown":reply_alert_unknown,"reply_alert_stuck":reply_alert_stuck,
    "reply_alert_email_dead":reply_alert_email_dead,"tracking_untracked_after_policy":tracking_gap,"deliverability":deliverability,
    "self_heal":("automatic_sender_reputation_throttle" if deliverability.get("state")=="throttled" else
      ("manual_deliverability_review_required" if deliverability.get("state")=="blocked" else
       ("automatic_retry_mailbox_and_reply_alert_dispatch" if health_state=="recovering" else
        ("manual_reconciliation_required" if owner_action else "not_needed"))))}
  item["today"]={"sent":int(item.pop("sent_today") or 0),"queued_or_sent":int(item.pop("queued_today") or 0),
    "effective_daily_cap":int(daily["cap"]),"owner_used":int(daily["used"]),"remaining":int(daily["remaining"]),
    "ramp_day":int(daily["age"])+1,"gate":gate,"timezone":daily["timezone"],
    "next_cap":daily.get("next_cap"),"next_change_date":daily.get("next_change_date").isoformat() if daily.get("next_change_date") else None}
  current_copy_version=svc._copy_version(item.get("subject_template") or "",item.get("body_template") or "","base")
  item["current_copy_version"]=current_copy_version
  copy_stats=list(item.get("copy_stats") or [])
  item["current_copy_stats"]=next(
    (x for x in copy_stats if str((x or {}).get("version") or "")==current_copy_version),
    {"version":current_copy_version,"total":0,"queued":0,"sent":0,"replied":0,"positive_replied":0,"opt_out":0},
  )
  item["current_copy_decision"]=current_copy_decision(item["current_copy_stats"],copy_stats)
  item["copy_revision"]={
    "status":item.get("copy_revision_status"),
    "version":item.get("copy_revision_version"),
    "needed_at":item.get("copy_revision_needed_at"),
    "owner_action_required":False,
  }
  if (
    item["current_copy_decision"].get("state")=="needs_revision"
    and item.get("copy_revision_status")=="pending"
    and item.get("copy_revision_version")==current_copy_version
  ):
    item["current_copy_decision"]["revision_status"]="pending"
    item["current_copy_decision"]["recommendation"]="После 30 отправок квалифицированных ответов нет. BORIS уже поставил эту версию в безопасную доработку; рабочая кампания продолжает отправку текущего проверенного текста до готовности замены."
  item["ab_decision"]=ab_decision(item.get("ab_stats") or [])
  items.append(item)
 return {"items":items}
@router.post("/campaigns/{campaign_id}/audience")
def audience(campaign_id:int,db:Session=Depends(get_db),user=Depends(get_current_user)):
 try:
  owner,_=campaign_scope(db,user,campaign_id)
  return {"ok":True,**svc.build_audience(owner,campaign_id)}
 except ValueError as e: raise HTTPException(404,str(e))
@router.post("/campaigns/{campaign_id}/status")
def status(campaign_id:int,body:StatusBody,db:Session=Depends(get_db),user=Depends(get_current_user)):
 try:
  owner,_=campaign_scope(db,user,campaign_id)
  return {"ok":svc.set_status(owner,campaign_id,body.status,source="api"),"status":body.status}
 except ValueError as e: raise HTTPException(400,str(e))

class CampaignCopyBody(BaseModel):
 subject_template:str=Field(min_length=2,max_length=2000)
 body_template:str=Field(min_length=120,max_length=20000)
 ab_variants:List[AbVariantBody]=Field(default_factory=list,max_length=5)

@router.post("/campaigns/{campaign_id}/copy")
def campaign_copy(campaign_id:int,body:CampaignCopyBody,db:Session=Depends(get_db),user=Depends(get_current_user)):
 owner,account_id=campaign_scope(db,user,campaign_id)
 pending=int(db.execute(text("""SELECT count(*)
   FROM prospect_campaign_members m JOIN email_queue q ON q.id=m.email_queue_id
   WHERE m.campaign_id=:c AND q.status='queued'"""),{"c":campaign_id}).scalar() or 0)
 if pending:
  raise HTTPException(409,f"В очереди уже есть {pending} подготовленных писем. Дождитесь их обработки перед заменой текста.")
 subject=body.subject_template.strip()
 copy=body.body_template.strip()
 variants=[x.model_dump() for x in body.ab_variants[:5]]
 if account_id=="__owner_outreach__":
  errors=svc.validate_owner_outreach_copy_set(subject,copy,variants,campaign_id=campaign_id)
  if errors:
   raise HTTPException(400,"Письмо не сохранено: проверка боевого текста не пройдена ("+", ".join(errors)+")")
 n=db.execute(text("""UPDATE prospect_campaigns
   SET subject_template=:s,body_template=:b,ab_variants=CAST(:ab AS JSONB),
       copy_revision_status=NULL,copy_revision_version=NULL,copy_revision_needed_at=NULL,
       updated_at=NOW()
   WHERE id=:c AND owner_id=:o"""),{"s":subject,"b":copy,"ab":__import__("json").dumps(variants,ensure_ascii=False),"c":campaign_id,"o":owner}).rowcount
 if not n: raise HTTPException(404,"campaign not found")
 db.commit()
 return {"ok":True,"campaign_id":campaign_id,"subject_template":subject,"body_template":copy,"ab_variants":variants,"pending_queued":0}

@router.post("/campaigns/{campaign_id}/tick")
def tick(campaign_id:int,db:Session=Depends(get_db),user=Depends(get_current_user)):
 campaign_scope(db,user,campaign_id)
 return {"ok":True,**svc.tick_campaign(campaign_id,10)}
@router.get("/owner-sla")
def owner_outreach_sla(db:Session=Depends(get_db),user=Depends(get_current_user)):
 if getattr(user,"role","")!="owner": raise HTTPException(403,"owner access denied")
 row=db.execute(text("SELECT value FROM storage WHERE account_id='owner_outreach_sla' AND key='current' ORDER BY id DESC LIMIT 1")).scalar()
 if not row: return {"ok":True,"sla":None}
 try: payload=json.loads(str(row))
 except Exception: payload=None
 return {"ok":True,"sla":payload}


@router.get("/owner-development")
def owner_development_campaign(db:Session=Depends(get_db),user=Depends(get_current_user)):
 if getattr(user,"role","")!="owner": raise HTTPException(403,"owner access denied")
 owner=uid(user)
 row=db.execute(text("""
   SELECT c.id,c.name,c.niche,c.status,c.daily_limit,c.min_quality_score,c.subject_template,c.body_template,c.mailbox_id,
          cm.email_address AS mailbox_email,
          count(m.id) FILTER (WHERE m.status='ready') AS ready_count,
          count(m.id) FILTER (WHERE m.status='queued') AS queued_count,
          count(m.id) FILTER (WHERE m.status='sent') AS sent_total,
          count(m.id) FILTER (WHERE m.sent_at IS NOT NULL AND timezone('Europe/Moscow',m.sent_at)::date=timezone('Europe/Moscow',now())::date) AS sent_today,
          count(m.id) FILTER (WHERE m.reply_status='replied') AS replied_total,
          count(m.id) FILTER (WHERE m.reply_status='opt_out') AS opt_out_total
   FROM prospect_campaigns c
   LEFT JOIN prospect_campaign_members m ON m.campaign_id=c.id
   LEFT JOIN client_mailboxes cm ON cm.id=c.mailbox_id
   WHERE c.owner_id=:o AND c.name='Кирилл · разработка SaaS/App'
   GROUP BY c.id,cm.email_address
   ORDER BY c.id DESC LIMIT 1
 """),{"o":owner}).mappings().first()
 if not row: return {"ok":True,"campaign":None}
 shortlist=db.execute(text("""
   SELECT count(*) AS total,
          count(*) FILTER (WHERE outreach_status='company_fit_rejected') AS rejected,
          count(*) FILTER (WHERE contact_email IS NOT NULL) AS with_email,
          count(*) FILTER (WHERE brief_json->>'outreach_gate'='pass') AS evidence_ready,
          count(*) FILTER (WHERE contact_email IS NOT NULL AND COALESCE(brief_json->>'outreach_gate','blocked')<>'pass') AS analysis_required,
          COALESCE(round(avg(NULLIF(brief_json->>'pain_score','')::numeric) FILTER (WHERE brief_json ? 'pain_score'),1),0) AS avg_pain_score
   FROM development_lead_shortlist
   WHERE owner_user_id=:o AND service_date=timezone('Europe/Moscow',now())::date
 """),{"o":owner}).mappings().one()
 item=dict(row); item["shortlist"]=dict(shortlist)
 item["tracking"]=open_tracking.campaign_tracking_stats(db,int(item["id"]),include_subjects=True)
 return {"ok":True,"campaign":item}

@router.get("/owner-development/leads")
def owner_development_leads(limit:int=Query(12,ge=1,le=50),db:Session=Depends(get_db),user=Depends(get_current_user)):
 if getattr(user,"role","")!="owner": raise HTTPException(403,"owner access denied")
 owner=uid(user)
 rows=db.execute(text("""
   SELECT s.id,s.rank,s.fit_score,s.contact_email,s.outreach_status,s.brief_json,
          c.name AS company,c.website
   FROM development_lead_shortlist s
   LEFT JOIN prospect_companies c ON c.id=s.company_id
   WHERE s.owner_user_id=:o AND s.service_date=timezone('Europe/Moscow',now())::date
     AND s.company_id>0
   ORDER BY CASE WHEN s.brief_json->>'outreach_gate'='pass' THEN 0 ELSE 1 END,
            COALESCE(NULLIF(s.brief_json->>'pain_score','')::int,0) DESC,s.rank
   LIMIT :lim
 """),{"o":owner,"lim":int(limit)}).mappings().all()
 return {"ok":True,"items":[dict(r) for r in rows]}

@router.get("/campaigns/{campaign_id}")
def stats(campaign_id:int,db:Session=Depends(get_db),user=Depends(get_current_user)):
 try:
  owner,_=campaign_scope(db,user,campaign_id)
  data=svc.campaign_stats(owner,campaign_id)
  data["tracking"]=open_tracking.campaign_tracking_stats(db,campaign_id,include_subjects=True)
  return {"ok":True,**data}
 except ValueError as e: raise HTTPException(404,str(e))
@router.post("/suppress")
def suppress(body:SuppressBody,user=Depends(get_current_user)):
 if body.kind not in {"email","phone"}: raise HTTPException(400,"kind must be email or phone")
 try:return {"ok":True,"normalized":svc.suppress(body.kind,body.value,body.reason)}
 except ValueError as e: raise HTTPException(400,str(e))
@router.post("/reply")
def reply(body:ReplyBody,db:Session=Depends(get_db),user=Depends(get_current_user)):
 if body.status not in {"replied","interested","not_interested","meeting","won","lost"}: raise HTTPException(400,"bad reply status")
 svc.ensure_schema(db)
 row=db.execute(text("SELECT c.id campaign_id,c.account_id FROM prospect_campaign_members m JOIN prospect_campaigns c ON c.id=m.campaign_id WHERE m.id=:m"),{"m":body.member_id}).mappings().first()
 if not row: raise HTTPException(404,"member not found")
 account_scope(db,user,row["account_id"])
 n=db.execute(text("UPDATE prospect_campaign_members SET reply_status=:s,replied_at=NOW(),updated_at=NOW() WHERE id=:m"),{"s":body.status,"m":body.member_id}).rowcount
 db.commit(); return {"ok":bool(n)}
@router.get("/campaigns/{campaign_id}/members")
def members(campaign_id:int,db:Session=Depends(get_db),user=Depends(get_current_user),limit:int=Query(200,ge=1,le=2000),offset:int=Query(0,ge=0)):
 svc.ensure_schema(db); open_tracking.ensure_schema(); campaign_scope(db,user,campaign_id)
 rows=db.execute(text("""SELECT m.id,c.name company,c.city,c.website,m.email,m.quality_score,m.status,m.skip_reason,m.email_queue_id,m.sent_at,m.reply_status,
   t.first_opened_at,t.last_opened_at,COALESCE(t.open_count,0) open_count,COALESCE(t.proxy_hint_count,0) proxy_hint_count
   FROM prospect_campaign_members m
   JOIN prospect_companies c ON c.id=m.company_id
   LEFT JOIN email_open_trackers t ON t.email_queue_id=m.email_queue_id
   WHERE m.campaign_id=:cid ORDER BY m.id LIMIT :l OFFSET :off"""),{"cid":campaign_id,"l":limit,"off":offset}).mappings().all()
 return {"items":[dict(r) for r in rows]}

class MailboxBody(BaseModel):
 account_id:str; email_address:str; display_name:Optional[str]=None; smtp_host:str; smtp_port:int=465; smtp_ssl:bool=True; imap_host:str; imap_port:int=993; imap_ssl:bool=True; username:str; password:str

@router.post("/mailboxes")
def mailbox_save(body:MailboxBody,db:Session=Depends(get_db),user=Depends(get_current_user)):
 try:
  data=body.model_dump(); data["account_id"]=account_scope(db,user,body.account_id)
  mid=mailboxes.save_mailbox(uid(user),**data); test=mailboxes.test_mailbox(mid); return {"ok":bool(test.get("smtp") and test.get("imap")),"mailbox_id":mid,"test":test}
 except HTTPException: raise
 except Exception as e: raise HTTPException(400,str(e))

class DraftTestBody(BaseModel):
 subject:str=Field(default="А если заявки уже теряются?",max_length=500)
 body_template:Optional[str]=Field(default=None,max_length=20000)
 company:Optional[str]=Field(default=None,max_length=300)
 niche:Optional[str]=Field(default=None,max_length=300)
 region:Optional[str]=Field(default=None,max_length=300)

@router.post("/mailboxes/{mailbox_id}/test-draft")
def mailbox_test_draft(mailbox_id:int,body:DraftTestBody,db:Session=Depends(get_db),user=Depends(get_current_user)):
 mailbox_scope(db,user,mailbox_id)
 row=db.execute(text("SELECT email_address FROM client_mailboxes WHERE id=:i"),{"i":mailbox_id}).first()
 if not row: raise HTTPException(404,"mailbox not found")
 from app.services.prospect_banner import render_banner
 import html as htmlmod
 niche=(body.niche or "Доставка сыпучих материалов").strip()
 region=(body.region or "Москва и Московская область").strip()
 company=(body.company or "СтройПроект").strip()
 ctx={"company":company,"niche":niche,"city":region,"website":""}
 subject=svc._render_subject(body.subject,ctx,mailbox_id) or f"Сыпучие материалы с доставкой по {region} — быстрый расчёт"
 if body.body_template and body.body_template.strip():
  text_body=svc._render(body.body_template,ctx).strip()
 else:
  text_body=(
   f"Добрый день, команда {company}!\n\n"
   f"🚚 Доставляем песок, щебень, ПГС и другие сыпучие материалы по {region}.\n\n"
   "📦 Подберём материал и объём под вашу задачу.\n\n"
   "🧱 Поможем с поставкой для фундамента, бетона, благоустройства, дорог и отсыпки.\n\n"
   "⏱ Быстро рассчитаем стоимость вместе с доставкой до объекта.\n\n"
   "💰 Сразу покажем понятный расчёт без долгой переписки.\n\n"
   "✅ Согласуем материал, объём, адрес и удобное время поставки.\n\n"
   "Если сейчас планируете закупку, отправьте в ответ адрес доставки и примерный объём.\n\n"
   "Можно просто ответить: «Нужен расчёт».\n\n"
   "С уважением,\nОтдел поставок."
  )
 low_niche=niche.lower()
 materials_demo=any(x in low_niche for x in ("сыпуч","щеб","песок","пгс","неруд"))
 static_materials=Path("/root/BORIS/frontend/public/reviews/materials.jpg")
 if materials_demo and static_materials.is_file():
  banner=str(static_materials); banner_filename="materials.jpg"; banner_mime="image/jpeg"
 else:
  banner=render_banner(company=company,niche=niche,city=region,campaign_id=0,member_id=mailbox_id)
  banner_filename="banner.png"; banner_mime="image/png"
 cid=f"draft-banner-{mailbox_id}"
 paragraphs="".join(
  f"<p style='margin:0 0 15px;font-size:16px;line-height:1.65;color:#2f3e55'>{htmlmod.escape(line.strip())}</p>"
  for line in text_body.splitlines() if line.strip()
 )
 banner_head=(
  "<div style='padding:22px 28px 20px;background:linear-gradient(135deg,#244d9d,#6157d9);color:#fff'>"
  "<div style='font-size:11px;font-weight:700;letter-spacing:.08em;opacity:.86'>ПОСТАВКА НА ОБЪЕКТ</div>"
  "<div style='margin-top:7px;font-size:28px;font-weight:800;line-height:1.1'>Песок · Щебень · ПГС</div>"
  "<div style='margin-top:7px;font-size:14px;line-height:1.45;opacity:.9'>Расчёт стоимости доставки по городу и области</div>"
  "</div>"
 ) if materials_demo else ""
 html=(
  "<html><body style='margin:0;background:#f3f6fb;font-family:Arial,sans-serif'>"
  "<div style='max-width:720px;margin:24px auto;background:#fff;border:1px solid #dfe7f2;border-radius:18px;overflow:hidden'>"
  + banner_head +
  f"<img src='cid:{cid}' style='display:block;width:100%;max-height:330px;object-fit:cover'>"
  f"<div style='padding:30px 32px'>{paragraphs}"
  "<div style='margin-top:22px;padding:18px 20px;border-radius:14px;background:#f2f6ff;border:1px solid #dbe6ff'>"
  "<div style='font-size:18px;font-weight:800;color:#284d9a'>Нужен расчёт?</div>"
  "<div style='margin-top:6px;font-size:14px;line-height:1.5;color:#53637a'>Ответьте на письмо адресом доставки и примерным объёмом.</div>"
  "</div></div></div></body></html>"
 )
 ok,reason,mid=mailboxes.save_draft(mailbox_id,row[0],subject,text_body,html=html,attachments=[{"path":banner,"filename":banner_filename,"mime":banner_mime,"inline":True,"cid":cid}])
 return {"ok":ok,"reason":reason,"message_id":mid,"folder":"Черновики" if ok else None,"subject_preview":subject}

@router.get("/mailboxes")
def mailbox_list(account_id:str=Query(...),db:Session=Depends(get_db),user=Depends(get_current_user)):
 a=account_scope(db,user,account_id)
 mailboxes.ensure_schema(db); rows=db.execute(text("SELECT id,account_id,email_address,display_name,smtp_host,smtp_port,imap_host,imap_port,status,last_checked_at,last_error,created_at FROM client_mailboxes WHERE account_id=:a ORDER BY id DESC"),{"a":a}).mappings().all(); return {"items":[dict(x) for x in rows]}

@router.post("/mailboxes/{mailbox_id}/test")
def mailbox_test(mailbox_id:int,db:Session=Depends(get_db),user=Depends(get_current_user)):
 mailbox_scope(db,user,mailbox_id)
 return {"ok":True,"test":mailboxes.test_mailbox(mailbox_id)}

@router.post("/mailboxes/{mailbox_id}/poll")
def mailbox_poll(mailbox_id:int,db:Session=Depends(get_db),user=Depends(get_current_user)):
 mailbox_scope(db,user,mailbox_id)
 return {"ok":True,**mailboxes.poll_mailbox(mailbox_id)}

@router.get("/replies")
def replies(account_id:str=Query(...),db:Session=Depends(get_db),user=Depends(get_current_user),limit:int=Query(100,ge=1,le=1000)):
 a=account_scope(db,user,account_id)
 mailboxes.ensure_schema(db); rows=db.execute(text("""SELECT r.id,r.from_email,r.subject,r.body_preview,r.crm_lead_id,r.received_at,r.created_at,
   c.name company,ca.name campaign,ca.account_id,m.reply_status,l.status crm_status,
   pa.status notification_status,pa.error notification_error,pa.attempted_at notification_attempted_at,
   pa.email_queue_id notification_email_queue_id
   FROM prospect_inbound_replies r JOIN prospect_campaigns ca ON ca.id=r.campaign_id
   LEFT JOIN prospect_campaign_members m ON m.id=r.member_id
   LEFT JOIN prospect_companies c ON c.id=m.company_id
   LEFT JOIN manager_leads l ON l.id=r.crm_lead_id
   LEFT JOIN prospect_reply_alerts pa ON pa.reply_id=r.id
   WHERE ca.account_id=:a AND COALESCE(r.message_kind,'human')='human'
   ORDER BY r.id DESC LIMIT :l"""),{"a":a,"l":limit}).mappings().all(); return {"items":[dict(x) for x in rows]}
