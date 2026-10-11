#!/usr/bin/env python3
from __future__ import annotations
import json, os, math, subprocess
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import text
from app.db.session import SessionLocal

OWNER_ID=int(os.getenv("PROSPECT_AUTOSTART_OWNER_ID","2") or 2)
TZ_NAME=os.getenv("PROSPECT_TIMEZONE","Europe/Moscow")
START=int(os.getenv("PROSPECT_SEND_START_HOUR","9") or 9)
END=int(os.getenv("PROSPECT_SEND_END_HOUR","21") or 21)
PACING=max(1,int(os.getenv("PROSPECT_MIN_SEND_INTERVAL_MINUTES","8") or 8))
CAMPAIGNS=(9,22)
CHECKPOINTS={(12,0):"1200",(15,0):"1500",(18,0):"1800",(20,0):"2000",(20,45):"2045"}

def put_storage(db,key:str,raw:str)->None:
    row=db.execute(text("SELECT id FROM storage WHERE account_id='owner_outreach_sla' AND key=:k ORDER BY id DESC LIMIT 1"),{"k":key}).first()
    if row:
        db.execute(text("UPDATE storage SET value=:v WHERE id=:i"),{"v":raw,"i":int(row[0])})
    else:
        db.execute(text("INSERT INTO storage(account_id,key,value) VALUES('owner_outreach_sla',:k,:v)"),{"k":key,"v":raw})

