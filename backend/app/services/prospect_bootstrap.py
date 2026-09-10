from __future__ import annotations
import os
from sqlalchemy import text
from app.db.session import SessionLocal

OWNER_ID=int(os.getenv('PROSPECT_AUTOSTART_OWNER_ID','2') or 2)
EMAIL=(os.getenv('PROSPECT_AUTOSTART_EMAIL') or '').strip().lower()
CHAT=os.getenv('PROSPECT_REPORT_TG_CHAT_ID','-1003952038222')
THREAD=int(os.getenv('PROSPECT_REPORT_TG_THREAD_ID','1747') or 1747)

def ensure_schema(db):
    db.execute(text('''CREATE TABLE IF NOT EXISTS prospect_outreach_bootstrap (
      owner_id BIGINT PRIMARY KEY, mailbox_id BIGINT, email_address VARCHAR(320), state VARCHAR(40) NOT NULL DEFAULT 'waiting_mailbox',
      test_queue_id BIGINT, test_message_id TEXT, last_error TEXT, updated_at TIMESTAMP NOT NULL DEFAULT NOW(), created_at TIMESTAMP NOT NULL DEFAULT NOW())'''))
    db.commit()

def _tg(text_msg):
    try:
        from app.telegram_bot import send_telegram_message
        send_telegram_message(CHAT,text_msg,thread_id=THREAD)
    except Exception: pass

def _find_inbox_message(mailbox_id:int,message_id:str)->bool:
    if not message_id:return False
    from app.services.client_mailboxes import _row
    from app.crypto_utils import decrypt_secret
    import imaplib
    r=_row(mailbox_id)
    if not r:return False
    pw=decrypt_secret(r['secret_encrypted'])
    cls=imaplib.IMAP4_SSL if r['imap_ssl'] else imaplib.IMAP4
    m=cls(r['imap_host'],r['imap_port']); m.login(r['username'],pw); m.select('INBOX',readonly=True)
    try:
        typ,data=m.search(None,'HEADER','Message-ID',f'"{message_id}"')
        return bool(typ=='OK' and data and data[0].strip())
    finally:
        try:m.logout()
        except Exception:pass

