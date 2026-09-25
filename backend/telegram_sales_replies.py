#!/usr/bin/env python3
from __future__ import annotations
from zoneinfo import ZoneInfo

import os
import re
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv('/root/BORIS/backend/.env')

from app.crypto_utils import decrypt_secret
from app.monitoring.telegram import build_client
from app.monitoring.telegram_outbound import resolve_personal_entity, verify_personal_message
from app.monitoring import store
from app.telegram_bot import send_telegram_message
from app.services.telegram_sales_safety import try_outbound_lock, release_outbound_lock, remaining_outbound, recent_outbound
from telegram_contact_reactivation import reactivation_quality_ok

DB=os.getenv('DATABASE_URL') or os.getenv('SQLALCHEMY_DATABASE_URL')
engine=create_engine(DB,pool_pre_ping=True)
CHAT_ID=os.getenv('HOT_LEAD_REPLY_CHAT_ID','-1003952038222')
THREAD_ID=int(os.getenv('HOT_LEAD_REPLY_THREAD_ID','126') or 126)


def ensure_schema(db):
    # Hot path: never request PostgreSQL relation locks when the canonical
    # reply/CRM schema is already complete. CREATE/ALTER IF NOT EXISTS still
    # takes locks and this worker runs every sales cycle.
    ready=db.execute(text("""SELECT
      to_regclass('public.boris_sales_tg_replies') IS NOT NULL
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='boris_sales_tg_replies' AND column_name='manager_lead_id')
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='boris_sales_tg_replies' AND column_name='auto_reply_policy')
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='manager_leads' AND column_name='telegram_peer_id')
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='manager_leads' AND column_name='lead_source')
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='boris_sales_hot_leads' AND column_name='followup_error')
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='boris_sales_hot_leads' AND column_name='followup_verified_at')
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='boris_sales_tg_replies' AND column_name='auto_reply_verified_at')
    """)).scalar()
    if ready:
        return
    db.execute(text('''CREATE TABLE IF NOT EXISTS boris_sales_tg_replies (
      id BIGSERIAL PRIMARY KEY,
      hot_lead_id BIGINT NOT NULL REFERENCES boris_sales_hot_leads(id) ON DELETE CASCADE,
      tg_message_id BIGINT NOT NULL,
      sender_username TEXT,
      body TEXT,
      received_at TIMESTAMPTZ,
      monitoring_lead_id UUID,
      alerted_at TIMESTAMPTZ,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE(hot_lead_id,tg_message_id)
    )'''))
    db.execute(text('ALTER TABLE boris_sales_tg_replies ADD COLUMN IF NOT EXISTS manager_lead_id BIGINT'))
    db.execute(text('ALTER TABLE boris_sales_tg_replies ADD COLUMN IF NOT EXISTS auto_reply_text TEXT'))
    db.execute(text('ALTER TABLE boris_sales_tg_replies ADD COLUMN IF NOT EXISTS auto_reply_message_id BIGINT'))
    db.execute(text('ALTER TABLE boris_sales_tg_replies ADD COLUMN IF NOT EXISTS auto_replied_at TIMESTAMPTZ'))
    db.execute(text('ALTER TABLE boris_sales_tg_replies ADD COLUMN IF NOT EXISTS auto_reply_policy TEXT'))
    db.execute(text('ALTER TABLE boris_sales_tg_replies ADD COLUMN IF NOT EXISTS auto_reply_verified_at TIMESTAMPTZ'))
    db.execute(text('ALTER TABLE manager_leads ADD COLUMN IF NOT EXISTS telegram_peer_id BIGINT'))
    db.execute(text('ALTER TABLE manager_leads ADD COLUMN IF NOT EXISTS telegram_username VARCHAR(128)'))
    db.execute(text('ALTER TABLE manager_leads ADD COLUMN IF NOT EXISTS lead_source VARCHAR(64)'))
    db.execute(text("""CREATE TABLE IF NOT EXISTS telegram_sales_identity_suppressions (
      peer_id BIGINT PRIMARY KEY, username VARCHAR(128), reason TEXT NOT NULL, evidence TEXT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )"""))
    db.execute(text('ALTER TABLE boris_sales_hot_leads ADD COLUMN IF NOT EXISTS followup_sent_at TIMESTAMPTZ'))
    db.execute(text('ALTER TABLE boris_sales_hot_leads ADD COLUMN IF NOT EXISTS followup_message_id BIGINT'))
    db.execute(text('ALTER TABLE boris_sales_hot_leads ADD COLUMN IF NOT EXISTS followup_error TEXT'))
    db.execute(text('ALTER TABLE boris_sales_hot_leads ADD COLUMN IF NOT EXISTS followup_verified_at TIMESTAMPTZ'))


def configured_sales_owner_email() -> str | None:
    owner_id=int(os.getenv('PROSPECT_AUTOSTART_OWNER_ID','0') or 0)
    if not owner_id:
        return None
    with engine.begin() as db:
        value=db.execute(text("SELECT email FROM users WHERE id=:i AND role IN ('owner','manager')"),{'i':owner_id}).scalar()
    value=str(value or '').strip()
    return value or None


def create_monitoring_lead(message_id: str):
    # The canonical monitoring lead must be actionable in the owner workspace.
    # Reuse the configured BORIS sales owner instead of creating an unassigned lead.
    manager_email=configured_sales_owner_email()
    con=store.connect()
    try:
        with con, con.cursor() as cur:
            lead_id=store.create_lead(cur,message_id,manager_email)
            if lead_id and manager_email:
                # Idempotently repair older automatically-created leads that predate
                # owner assignment; never overwrite an explicit manager assignment.
                cur.execute("UPDATE leads SET manager_email=COALESCE(manager_email,%s), updated_at=NOW() WHERE id=%s",
                            (manager_email,lead_id))
            return lead_id
    finally:
        con.close()


def create_manager_lead_for_tg(r, body: str, label: str):
    # CRM ownership must follow the configured BORIS owner, not the outreach
    # mailbox/contact email. PROSPECT_AUTOSTART_EMAIL may be a sending address
    # that is not a users.email and would make the lead invisible in /manager/leads.
    owner_id=int(os.getenv('PROSPECT_AUTOSTART_OWNER_ID','0') or 0)
    with engine.begin() as db:
        manager_email=db.execute(text("SELECT email FROM users WHERE id=:i AND role IN ('owner','manager')"),{'i':owner_id}).scalar()
    manager_email=str(manager_email or '').strip()
    if not manager_email:
        return None
    peer=r.get('contact_peer_id')
    username=str(r.get('contact_username') or '').strip().lstrip('@') or None
    name=str(r.get('contact_name') or '').strip() or None
    topic=str(r.get('first_message_draft') or r.get('original_text') or '').strip().replace('\n',' ')
    is_reactivation=str(r.get('monitor_message_id') or '').startswith('tg_contact:') or str(r.get('approval_note') or '')=='existing_telegram_contact'
    lead_source='telegram_reactivation' if is_reactivation else 'telegram_prospecting'
    source_label='Telegram-реактивацию' if is_reactivation else 'Telegram-проспектинг'
    comment=(f"Ответ на {source_label} BORIS. Контакт: {label}. "
             f"Почему писали: {topic[:500]}. Ответ клиента: {str(body or '')[:700]}")
    with engine.begin() as db:
        ensure_schema(db)
        lead=None
        if peer:
            lead=db.execute(text("SELECT id FROM manager_leads WHERE manager_email=:m AND telegram_peer_id=:p ORDER BY id DESC LIMIT 1"),{'m':manager_email,'p':int(peer)}).scalar()
        if not lead and username:
            lead=db.execute(text("SELECT id FROM manager_leads WHERE manager_email=:m AND lower(coalesce(telegram_username,''))=lower(:u) ORDER BY id DESC LIMIT 1"),{'m':manager_email,'u':username}).scalar()
        if lead:
            db.execute(text("UPDATE manager_leads SET status=CASE WHEN status IN ('квалифицирован','целевое действие','сделка','оплатил') THEN status ELSE 'в диалоге' END,comment=:c,telegram_peer_id=coalesce(telegram_peer_id,:p),telegram_username=coalesce(telegram_username,:u),lead_source=coalesce(lead_source,:src),updated_at=NOW() WHERE id=:i"),{'c':comment,'p':peer,'u':username,'src':lead_source,'i':lead})
            lead_id=int(lead)
        else:
            lead_id=int(db.execute(text("INSERT INTO manager_leads(manager_email,name,comment,status,telegram_peer_id,telegram_username,lead_source) VALUES(:m,:n,:c,'в диалоге',:p,:u,:src) RETURNING id"),{'m':manager_email,'n':name,'c':comment,'p':peer,'u':username,'src':lead_source}).scalar_one())
        db.execute(text("INSERT INTO manager_notes(manager_email,lead_id,kind,text) VALUES(:m,:l,'note',:t)"),{
            'm':manager_email,'l':lead_id,'t':f"Telegram · входящий ответ от {label}: {str(body or '')[:1500]}"
        })
        return lead_id


def is_decline(value: str) -> bool:
    t=' '.join(str(value or '').lower().replace('ё','е').split())
    return any(x in t for x in (
        'не интересно','неинтересно','не актуально','неактуально','не нужно','не надо',
        'нет, спасибо','нет спасибо','не пишите','больше не пишите','прошу не писать','стоп',
    ))



def classify_decline_reason(value: str) -> str:
    """Deterministic owner-facing loss reason; no paid AI call."""
    t=' '.join(str(value or '').lower().replace('ё','е').split())
    if any(x in t for x in ('недостаточно проработ','плохо проработ','видео не','видео такое','демо не','демонстрац','материал не','презентац')):
        return 'качество демонстрации/материалов'
    if any(x in t for x in ('дорого','дороговато','цена','стоимость','не окуп','экономик','бюджет')):
        return 'цена/экономика'
    if any(x in t for x in ('уже работаем с','уже работаю с','уже есть авитолог','есть свой авитолог','нашли подрядчика','нашел подрядчика','нашёл подрядчика')):
        return 'уже есть подрядчик'
    if any(x in t for x in ('не актуально','неактуально','не нужно','не надо','сейчас не','пока не')):
        return 'неактуально/не вовремя'
    if any(x in t for x in ('не интересно','неинтересно')):
        return 'не заинтересовало предложение'
    if any(x in t for x in ('не довер','сомн','нет кейс','без кейс','нужны кейс','покажите кейс')):
        return 'не хватило доказательств/кейсов'
    return 'другое'

