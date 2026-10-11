#!/usr/bin/env python3
from __future__ import annotations
import argparse, os, re
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
load_dotenv('/root/BORIS/backend/.env')
from app.monitoring.telegram_outbound import send_personal_message, send_personal_peer, send_personal_from_message
from app.services.telegram_sales_safety import try_outbound_lock, release_outbound_lock, remaining_outbound, recent_outbound
from telegram_contact_reactivation import reactivation_quality_ok
from app.monitoring.quality import classify as quality_classify
from app.services.outreach_branding import ensure_contact_block

DB=os.getenv('DATABASE_URL') or os.getenv('SQLALCHEMY_DATABASE_URL')
engine=create_engine(DB,pool_pre_ping=True)
MIN_FIT=int(os.getenv('HOT_LEAD_AUTO_MIN_FIT','75'))
DAILY_CAP=int(os.getenv('HOT_LEAD_AUTO_DAILY_CAP','10'))
REACT_DAILY_CAP=int(os.getenv('TG_CONTACT_REACT_DAILY_CAP','4'))
REACT_MAX_AGE_DAYS=int(os.getenv('TG_CONTACT_REACT_MAX_AGE_DAYS','365'))
MAX_AGE_HOURS=int(os.getenv('HOT_LEAD_AUTO_MAX_AGE_HOURS','72'))
MIN_INTERVAL_MINUTES=int(os.getenv('HOT_LEAD_AUTO_MIN_INTERVAL_MINUTES','10'))
SEND_TZ=ZoneInfo(os.getenv('HOT_LEAD_AUTO_TIMEZONE','Europe/Moscow'))
_CONFIG_SEND_START_HOUR=int(os.getenv('HOT_LEAD_AUTO_SEND_START_HOUR','9'))
_CONFIG_SEND_END_HOUR=int(os.getenv('HOT_LEAD_AUTO_SEND_END_HOUR','19'))
HARD_SEND_START_HOUR=int(os.getenv('HOT_LEAD_AUTO_HARD_START_HOUR','9'))
HARD_SEND_END_HOUR=int(os.getenv('HOT_LEAD_AUTO_HARD_END_HOUR','19'))
# Safety clamp for a personal Telegram account: runtime/config drift must never
# widen the owner-approved outreach window silently.
SEND_START_HOUR=max(_CONFIG_SEND_START_HOUR,HARD_SEND_START_HOUR)
SEND_END_HOUR=min(_CONFIG_SEND_END_HOUR,HARD_SEND_END_HOUR)
ENABLED=os.getenv('HOT_LEAD_AUTO_SEND','0')=='1'
USER_RE=re.compile(r'^[A-Za-z0-9_]{5,32}$')
FLOOD_KEY='tg_personal_peer_flood_cooldown_until'
FLOOD_COOLDOWN_MINUTES=max(15,int(os.getenv('TG_PERSONAL_PEER_FLOOD_COOLDOWN_MINUTES','60') or 60))

def clean(v):
    u=str(v or '').strip().lstrip('@')
    if not USER_RE.fullmatch(u):
        return ''
    if u.lower().endswith('bot'):
        return ''
    return u

def select_outbound_route(username, peer):
    """Prefer an explicit validated username over an observed publisher peer."""
    u=clean(username)
    if u:
        return 'username',u
    if peer not in (None,''):
        try: return 'peer',int(peer)
        except Exception: pass
    return 'none',None

def permanent_tg_error(value):
    s=str(value or '')
    low=s.lower()
    return any(x in s for x in (
        'UsernameInvalidError','UsernameNotOccupiedError','UserPrivacyRestrictedError',
        'ChatWriteForbiddenError','UserIsBlockedError',
        'InputUserDeactivatedError','UserDeactivatedBanError','UserDeactivatedError',
    )) or ('no user has' in low and 'as username' in low)

