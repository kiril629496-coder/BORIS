from __future__ import annotations
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import text
from app.db.session import SessionLocal

CHAT=os.getenv('PROSPECT_REPORT_TG_CHAT_ID','-1003952038222')
THREAD=int(os.getenv('PROSPECT_REPORT_TG_THREAD_ID','1747') or 1747)
TZ=ZoneInfo(os.getenv('PROSPECT_TIMEZONE','Europe/Moscow'))

def ensure_schema(db):
    ready=db.execute(text("""SELECT
      to_regclass('public.prospect_send_reports') IS NOT NULL
      AND to_regclass('public.prospect_daily_reports') IS NOT NULL
      AND to_regclass('public.prospect_mailbox_action_alerts') IS NOT NULL
    """)).scalar()
    if ready:
        return
    db.execute(text('''CREATE TABLE IF NOT EXISTS prospect_send_reports (email_queue_id BIGINT PRIMARY KEY, reported_at TIMESTAMP NOT NULL DEFAULT NOW())'''))
    db.execute(text('''CREATE TABLE IF NOT EXISTS prospect_daily_reports (report_date DATE PRIMARY KEY, reported_at TIMESTAMP NOT NULL DEFAULT NOW())'''))
    db.execute(text('''CREATE TABLE IF NOT EXISTS prospect_mailbox_action_alerts (
      mailbox_id BIGINT NOT NULL,
      error_kind VARCHAR(64) NOT NULL,
      first_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
      notified_at TIMESTAMP NULL,
      resolved_at TIMESTAMP NULL,
      last_error TEXT NULL,
      updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
      PRIMARY KEY(mailbox_id,error_kind)
    )'''))
    db.commit()

def report_mailbox_action_required()->dict:
    """Send one durable owner alert for non-self-healable mailbox auth failures."""
    from app.services.reliability import email_delivery_health
    from app.telegram_bot import send_telegram_message

    health=email_delivery_health()
    raw_issues=list(health.get('mailbox_action_required') or [])
    grouped={}
    for issue in raw_issues:
        key=(int(issue.get('mailbox_id')),str(issue.get('kind') or 'unknown'))
        grouped.setdefault(key,set()).add(str(issue.get('channel') or 'unknown').upper())

    db=SessionLocal(); ensure_schema(db)
    sent=resolved=pending=0
    try:
        current=set(grouped)
        open_rows=db.execute(text("""SELECT mailbox_id,error_kind
          FROM prospect_mailbox_action_alerts
          WHERE resolved_at IS NULL""")).all()
        for mailbox_id,error_kind in open_rows:
            key=(int(mailbox_id),str(error_kind))
            if key not in current:
                db.execute(text("""UPDATE prospect_mailbox_action_alerts
                  SET resolved_at=NOW(),updated_at=NOW()
                  WHERE mailbox_id=:m AND error_kind=:k AND resolved_at IS NULL"""),
                  {'m':key[0],'k':key[1]})
                resolved+=1

        for (mailbox_id,kind),channels in grouped.items():
            row=db.execute(text("""SELECT email_address,smtp_last_error,imap_last_error
              FROM client_mailboxes WHERE id=:m"""),{'m':mailbox_id}).mappings().first()
            if not row:
                continue
            last_error='; '.join(
                str(x) for x in (row.get('smtp_last_error'),row.get('imap_last_error')) if x
            )[:1000]
            db.execute(text("""INSERT INTO prospect_mailbox_action_alerts
              (mailbox_id,error_kind,last_error,first_seen_at,updated_at)
              VALUES(:m,:k,:e,NOW(),NOW())
              ON CONFLICT(mailbox_id,error_kind) DO UPDATE SET
                notified_at=CASE
                  WHEN prospect_mailbox_action_alerts.resolved_at IS NOT NULL THEN NULL
                  ELSE prospect_mailbox_action_alerts.notified_at END,
                resolved_at=NULL,last_error=EXCLUDED.last_error,updated_at=NOW()"""),
              {'m':mailbox_id,'k':kind,'e':last_error})
        db.commit()

        rows=db.execute(text("""SELECT a.mailbox_id,a.error_kind,m.email_address
          FROM prospect_mailbox_action_alerts a
          JOIN client_mailboxes m ON m.id=a.mailbox_id
          WHERE a.resolved_at IS NULL AND a.notified_at IS NULL
          ORDER BY a.first_seen_at,a.mailbox_id""")).mappings().all()
        pending=len(rows)
        for row in rows:
            key=(int(row['mailbox_id']),str(row['error_kind']))
            channels=' + '.join(sorted(grouped.get(key) or {'SMTP','IMAP'}))
            if row['error_kind']=='application_password_required':
                reason='Mail.ru требует пароль приложения для внешнего доступа.'
                action=('Создайте пароль приложения в настройках безопасности Mail.ru '
                        'и сохраните его в подключении почты BORIS. После сохранения '
                        'BORIS сам сбросит ошибку и перепроверит SMTP/IMAP.')
            else:
                reason='Почтовый провайдер отклонил авторизацию.'
                action='Обновите данные подключения почты в BORIS.'
            msg=(f"⚠️ <b>BORIS · почта требует действие</b>\n\n"
                 f"Ящик: {row['email_address']}\n"
                 f"Каналы: {channels}\n"
                 f"Причина: {reason}\n\n"
                 f"BORIS уже остановил новые email и не продвигает IMAP-курсор, "
                 f"чтобы не потерять ответы. Повторные проверки ограничены.\n\n"
                 f"<b>Нужно:</b> {action}")
            ok=send_telegram_message(CHAT,msg,thread_id=THREAD)
            if not (isinstance(ok,dict) and ok.get('ok')):
                continue
            db.execute(text("""UPDATE prospect_mailbox_action_alerts
              SET notified_at=NOW(),updated_at=NOW()
              WHERE mailbox_id=:m AND error_kind=:k AND resolved_at IS NULL"""),
              {'m':key[0],'k':key[1]})
            db.commit(); sent+=1
        return {'issues':len(grouped),'pending':pending,'sent':sent,'resolved':resolved}
    finally:
        db.close()


