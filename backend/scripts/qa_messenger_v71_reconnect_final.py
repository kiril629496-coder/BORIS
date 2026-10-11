"""Post-load offline delivery/read receipts and four concurrent SSE readers."""
from pathlib import Path
exec(Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text().split("access0,team0=set_viewer()")[0])
import concurrent.futures,threading
tokens={};sessions=[];checks={};cid=None;before_dm=dm_ids()
def req(uid,method,path,body=None):
 r=requests.request(method,WEB+"/api/internal-messenger"+path,headers={"Authorization":"Bearer "+tokens[uid]},json=body,timeout=20);assert r.status_code==200,(path,r.status_code);return r.json()
try:
 for u in (OWNER_ID,EMPLOYEE_ID):
  t,s,_=issue(u,"V71 postload reconnect");tokens[u]=t;sessions.append((u,s))
 cid=req(46,"POST","/conversations",{"account_id":ACCOUNT,"kind":"GROUP","title":GROUP_TITLE,"member_user_ids":[1932]})["conversation_id"]
 with sync_playwright() as pw:
  b=pw.chromium.launch(headless=True,args=["--no-sandbox"])
  oc=b.new_context(viewport={"width":1280,"height":800});ec=b.new_context(viewport={"width":1280,"height":800})
  op=oc.new_page();ep=ec.new_page()
  for u,p in ((46,op),(1932,ep)):workspace(p,tokens[u])
  op.locator("button").filter(has_text=GROUP_TITLE).first.click();open_general(ep)
  ep.locator("h2").filter(has_text="Общий чат").wait_for(timeout=10000)
  baseline_unread=next(x["unread_count"] for x in req(1932,"GET","/conversations?account_id="+ACCOUNT)["conversations"] if x["id"]==cid)
  ec.set_offline(True);ep.get_by_text("Нет связи",exact=True).first.wait_for(timeout=12000)
  checks["OFFLINE_HEADER"]=True
  cursor=req(46,"GET","/events?account_id="+ACCOUNT+"&after_id=0&limit=200")["next_after_id"]
  # Read from the actual latest visible cursor to avoid an old backlog.
  cursor=dbrow("SELECT coalesce(max(id),0) n FROM internal_messenger_events WHERE account_id=:a",{"a":ACCOUNT})["n"]
  ready=[threading.Event() for _ in range(4)]
  def listen(i):
   with requests.get(WEB+f"/api/internal-messenger/events/stream?account_id={ACCOUNT}&after_id={cursor}",headers={"Authorization":"Bearer "+tokens[1932]},stream=True,timeout=(10,20)) as r:
    assert r.status_code==200,r.status_code
    ready[i].set()
    for line in r.iter_lines(chunk_size=1,decode_unicode=True):
     if line.startswith("data:"):
      event=json.loads(line[5:])
      if event.get("payload",{}).get("body")==PREFIX+" reconnect 1":return event["id"]
   raise AssertionError("SSE ended before target")
  with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
   futures=[pool.submit(listen,i) for i in range(4)]
   assert all(e.wait(10) for e in ready)
   ids=[]
   for i in range(1,6):
    ids.append(req(46,"POST",f"/conversations/{cid}/messages",{"body":PREFIX+f" reconnect {i}"})["message"]["id"])
    time.sleep(.7)
   delivered=[f.result(timeout=25) for f in futures]
   checks["CONCURRENT_SSE_4"]=len(delivered)==4 and len(set(delivered))==1
  ec.set_offline(False);ep.get_by_text("Онлайн",exact=True).first.wait_for(timeout=25000)
  checks["RECONNECT_HEADER"]=True
  row=ep.locator('button[class*="internalRow"]').filter(has_text=GROUP_TITLE)
  row.locator("b").filter(has_text=re.compile("^"+str(baseline_unread+5)+"$")).wait_for(timeout=20000)
  checks["UNREAD_AFTER_RECONNECT"]=True
  row.click();ep.get_by_text(PREFIX+" reconnect 5",exact=True).wait_for(timeout=20000)
  texts=ep.locator('[id^="internal-message-"] p').all_text_contents()
  matched=[x for x in texts if x.startswith(PREFIX+" reconnect ")]
  checks["MISSED_5_ORDER_NO_DUPLICATES"]=matched==[PREFIX+f" reconnect {i}" for i in range(1,6)]
  bubble=op.locator("#internal-message-"+str(ids[-1]))
  bubble.get_by_text(re.compile("Прочитано")).wait_for(timeout=20000)
  checks["READ_RECEIPT_AFTER_RECONNECT"]=True
  b.close()
finally:
 for u,s in sessions:revoke(u,s)
 cleanup(before_dm)
print(json.dumps({"status":"PASS" if checks and all(checks.values()) else "FAIL","checks":checks},ensure_ascii=False,indent=2))
assert checks and all(checks.values())