# Stable public helper used by tests/backfills/reporting integrations.
def decline_reason(value: str) -> str:
    return classify_decline_reason(value)

def _close_manager_lead_for_tg_db(db, manager_email: str, r, body: str, label: str):
    """Transactional core for an explicit Telegram refusal."""
    peer=r.get('contact_peer_id')
    username=str(r.get('contact_username') or '').strip().lstrip('@') or None
    lead=None
    if peer:
        lead=db.execute(text("SELECT id FROM manager_leads WHERE manager_email=:m AND telegram_peer_id=:p ORDER BY id DESC LIMIT 1"),{'m':manager_email,'p':int(peer)}).scalar()
    if not lead and username:
        lead=db.execute(text("SELECT id FROM manager_leads WHERE manager_email=:m AND lower(coalesce(telegram_username,''))=lower(:u) ORDER BY id DESC LIMIT 1"),{'m':manager_email,'u':username}).scalar()
    if not lead:
        return None
    lead_id=int(lead)
    db.execute(text("""UPDATE manager_leads SET
      status=CASE WHEN status IN ('оплатил','сделка') THEN status ELSE 'отказ' END,
      comment=:c,updated_at=NOW() WHERE id=:i"""),{
        'i':lead_id,'c':f"Telegram · отказ от {label}: {str(body or '')[:700]}"
    })
    db.execute(text("UPDATE manager_notes SET done=TRUE WHERE lead_id=:i AND manager_email=:m AND kind='reminder' AND COALESCE(done,FALSE)=FALSE"),{'i':lead_id,'m':manager_email})
    db.execute(text("INSERT INTO manager_notes(manager_email,lead_id,kind,text) VALUES(:m,:l,'note',:t)"),{
        'm':manager_email,'l':lead_id,'t':f"Telegram · клиент отказался от дальнейшего контакта: {str(body or '')[:1000]}"
    })
    loss_reason=classify_decline_reason(body)
    loss_text=f'Причина отказа: {loss_reason}'
    exists=db.execute(text("SELECT 1 FROM manager_notes WHERE lead_id=:l AND kind='note' AND text=:t LIMIT 1"),{'l':lead_id,'t':loss_text}).first()
    if not exists:
        db.execute(text("INSERT INTO manager_notes(manager_email,lead_id,kind,text) VALUES(:m,:l,'note',:t)"),{'m':manager_email,'l':lead_id,'t':loss_text})
    return lead_id


def close_manager_lead_for_tg(r, body: str, label: str):
    """Close the owner-facing CRM card after an explicit Telegram refusal."""
    manager_email=configured_sales_owner_email()
    if not manager_email:
        return None
    with engine.begin() as db:
        ensure_schema(db)
        return _close_manager_lead_for_tg_db(db,manager_email,r,body,label)


def suppress_nonbuyer_identity(r, body: str, reason: str='publisher_redirect'):
    peer=r.get('contact_peer_id')
    username=str(r.get('contact_username') or '').strip().lstrip('@') or None
    if not peer:
        return False
    with engine.begin() as db:
        ensure_schema(db)
        db.execute(text("""INSERT INTO telegram_sales_identity_suppressions(peer_id,username,reason,evidence,created_at,last_seen_at)
          VALUES(:p,:u,:r,:e,NOW(),NOW())
          ON CONFLICT(peer_id) DO UPDATE SET username=COALESCE(EXCLUDED.username,telegram_sales_identity_suppressions.username),
            reason=EXCLUDED.reason,evidence=EXCLUDED.evidence,last_seen_at=NOW()"""),
          {'p':int(peer),'u':username,'r':reason,'e':str(body or '')[:1000]})
        db.execute(text("""UPDATE boris_sales_hot_leads SET status='skipped',skipped_at=COALESCE(skipped_at,NOW()),
          approval_note=:note,updated_at=NOW() WHERE contact_peer_id=:p AND status IN ('new','approved','replied')"""),
          {'p':int(peer),'note':'telegram_publisher_redirect_suppressed'})
    return True



BORIS_FULL_VALUE_4D=(
    'BORIS закрывает весь путь клиента по принципу «боль → решение»: мало или дорогие лиды → AI-маркетолог ищет точки роста и контролирует CPL; '
    'много ручной работы на Авито → объявления, фото, тексты, ставки и Feed Factory; медленно отвечают или теряют обращения → AI-менеджер; '
    'хаос по лидам → CRM; теряются звонки → телефония с карточкой клиента; менеджеры плохо дожимают → AI-РОП разбирает диалоги и звонки; '
    'непонятно, что окупается → аналитика показывает источник, CPL и результат; старая база лежит без дела → реактивация возвращает клиентов. '
    'Если хотите проверить без долгих обещаний, можем дать бесплатный тест BORIS на 4 дня на вашем сценарии.'
)

BORIS_FULL_VALUE_4D_NO_AVITO=(
    'BORIS закрывает весь путь клиента по принципу «боль → решение»: мало или дорогие лиды → поиск публичного спроса и точек контакта; '
    'медленно отвечают или теряют обращения → AI-менеджер; хаос по лидам → CRM; теряются звонки → телефония с карточкой клиента; '
    'менеджеры плохо дожимают → AI-РОП разбирает диалоги и звонки; непонятно, что окупается → аналитика показывает источник, CPL и результат; '
    'старая база лежит без дела → реактивация возвращает клиентов. '
    'Если хотите проверить без долгих обещаний, можем дать бесплатный тест BORIS на 4 дня на вашем сценарии.'
)

QUALIFY_POLICIES={'price_question','positive_interest','product_question','avito_context','yandex_context','sales_stack_context','short_positive','meeting_request','proof_demo','qualification_answer','qualification_complete','channel_constraint_ack'}
BASIC_QUALIFY_POLICIES={'positive_interest','product_question','avito_context','yandex_context','sales_stack_context','short_positive'}
AUTO_CONTINUE_PER_LEAD_24H=int(os.getenv('HOT_LEAD_AUTO_CONTINUE_PER_LEAD_24H','2') or 2)


def _dialogue_norm(value: str) -> str:
    return re.sub(r'[^a-zа-яё0-9]+',' ',str(value or '').lower().replace('ё','е')).strip()


def _question_slots(value: str) -> set[str]:
    """Business fields requested by a message. Used as an anti-loop fence."""
    n=_dialogue_norm(value)
    slots=set()
    if 'ниш' in n or 'чем занимает' in n:
        slots.add('niche')
    if any(x in n for x in ('город','географ')) and any(x in n for x in ('какой','напишите','подскажите','где')):
        slots.add('geo')
    if any(x in n for x in ('сколько заяв','сколько лид','заявок за неделю','лидов за неделю','объем заяв','объём заяв')):
        slots.add('lead_volume')
    if any(x in n for x in ('какой канал','основной канал','откуда приходят','что сейчас уже используется')):
        slots.add('channels')
    if any(x in n for x in ('рекламный бюджет','какой бюджет','примерный бюджет')):
        slots.add('budget')
    if any(x in n for x in ('главная задача','что сейчас важнее','главную боль','главную цель')):
        slots.add('goal')
    return slots


def _near_duplicate_text(a: str, b: str) -> bool:
    na=_dialogue_norm(a); nb=_dialogue_norm(b)
    if not na or not nb:
        return False
    if na==nb:
        return True
    wa=set(na.split()); wb=set(nb.split())
    if len(wa) >= 8 and len(wb) >= 8:
        if len(wa & wb) / max(1,len(wa | wb)) >= 0.82:
            return True
    sa=_question_slots(a); sb=_question_slots(b)
    if not sa or not sb:
        return False
    if sa==sb:
        return True
    overlap=sa & sb
    return len(overlap) >= 2 and len(sa ^ sb) <= 1


def live_outbound_duplicate(client, entity, body: str, lookback: int = 30) -> str | None:
    """Check Telegram history too, because older/manual sends may be absent from DB."""
    try:
        now=datetime.now(timezone.utc)
        for msg in client.iter_messages(entity,limit=max(8,int(lookback))):
            if not bool(getattr(msg,'out',False)):
                continue
            dt=getattr(msg,'date',None)
            if dt:
                if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
                if dt < now-timedelta(days=14):
                    break
            prev=str(getattr(msg,'message',None) or '').strip()
            if prev and _near_duplicate_text(prev,body):
                return 'same_outbound_or_question'
    except Exception:
        return None
    return None


def _dialogue_snapshot(db, hot_lead_id: int) -> dict:
    lead=db.execute(text("""SELECT original_text,first_message_draft
      FROM boris_sales_hot_leads WHERE id=:l"""),{'l':hot_lead_id}).mappings().first() or {}
    rows=db.execute(text("""SELECT body,auto_reply_text,auto_reply_policy,received_at
      FROM boris_sales_tg_replies WHERE hot_lead_id=:l ORDER BY received_at,id"""),{'l':hot_lead_id}).mappings().all()
    original=str(lead.get('original_text') or '')
    inbound=[str(x.get('body') or '') for x in rows if str(x.get('body') or '').strip()]
    outbound=[str(x.get('auto_reply_text') or '') for x in rows if str(x.get('auto_reply_text') or '').strip()]
    all_text=' '.join([original,*inbound]).lower().replace('ё','е')
    avito_excluded=any(x in all_text for x in (
        'не через авито','без авито','авито не надо','авито не нужен','авито не подходит',
        'авито не рассматриваю','авито не рассматриваем','кроме авито','только не авито','для авито дорого'))
    yandex_excluded=any(x in all_text for x in (
        'не через яндекс','без яндекс','без яндекса','директ не надо','не через директ','яндекс директ не рассматри'))
    site_excluded=any(x in all_text for x in (
        'не через сайт','не через сайты','без сайта','без сайтов','сайта у меня нет','сайта нет'))
    geo_known=bool(re.search(r'\b(мск|москва|мо|спб|санкт[- ]петербург|питер|область)\b',all_text))
    volume_known=False
    qualification_line=''
    for body in reversed(inbound):
        low=body.lower().replace('ё','е')
        if re.search(r'\b\d+\b',low) and re.search(r'\b(мск|москва|мо|спб|санкт[- ]петербург|питер|область)\b',low):
            volume_known=True
            qualification_line=body.strip()
            break
    asked=set()
    for out in outbound:
        asked |= _question_slots(out)
    trial_offered=any('4 дня' in out.lower() or '4дня' in out.lower() for out in outbound)
    return {
        'original':original,'inbound':inbound,'outbound':outbound,'all_text':all_text,
        'avito_excluded':avito_excluded,'yandex_excluded':yandex_excluded,'site_excluded':site_excluded,
        'geo_known':geo_known,'volume_known':volume_known,'qualification_line':qualification_line,
        'asked_slots':asked,'trial_offered':trial_offered,
    }