def report_new_sends(limit:int=30)->int:
    from app.telegram_bot import send_telegram_message
    db=SessionLocal(); ensure_schema(db); done=0
    try:
        rows=db.execute(text('''SELECT q.id,q.to_addresses,q.subject,q.sent_at,q.provider_message_id,q.mailbox_id,m.id member_id,pc.name company,pc.city,ca.name campaign,ca.niche
          FROM email_queue q JOIN prospect_campaign_members m ON m.email_queue_id=q.id JOIN prospect_campaigns ca ON ca.id=m.campaign_id JOIN prospect_companies pc ON pc.id=m.company_id
          LEFT JOIN prospect_send_reports r ON r.email_queue_id=q.id
          WHERE q.status='sent' AND r.email_queue_id IS NULL ORDER BY q.id LIMIT :l'''),{'l':limit}).mappings().all()
        for x in rows:
            copy_line='Копия сохранена в «Отправленных» вашего почтового ящика.'
            if x.get('mailbox_id') and x.get('provider_message_id'):
                try:
                    from app.services.client_mailboxes import sent_copy_saved
                    if not sent_copy_saved(int(x['mailbox_id']),str(x['provider_message_id'])): copy_line='Письмо отправлено; копия в «Отправленные» стоит в очереди синхронизации.'
                except Exception: pass
            msg=(f"✉️ <b>BORIS отправил письмо</b>\n\nКомпания: {x['company']}\nГород: {x['city'] or '—'}\nНиша: {x['niche']}\nКому: {x['to_addresses']}\nТема: {x['subject']}\nКампания: {x['campaign']}\n\n{copy_line}")
            ok=send_telegram_message(CHAT,msg,thread_id=THREAD)
            if not (isinstance(ok,dict) and ok.get('ok')): continue
            db.execute(text('INSERT INTO prospect_send_reports(email_queue_id) VALUES(:q) ON CONFLICT DO NOTHING'),{'q':x['id']}); db.commit(); done+=1
        return done
    finally: db.close()