def tick()->dict:
    if not EMAIL:return {'state':'disabled'}
    from app.services import client_mailboxes
    from app.services.email_queue import enqueue_email
    from app.services.prospect_banner import render_banner
    db=SessionLocal(); ensure_schema(db)
    try:
        mb=db.execute(text("SELECT id FROM client_mailboxes WHERE owner_user_id=:o AND lower(email_address)=:e AND status='active' ORDER BY id DESC LIMIT 1"),{'o':OWNER_ID,'e':EMAIL}).scalar()
        row=db.execute(text('SELECT * FROM prospect_outreach_bootstrap WHERE owner_id=:o'),{'o':OWNER_ID}).mappings().first()
        if not mb:
            if not row: db.execute(text("INSERT INTO prospect_outreach_bootstrap(owner_id,email_address,state) VALUES(:o,:e,'waiting_mailbox') ON CONFLICT DO NOTHING"),{'o':OWNER_ID,'e':EMAIL});db.commit()
            return {'state':'waiting_mailbox'}
        mb=int(mb)
        if not row or row['mailbox_id']!=mb:
            test=client_mailboxes.test_mailbox(mb)
            if not (test.get('smtp') and test.get('imap')):
                db.execute(text("INSERT INTO prospect_outreach_bootstrap(owner_id,mailbox_id,email_address,state,last_error) VALUES(:o,:m,:e,'mailbox_failed',:err) ON CONFLICT(owner_id) DO UPDATE SET mailbox_id=EXCLUDED.mailbox_id,email_address=EXCLUDED.email_address,state='mailbox_failed',last_error=EXCLUDED.last_error,updated_at=NOW()"),{'o':OWNER_ID,'m':mb,'e':EMAIL,'err':str(test)[:500]});db.commit();return {'state':'mailbox_failed','test':test}
            body='''Кирилл, это контрольное письмо BORIS.\n\nЕсли вы видите его во «Входящих», значит исходящая почта работает. Точная копия этого же письма должна быть в папке «Отправленные».\n\nПосле подтверждения транспорта активная owner-outreach кампания продолжает работу по своему плану: безопасный ramp 10→20 писем в сутки и интервал не менее 20 минут. Черновые кампании автоматически не активируются.'''
            banner_mode=(os.getenv('PROSPECT_EMAIL_BANNER_MODE') or 'off').strip().lower()
            attachments=[]; cid=''
            if banner_mode == 'reuse':
                try:
                    banner=render_banner(company='Контрольный тест BORIS',niche='B2B outreach',city='Россия',campaign_id=0,member_id=mb)
                    cid='boris-self-test'
                    attachments=[{'path':banner,'mime':'image/png','inline':True,'cid':cid}]
                except Exception:
                    cid=''
            img=(f'<img src="cid:{cid}" style="width:100%">' if cid else '')
            html=f'''<html><body style="font-family:Arial;background:#f5f7fb;padding:24px"><div style="max-width:680px;margin:auto;background:white;border-radius:18px;overflow:hidden">{img}<div style="padding:28px;line-height:1.6">{body.replace(chr(10),'<br>')}</div></div></body></html>'''
            q=enqueue_email(EMAIL,'BORIS TEST — почта и «Отправленные» работают',body,html=html,source='prospect_self_test',idempotency_key=f'prospect_self_test:{OWNER_ID}:{mb}',send_now=False,attachments=attachments,mailbox_id=mb)
            db.execute(text("INSERT INTO prospect_outreach_bootstrap(owner_id,mailbox_id,email_address,state,test_queue_id,last_error) VALUES(:o,:m,:e,'self_queued',:q,NULL) ON CONFLICT(owner_id) DO UPDATE SET mailbox_id=EXCLUDED.mailbox_id,email_address=EXCLUDED.email_address,state='self_queued',test_queue_id=EXCLUDED.test_queue_id,last_error=NULL,updated_at=NOW()"),{'o':OWNER_ID,'m':mb,'e':EMAIL,'q':q.get('id')});db.commit()
            _tg('📮 <b>BORIS: Mail.ru подключён</b>\n\nSMTP + IMAP прошли проверку. Поставил контрольное письмо самому себе в очередь. Холодная рассылка ещё не запущена.')
            return {'state':'self_queued','queue_id':q.get('id')}
        state=row['state']
        if state in ('self_queued','self_sent'):
            q=db.execute(text('SELECT status,provider_message_id,last_error FROM email_queue WHERE id=:q'),{'q':row['test_queue_id']}).mappings().first()
            if not q:return {'state':state}
            if q['status']=='dead':
                db.execute(text("UPDATE prospect_outreach_bootstrap SET state='self_failed',last_error=:e,updated_at=NOW() WHERE owner_id=:o"),{'e':q['last_error'],'o':OWNER_ID});db.commit();_tg('❌ <b>BORIS: тест Mail.ru не прошёл</b>\n\nРассылка не запущена.');return {'state':'self_failed'}
            if q['status']=='sent':
                mid=q['provider_message_id'] or ''
                if _find_inbox_message(mb,mid):
                    # Bootstrap proves mailbox transport only. It must never activate
                    # draft/paused business campaigns implicitly.
                    pending=db.execute(text("SELECT count(*) FROM mailbox_sent_copy_queue WHERE mailbox_id=:m AND message_id=:mid AND status='pending'"),{'m':mb,'mid':mid}).scalar() or 0
                    if pending:return {'state':'waiting_sent_copy'}
                    db.execute(text("UPDATE prospect_outreach_bootstrap SET state='active',test_message_id=:mid,last_error=NULL,updated_at=NOW() WHERE owner_id=:o"),{'mid':mid,'o':OWNER_ID});db.commit()
                    _tg('✅ <b>BORIS: контрольный email-тест PASS</b>\n\nПроверено: SMTP, IMAP, письмо во «Входящих», копия в «Отправленных». Почтовый контур готов. Активные owner-outreach кампании работают только по своей явной конфигурации: ramp 10→20/сутки, интервал ≥20 минут. Черновики автоматически не активируются.')
                    return {'state':'active'}
                db.execute(text("UPDATE prospect_outreach_bootstrap SET state='self_sent',test_message_id=:mid,updated_at=NOW() WHERE owner_id=:o"),{'mid':mid,'o':OWNER_ID});db.commit();return {'state':'self_sent_waiting_inbox'}
        return {'state':state}
    finally: db.close()