def _main_locked(dry=False):
    now_local=datetime.now(SEND_TZ)
    # TG_PEER_FLOOD_COOLDOWN_V1: PeerFlood is an account-level provider brake,
    # not a per-recipient retry signal. Persist a bounded cooldown so every
    # 15-minute cycle does not hammer Telegram and prolong the restriction.
    if not dry:
        with engine.connect() as db:
            raw=db.execute(text("select value_text from monitor_runtime_state where key=:k"),{'k':FLOOD_KEY}).scalar()
        if raw:
            try:
                until=datetime.fromisoformat(str(raw).replace('Z','+00:00'))
                if until.tzinfo is None: until=until.replace(tzinfo=timezone.utc)
                if until>datetime.now(timezone.utc):
                    print(f'AUTO_SEND=SKIP peer_flood_cooldown until={until.isoformat()}')
                    return 0
            except Exception:
                pass
    if not dry and (now_local.hour < SEND_START_HOUR or now_local.hour >= SEND_END_HOUR):
        print(f'AUTO_SEND=SKIP outside_window {SEND_START_HOUR}:00-{SEND_END_HOUR}:00 {SEND_TZ.key}')
        return 0
    with engine.begin() as db:
        db.execute(text("""CREATE TABLE IF NOT EXISTS telegram_sales_identity_suppressions (peer_id BIGINT PRIMARY KEY, username VARCHAR(128), reason TEXT NOT NULL, evidence TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""))
        if not db.execute(text("""SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='boris_sales_hot_leads' AND column_name='outbound_verified_at')""")).scalar():
            db.execute(text('ALTER TABLE boris_sales_hot_leads ADD COLUMN outbound_verified_at TIMESTAMPTZ'))
        if not db.execute(text("""SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='boris_sales_hot_leads' AND column_name='reactivation_priority')""")).scalar():
            db.execute(text('ALTER TABLE boris_sales_hot_leads ADD COLUMN reactivation_priority INTEGER'))
        if not db.execute(text("""SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='boris_sales_hot_leads' AND column_name='reactivation_priority_reason')""")).scalar():
            db.execute(text('ALTER TABLE boris_sales_hot_leads ADD COLUMN reactivation_priority_reason TEXT'))
        db.execute(text("""UPDATE boris_sales_hot_leads l SET status='skipped',skipped_at=COALESCE(skipped_at,NOW()),approval_note='suppressed_identity',updated_at=NOW() WHERE l.status='new' AND EXISTS (SELECT 1 FROM telegram_sales_identity_suppressions s WHERE (l.contact_peer_id IS NOT NULL AND s.peer_id=l.contact_peer_id) OR (COALESCE(l.contact_username,'')<>'' AND COALESCE(s.username,'')<>'' AND lower(s.username)=lower(l.contact_username)))"""))
        # TG_STALE_APPROVED_SELF_HEAL_V1: a process can die after the atomic
        # new->approved claim and before Telegram returns. If no message/timestamp
        # was ever recorded, requeue it after 20 minutes instead of poisoning
        # queue_safety forever. A delivered/contacted row is never touched.
        db.execute(text("""UPDATE boris_sales_hot_leads
          SET status='new',approved_at=NULL,approved_by=NULL,
              approval_note='stale_approved_requeued',updated_at=NOW()
          WHERE status='approved'
            AND coalesce(approval_required,false)=false
            AND contacted_at IS NULL AND outbound_message_id IS NULL
            AND approved_at < NOW()-INTERVAL '20 minutes'"""))
        budget=remaining_outbound(db,DAILY_CAP,SEND_TZ.key)
        react_sent_today=db.execute(text("SELECT count(*) FROM boris_sales_hot_leads WHERE outbound_transport='telegram_personal' AND monitor_intent='existing_contact_reactivation' AND timezone(:tz,contacted_at)::date=timezone(:tz,now())::date"),{'tz':SEND_TZ.key}).scalar() or 0
        if budget['remaining']<=0:
            print(f"AUTO_SEND=SKIP daily_cap {budget['total']}/{budget['cap']} initial={budget['initial']} followup={budget['followup']}"); return 0
        if recent_outbound(db,MIN_INTERVAL_MINUTES):
            print(f'AUTO_SEND=SKIP paced interval={MIN_INTERVAL_MINUTES}m'); return 0
        r=db.execute(text("""
          SELECT l.*, m.external_message_id AS source_message_id, m.payload AS monitor_payload,
                 s.external_id AS source_external_id, s.payload AS source_payload
          FROM boris_sales_hot_leads l
          LEFT JOIN monitor_messages m ON m.id::text=l.monitor_message_id
          LEFT JOIN monitor_sources s ON s.id=m.source_id
          WHERE l.status='new'
            AND COALESCE(l.approval_required,false)=false
            AND l.boris_fit >= :fit
            AND (
              (l.contact_username IS NOT NULL AND l.contact_username<>'')
              OR l.contact_peer_id IS NOT NULL
            )
            AND (
              (l.monitor_intent='existing_contact_reactivation' AND :react_sent < :react_cap
               AND COALESCE(l.source_posted_at,l.discovered_at) >= now() - make_interval(days => :react_max_age))
              OR (
                l.monitor_intent IS DISTINCT FROM 'existing_contact_reactivation'
                AND COALESCE(l.source_posted_at,l.discovered_at) >= now() - make_interval(hours => :hrs)
              )
            )
            -- Existing-contact reactivation has a stricter buyer-safety gate than
            -- fresh public demand. Old dialogs may contain vendors, support accounts or
            -- historical refusals that happen to mention Avito/CRM keywords.
            AND (
              l.monitor_intent IS DISTINCT FROM 'existing_contact_reactivation'
              OR (
                lower(coalesce(l.contact_name,'')) !~ '(поддержк|кандидат|ваканси|соискател)'
                AND lower(coalesce(l.original_text,'')) !~ '(пишу с предложением|предлагаю.{0,40}сотруднич|предлагаю услуги|наш сервис|наша команда занимается продвиж|мы помогаем.{0,80}получать клиент|занимаемся продвижением youtube|предлагаем [0-9 ]+ проверок.*бесплат|являюсь представителем сервиса|мы прода[её]м спикерские места|помогаем бизнесу увеличивать количество клиентов|я авитолог, нужны клиенты|занимаемся ии-агент|занимаюсь ии-агент|занимаемся ai-агент|главные источники клиентов|увеличивать продажи, важно использовать площадки|частный инвестор|вам начислено [0-9]|срочно: вам начислено|веду клиентов.{0,20}авитолог|перехват лидов|конкурентный маркетинг|делаем стоимость лида|лиды от [0-9]|на лиды и заявки не смотрите|работайте только бюджет|мы можем себе позволить.{0,80}аналитик|вы нас рекомендуете.{0,80}процент|готовый арендный бизнес|обмениваться лидами|от нас [0-9]+% от чека|сами продаем на авито|сделали свой|консалтинговая компания|мы консалтинго|профессионально занимаемся avito|команда.{0,40}занимаемся avito|работаем с авито профессионально|специализируемся на авито|работаем с производителями.{0,80}сайты|если подрядчика еще не нашли|агентские|передачи клиентов|наши условия.{0,30}клиент|продаем аккаунты|продаём аккаунты|цена аккаунта|доступ в складчину|складчина.{0,30}месяц|поток лидов через вертикальные ролики|бесплатный тест контент-воронки|использую свои площадки.{0,80}маркетплейс|передаю контакты.{0,50}процент|предлагаю бесплатный тест|список услуг, которые мы предлагаем|вы развиваете продажи через авито и ищете способ|мы ему своими методами лиды привлекаем|мини продукт и менеджеры продают наставничество|готовы предложить 15% с выручки|не, по авито нет к сожалению|вы обратились в bright4auto|аферист ты|спасибо, подумаем|если что буду иметь ввиду|если что буду иметь в виду|эти доки должны быть в личном кабинете на авито|2000[+] экспертов и предпринимателей сейчас в клубе|передал ваш контакт коллеге|свяжется по поводу заявок конкурентов с авито)'
                AND lower(coalesce(l.original_text,'')) !~ '(не запускать сотрудничество|решение пока не запускать|уже наш[её]л.{0,30}(авитолог|специалист)|остановимся на вашей кандидатуре|буду готов.{0,30}обращусь|уже наш[её]л[аи]?.{0,20}авитолог|выбрал подрядчиков|если не сработаемся|позже вернусь к этому вопросу|здравствуйте, нет|не актуаль|не интересно|больше не пишите|приняли решение.{0,80}сотрудничат|будем иметь в виду|будем иметь ввиду|спасибо за информацию|решение буду принимать по результатам анализа|пока не рассматриваю|пока что оплачивать не готовы|сделал выбор|подумаю и дам вам ответ|пауза до|слишком низк|не пойдем|дорого.{0,20}не|авито не работает)'
                AND (
                  lower(coalesce(l.contact_name,'')) ~ '(нужен авитолог|авито|crm|срм|телефон|лид|продаж)'
                  OR lower(coalesce(l.original_text,'')) ~ '(нужен|нужно|ищу|интересует|стоимост|сколько|цена|покажите|давайте|заявк|лид|авито|crm|срм|телефон|коллтрекинг|сквозн|аналитик)'
                )
              )
            )
            AND NOT EXISTS (
              SELECT 1 FROM telegram_sales_identity_suppressions sup
              WHERE (l.contact_peer_id IS NOT NULL AND sup.peer_id=l.contact_peer_id)
                 OR (COALESCE(l.contact_username,'')<>'' AND COALESCE(sup.username,'')<>'' AND lower(sup.username)=lower(l.contact_username))
            )
            AND NOT EXISTS (
              SELECT 1 FROM boris_sales_hot_leads x
              WHERE x.contacted_at IS NOT NULL
                AND (
                  (l.contact_peer_id IS NOT NULL AND x.contact_peer_id=l.contact_peer_id)
                  OR (
                    l.contact_username IS NOT NULL AND l.contact_username<>''
                    AND x.contact_username IS NOT NULL
                    AND lower(x.contact_username)=lower(l.contact_username)
                  )
                )
            )
          ORDER BY
            CASE WHEN l.monitor_intent='existing_contact_reactivation' THEN 1 ELSE 0 END,
            CASE WHEN l.monitor_intent='existing_contact_reactivation' THEN COALESCE(l.reactivation_priority,0) ELSE 0 END DESC,
            CASE
              WHEN l.monitor_intent='existing_contact_reactivation' AND lower(coalesce(l.original_text,'')) ~ '(назнач.{0,20}(созвон|звон|встреч)|когда.{0,20}(созвон|связ|показ)|давайте.{0,20}(созвон|видео|встреч)|завтра.{0,25}(созвон|связ|позвон)|номер телефона|напишите.{0,20}номер|по видео|видеосозвон|связаться в|списаться в)' THEN 0
              WHEN l.monitor_intent='existing_contact_reactivation' AND lower(coalesce(l.original_text,'')) ~ '(хочу.{0,35}(запуст|реклам|продвиж)|планирую.{0,35}(выбрать|запуст)|выбираю.{0,30}подрядчик|нужны.{0,25}лид|нужен.{0,25}поток|хочу.{0,30}выстроить.{0,30}источник|сможем.{0,20}(по.?взаимодействовать|поработать)|есть ли смысл.{0,30}авито)' THEN 1
              WHEN l.monitor_intent='existing_contact_reactivation' AND lower(coalesce(l.original_text,'')) ~ '(ищу.{0,40}авитолог|нужен.{0,40}авитолог|нужна.{0,40}(работа|помощ|стратег)|выбираем авитолог|подрядчик по авито)' THEN 2
              WHEN l.monitor_intent='existing_contact_reactivation' AND lower(coalesce(l.original_text,'')) ~ '(стоимост|сколько|цена|цифр|кейс|покажите|результат)' THEN 3
              WHEN l.monitor_intent='existing_contact_reactivation' AND lower(coalesce(l.original_text,'')) ~ '(мало заяв|нет заяв|не окуп|никто не пишет|по нулям|больше клиент|лид|заявк)' THEN 4
              WHEN l.monitor_intent='existing_contact_reactivation' AND btrim(coalesce(l.original_text,''))<>'' AND lower(coalesce(l.contact_name,'')) ~ '(нужен авитолог|авито)' THEN 5
              WHEN l.monitor_intent='existing_contact_reactivation' AND lower(coalesce(l.original_text,'')) ~ '(crm|срм|продаж|клиент|телефон|коллтрекинг|аналитик)' THEN 6
              WHEN l.monitor_intent='existing_contact_reactivation' AND btrim(coalesce(l.original_text,''))<>'' THEN 7
              WHEN l.monitor_intent='existing_contact_reactivation' THEN 8
              ELSE 0
            END,
            l.boris_fit DESC,
            COALESCE(l.source_posted_at,l.discovered_at) DESC,
            l.id
          LIMIT 1
        """),{'fit':MIN_FIT,'hrs':MAX_AGE_HOURS,'react_sent':int(react_sent_today),'react_cap':REACT_DAILY_CAP,'react_max_age':REACT_MAX_AGE_DAYS}).mappings().first()
        if not r:
            print('AUTO_SEND=SKIP no_candidate'); return 0
        if r.get('monitor_intent')=='existing_contact_reactivation' and not reactivation_quality_ok(r.get('contact_name') or '',r.get('original_text') or ''):
            db.execute(text("UPDATE boris_sales_hot_leads SET status='skipped',skipped_at=COALESCE(skipped_at,now()),approval_note='reactivation_canonical_quality_guard',updated_at=now() WHERE id=:id AND status='new'"),{'id':r['id']})
            print(f'AUTO_SEND=SKIP canonical_reactivation_quality lead={r["id"]}')
            return 0
        if r.get('monitor_intent')!='existing_contact_reactivation':
            q=quality_classify(r.get('original_text') or '',r.get('monitor_intent'),r.get('monitor_score'))
            if q.get('reject') or not q.get('qualified'):
                db.execute(text("UPDATE boris_sales_hot_leads SET status='skipped',skipped_at=COALESCE(skipped_at,now()),approval_note='fresh_central_quality_guard',updated_at=now() WHERE id=:id AND status='new'"),{'id':r['id']})
                print(f'AUTO_SEND=SKIP fresh_central_quality lead={r["id"]} reasons={q.get("reasons") or []}')
                return 0
        peer=r.get('contact_peer_id'); access_hash=r.get('contact_access_hash'); draft=ensure_contact_block(str(r.get('first_message_draft') or '').strip(), include_site=True)
        route,target=select_outbound_route(r.get('contact_username'),peer)
        u=target if route=='username' else ''
        if route=='none' or not draft:
            print('AUTO_SEND=SKIP invalid_candidate'); return 0
        recipient='@'+str(target) if route=='username' else f'peer:{target}'
        print(f'CANDIDATE id={r["id"]} fit={r["boris_fit"]} recipient={recipient}')
        if dry or not ENABLED:
            print('AUTO_SEND=DRY_RUN' if dry else 'AUTO_SEND=DISABLED'); return 0
        changed=db.execute(text("UPDATE boris_sales_hot_leads SET status='approved',approved_at=now(),approved_by='auto_policy_v1',updated_at=now() WHERE id=:id AND status='new'"),{'id':r['id']}).rowcount
        if changed != 1:
            print('AUTO_SEND=SKIP claim_lost'); return 0
    res=send_personal_message(str(target),draft) if route=='username' else send_personal_peer(int(target),draft,recipient_label=recipient,access_hash=int(access_hash) if access_hash is not None else None)
    if route=='peer' and peer and (not res.get('ok')) and 'PeerIdInvalidError' in str(res.get('error') or ''):
        sp=r.get('source_payload') or {}
        src_hash=sp.get('telegram_access_hash') if isinstance(sp,dict) else None
        msg_id=r.get('source_message_id'); src_id=r.get('source_external_id')
        if src_hash and msg_id and src_id:
            res=send_personal_from_message(int(peer),draft,source_external_id=str(src_id),source_access_hash=int(src_hash),source_message_id=int(msg_id),recipient_label=recipient)

    with engine.begin() as db:
        if res.get('ok'):
            verified=bool(res.get('verified'))
            db.execute(text("UPDATE boris_sales_hot_leads SET status='contacted',contacted_at=now(),outbound_message_id=:mid,outbound_transport='telegram_personal',outbound_recipient=:recipient,contact_peer_id=COALESCE(:peer,contact_peer_id),contact_username=COALESCE(NULLIF(:username,''),contact_username),contact_access_hash=COALESCE(:ah,contact_access_hash),outbound_verified_at=CASE WHEN :verified THEN now() ELSE NULL END,outbound_error=CASE WHEN :verified THEN NULL ELSE 'delivery_unverified' END,updated_at=now() WHERE id=:id"),{'mid':res.get('message_id'),'recipient':recipient,'peer':res.get('peer_id'),'username':res.get('username'),'ah':res.get('access_hash'),'verified':verified,'id':r['id']})
            print(f'AUTO_SEND=PASS lead={r["id"]} recipient={recipient} message_id={res.get("message_id")} verified={1 if verified else 0}')
        else:
            err=str(res.get('error') or '')
            if 'PeerFloodError' in err:
                until=datetime.now(timezone.utc)+timedelta(minutes=FLOOD_COOLDOWN_MINUTES)
                db.execute(text("""INSERT INTO monitor_runtime_state(key,value_text,updated_at) VALUES(:k,:v,NOW())
                  ON CONFLICT(key) DO UPDATE SET value_text=EXCLUDED.value_text,updated_at=NOW()"""),{'k':FLOOD_KEY,'v':until.isoformat()})
                db.execute(text("UPDATE boris_sales_hot_leads SET status='new',approved_at=NULL,approved_by=NULL,outbound_error=:err,updated_at=now() WHERE id=:id"),{'err':err,'id':r['id']})
                print(f'AUTO_SEND=DEFER peer_flood_cooldown until={until.isoformat()} lead={r["id"]}')
                return 0
            if permanent_tg_error(err):
                db.execute(text("UPDATE boris_sales_hot_leads SET status='skipped',skipped_at=COALESCE(skipped_at,now()),approved_at=NULL,approved_by=NULL,outbound_error=:err,approval_note='permanent_telegram_outbound_error',updated_at=now() WHERE id=:id"),{'err':err,'id':r['id']})
                print(f'AUTO_SEND=SKIP_PERMANENT lead={r["id"]} error={err}')
                return 0
            db.execute(text("UPDATE boris_sales_hot_leads SET status='new',approved_at=NULL,approved_by=NULL,outbound_error=:err,updated_at=now() WHERE id=:id"),{'err':err,'id':r['id']})
            print(f'AUTO_SEND=FAIL_RETRYABLE lead={r["id"]} error={err}')
            return 2
    return 0


def main(dry=False):
    """Run one Telegram initial-send cycle under a session advisory lock.

    The dedicated lock connection stays open across the Telegram network call,
    while the actual lead read/write transactions remain short-lived.
    """
    lock_db=engine.connect(); acquired=False
    try:
        if not try_outbound_lock(lock_db):
            print('AUTO_SEND=SKIP lock_busy'); return 0
        acquired=True
        # End the implicit transaction opened by pg_try_advisory_lock(). The
        # session lock survives commit and remains held by this connection.
        lock_db.commit()
        return _main_locked(dry)
    finally:
        if acquired:
            try:
                release_outbound_lock(lock_db); lock_db.commit()
            except Exception:
                try: lock_db.rollback()
                except Exception: pass
        lock_db.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--dry-run',action='store_true'); a=p.parse_args(); raise SystemExit(main(a.dry_run))
