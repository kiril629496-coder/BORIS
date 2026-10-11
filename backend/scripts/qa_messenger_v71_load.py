"""Bounded synthetic QA load; exact-ID cleanup, no outbox/provider calls."""
from pathlib import Path
exec(Path('/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py').read_text().split('access0,team0=set_viewer()')[0])
import secrets,statistics,concurrent.futures
from app.api.auth import hash_password
checks={};users=[];conversations=[];sid=None
prefix='QA-V71-LOAD-'+STAMP
manifest=Path('/root/BORIS/backend/run/'+prefix+'.json')
def record():manifest.write_text(json.dumps({'account':ACCOUNT,'users':users,'conversations':conversations}))
def api(path):
 start=time.monotonic();r=requests.get(WEB+'/api/internal-messenger'+path,headers={'Authorization':'Bearer '+token},timeout=30)
 assert r.status_code==200,(path,r.status_code)
 return r.json(),round((time.monotonic()-start)*1000,1)
try:
 token,sid,_=issue(OWNER_ID,'V71 bounded load')
 db=SessionLocal()
 try:
  password=hash_password(secrets.token_urlsafe(40))
  for i in range(100):
   u=User(email=prefix.lower()+f'.{i}@example.com',password_hash=password,role='client',is_active=True,status='active')
   db.add(u);db.flush();users.append(u.id)
   db.execute(text('INSERT INTO user_account_access(user_id,account_id,role,can_view,can_reply) VALUES (:u,:a,\'MANAGER\',true,true)'),{'u':u.id,'a':ACCOUNT})
  db.commit();record()
 finally:db.close()
 people,ms=api('/people?account_id='+ACCOUNT)
 checks['PICKER_100_API']={'ok':all(u in [p['user_id'] for p in people['people']] for u in users),'ms':ms}
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1440,'height':900})
  workspace(page,token)
  page.get_by_role('button',name='Новый чат',exact=True).click()
  checks['PICKER_100_UI']={'ok':page.get_by_text(prefix.lower()+'.99',exact=False).count()>0}
  page.keyboard.press('Escape')
  for size in (500,1000):
   db=SessionLocal()
   try:
    rows=db.execute(text("INSERT INTO internal_conversations(account_id,kind,title,created_by_user_id) SELECT :a,'GROUP',:p||n::text,:u FROM generate_series(:lo,:hi) n RETURNING id"),{'a':ACCOUNT,'p':prefix+' ','u':OWNER_ID,'lo':len(conversations)+1,'hi':size}).scalars().all()
    conversations.extend(rows)
    db.execute(text("INSERT INTO internal_conversation_members(conversation_id,user_id,role) SELECT unnest(CAST(:ids AS bigint[])),:u,'OWNER'"),{'ids':rows,'u':OWNER_ID})
    db.commit();record()
   finally:db.close()
   payload,ms=api('/conversations?account_id='+ACCOUNT)
   checks[f'CHATS_{size}_API']={'ok':len(payload['conversations'])>=size,'ms':ms,'count':len(payload['conversations'])}
   start=time.monotonic();page.reload();page.get_by_role('button').filter(has_text=prefix+' '+str(size)).first.wait_for(timeout=30000)
   checks[f'CHATS_{size}_UI']={'ok':True,'ms':round((time.monotonic()-start)*1000,1),'rows':page.locator('button[class*="internalRow"]').count()}
  cid=conversations[-1]
  db=SessionLocal()
  try:
   db.execute(text("INSERT INTO internal_messages(conversation_id,sender_user_id,body) SELECT :c,:u,:p||n::text FROM generate_series(1,10000) n"),{'c':cid,'u':OWNER_ID,'p':prefix+' message '});db.commit()
  finally:db.close()
  first,ms=api(f'/conversations/{cid}/messages?limit=100')
  ids=[m['id'] for m in first['messages']]
  second,ms2=api(f'/conversations/{cid}/messages?limit=100&before_id={min(ids)}')
  ids2=[m['id'] for m in second['messages']]
  checks['PAGINATION_10000']={'ok':len(ids)==100 and len(ids2)==100 and not set(ids)&set(ids2) and max(ids2)<min(ids),'first_ms':ms,'second_ms':ms2}
  page.get_by_role('button').filter(has_text=prefix+' 1000').first.click()
  page.get_by_text(prefix+' message 10000',exact=True).wait_for(timeout=20000)
  initial=page.locator('[id^="internal-message-"]').count()
  page.get_by_role('button',name='Загрузить более ранние сообщения',exact=True).click()
  page.wait_for_function("document.querySelectorAll('[id^=internal-message-]').length>100")
  loaded=page.locator('[id^="internal-message-"]').count()
  checks['BOUNDED_MESSAGE_DOM']={'ok':initial<=100 and loaded<=200,'initial':initial,'after_page':loaded,'stored':10000}
  with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
   results=list(pool.map(lambda _:api('/events?account_id='+ACCOUNT+'&after_id=0&limit=1'),range(12)))
  timings=[r[1] for r in results]
  checks['CONCURRENT_API_4']={'ok':True,'requests':12,'median_ms':statistics.median(timings),'max_ms':max(timings)}
  browser.close()
except Exception as exc:checks['UNEXPECTED']={'ok':False,'error':type(exc).__name__+':'+str(exc)}
finally:
 db=SessionLocal()
 try:
  if conversations:db.execute(text('DELETE FROM internal_conversations WHERE account_id=:a AND id=ANY(CAST(:ids AS bigint[]))'),{'a':ACCOUNT,'ids':conversations})
  if users:
   for table in ('internal_conversation_members','team_memberships','user_account_access'):
    db.execute(text('DELETE FROM '+table+' WHERE user_id=ANY(CAST(:ids AS integer[]))'),{'ids':users})
   db.execute(text('DELETE FROM users WHERE id=ANY(CAST(:ids AS integer[]))'),{'ids':users})
  db.commit()
  checks['CLEANUP']={'ok':True,'users':len(users),'conversations':len(conversations)}
 finally:db.close()
 if sid:revoke(OWNER_ID,sid)
print(json.dumps({'status':'PASS' if checks and all(v['ok'] for v in checks.values()) else 'FAIL','checks':checks},ensure_ascii=False,indent=2))
assert checks and all(v['ok'] for v in checks.values())