def owner_outreach_summary(db)->str:
    """Plain-language health/performance block for the owner's BORIS campaign."""
    from app.services import prospect_campaigns as campaigns

    rows=db.execute(text("""SELECT id,name,subject_template,body_template
      FROM prospect_campaigns
      WHERE account_id='__owner_outreach__' AND status='active'
      ORDER BY id""")).mappings().all()
    if not rows:
        return ""

    blocks=[]
    for row in rows:
        cid=int(row["id"])
        copy_version=campaigns._copy_version(
            str(row.get("subject_template") or ""),
            str(row.get("body_template") or ""),
            "base",
        )
        stats=db.execute(text("""SELECT
          count(*) FILTER (WHERE m.sent_at IS NOT NULL)::int AS sent,
          count(*) FILTER (
            WHERE m.reply_status='replied'
              AND EXISTS(
                SELECT 1 FROM prospect_inbound_replies pr
                JOIN manager_leads ml ON ml.id=pr.crm_lead_id
                WHERE pr.member_id=m.id
                  AND COALESCE(pr.message_kind,'human')='human'
                  AND ml.status IN ('квалифицирован','целевое действие','пробный','оплатил','сделка')
              )
          )::int AS positive_replied,
          count(*) FILTER (WHERE m.reply_status='opt_out')::int AS opt_out
          FROM prospect_campaign_members m
          WHERE m.campaign_id=:c AND m.copy_version=:v"""),
          {"c":cid,"v":copy_version}).mappings().one()
        deliverability=campaigns.campaign_deliverability(db,cid)
        state=str(deliverability.get("state") or "ok")
        if state=="throttled":
            safety="BORIS сам снизил темп отправки"
        elif state=="blocked":
            safety="отправка остановлена для защиты почты; нужна проверка владельца"
        else:
            safety="норма"
        blocks.append(
            f"{row['name']}: новый текст — {int(stats.get('sent') or 0)} отправлено, "
            f"{int(stats.get('positive_replied') or 0)} полезных ответов, "
            f"{int(stats.get('opt_out') or 0)} отказов. "
            f"Недоступных адресов за 7 дней: {int(deliverability.get('hard_failures') or 0)}/"
            f"{int(deliverability.get('sample') or 0)} "
            f"({float(deliverability.get('hard_failure_rate_pct') or 0):.1f}%) — {safety}."
        )
    return "\n".join(blocks)