def safe_self_heal(items:list[dict])->list[str]:
    actions=[]
    reasons={int(x["campaign_id"]):str(x.get("reason") or "") for x in items}
    if any(v in {"NO_READY_CONTACTS","QUEUE_STUCK","DEADLINE_AT_RISK","PACING"} for v in reasons.values()):
        subprocess.run(["systemctl","start","--no-block","boris-email-queue.service"],check=False,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        actions.append("email_queue_worker_started")
    if reasons.get(22) in {"NO_READY_CONTACTS","DEADLINE_AT_RISK"}:
        subprocess.run(["systemctl","start","--no-block","boris-development-leads-refill.service"],check=False,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        actions.append("development_refill_started")
    return actions

def expected_by_now(target:int, now:datetime)->int:
    minute=now.hour*60+now.minute
    a=START*60; b=END*60
    if minute<a:return 0
    if minute>=b:return target
    span=max(1,b-a)
    return min(target,max(0,math.floor(target*(minute-a)/span)))

def campaign_state(row:dict, now:datetime, aggregate_remaining:int)->dict:
    target=max(0,int(row.get("daily_limit") or 0))
    sent=int(row.get("sent_today") or 0)
    remaining=max(0,target-sent)
    ready=int(row.get("ready") or 0)
    queued=int(row.get("queued") or 0)
    failed=int(row.get("failed_today") or 0)
    bounced=int(row.get("hard_bounced_today") or 0)
    stuck=int(row.get("queue_stuck") or 0)
    mailbox_error=str(row.get("mailbox_error") or "").strip()
    smtp_error=str(row.get("smtp_error") or "").strip()
    imap_error=str(row.get("imap_error") or "").strip()
    status=str(row.get("status") or "")
    status_reason=str(row.get("status_reason") or "")
    owner_cap=int(row.get("owner_daily_cap") or 30)
    owner_used=int(row.get("owner_sent_today") or 0)
    expected=expected_by_now(target,now)
    lag=max(0,expected-sent)
    minute=now.hour*60+now.minute
    minutes_left=max(0,END*60-minute)
    reason="OK"; human="План выполнен."; heal="not_needed"; owner=False
    if remaining:
        status_low=status_reason.lower()
        if status!="active":
            if "copy" in status_low or "content" in status_low:
                reason="CONTENT_CONTRACT"; human=f"Контентный guard остановил кампанию: {status_reason or status}."; heal="restore_approved_copy"; owner=True
            elif "deliver" in status_low or "throttle" in status_low:
                reason="DELIVERABILITY_THROTTLE"; human=f"Доставляемость ограничила темп: {status_reason or status}."; heal="deliverability_recheck"; owner=False
            else:
                reason="CAMPAIGN_PAUSED"; human=f"Кампания не активна: {status_reason or status}."; heal="restore_desired_active_if_safe"; owner=bool(status_reason)
        elif smtp_error and "auth" in smtp_error.lower():
            reason="SMTP_AUTH"; human="SMTP отклонил авторизацию почтового ящика."; heal="mailbox_health_recheck"; owner=True
        elif imap_error and "auth" in imap_error.lower():
            reason="IMAP_AUTH"; human="IMAP отклонил авторизацию почтового ящика."; heal="mailbox_health_recheck"; owner=True
        elif mailbox_error:
            reason="MAILBOX_ERROR"; human="Почтовый ящик не подтвердил рабочее состояние."; heal="mailbox_health_recheck"; owner=True
        elif owner_used>=owner_cap:
            reason="DAILY_CAP"; human=f"Общий дневной лимит {owner_cap} уже исчерпан."; heal="wait_next_service_day"; owner=False
        elif sent>=5 and bounced>=max(2,int(sent*0.2)):
            reason="BOUNCE_RATE"; human=f"Высокая доля жёстких недоставок: {bounced} при {sent} отправках."; heal="suppress_bad_contacts_and_refill"; owner=False
        elif stuck:
            reason="QUEUE_STUCK"; human=f"В очереди зависло писем: {stuck}."; heal="canonical_queue_retry"; owner=False
        elif ready<=0 and queued<=0:
            reason="NO_READY_CONTACTS"; human="Нет готовых адресатов для оставшихся отправок."; heal="audience_refill"; owner=False
        elif minute < START*60:
            reason="OUTSIDE_WINDOW"; human=f"Окно отправки начнётся в {START:02d}:00."; heal="wait_for_window"; owner=False
        elif minute >= END*60:
            reason="WINDOW_CLOSED_SHORTFALL"; human=f"Окно закрыто, недобор {remaining}."; heal="resume_next_service_day"; owner=False
        elif aggregate_remaining*PACING > minutes_left:
            reason="DEADLINE_AT_RISK"; human=f"До закрытия окна {minutes_left} мин, при текущем pacing план уже под риском."; heal="prioritize_owner_outreach"; owner=False
        elif lag>0:
            reason="PACING"; human=f"Отстаём от контрольного темпа на {lag}, но очередь доступна."; heal="scheduler_catch_up"; owner=False
        else:
            reason="ON_TRACK"; human=f"По графику. Осталось {remaining}."; heal="continue_scheduler"; owner=False
    return {
        "campaign_id":int(row["id"]),"name":row["name"],"target_today":target,
        "sent_today":sent,"remaining_today":remaining,"ready":ready,"queued":queued,
        "failed_today":failed,"hard_bounced_today":bounced,"queue_stuck":stuck,
        "expected_by_now":expected,"lag":lag,"minutes_until_window_close":minutes_left,
        "reason":reason,"human_reason":human,"self_heal_action":heal,
        "owner_action_required":owner,
        "status":"PASS" if remaining==0 else ("FAIL" if minute>=END*60 else "WORKING"),
    }

def main()->int:
    now=datetime.now(ZoneInfo(TZ_NAME))
    db=SessionLocal()
    try:
        rows=[dict(r) for r in db.execute(text("""
          SELECT c.id,c.name,c.status,c.status_reason,c.daily_limit,
            count(m.id) FILTER (WHERE m.status='ready')::int ready,
            count(m.id) FILTER (WHERE m.status='queued')::int queued,
            count(m.id) FILTER (
              WHERE m.sent_at IS NOT NULL AND timezone(:tz,m.sent_at)::date=:d
            )::int sent_today,
            count(m.id) FILTER (
              WHERE m.status='failed' AND timezone(:tz,m.updated_at)::date=:d
            )::int failed_today,
            count(m.id) FILTER (
              WHERE (m.reply_status='bounce' OR m.skip_reason IN ('smtp_recipient_refused','async_permanent_bounce'))
                AND timezone(:tz,m.updated_at)::date=:d
            )::int hard_bounced_today,
            (SELECT count(*) FROM email_queue q JOIN prospect_campaign_members qm ON qm.email_queue_id=q.id
              WHERE qm.campaign_id=c.id AND q.status IN ('queued','sending')
                AND q.updated_at<NOW()-interval '30 minutes')::int queue_stuck,
            cm.last_error mailbox_error,
            cm.smtp_last_error smtp_error,
            cm.imap_last_error imap_error,
            COALESCE(od.daily_cap,30)::int owner_daily_cap,
            (SELECT count(*) FROM prospect_campaign_members mm
               JOIN prospect_campaigns cc ON cc.id=mm.campaign_id
              WHERE cc.owner_id=:o
                AND mm.sent_at IS NOT NULL
                AND timezone(:tz,mm.sent_at)::date=:d)::int owner_sent_today
          FROM prospect_campaigns c
          LEFT JOIN prospect_campaign_members m ON m.campaign_id=c.id
          LEFT JOIN client_mailboxes cm ON cm.id=c.mailbox_id
          LEFT JOIN prospect_owner_daily_limits od ON od.owner_id=c.owner_id AND od.service_date=:d
          WHERE c.owner_id=:o AND c.id=ANY(:ids)
          GROUP BY c.id,cm.last_error,cm.smtp_last_error,cm.imap_last_error,od.daily_cap
          ORDER BY c.id
        """),{"o":OWNER_ID,"ids":list(CAMPAIGNS),"tz":TZ_NAME,"d":now.date()}).mappings().all()]
        aggregate_remaining=sum(max(0,int(r.get("daily_limit") or 0)-int(r.get("sent_today") or 0)) for r in rows)
        items=[campaign_state(r,now,aggregate_remaining) for r in rows]
        target=sum(x["target_today"] for x in items); sent=sum(x["sent_today"] for x in items)
        payload={
          "service_date":now.date().isoformat(),"timezone":TZ_NAME,"checked_at":now.isoformat(),
          "window":f"{START:02d}:00–{END:02d}:00","pacing_minutes":PACING,
          "target_today":target,"sent_today":sent,"remaining_today":max(0,target-sent),
          "status":"PASS" if target and sent>=target else ("FAIL" if now.hour>=END else "WORKING"),
          "campaigns":items,
        }
        payload["self_heal_actions"]=safe_self_heal(items)
        raw=json.dumps(payload,ensure_ascii=False,separators=(",",":"))
        put_storage(db,"current",raw)
        cp=CHECKPOINTS.get((now.hour,now.minute))
        if cp:
            put_storage(db,f"checkpoint:{now.date().isoformat()}:{cp}",raw)
        if now.hour>=END:
            put_storage(db,f"final:{now.date().isoformat()}",raw)
        db.commit()
        print("OWNER_OUTREACH_SLA="+payload["status"]+" sent="+str(sent)+"/"+str(target))
        for x in items: print(f"{x['campaign_id']} {x['sent_today']}/{x['target_today']} {x['reason']} owner={int(x['owner_action_required'])}")
        return 0 if payload["status"]!="FAIL" else 2
    finally:
        db.close()

if __name__=="__main__":
    raise SystemExit(main())