def _human_known_summary(snap: dict) -> str:
    line=str(snap.get('qualification_line') or '').strip()
    parts=[x.strip(' .') for x in line.split(',') if x.strip(' .')]
    bits=[]
    if parts:
        bits.append(parts[0])
    low=line.lower().replace('ё','е')
    if 'мск' in low or 'москва' in low:
        bits.append('Москва и МО' if re.search(r'\bмо\b',low) else 'Москва')
    elif 'спб' in low or 'питер' in low or 'санкт' in low:
        bits.append('Санкт-Петербург')
    nums=re.findall(r'\b\d+\b',line)
    if nums:
        bits.append(f'сейчас заявок — {nums[-1]}')
    excluded=[]
    if snap.get('avito_excluded'): excluded.append('Авито')
    if snap.get('yandex_excluded'): excluded.append('Яндекс Директ')
    if snap.get('site_excluded'): excluded.append('сайты')
    summary=', '.join(bits) if bits else 'данные из переписки уже принял'
    if excluded:
        summary += '; ' + ', '.join(excluded) + ' не используем'
    return summary


def _human_value_offer(snap: dict, *, acknowledge: bool = False) -> str:
    rows=[]
    if snap.get('avito_excluded'):
        rows.append('• мало входящих лидов → ищем публичный спрос и точки контакта в других каналах, без Авито')
    else:
        rows.append('• мало или дорогие лиды → AI-маркетолог ищет точки роста и контролирует CPL')
        rows.append('• много ручной работы на Авито → Feed Factory готовит объявления, фото, тексты и массовое размещение')
    rows.extend([
        '• долго отвечают или теряют обращения → AI-менеджер ведёт первый контакт и квалификацию',
        '• хаос по клиентам → CRM хранит диалог, этап и следующий шаг',
        '• теряются звонки → телефония привязывает звонок к карточке клиента',
        '• менеджеры плохо дожимают → AI-РОП разбирает переписки и звонки',
        '• непонятно, что окупается → сквозная аналитика показывает источник, CPL и результат',
        '• старая база лежит без дела → реактивация возвращает клиентов в работу',
    ])
    intro='Повторно анкету гонять не буду. ' if acknowledge else ''
    if not snap.get('trial_offered'):
        tail='Если хотите, дам бесплатный тест BORIS на 4 дня именно на вашем сценарии — по результату уже решите, есть ли смысл продолжать.'
    else:
        tail='Если актуально, могу перейти сразу к конкретному следующему шагу по вашему сценарию, без повторных вопросов.'
    return intro + 'Что здесь может закрыть BORIS:\n' + '\n'.join(rows) + '\n\n' + tail


def contextual_sales_auto_reply(db, hot_lead_id: int, current_reply_id: int, value: str) -> tuple[str | None,str | None]:
    """Stateful continuation based on the whole dialogue, not only the last phrase."""
    policy,auto_text=safe_sales_auto_reply(value)
    snap=_dialogue_snapshot(db,hot_lead_id)
    latest=' '.join(str(value or '').lower().replace('ё','е').split())
    if any(x in latest for x in (
        'тут же написано','там же написано','я уже написал','я уже писала','выше написал','выше писала','я же написал','я же писала')):
        return ('context_repair',
                'Да, вижу. Вы уже написали: ' + _human_known_summary(snap) + '. '
                + _human_value_offer(snap,acknowledge=True))
    if policy=='avito_price_objection' or (policy=='avito_context' and snap.get('avito_excluded')):
        return ('channel_constraint_ack',
                'Понял. Авито здесь вообще убираем — это уже зафиксировано из переписки. '
                + _human_value_offer(snap,acknowledge=True))
    if policy=='qualification_answer':
        return ('qualification_answer',
                'Принял: ' + _human_known_summary(snap) + '. '
                + _human_value_offer(snap,acknowledge=True))
    if policy in BASIC_QUALIFY_POLICIES and snap.get('geo_known') and snap.get('volume_known'):
        return ('qualification_complete',
                'Данные для первого разбора уже есть: ' + _human_known_summary(snap) + '. '
                + _human_value_offer(snap,acknowledge=True))
    slots=_question_slots(auto_text or '')
    if slots and (slots & set(snap.get('asked_slots') or set())):
        return ('duplicate_qualification_guard',None)
    return policy,auto_text


def guard_duplicate_dialogue(db, hot_lead_id: int, current_reply_id: int, policy: str | None, auto_text: str | None):
    """Block literal and semantic repeat loops before any Telegram send.

    The previous implementation classified each inbound message in isolation, so a
    phrase that merely contained «Авито» could re-trigger the same niche/city question
    even after the buyer had already answered it. Keep the classifier deterministic,
    but add conversation memory at the send boundary.
    """
    if not auto_text:
        return policy,auto_text
    rows=db.execute(text("""
      SELECT auto_reply_policy,auto_reply_text,body
      FROM boris_sales_tg_replies
      WHERE hot_lead_id=:l AND id<>:r
        AND received_at>=NOW()-interval '14 days'
      ORDER BY received_at DESC,id DESC
      LIMIT 12
    """),{'l':hot_lead_id,'r':current_reply_id}).mappings().all()
    try:
        original=str(db.execute(text("SELECT original_text FROM boris_sales_hot_leads WHERE id=:l"),{'l':hot_lead_id}).scalar() or '')
    except Exception:
        original=''
    dialog_context=' '.join([original]+[str(row.get('body') or '') for row in rows]).lower()
    avito_excluded=any(x in dialog_context for x in (
        'не через авито','без авито','авито не надо','авито не нужен','авито не подходит',
        'авито не рассматриваю','авито не рассматриваем','кроме авито','только не авито','для авито дорого',
    ))
    if avito_excluded and BORIS_FULL_VALUE_4D in auto_text:
        auto_text=auto_text.replace(BORIS_FULL_VALUE_4D,BORIS_FULL_VALUE_4D_NO_AVITO)
    wanted=_dialogue_norm(auto_text)
    previous_policies=[]
    for row in rows:
        prev=_dialogue_norm(row.get('auto_reply_text') or '')
        if wanted and prev and wanted==prev:
            return 'duplicate_dialogue_guard',None
        p=str(row.get('auto_reply_policy') or '')
        if p.startswith('backlog_'):
            p=p[len('backlog_'):]
        previous_policies.append(p)
    base_policy=str(policy or '')
    if base_policy.startswith('backlog_'):
        base_policy=base_policy[len('backlog_'):]
    if base_policy in BASIC_QUALIFY_POLICIES and any(p in BASIC_QUALIFY_POLICIES for p in previous_policies):
        return 'duplicate_qualification_guard',None
    return policy,auto_text
FLOOD_KEY='tg_personal_peer_flood_cooldown_until'
FLOOD_COOLDOWN_MINUTES=max(15,int(os.getenv('TG_PERSONAL_PEER_FLOOD_COOLDOWN_MINUTES','60') or 60))

def _peer_flood_cooldown_active():
    with engine.connect() as db:
        raw=db.execute(text("select value_text from monitor_runtime_state where key=:k"),{'k':FLOOD_KEY}).scalar()
    if not raw: return None
    try:
        until=datetime.fromisoformat(str(raw).replace('Z','+00:00'))
        if until.tzinfo is None: until=until.replace(tzinfo=timezone.utc)
        return until if until>datetime.now(timezone.utc) else None
    except Exception:
        return None

def _set_peer_flood_cooldown():
    until=datetime.now(timezone.utc)+timedelta(minutes=FLOOD_COOLDOWN_MINUTES)
    with engine.begin() as db:
        db.execute(text("""INSERT INTO monitor_runtime_state(key,value_text,updated_at) VALUES(:k,:v,NOW())
          ON CONFLICT(key) DO UPDATE SET value_text=EXCLUDED.value_text,updated_at=NOW()"""),{'k':FLOOD_KEY,'v':until.isoformat()})
    return until

def send_personal_serialized(client, entity, body: str, tag: str):
    """Serialize every personal Telegram outbound across workers.

    Returns (message, reason). If the global outbound lock is busy or the
    cross-worker pacing window is active, no Telegram call is made and the
    caller leaves the reply pending for a later cycle.
    """
    lock_db=engine.connect(); acquired=False
    try:
        if not try_outbound_lock(lock_db):
            return None,'lock_busy',False
        acquired=True
        lock_db.commit()
        # TG_PERSONAL_GLOBAL_PEER_FLOOD_BRAKE_V1: PeerFlood is account-wide.
        # Even conversational sends must defer while Telegram has applied the
        # provider brake; retrying from another lane prolongs the restriction.
        until=_peer_flood_cooldown_active()
        if until:
            return None,f'peer_flood_cooldown:{until.isoformat()}',False
        dup=live_outbound_duplicate(client,entity,body)
        if dup:
            return None,f'duplicate_live:{dup}',False
        try:
            msg=client.send_message(entity,body,link_preview=False)
        except Exception as exc:
            if type(exc).__name__=='PeerFloodError':
                until=_set_peer_flood_cooldown()
                return None,f'peer_flood_cooldown:{until.isoformat()}',False
            raise
        verified=verify_personal_message(client,entity,getattr(msg,'id',None),body)
        return msg,'sent',verified
    finally:
        if acquired:
            try:
                release_outbound_lock(lock_db); lock_db.commit()
            except Exception:
                try: lock_db.rollback()
                except Exception: pass
        lock_db.close()

HARD_SEND_START_HOUR=int(os.getenv('HOT_LEAD_AUTO_HARD_START_HOUR','9') or 9)
HARD_SEND_END_HOUR=int(os.getenv('HOT_LEAD_AUTO_HARD_END_HOUR','19') or 19)