def owner_outreach_period_summary(owner_ids:list[int],tz_name:str)->str:
    """Compact 7/30-day owner email funnel for the automatic report."""
    from app.services.owner_outreach_metrics import owner_periods

    blocks=[]
    for owner_id in sorted({int(x) for x in owner_ids}):
        data=owner_periods(owner_id,(7,30),tz_name)
        if not data.get("campaign_ids"):
            continue
        lines=[]
        for key,label in (("7d","7 дней"),("30d","30 дней")):
            summary=((data.get("periods") or {}).get(key) or {}).get("summary") or {}
            tracked=int(summary.get("tracked_sent") or 0)
            opened=int(summary.get("opened_unique") or 0)
            human=int(summary.get("likely_human_opened_unique") or 0)
            lines.append(
                f"{label}: отправлено {int(summary.get('sent') or 0)}, "
                f"доставлено ≈{int(summary.get('delivered_estimated') or 0)}, "
                f"открыто {opened}/{tracked} tracked "
                f"({float(summary.get('open_rate_pct') or 0):.1f}%; "
                f"вероятно людьми {human}), "
                f"ответов {int(summary.get('replied_unique') or 0)}, "
                f"квалифицировано {int(summary.get('qualified_unique') or 0)}, "
                f"целевых действий {int(summary.get('target_action_unique') or 0)}, "
                f"отказов {int(summary.get('opt_out_unique') or 0)}."
            )
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def maybe_daily_report()->bool:
    now=datetime.now(TZ)
    report_hour=int(os.getenv('PROSPECT_DAILY_REPORT_HOUR','19') or 19)
    report_minute=int(os.getenv('PROSPECT_DAILY_REPORT_MINUTE','5') or 5)
    if (now.hour,now.minute) < (report_hour,report_minute): return False
    from app.telegram_bot import send_telegram_message
    db=SessionLocal(); ensure_schema(db)
    try:
        if db.execute(text('SELECT 1 FROM prospect_daily_reports WHERE report_date=:d'),{'d':now.date()}).first(): return False
        # Daily report follows the actually active outreach campaigns. A client
        # campaign may belong to a different BORIS user than the platform owner.
        owner_ids=[int(x[0]) for x in db.execute(text("SELECT DISTINCT owner_id FROM prospect_campaigns WHERE status='active'")).all()]
        owner_outreach_owner_ids=[int(x[0]) for x in db.execute(text(
            "SELECT DISTINCT owner_id FROM prospect_campaigns "
            "WHERE status='active' AND account_id='__owner_outreach__'"
        )).all()]
        tz_name=os.getenv('PROSPECT_TIMEZONE','Europe/Moscow')
        r={
          'sent':int(db.execute(text("""SELECT count(*) FROM prospect_campaign_members m JOIN prospect_campaigns ca ON ca.id=m.campaign_id
            WHERE ca.status='active' AND timezone(:tz,m.sent_at)::date=:d"""),{'tz':tz_name,'d':now.date()}).scalar() or 0),
          'failed':int(db.execute(text("""SELECT count(*) FROM email_queue q JOIN prospect_campaign_members m ON m.email_queue_id=q.id
            JOIN prospect_campaigns ca ON ca.id=m.campaign_id WHERE ca.status='active' AND q.status='dead'
            AND timezone(:tz,q.updated_at)::date=:d"""),{'tz':tz_name,'d':now.date()}).scalar() or 0),
          'replies':int(db.execute(text("""SELECT count(*) FROM prospect_campaign_members m JOIN prospect_campaigns ca ON ca.id=m.campaign_id
            WHERE ca.status='active' AND m.reply_status='replied' AND timezone(:tz,m.replied_at)::date=:d"""),{'tz':tz_name,'d':now.date()}).scalar() or 0),
          'opt_outs':int(db.execute(text("""SELECT count(*) FROM prospect_campaign_members m JOIN prospect_campaigns ca ON ca.id=m.campaign_id
            WHERE ca.status='active' AND m.reply_status='opt_out' AND timezone(:tz,m.replied_at)::date=:d"""),{'tz':tz_name,'d':now.date()}).scalar() or 0),
        }
        from app.services.prospect_campaigns import _owner_daily_state
        daily_states=[_owner_daily_state(db,owner_id) for owner_id in owner_ids]
        effective_cap=sum(int(x['cap']) for x in daily_states)
        daily_used=sum(int(x['used']) for x in daily_states)
        next_steps=[x for x in daily_states if x.get('next_cap') and x.get('next_change_date')]
        next_step=min(next_steps,key=lambda x:x['next_change_date']) if next_steps else None
        used=int(r['sent'] or 0)
        email_qualified=int(db.execute(text("""SELECT count(DISTINCT pr.crm_lead_id)
          FROM prospect_inbound_replies pr JOIN manager_leads l ON l.id=pr.crm_lead_id
          WHERE timezone(:tz,pr.received_at)::date=:d AND l.lead_source='email_outreach' AND l.status='квалифицирован'"""),{'tz':tz_name,'d':now.date()}).scalar() or 0)
        email_target_actions=int(db.execute(text("""SELECT count(DISTINCT pr.crm_lead_id)
          FROM prospect_inbound_replies pr JOIN manager_leads l ON l.id=pr.crm_lead_id
          WHERE timezone(:tz,pr.received_at)::date=:d AND l.lead_source='email_outreach' AND l.status='целевое действие'"""),{'tz':tz_name,'d':now.date()}).scalar() or 0)
        from app.services.telegram_sales_safety import outbound_counts
        tg_outbound=outbound_counts(db,tz_name)
        tg_reactivation=int(db.execute(text("""SELECT count(*) FROM boris_sales_hot_leads l
          WHERE l.monitor_intent='existing_contact_reactivation'
            AND timezone(:tz,l.contacted_at)::date=:d"""),{'tz':tz_name,'d':now.date()}).scalar() or 0)
        tg_replies=int(db.execute(text("SELECT count(DISTINCT hot_lead_id) FROM boris_sales_tg_replies WHERE timezone(:tz,received_at)::date=:d"),{'tz':tz_name,'d':now.date()}).scalar() or 0)
        tg_auto_replies=int(db.execute(text("SELECT count(*) FROM boris_sales_tg_replies WHERE timezone(:tz,auto_replied_at)::date=:d"),{'tz':tz_name,'d':now.date()}).scalar() or 0)
        tg_qualified=int(db.execute(text("""SELECT count(DISTINCT r.hot_lead_id)
          FROM boris_sales_tg_replies r JOIN boris_sales_hot_leads l ON l.id=r.hot_lead_id
          WHERE timezone(:tz,r.received_at)::date=:d AND l.status='qualified'"""),{'tz':tz_name,'d':now.date()}).scalar() or 0)
        tg_declined=int(db.execute(text("""SELECT count(DISTINCT r.hot_lead_id)
          FROM boris_sales_tg_replies r JOIN boris_sales_hot_leads l ON l.id=r.hot_lead_id
          WHERE timezone(:tz,r.received_at)::date=:d AND l.status='declined'"""),{'tz':tz_name,'d':now.date()}).scalar() or 0)
        target_actions=int(db.execute(text("SELECT count(*) FROM manager_leads WHERE lead_source IN ('telegram_reactivation','telegram_prospecting') AND status='целевое действие' AND timezone(:tz,updated_at)::date=:d"),{'tz':tz_name,'d':now.date()}).scalar() or 0)
        ready_now=int(db.execute(text("""SELECT count(*) FROM prospect_campaign_members m JOIN prospect_campaigns c ON c.id=m.campaign_id WHERE c.status='active' AND m.status='ready'""")).scalar() or 0)
        active_campaigns=int(db.execute(text("SELECT count(*) FROM prospect_campaigns WHERE status='active'")).scalar() or 0)
        ready_target=max(1,int(os.getenv('PROSPECT_READY_TARGET','100') or 100))*max(1,active_campaigns)
        ramp_line=(f"\nСледующий лимит: {int(next_step['next_cap'])}/день с {next_step['next_change_date'].strftime('%d.%m')}" if next_step else '')
        owner_summary=owner_outreach_summary(db)
        owner_period_summary=owner_outreach_period_summary(owner_outreach_owner_ids,tz_name)
        owner_parts=[x for x in (owner_summary,owner_period_summary) if x]
        owner_block=(f"\n\n<b>Личная рассылка BORIS</b>\n" + "\n".join(owner_parts) if owner_parts else "")
        msg=(f"📊 <b>BORIS · продажи за день</b>\n\n"
             f"<b>Email</b>\nОтправлено: {used}/{effective_cap}\nОшибок: {int(r['failed'] or 0)}\nОтветов-лидов: {int(r['replies'] or 0)}\nКвалифицировано: {email_qualified}\nЦелевое действие: {email_target_actions}\nОтказов / отписок: {int(r['opt_outs'] or 0)}\nГотовая база: {ready_now}/{ready_target}\nОсталось сегодня: {max(0,effective_cap-daily_used)}{ramp_line}{owner_block}\n\n"
             f"<b>Telegram</b>\nИсходящих касаний: {int(tg_outbound['total'])}\nПервых сообщений: {int(tg_outbound['initial'])}\nИз них реактивация: {tg_reactivation}\nFollow-up: {int(tg_outbound['followup'])}\nАвтоответов в активных диалогах: {tg_auto_replies}\nОтветивших лидов: {tg_replies}\nКвалифицировано сегодня: {tg_qualified}\nЦелевое действие: {target_actions}\nОтказов: {tg_declined}")
        ok=send_telegram_message(CHAT,msg,thread_id=THREAD)
        if not (isinstance(ok,dict) and ok.get('ok')): return False
        db.execute(text('INSERT INTO prospect_daily_reports(report_date) VALUES(:d) ON CONFLICT DO NOTHING'),{'d':now.date()});db.commit();return True
    finally:db.close()