def outbound_window_open() -> tuple[bool,int,int,ZoneInfo]:
    tz=ZoneInfo(os.getenv('HOT_LEAD_AUTO_TIMEZONE','Europe/Moscow'))
    cfg_start=int(os.getenv('HOT_LEAD_AUTO_SEND_START_HOUR','9') or 9)
    cfg_end=int(os.getenv('HOT_LEAD_AUTO_SEND_END_HOUR','19') or 19)
    start=max(cfg_start,HARD_SEND_START_HOUR)
    end=min(cfg_end,HARD_SEND_END_HOUR)
    now=datetime.now(tz)
    return (start <= now.hour < end),start,end,tz


def next_allowed_send_time(start_hour: int, tz: ZoneInfo, minute: int = 5):
    now=datetime.now(tz)
    candidate=now.replace(hour=int(start_hour),minute=int(minute),second=0,microsecond=0)
    if now >= candidate:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def safe_sales_auto_reply(value: str) -> tuple[str | None,str | None]:
    """Deterministic no-AI continuation for obvious sales replies only.

    Complex/ambiguous messages intentionally go to the manager queue instead of
    improvising. This keeps auto-sales useful without hallucinated promises or prices.
    """
    t=' '.join(str(value or '').lower().split())
    if not t or is_decline(t):
        return None,None
    vendor_reply=any(x in t for x in (
        'я авитолог','мы авитологи','я директолог','мы директологи','я маркетолог','мы маркетологи',
        'у меня агентство','у нас агентство','наша команда занимается','мы занимаемся продвижением',
        'профессионально занимаемся avito','профессионально занимаемся авито',
        'работаем с авито профессионально','специализируемся на авито',
        'продаем лиды','продаём лиды','делаем лидогенерацию','приводим лиды клиентам','приводим заявки клиентам',
        'маркетинговое агентство','мы агентство','наша услуга','главная услуга','услугу, которую продаем','услугу, которую продаём',
        'мы тоже занимаемся','мы помогаем бизнесу','наши клиенты','моим клиентам','нашим клиентам',
        'сведения о данной вакансии можно получить через бота','сведения о вакансии можно получить через бота',
        'подробности вакансии через бота','подробности о вакансии через бота','получить через бота',
        'напишите в бот','пишите в бот','через бота @','вакансии можно получить через бота',
    ))
    if vendor_reply:
        return ('vendor_handoff',None)
    if any(x in t for x in (
        'давайте позже','давайте потом','вернемся позже','вернёмся позже','напишите позже','напиши позже',
        'не сейчас','через неделю','через две недели','через 2 недели','через месяц','в следующем месяце',
        'после отпуска','пока отложим','отложим вопрос','вернемся к этому позже','вернёмся к этому позже',
    )):
        return ('later_handoff',None)
    if any(x in t for x in (
        'тут же написано','там же написано','я уже написал','я уже писала','уже написал выше','уже писала выше',
        'выше написал','выше писала','я это уже писал','я это уже писала','я же написал','я же писала',
    )):
        return ('context_repair',
                'Да, вижу — вы уже дали эти данные. Повторно спрашивать их не буду. ' + BORIS_FULL_VALUE_4D)
    if any(x in t for x in (
        'похожий аккаунт','похожий аккаунт в авито','пример аккаунта','ссылку на аккаунт',
        'пришлите ссылку','покажите аккаунт','покажи аккаунт',
    )):
        return ('proof_account_request',
                'Понял. Чужой клиентский аккаунт без согласия не буду выдавать как пример. Могу показать обезличенный кабинет, логику работы и результат на живой демонстрации. ' + BORIS_FULL_VALUE_4D)
    if any(x in t for x in (
        'для авито дорого','авито дорого','авито не подходит','для авито не подходит','авито не подойдет','авито не подойдёт',
        'не хочу авито','авито не надо','без авито','не через авито',
    )):
        return ('avito_price_objection',
                'Понял, тогда Авито не навязываю. BORIS не привязан только к Авито: можно использовать поиск публичного спроса, AI-менеджера, CRM, телефонию, AI-РОП, аналитику и реактивацию базы. ' +
                'Если проблема именно в цене отдельного канала, логичнее проверить весь контур на практике. Можем дать бесплатный тест BORIS на 4 дня.')
    if len(t) <= 180 and (',' in t or ';' in t) and re.search(r'\b\d+\b',t) and re.search(r'\b(мск|москва|мо|спб|санкт[- ]петербург|питер|область|город)\b',t):
        return ('qualification_answer',
                'Принял данные. Нишу, географию и текущий объём повторно спрашивать не буду. Следующий шаг — разбор, где можно получить результат и что подключать первым. ' + BORIS_FULL_VALUE_4D)
    if any(x in t for x in (
        'как вы сейчас клиентов ищете','как клиентов ищете','как вы клиентов ищете',
        'где вы клиентов ищете','где клиентов ищете','как нашли меня','как вы меня нашли',
        'сами, по чатам','сами по чатам','по чатам ищете','по чатам находите',
    )):
        return ('source_transparency',
                'Да, часть потока BORIS сейчас находит автоматически в публичных Telegram-чатах и каналах по явным запросам вроде «нужен авитолог» или «мало заявок с Авито». Ваше сообщение было как раз таким публичным запросом. Дальше система отсекает вакансии, саморекламу и нецелевые сообщения, а пишем только по подходящему спросу. Параллельно используем и другие каналы — в первую очередь Авито и работу с уже существующей клиентской базой.')
    if any(x in t for x in ('сколько стоит','стоимость','какая цена','цена?','по цене','прайс','тариф')):
        return ('price_question',
                'По стоимости не буду придумывать цифру без вашего сценария: она зависит от того, какие модули реально нужны. ' +
                BORIS_FULL_VALUE_4D +
                ' Если после теста будет смысл продолжать, тогда уже посчитаем только нужный вам контур, без лишних модулей.')
    # Once the buyer starts comparing capability/results, stop re-qualifying the
    # same basics and move toward a concrete demo. This branch is intentionally
    # narrow: it requires an explicit comparative/result signal.
    if any(x in t for x in ('лучше среднего','сильнее среднего','сравнить','сравнение','покажите результат','покажи результат','покажите кейс','покажи кейс','какие результаты','что по результатам')):
        return ('proof_demo',
                'Это как раз интересно сравнить предметно. Могу показать BORIS на живом сценарии: как он находит точки роста, готовит объявления и контент, контролирует обращения и доводит их до CRM. Напишите удобный день и время по Москве — покажу коротко на конкретном примере.')
    if any(x in t for x in ('когда удобно показать','когда показать','когда созвонимся','давайте созвонимся','можем созвониться','назначим звонок','назначить звонок','демо','демонстрац','встречу','встреча')):
        return ('meeting_request',
                'Да, покажу. Напишите, пожалуйста, удобный день и время по Москве — согласуем короткий созвон и я покажу BORIS именно на вашем сценарии, без общей презентации.')
    if any(x in t for x in ('покажите','покажи','интересно','расскажите','расскажи','подробнее','давайте','да, давайте','актуально','да актуально','хочу посмотреть')):
        return ('positive_interest',
                'Конечно. ' + BORIS_FULL_VALUE_4D +
                ' Если напишете одним сообщением нишу, город и главную боль, покажу именно нужный кусок, а уже известные данные повторно спрашивать не буду.')
    if any(x in t for x in ('что такое boris','что за boris','что это','как работает','что умеет')):
        return ('product_question', BORIS_FULL_VALUE_4D)
    if any(x in t for x in ('яндекс директ','яндекс.директ','директолог','директ ','контекстная реклама','реклама в яндекс')) and len(t) <= 420:
        return ('yandex_context',
                'Понял. Тогда логичнее смотреть не только Авито, а весь контур привлечения. Напишите, пожалуйста, примерный рекламный бюджет и сколько из этих заявок в итоге доходят до квалифицированного лида или продажи. Так станет понятно, где основной резерв — реклама, цена лида или обработка.')
    avito_negative=any(x in t for x in ('не с авито','не из авито','авито мало','на авито мало','авито не основной','avito не основной'))
    if not avito_negative and any(x in t for x in ('авито','avito','авитолог')) and len(t) <= 220:
        return ('avito_context',
                'Понял. По Авито тогда начнём с самого важного: какая ниша, город и примерно сколько заявок сейчас получаете за неделю? По этим трём пунктам уже можно понять, где искать основной резерв.')
    if any(x in t for x in ('crm','срм','коллтрекинг','телефони','аналитик')) and len(t) <= 220:
        return ('sales_stack_context',
                'Понял. Подскажите, что сейчас уже используется: CRM, телефония/коллтрекинг и откуда в основном приходят обращения? Тогда покажу, какой кусок BORIS имеет смысл подключать первым.')
    # Very short affirmative replies are safe to qualify automatically.
    if t in {'да','ага','ок','окей','можно','давай','давайте','интересно!','актуально!'}:
        return ('short_positive',
                'Отлично. Напишите, пожалуйста, нишу и главную задачу сейчас: получить больше лидов, снизить их стоимость или не терять обращения в продажах?')
    return None,None



def later_delay_days(value: str) -> int:
    t=' '.join(str(value or '').lower().split())
    if 'через неделю' in t: return 7
    if 'через две недели' in t or 'через 2 недели' in t: return 14
    if 'через месяц' in t or 'в следующем месяце' in t: return 30
    if 'после отпуска' in t: return 21
    return 14

def send_pending_safe_reply_backlog(client) -> tuple[int,int]:
    """Recover recent obvious buyer replies captured before auto-continuation existed.

    Never double-answer: skip if a newer inbound reply exists or any outbound
    Telegram message was already sent after the captured reply.
    """
    cap=int(os.getenv('HOT_LEAD_SAFE_REPLY_BACKLOG_DAILY_CAP','2') or 2)
    allowed,start_h,end_h,send_tz=outbound_window_open()
    if not allowed:
        print(f'TG_AUTO_BACKLOG=SKIP outside_window {start_h}:00-{end_h}:00 {send_tz.key}')
        return 0,0
    sent=errors=0
    with engine.begin() as db:
        ensure_schema(db)
        done=int(db.execute(text("SELECT count(*) FROM boris_sales_tg_replies WHERE (auto_replied_at AT TIME ZONE 'Europe/Moscow')::date = (now() AT TIME ZONE 'Europe/Moscow')::date")).scalar() or 0)
        if done>=cap:
            return 0,0
        rows=db.execute(text("""
          SELECT r.id reply_id,r.hot_lead_id,r.body,r.received_at,r.manager_lead_id,
                 l.contact_username,l.contact_peer_id,
                 ml.status AS manager_status
          FROM boris_sales_tg_replies r
          JOIN boris_sales_hot_leads l ON l.id=r.hot_lead_id
          LEFT JOIN manager_leads ml ON ml.id=r.manager_lead_id
          WHERE r.auto_replied_at IS NULL
            AND r.received_at>=NOW()-interval '48 hours'
            AND l.status IN ('replied','qualified')
            AND NOT EXISTS (
              SELECT 1 FROM boris_sales_tg_replies r2
              WHERE r2.hot_lead_id=r.hot_lead_id AND r2.received_at>r.received_at
            )
          ORDER BY r.received_at DESC
          LIMIT :lim
        """),{'lim':max(0,cap-done)*4}).mappings().all()
    for r in rows:
        with engine.begin() as db:
            policy,auto_text=contextual_sales_auto_reply(
                db,int(r['hot_lead_id']),int(r['reply_id']),r.get('body') or ''
            )
        # A customer who already reached the target-action stage must never be
        # sent back through basic qualification after an overnight handoff.
        if r.get('manager_status')=='целевое действие' and policy in {'positive_interest','short_positive','product_question','avito_context','sales_stack_context'}:
            policy='meeting_request'
            auto_text=('Да, актуально. Тогда давайте не возвращаться к общей квалификации — '
                       'лучше покажу BORIS предметно на вашем сценарии. Напишите удобное время по Москве сегодня или завтра, '
                       'и согласуем короткий созвон/демонстрацию.')
        with engine.begin() as db:
            policy,auto_text=guard_duplicate_dialogue(
                db,int(r['hot_lead_id']),int(r['reply_id']),policy,auto_text
            )
        if not auto_text:
            with engine.begin() as db:
                db.execute(text("UPDATE boris_sales_tg_replies SET auto_reply_policy=COALESCE(auto_reply_policy,:p) WHERE id=:i"),{'i':r['reply_id'],'p':policy or 'manager_required'})
            continue
        # Backlog recovery must obey the exact same per-lead anti-loop cap as
        # real-time continuation. Otherwise an old captured reply could become
        # a third/fourth automatic message after the live path already hit cap.
        with engine.begin() as db:
            lead_auto_count=int(db.execute(text("SELECT count(*) FROM boris_sales_tg_replies WHERE hot_lead_id=:l AND auto_replied_at>=NOW()-interval '24 hours'"),{'l':r['hot_lead_id']}).scalar() or 0)
        if lead_auto_count >= AUTO_CONTINUE_PER_LEAD_24H:
            with engine.begin() as db:
                db.execute(text("UPDATE boris_sales_tg_replies SET auto_reply_policy=COALESCE(auto_reply_policy,:p) WHERE id=:i"),{'i':r['reply_id'],'p':(policy or 'qualified')+'_manual_handoff'})
            print(f'TG_AUTO_BACKLOG=SKIP_CAP lead={r["hot_lead_id"]} count={lead_auto_count}')
            continue
        username=str(r.get('contact_username') or '').strip().lstrip('@')
        peer=r.get('contact_peer_id')
        if not username and not peer:
            continue
        try:
            entity=resolve_personal_entity(client,int(peer) if peer else username)
            received=r.get('received_at')
            manual_after=False
            for m in client.iter_messages(entity,limit=20):
                dt=getattr(m,'date',None)
                if not dt or not received:
                    continue
                if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
                rr=received if received.tzinfo else received.replace(tzinfo=timezone.utc)
                if dt<=rr:
                    break
                if bool(getattr(m,'out',False)):
                    manual_after=True; break
            if manual_after:
                with engine.begin() as db:
                    db.execute(text("UPDATE boris_sales_tg_replies SET auto_reply_policy='manual_response_detected' WHERE id=:i"),{'i':r['reply_id']})
                print(f'TG_AUTO_BACKLOG=SKIP_MANUAL lead={r["hot_lead_id"]}')
                continue
            m,send_reason,verified=send_personal_serialized(client,entity,auto_text,'backlog')
            if m is None:
                print(f'TG_AUTO_BACKLOG=SKIP_{str(send_reason).upper()} lead={r["hot_lead_id"]}')
                continue
            mid=int(getattr(m,'id',0) or 0) or None
            with engine.begin() as db:
                db.execute(text("""
                  UPDATE boris_sales_tg_replies
                  SET auto_reply_text=:txt,auto_reply_message_id=:mid,auto_replied_at=NOW(),auto_reply_policy=:policy,
                      auto_reply_verified_at=CASE WHEN :verified THEN NOW() ELSE NULL END
                  WHERE id=:i AND auto_replied_at IS NULL
                """),{'txt':auto_text,'mid':mid,'policy':'backlog_'+str(policy),'verified':bool(verified),'i':r['reply_id']})
                if policy in QUALIFY_POLICIES:
                    db.execute(text("UPDATE boris_sales_hot_leads SET status='qualified',updated_at=NOW() WHERE id=:i AND status IN ('replied','contacted')"),{'i':r['hot_lead_id']})
            sent+=1
            print(f'TG_AUTO_BACKLOG=PASS lead={r["hot_lead_id"]} policy={policy} message_id={mid}')
            if sent>=max(0,cap-done):
                break
        except Exception as e:
            errors+=1
            print(f'TG_AUTO_BACKLOG=ERROR lead={r["hot_lead_id"]} type={type(e).__name__}')
    return sent,errors


def _send_due_followups_locked(client) -> tuple[int,int]:
    """One bounded follow-up for unanswered high-intent external leads."""
    allowed,start_h,end_h,send_tz=outbound_window_open()
    now_local=datetime.now(send_tz)
    if not allowed:
        print(f'TG_FOLLOWUP=SKIP outside_window {start_h}:00-{end_h}:00 {send_tz.key}')
        return 0,0
    until=_peer_flood_cooldown_active()
    if until:
        print(f'TG_FOLLOWUP=DEFER peer_flood_cooldown until={until.isoformat()}')
        return 0,0
    after_h=int(os.getenv('HOT_LEAD_FOLLOWUP_AFTER_HOURS','24') or 24)
    react_after_h=int(os.getenv('TG_CONTACT_REACT_FOLLOWUP_AFTER_HOURS','72') or 72)
    cap=int(os.getenv('HOT_LEAD_FOLLOWUP_DAILY_CAP','3') or 3)
    global_cap=int(os.getenv('HOT_LEAD_AUTO_DAILY_CAP','10') or 10)
    min_interval=int(os.getenv('HOT_LEAD_AUTO_MIN_INTERVAL_MINUTES','10') or 10)
    sent=errors=0
    with engine.begin() as db:
        ensure_schema(db)
        budget=remaining_outbound(db,global_cap,send_tz.key)
        done=int(budget['followup'])
        if done>=cap or budget['remaining']<=0:
            return 0,0
        if recent_outbound(db,min_interval):
            print(f'TG_FOLLOWUP=SKIP paced interval={min_interval}m')
            return 0,0
        rows=db.execute(text("""
          SELECT l.id,l.contact_username,l.contact_peer_id,l.contact_name,l.original_text,l.contacted_at,
                 l.monitor_intent,l.status,l.later_at,
                 (SELECT max(rr.received_at) FROM boris_sales_tg_replies rr WHERE rr.hot_lead_id=l.id) AS last_reply_at,
                 (SELECT rr2.auto_reply_policy FROM boris_sales_tg_replies rr2 WHERE rr2.hot_lead_id=l.id ORDER BY rr2.received_at DESC,rr2.id DESC LIMIT 1) AS latest_reply_policy,
                 (SELECT rr2.auto_replied_at FROM boris_sales_tg_replies rr2 WHERE rr2.hot_lead_id=l.id ORDER BY rr2.received_at DESC,rr2.id DESC LIMIT 1) AS latest_auto_reply_at,
                 (SELECT rr2.body FROM boris_sales_tg_replies rr2 WHERE rr2.hot_lead_id=l.id ORDER BY rr2.received_at DESC,rr2.id DESC LIMIT 1) AS latest_reply_body,
                 (SELECT rr2.manager_lead_id FROM boris_sales_tg_replies rr2 WHERE rr2.hot_lead_id=l.id AND rr2.manager_lead_id IS NOT NULL ORDER BY rr2.received_at DESC,rr2.id DESC LIMIT 1) AS manager_lead_id,
                 (SELECT ml.status FROM boris_sales_tg_replies rr3 JOIN manager_leads ml ON ml.id=rr3.manager_lead_id WHERE rr3.hot_lead_id=l.id ORDER BY rr3.received_at DESC,rr3.id DESC LIMIT 1) AS manager_status
          FROM boris_sales_hot_leads l
          WHERE l.boris_fit>=75
            AND l.followup_sent_at IS NULL
            AND (
              (
                l.status='contacted'
                AND (
                  (l.monitor_intent='buy_service' AND l.contacted_at <= NOW()-make_interval(hours=>:h) AND l.contacted_at >= NOW()-interval '7 days')
                  OR
                  (l.monitor_intent='existing_contact_reactivation' AND l.contacted_at <= NOW()-make_interval(hours=>:rh) AND l.contacted_at >= NOW()-interval '14 days')
                )
                AND NOT EXISTS (SELECT 1 FROM boris_sales_tg_replies r0 WHERE r0.hot_lead_id=l.id)
              )
              OR
              (
                l.status='replied' AND l.monitor_intent='buy_service'
                AND (SELECT rr2.auto_reply_policy FROM boris_sales_tg_replies rr2 WHERE rr2.hot_lead_id=l.id ORDER BY rr2.received_at DESC,rr2.id DESC LIMIT 1) IN ('source_transparency','backlog_source_transparency')
                AND (SELECT rr2.auto_replied_at FROM boris_sales_tg_replies rr2 WHERE rr2.hot_lead_id=l.id ORDER BY rr2.received_at DESC,rr2.id DESC LIMIT 1) <= NOW()-make_interval(hours=>:h)
                AND (SELECT rr2.auto_replied_at FROM boris_sales_tg_replies rr2 WHERE rr2.hot_lead_id=l.id ORDER BY rr2.received_at DESC,rr2.id DESC LIMIT 1) >= NOW()-interval '7 days'
              )
              OR
              (
                l.status='qualified'
                AND COALESCE((SELECT rrq.auto_reply_policy FROM boris_sales_tg_replies rrq WHERE rrq.hot_lead_id=l.id ORDER BY rrq.received_at DESC,rrq.id DESC LIMIT 1),'') NOT IN (
                  'context_repair','backlog_context_repair','avito_price_objection','backlog_avito_price_objection',
                  'proof_account_request','backlog_proof_account_request','duplicate_dialogue_guard','duplicate_qualification_guard'
                )
                AND (SELECT max(rrq.received_at) FROM boris_sales_tg_replies rrq WHERE rrq.hot_lead_id=l.id) <= NOW()-interval '24 hours'
                AND (SELECT max(rrq.received_at) FROM boris_sales_tg_replies rrq WHERE rrq.hot_lead_id=l.id) >= NOW()-interval '14 days'
                AND EXISTS (
                  SELECT 1 FROM boris_sales_tg_replies rrq
                  JOIN manager_leads mlq ON mlq.id=rrq.manager_lead_id
                  JOIN manager_notes nq ON nq.lead_id=mlq.id
                  WHERE rrq.hot_lead_id=l.id
                    AND mlq.status IN ('квалифицирован','целевое действие')
                    AND nq.kind='reminder' AND COALESCE(nq.done,FALSE)=FALSE
                    AND nq.remind_at < NOW()-interval '6 hours'
                )
              )
              OR
              (
                l.status='later' AND l.later_at IS NOT NULL AND l.later_at<=NOW() AND l.later_at>=NOW()-interval '7 days'
                AND (SELECT rr2.auto_reply_policy FROM boris_sales_tg_replies rr2 WHERE rr2.hot_lead_id=l.id ORDER BY rr2.received_at DESC,rr2.id DESC LIMIT 1)='later_handoff'
              )
            )
          ORDER BY CASE WHEN l.status='contacted' AND l.monitor_intent='buy_service' THEN 0 WHEN l.status='replied' THEN 1 WHEN l.status='qualified' THEN 2 WHEN l.status='later' THEN 3 ELSE 4 END,
                   l.boris_fit DESC,COALESCE(l.later_at,l.contacted_at) ASC
          LIMIT 1
        """),{'h':after_h,'rh':react_after_h}).mappings().all()
    for r in rows:
        if r.get('monitor_intent')=='existing_contact_reactivation' and not reactivation_quality_ok(r.get('contact_name') or '',r.get('original_text') or ''):
            with engine.begin() as db:
                db.execute(text("UPDATE boris_sales_hot_leads SET status='skipped',skipped_at=COALESCE(skipped_at,NOW()),approval_note='reactivation_followup_quality_guard',updated_at=NOW() WHERE id=:i AND status IN ('contacted','later')"),{'i':r['id']})
            print(f'TG_FOLLOWUP=SKIP quality_guard lead={r["id"]}')
            continue
        username=str(r.get('contact_username') or '').strip().lstrip('@')
        peer=r.get('contact_peer_id')
        if not username and not peer:
            continue
        label='@'+username if username else f'peer:{peer}'
        if r.get('status')=='later':
            text_msg=('Привет! Вы просили вернуться к вопросу позже — поэтому аккуратно пишу сейчас. '
                      'Если задача по Авито/лидам ещё актуальна, могу показать BORIS на вашем сценарии. '
                      'Если пока не время — просто скажите, и больше отвлекать не буду.')
        elif r.get('status')=='replied' and r.get('latest_reply_policy') in {'source_transparency','backlog_source_transparency'}:
            text_msg=('Добрый день. Вернусь к вашему вопросу одним сообщением: если задача по Авито ещё актуальна, '
                      'могу показать на вашем магазине, что бы BORIS проверил первым для роста заявок. '
                      'Если уже не актуально — больше отвлекать не буду.')
        elif r.get('status')=='qualified':
            if r.get('manager_status')=='целевое действие':
                text_msg=('Добрый день. Возвращаюсь к нашему диалогу — вижу, что тогда мы не довели разбор/демонстрацию до конца. '
                          'Если задача ещё актуальна, могу продолжить с того места, где остановились, и показать BORIS предметно. '
                          'Если уже не актуально — просто скажите, больше отвлекать не буду.')
            else:
                with engine.begin() as db:
                    snap=_dialogue_snapshot(db,int(r['id']))
                text_msg=('Добрый день. Возвращаюсь к нашему диалогу. Повторять нишу, город и цифры не нужно — они уже есть в переписке. '
                          + _human_value_offer(snap,acknowledge=False))
        elif r.get('monitor_intent')=='existing_contact_reactivation':
            text_msg=('Привет! Коротко вернусь к сообщению выше. Если задача по Авито или продажам сейчас ещё актуальна, '
                      'могу показать, что BORIS делает именно на вашем случае. Если уже не актуальна — всё в порядке, больше отвлекать не буду.')
        else:
            text_msg=('Добрый день. Коротко напомню про ваш запрос. Если задача ещё актуальна, '
                      'можете написать нишу и главную цель сейчас — больше заявок, ниже CPL или лучше обработка обращений. '
                      'Я отвечу предметно по вашему случаю.')
        try:
            entity=resolve_personal_entity(client,int(peer) if peer else username)
            # Final live guard against the narrow race where the contact replies
            # after the DB reply scan but before this follow-up send.
            contacted=(r.get('last_reply_at') if r.get('status') in {'later','qualified'} else (r.get('latest_auto_reply_at') if r.get('status')=='replied' else r.get('contacted_at')))
            inbound_pending=False
            manual_after=False
            for recent_msg in client.iter_messages(entity,limit=8):
                is_out=bool(getattr(recent_msg,'out',False))
                msg_dt=getattr(recent_msg,'date',None)
                if is_out and r.get('status')!='qualified':
                    continue
                if msg_dt and contacted:
                    if msg_dt.tzinfo is None: msg_dt=msg_dt.replace(tzinfo=timezone.utc)
                    ct=contacted if contacted.tzinfo else contacted.replace(tzinfo=timezone.utc)
                    if msg_dt > ct:
                        if is_out and r.get('status')=='qualified':
                            manual_after=True
                        elif not is_out:
                            inbound_pending=True
                        break
            if inbound_pending:
                print(f'TG_FOLLOWUP=SKIP new_inbound_pending lead={r["id"]} recipient={label}')
                continue
            if manual_after:
                print(f'TG_FOLLOWUP=SKIP manual_outbound_after_reply lead={r["id"]} recipient={label}')
                continue
            dup=live_outbound_duplicate(client,entity,text_msg)
            if dup:
                with engine.begin() as db:
                    db.execute(text("UPDATE boris_sales_hot_leads SET followup_sent_at=NOW(),followup_error='duplicate_live_guard',updated_at=NOW() WHERE id=:i"),{'i':r['id']})
                print(f'TG_FOLLOWUP=SKIP duplicate_live lead={r["id"]} recipient={label}')
                continue
            m=client.send_message(entity,text_msg,link_preview=False)
            mid=int(getattr(m,'id',0) or 0) or None
            verified=verify_personal_message(client,entity,mid,text_msg)
            with engine.begin() as db:
                db.execute(text("UPDATE boris_sales_hot_leads SET followup_sent_at=NOW(),followup_message_id=:m,followup_verified_at=CASE WHEN :verified THEN NOW() ELSE NULL END,followup_error=CASE WHEN :verified THEN NULL ELSE 'delivery_unverified' END,updated_at=NOW() WHERE id=:i"),{'m':mid,'verified':bool(verified),'i':r['id']})
                if r.get('status')=='qualified' and r.get('manager_lead_id'):
                    db.execute(text("UPDATE manager_notes SET done=true WHERE lead_id=:i AND kind='reminder' AND COALESCE(done,FALSE)=FALSE AND remind_at<NOW()"),{'i':int(r['manager_lead_id'])})
            sent+=1
            print(f'TG_FOLLOWUP=PASS lead={r["id"]} recipient={label} message_id={mid}')
        except Exception as e:
            if type(e).__name__=='PeerFloodError':
                until=_set_peer_flood_cooldown()
                with engine.begin() as db:
                    db.execute(text("UPDATE boris_sales_hot_leads SET followup_error=:e,updated_at=NOW() WHERE id=:i"),{'e':f'PeerFloodError deferred until {until.isoformat()}'[:500],'i':r['id']})
                print(f'TG_FOLLOWUP=DEFER peer_flood_cooldown until={until.isoformat()} lead={r["id"]}')
                break
            errors+=1
            with engine.begin() as db:
                db.execute(text("UPDATE boris_sales_hot_leads SET followup_error=:e,updated_at=NOW() WHERE id=:i"),{'e':f'{type(e).__name__}: {e}'[:500],'i':r['id']})
            print(f'TG_FOLLOWUP=ERROR lead={r["id"]} type={type(e).__name__}')
    return sent,errors


def send_due_followups(client) -> tuple[int,int]:
    """Run one bounded follow-up cycle under a session advisory lock."""
    lock_db=engine.connect(); acquired=False
    try:
        if not try_outbound_lock(lock_db):
            print('TG_FOLLOWUP=SKIP lock_busy')
            return 0,0
        acquired=True
        lock_db.commit()
        return _send_due_followups_locked(client)
    finally:
        if acquired:
            try:
                release_outbound_lock(lock_db); lock_db.commit()
            except Exception:
                try: lock_db.rollback()
                except Exception: pass
        lock_db.close()


def ensure_active_crm_next_actions() -> int:
    """Restore one owner next-action for active high-stage Telegram CRM cards."""
    repaired=0
    with engine.begin() as db:
        rows=db.execute(text("""select id,manager_email,status from manager_leads ml
          where ml.lead_source in ('telegram_prospecting','telegram_reactivation')
            and ml.status in ('квалифицирован','целевое действие')
            and not exists (select 1 from manager_notes n where n.lead_id=ml.id and n.kind='reminder' and coalesce(n.done,false)=false)
          order by ml.updated_at desc""")).mappings().all()
        for row in rows:
            task='Согласовать демонстрацию BORIS с Telegram-лидом' if row['status']=='целевое действие' else 'Показать BORIS квалифицированному Telegram-лиду'
            repaired += int(db.execute(text("""insert into manager_notes(manager_email,lead_id,kind,text,remind_at)
              select :m,:i,'reminder',:t,now()
              where not exists (select 1 from manager_notes n where n.lead_id=:i and n.kind='reminder' and coalesce(n.done,false)=false)"""),
              {'m':row['manager_email'],'i':row['id'],'t':task}).rowcount or 0)
    if repaired:
        print(f'TG_CRM_NEXT_ACTION_REPAIR=PASS repaired={repaired}')
    return repaired


def alert_unalerted_declines(limit: int = 10) -> tuple[int,int]:
    """Retry owner alerts for explicit Telegram declines without messaging the client."""
    with engine.connect() as db:
        rows=db.execute(text("""
          select r.id,r.hot_lead_id,r.body,r.received_at,
                 coalesce(nullif(l.contact_username,''),l.contact_name,'Telegram-контакт') as label
          from boris_sales_tg_replies r
          join boris_sales_hot_leads l on l.id=r.hot_lead_id
          where l.status='declined' and r.alerted_at is null
            and r.received_at>=now()-interval '7 days'
          order by r.received_at asc,r.id asc
          limit :lim
        """),{'lim':max(1,int(limit))}).mappings().all()
    sent=errors=0
    for r in rows:
        label=str(r.get('label') or 'Telegram-контакт')
        if label and not label.startswith('@') and str(label).replace('_','').isalnum() and ' ' not in label:
            label='@'+label
        body=str(r.get('body') or '')[:900]
        try:
            resp=send_telegram_message(
                CHAT_ID,
                f"❌ <b>Telegram-лид отказался</b>\n\nКонтакт: {label}\nЛид: #{r['hot_lead_id']}\n\n{body}\n\nCRM автоматически закрыта как отказ. Повторная автоотправка этому контакту заблокирована.",
                THREAD_ID,
            )
            if isinstance(resp,dict) and resp.get('ok'):
                with engine.begin() as db:
                    db.execute(text("update boris_sales_tg_replies set alerted_at=now() where id=:i and alerted_at is null"),{'i':r['id']})
                sent+=1
                print(f"TG_DECLINE_ALERT=PASS reply={r['id']} lead={r['hot_lead_id']}")
            else:
                errors+=1; print(f"TG_DECLINE_ALERT=DEGRADED reply={r['id']} lead={r['hot_lead_id']}")
        except Exception as e:
            errors+=1; print(f"TG_DECLINE_ALERT=ERROR reply={r['id']} type={type(e).__name__}")
    return sent,errors

def alert_unalerted_buyer_replies(limit: int = 10) -> tuple[int,int]:
    """Retry stale owner alerts for real buyer replies without messaging clients.

    The live scanner inserts a reply once. If the manager Telegram alert then
    fails, ON CONFLICT makes the next scan skip that row forever. Retry only
    replies older than five minutes so this recovery path cannot race the live
    first-attempt alert.
    """
    with engine.connect() as db:
        rows=db.execute(text("""
          select r.id,r.hot_lead_id,r.manager_lead_id,r.body,r.received_at,
                 r.auto_reply_policy,r.auto_replied_at,
                 coalesce(nullif(l.contact_username,''),l.contact_name,'Telegram-контакт') as label
          from boris_sales_tg_replies r
          join boris_sales_hot_leads l on l.id=r.hot_lead_id
          where r.alerted_at is null
            and coalesce(r.auto_reply_policy,'')<>'vendor_handoff'
            and coalesce(l.status,'')<>'declined'
            and r.received_at<now()-interval '5 minutes'
            and r.received_at>=now()-interval '7 days'
          order by r.received_at asc,r.id asc
          limit :lim
        """),{'lim':max(1,int(limit))}).mappings().all()
    sent=errors=0
    for r in rows:
        label=str(r.get('label') or 'Telegram-контакт')
        if label and not label.startswith('@') and label.replace('_','').isalnum() and ' ' not in label:
            label='@'+label
        body=str(r.get('body') or '')[:900]
        policy=str(r.get('auto_reply_policy') or '')
        if r.get('auto_replied_at'):
            next_line='BORIS уже автоматически продолжил квалификацию; ответ сохранён в CRM.'
        elif policy=='manual_response_detected':
            next_line='Нужен ответ менеджера: BORIS обнаружил ручной/неоднозначный сценарий.'
        else:
            next_line='Ответ сохранён в CRM; нужен контроль следующего действия менеджера.'
        try:
            resp=send_telegram_message(
                CHAT_ID,
                f"🔥 <b>Ответ на личное сообщение BORIS</b>\n\nКонтакт: {label}\nЛид: #{r['hot_lead_id']}\nCRM: #{r.get('manager_lead_id') or '—'}\n\n{body}\n\n{next_line}",
                THREAD_ID,
            )
            if isinstance(resp,dict) and resp.get('ok'):
                with engine.begin() as db:
                    db.execute(text("update boris_sales_tg_replies set alerted_at=now() where id=:i and alerted_at is null"),{'i':r['id']})
                sent+=1
                print(f"TG_BUYER_ALERT_RETRY=PASS reply={r['id']} lead={r['hot_lead_id']}")
            else:
                errors+=1
                print(f"TG_BUYER_ALERT_RETRY=DEGRADED reply={r['id']} lead={r['hot_lead_id']}")
        except Exception as e:
            errors+=1
            print(f"TG_BUYER_ALERT_RETRY=ERROR reply={r['id']} type={type(e).__name__}")
    return sent,errors


def main()->int:
    ensure_active_crm_next_actions()
    decline_alerted,decline_alert_errors=alert_unalerted_declines()
    buyer_alerted,buyer_alert_errors=alert_unalerted_buyer_replies()
    with engine.begin() as db:
        ensure_schema(db)
        rows=db.execute(text("""
          SELECT id,monitor_message_id,monitor_intent,contact_username,contact_peer_id,contact_name,contacted_at,first_message_draft,original_text,approval_note
          FROM boris_sales_hot_leads
          WHERE contacted_at IS NOT NULL
            AND ((contact_username IS NOT NULL AND contact_username<>'') OR contact_peer_id IS NOT NULL)
            AND contacted_at >= NOW()-interval '30 days'
          ORDER BY contacted_at DESC LIMIT 100
        """)).mappings().all()

    if not rows:
        print('TG_REPLIES=IDLE no_contacted_leads'); return 0

    client=build_client(
        decrypt_secret(os.environ['MONITOR_TG_SESSION_ENC']),
        int(os.environ['MONITOR_TG_API_ID']),
        os.environ['MONITOR_TG_API_HASH'],
    )
    found=0; alerted=decline_alerted+buyer_alerted; errors=decline_alert_errors+buyer_alert_errors
    try:
        for r in rows:
            username=str(r['contact_username'] or '').strip().lstrip('@')
            peer=r.get('contact_peer_id')
            if not username and not peer: continue
            label='@'+username if username else f'peer:{peer}'
            try:
                entity=resolve_personal_entity(client,int(peer) if peer else username)
                after=r['contacted_at']
                for msg in client.iter_messages(entity,limit=30):
                    if getattr(msg,'out',False):
                        continue
                    dt=getattr(msg,'date',None)
                    if dt and after:
                        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
                        if after.tzinfo is None: after=after.replace(tzinfo=timezone.utc)
                        if dt <= after: continue
                    body=str(getattr(msg,'message',None) or '').strip()
                    if not body: continue
                    mid=int(getattr(msg,'id',0) or 0)
                    if not mid: continue
                    with engine.begin() as db:
                        rid=db.execute(text("""
                          INSERT INTO boris_sales_tg_replies(hot_lead_id,tg_message_id,sender_username,body,received_at)
                          VALUES(:l,:m,:u,:b,:dt) ON CONFLICT(hot_lead_id,tg_message_id) DO NOTHING RETURNING id
                        """),{'l':r['id'],'m':mid,'u':label,'b':body[:4000],'dt':dt}).scalar()
                    if not rid: continue
                    found+=1
                    if is_decline(body):
                        manager_lead_id=None
                        try:
                            manager_lead_id=close_manager_lead_for_tg(r,body,label)
                        except Exception:
                            manager_lead_id=None
                        with engine.begin() as db:
                            db.execute(text("UPDATE boris_sales_hot_leads SET status='declined',approval_note='telegram_declined',updated_at=NOW() WHERE id=:i"),{'i':r['id']})
                            if manager_lead_id:
                                db.execute(text("UPDATE boris_sales_tg_replies SET manager_lead_id=:c WHERE id=:i"),{'c':int(manager_lead_id),'i':rid})
                        print(f'TG_REPLY_DECLINED lead={r["id"]} recipient={label} manager_lead={manager_lead_id or 0}')
                        break
                    # Classify obvious vendor/self-promo replies before CRM creation.
                    # A seller answering our outreach is not a buyer and must never pollute
                    # the owner sales funnel merely because they replied.
                    pre_policy,pre_auto_text=safe_sales_auto_reply(body)
                    if pre_policy=='vendor_handoff':
                        manager_lead_id=None
                        try:
                            suppress_nonbuyer_identity(r,body,'publisher_or_vendor_reply')
                            manager_lead_id=close_manager_lead_for_tg(r,body,label)
                        except Exception:
                            manager_lead_id=None
                        with engine.begin() as db:
                            db.execute(text("UPDATE boris_sales_hot_leads SET status='skipped',skipped_at=COALESCE(skipped_at,NOW()),approval_note='telegram_vendor_reply',updated_at=NOW() WHERE id=:i"),{'i':r['id']})
                            db.execute(text("UPDATE boris_sales_tg_replies SET auto_reply_policy='vendor_handoff',manager_lead_id=COALESCE(manager_lead_id,:m) WHERE id=:i"),{'i':rid,'m':manager_lead_id})
                        print(f'TG_REPLY_VENDOR_BLOCKED lead={r["id"]} recipient={label} manager_lead={manager_lead_id or 0}')
                        break

                    crm_id=None; manager_lead_id=None
                    try:
                        crm_id=create_monitoring_lead(str(r['monitor_message_id']))
                    except Exception:
                        crm_id=None
                    # Every non-decline reply must reach the owner-facing CRM. Keep the
                    # monitoring lead as operational trace, but always create/update one
                    # idempotent manager_leads card so a qualified Telegram buyer cannot
                    # disappear from the main sales workspace.
                    try:
                        manager_lead_id=create_manager_lead_for_tg(r,body,label)
                    except Exception:
                        manager_lead_id=None
                    with engine.begin() as db:
                        # Do not downgrade a lead that was already qualified by an earlier
                        # positive reply. Later conversational messages must preserve the
                        # highest reached sales stage unless the customer explicitly declines.
                        db.execute(text("UPDATE boris_sales_hot_leads SET status=CASE WHEN status='qualified' THEN status ELSE 'replied' END,updated_at=NOW() WHERE id=:i"),{'i':r['id']})
                        if crm_id:
                            db.execute(text("UPDATE boris_sales_tg_replies SET monitoring_lead_id=:c WHERE id=:i"),{'c':str(crm_id),'i':rid})
                        if manager_lead_id:
                            db.execute(text("UPDATE boris_sales_tg_replies SET manager_lead_id=:c WHERE id=:i"),{'c':int(manager_lead_id),'i':rid})

                    # Continue only obvious, low-risk sales branches without any paid AI call,
                    # but compose the next turn from the whole dialogue state.
                    with engine.begin() as db:
                        policy,auto_text=contextual_sales_auto_reply(
                            db,int(r['id']),int(rid),body
                        )
                        policy,auto_text=guard_duplicate_dialogue(
                            db,int(r['id']),int(rid),policy,auto_text
                        )
                    auto_ok=False; auto_mid=None
                    if policy and not auto_text:
                        with engine.begin() as db:
                            db.execute(text("UPDATE boris_sales_tg_replies SET auto_reply_policy=COALESCE(auto_reply_policy,:p) WHERE id=:i"),{'p':policy,'i':rid})
                    if policy=='later_handoff':
                        delay=later_delay_days(body)
                        with engine.begin() as db:
                            db.execute(text("UPDATE boris_sales_hot_leads SET status='later',later_at=NOW()+make_interval(days=>:d),updated_at=NOW() WHERE id=:i"),{'i':r['id'],'d':delay})
                            if manager_lead_id:
                                db.execute(text("INSERT INTO manager_notes(manager_email,lead_id,kind,text,remind_at) SELECT manager_email,id,'reminder','Вернуться к Telegram-лиду после просьбы написать позже',NOW()+make_interval(days=>:d) FROM manager_leads ml WHERE ml.id=:i AND NOT EXISTS (SELECT 1 FROM manager_notes n WHERE n.lead_id=ml.id AND n.kind='reminder' AND n.done=false AND n.text='Вернуться к Telegram-лиду после просьбы написать позже')"),{'i':manager_lead_id,'d':delay})
                        print(f'TG_REPLY_LATER lead={r["id"]} days={delay}')
                    if policy in QUALIFY_POLICIES:
                        with engine.begin() as db:
                            db.execute(text("UPDATE boris_sales_hot_leads SET status='qualified',updated_at=NOW() WHERE id=:i AND status='replied'"),{'i':r['id']})
                            if crm_id:
                                crm_stage='target_action' if policy in {'meeting_request','proof_demo'} else 'qualified'
                                db.execute(text("UPDATE leads SET status=CASE WHEN status IN ('won','closed','target_action') THEN status ELSE :st END, manager_email=COALESCE(manager_email,:m), updated_at=NOW() WHERE id=:i"),
                                           {'i':str(crm_id),'st':crm_stage,'m':configured_sales_owner_email()})
                            if manager_lead_id:
                                crm_status='целевое действие' if policy in {'meeting_request','proof_demo'} else 'квалифицирован'
                                final_status=db.execute(text("UPDATE manager_leads SET status=CASE WHEN status='сделка' THEN status WHEN :st='целевое действие' THEN 'целевое действие' WHEN status='целевое действие' THEN status ELSE :st END,updated_at=NOW() WHERE id=:i RETURNING status"),{'i':manager_lead_id,'st':crm_status}).scalar()
                                qualified_task='Показать BORIS квалифицированному Telegram-лиду'
                                target_task='Согласовать демонстрацию BORIS с Telegram-лидом'
                                reminder_text=target_task if final_status in {'целевое действие','сделка'} else qualified_task
                                # Keep exactly one BORIS sales task open. A later
                                # conversational reply must never recreate a lower-stage
                                # task after the lead already reached target action.
                                db.execute(text("""UPDATE manager_notes SET done=true
                                  WHERE lead_id=:i AND kind='reminder' AND done=false
                                    AND text IN (:qualified,:target) AND text<>:txt"""),
                                  {'i':manager_lead_id,'qualified':qualified_task,'target':target_task,'txt':reminder_text})
                                db.execute(text("""
                                  INSERT INTO manager_notes(manager_email,lead_id,kind,text,remind_at)
                                  SELECT manager_email,id,'reminder',:txt,NOW()
                                  FROM manager_leads ml WHERE ml.id=:i
                                    AND NOT EXISTS (
                                      SELECT 1 FROM manager_notes n
                                      WHERE n.lead_id=ml.id AND n.kind='reminder' AND n.done=false
                                        AND n.text=:txt
                                    )
                                """),{'i':manager_lead_id,'txt':reminder_text})
                    if auto_text:
                        with engine.begin() as db:
                            auto_count=int(db.execute(text("SELECT count(*) FROM boris_sales_tg_replies WHERE hot_lead_id=:l AND auto_replied_at>=NOW()-interval '24 hours'"),{'l':r['id']}).scalar() or 0)
                        if auto_count >= AUTO_CONTINUE_PER_LEAD_24H:
                            auto_text=None
                            policy=(policy or 'qualified')+'_manual_handoff'
                            with engine.begin() as db:
                                db.execute(text("UPDATE boris_sales_tg_replies SET auto_reply_policy=:p WHERE id=:i"),{'p':policy,'i':rid})
                    if auto_text:
                        allowed,start_h,end_h,send_tz=outbound_window_open()
                        if not allowed:
                            due_at=next_allowed_send_time(start_h,send_tz,5)
                            with engine.begin() as db:
                                db.execute(text("UPDATE boris_sales_tg_replies SET auto_reply_policy=:p WHERE id=:i"),{'p':(policy or 'qualified')+'_outside_window_handoff','i':rid})
                                if manager_lead_id and policy in QUALIFY_POLICIES:
                                    moved=db.execute(text("UPDATE manager_notes SET remind_at=:due WHERE lead_id=:i AND kind='reminder' AND COALESCE(done,FALSE)=FALSE"),{'due':due_at,'i':int(manager_lead_id)}).rowcount
                                    if not moved:
                                        db.execute(text("""INSERT INTO manager_notes(manager_email,lead_id,kind,text,remind_at)
                                          SELECT manager_email,id,'reminder','Ответить Telegram-лиду в ближайшее разрешённое окно',:due
                                          FROM manager_leads ml WHERE ml.id=:i AND NOT EXISTS (SELECT 1 FROM manager_notes n WHERE n.lead_id=ml.id AND n.kind='reminder' AND COALESCE(n.done,FALSE)=FALSE)"""),{'due':due_at,'i':int(manager_lead_id)})
                            print(f'TG_AUTO_CONTINUE=SKIP outside_window {start_h}:00-{end_h}:00 {send_tz.key} lead={r["id"]} next_action={due_at.isoformat()}')
                            auto_text=None
                    if auto_text:
                        try:
                            sent,send_reason,auto_verified=send_personal_serialized(client,entity,auto_text,'live_reply')
                            auto_mid=int(getattr(sent,'id',0) or 0) or None if sent is not None else None
                            auto_ok=bool(auto_mid)
                            if sent is None:
                                print(f'TG_AUTO_CONTINUE=SKIP_{str(send_reason).upper()} lead={r["id"]}')
                        except Exception as e:
                            print(f'TG_AUTO_CONTINUE_ERROR lead={r["id"]} type={type(e).__name__}')
                        with engine.begin() as db:
                            db.execute(text("""
                              UPDATE boris_sales_tg_replies
                              SET auto_reply_text=:txt,auto_reply_message_id=:mid,
                                  auto_replied_at=CASE WHEN :ok THEN NOW() ELSE auto_replied_at END,
                                  auto_reply_policy=:policy,
                                  auto_reply_verified_at=CASE WHEN :ok AND :verified THEN NOW() ELSE auto_reply_verified_at END
                              WHERE id=:i
                            """),{'txt':auto_text,'mid':auto_mid,'ok':auto_ok,'verified':bool(auto_verified) if auto_ok else False,'policy':policy,'i':rid})
                        if auto_ok:
                            print(f'TG_AUTO_CONTINUE=PASS lead={r["id"]} policy={policy} message_id={auto_mid}')

                    preview=body[:900]
                    next_line=('BORIS уже автоматически продолжил квалификацию.' if auto_ok else 'Нужен ответ менеджера: сообщение неоднозначное или требует решения.')
                    resp=send_telegram_message(
                        CHAT_ID,
                        f"🔥 <b>Ответ на личное сообщение BORIS</b>\n\nКонтакт: {label}\nЛид: #{r['id']}\n\n{preview}\n\n{next_line}",
                        THREAD_ID,
                    )
                    if isinstance(resp,dict) and resp.get('ok'):
                        with engine.begin() as db:
                            db.execute(text("UPDATE boris_sales_tg_replies SET alerted_at=NOW() WHERE id=:i"),{'i':rid})
                        alerted+=1
                    break
            except Exception as e:
                errors+=1
                print(f'REPLY_CHECK_ERROR lead={r["id"]} type={type(e).__name__}')
        backlog_sent,backlog_errors=send_pending_safe_reply_backlog(client)
        errors += backlog_errors
        follow_sent,follow_errors=send_due_followups(client)
        errors += follow_errors
    finally:
        client.disconnect()
    print(f'TG_REPLIES=PASS found={found} alerted={alerted} backlog={backlog_sent} followups={follow_sent} errors={errors}')
    return 0

if __name__=='__main__':
    raise SystemExit(main())
