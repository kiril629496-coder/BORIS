"""Live V7.1 privacy acceptance. Synthetic QA tenant only; no external sends."""
from pathlib import Path
exec(Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text().split("access0,team0=set_viewer()")[0])
import threading
tokens={};sessions=[];checks={};streams=[];before_dm=dm_ids();group=None
def check(k,v,detail=None): checks[k]={"ok":bool(v),"detail":detail}
def req(uid,method,path,body=None,token=None):
 return requests.request(method,WEB+path,headers={"Authorization":"Bearer "+(token or tokens[uid])},json=body,timeout=25)
def feed(uid,cursor):
 r=req(uid,"GET",f"/api/internal-messenger/events?account_id={ACCOUNT}&after_id={cursor}&limit=200")
 assert r.status_code==200,r.status_code
 return r.json()
def cursor():return int(dbrow("SELECT COALESCE(max(id),0) n FROM internal_messenger_events")["n"])
def post(c,body):
 r=req(OWNER_ID,"POST",f"/api/internal-messenger/conversations/{c}/messages",{"body":body})
 assert r.status_code==200,r.status_code
 return r.json()["message"]["id"]
class Stream:
 def __init__(self,uid,after,header=False):
  self.lines=[];self.events=[];self.ready=threading.Event();self.closed=threading.Event();self.error=None;self.status=None
  self.uid=uid;self.after=after;self.header=header
  self.thread=threading.Thread(target=self.run,daemon=True);streams.append(self);self.thread.start()
  assert self.ready.wait(15),"stream not ready"
 def run(self):
  try:
   headers={"Authorization":"Bearer "+tokens[self.uid]}
   if self.header:headers["Last-Event-ID"]=str(self.after)
   query=0 if self.header else self.after
   with requests.get(WEB+f"/api/internal-messenger/events/stream?account_id={ACCOUNT}&after_id={query}",headers=headers,stream=True,timeout=(10,25)) as r:
    self.status=r.status_code;self.ready.set()
    for line in r.iter_lines(chunk_size=1,decode_unicode=True):
     self.lines.append(line)
     if line.startswith("data: "):self.events.append(json.loads(line[6:]))
  except Exception as e:self.error=type(e).__name__;self.ready.set()
  finally:self.closed.set()
def wait_event(s,mid,timeout=12):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  if any(str(e.get("entity_id"))==str(mid) and e["type"]=="internal.message.created" for e in s.events):return True
  time.sleep(.15)
 return False
try:
 for uid in (OWNER_ID,EMPLOYEE_ID,VIEWER_ID):
  tok,sid,_=issue(uid,"V71 live privacy");tokens[uid]=tok;sessions.append((uid,sid))
 extra,extra_sid,old_epoch=issue(EMPLOYEE_ID,"V71 revoker");sessions.append((EMPLOYEE_ID,extra_sid))
 r=req(OWNER_ID,"POST","/api/internal-messenger/general/ensure",{"account_id":ACCOUNT});assert r.status_code==200
 general=r.json()["conversation_id"]
 start=cursor()
 outsider=Stream(VIEWER_ID,start);member=Stream(EMPLOYEE_ID,start)
 ready_before=req(VIEWER_ID,"GET",f"/api/internal-messenger/readiness?account_id={ACCOUNT}").json()
 convs={}
 for kind in ("DM","GROUP"):
  r=req(OWNER_ID,"POST","/api/internal-messenger/conversations",{"account_id":ACCOUNT,"kind":kind,"title":GROUP_TITLE if kind=="GROUP" else None,"member_user_ids":[EMPLOYEE_ID]})
  assert r.status_code==200,r.status_code
  convs[kind]=r.json()["conversation_id"]
 group=convs["GROUP"]
 private_ids=[];mids=[]
 for kind,c in convs.items():
  mid=post(c,PREFIX+" PRIVATE "+kind);mids.append(mid)
  assert wait_event(member,mid),"member stream missing message"
  own=feed(EMPLOYEE_ID,start)["items"]
  ids=[e["id"] for e in own if e.get("payload",{}).get("conversation_id")==c or (e.get("entity_type")=="conversation" and str(e.get("entity_id"))==str(c))]
  private_ids+=ids
  denied=feed(VIEWER_ID,start)
  check(kind+"_POLLING_ABSENT",not any(e["id"] in ids for e in denied["items"]))
  check(kind+"_MEMBER_SSE",wait_event(member,mid))
  for method,path,body in [
   ("PATCH",f"/api/internal-messenger/messages/{mid}",{"body":PREFIX+" EDIT "+kind}),
   ("POST",f"/api/internal-messenger/messages/{mid}/reactions",{"reaction":"👍"}),
   ("POST",f"/api/internal-messenger/messages/{mid}/pin",None)]:
   r=req(OWNER_ID,method,path,body);check(kind+"_"+path.rsplit("/",1)[-1],r.status_code==200,r.status_code)
 # Readiness counters remain unchanged by private events.
 ready_after=req(VIEWER_ID,"GET",f"/api/internal-messenger/readiness?account_id={ACCOUNT}").json()
 check("READINESS_PRIVATE_CURSOR_ABSENT",ready_before["realtime"]==ready_after["realtime"])
 check("READINESS_PRIVATE_RATE_ABSENT",ready_before["observability"]==ready_after["observability"])
 gm=post(general,PREFIX+" GENERAL")
 check("GENERAL_MEMBER_SSE",wait_event(member,gm))
 check("GENERAL_OUTSIDER_SSE",wait_event(outsider,gm))
 denied=feed(VIEWER_ID,start)["items"]
 def is_private(e):
  return e.get("payload",{}).get("conversation_id") in convs.values() or (e.get("entity_type")=="conversation" and str(e.get("entity_id")) in [str(c) for c in convs.values()]) or str(e.get("entity_id")) in [str(m) for m in mids]
 check("PRIVATE_METADATA_POLLING_ZERO",not any(is_private(e) for e in denied) and not any("PRIVATE" in json.dumps(e) or e["type"]=="cursor.advance" for e in denied))
 check("PRIVATE_METADATA_SSE_ZERO",not any(is_private(e) for e in outsider.events))
 replay=Stream(VIEWER_ID,start,header=True)
 check("GENERAL_LAST_EVENT_ID_REPLAY",wait_event(replay,gm))
 check("PRIVATE_LAST_EVENT_ID_REPLAY_ABSENT",not any(is_private(e) for e in replay.events))
 r=req(OWNER_ID,"DELETE",f"/api/internal-messenger/conversations/{group}/members/{EMPLOYEE_ID}")
 check("REMOVE_MEMBER",r.status_code==200,r.status_code)
 removed_mid=post(group,PREFIX+" REMOVED")
 time.sleep(3)
 check("REMOVED_MEMBER_REPLAY_ABSENT",not any(e.get("payload",{}).get("conversation_id")==group or (e.get("entity_type")=="conversation" and str(e.get("entity_id"))==str(group)) for e in feed(EMPLOYEE_ID,start)["items"]))
 check("REMOVED_MEMBER_NEW_SSE_ABSENT",not any(str(e.get("entity_id"))==str(removed_mid) for e in member.events))
 # Real account other than QA is requested read-only with QA employee token.
 cross=dbrow("SELECT account_id FROM accounts WHERE account_id<>:a AND owner_user_id<>:u AND NOT EXISTS (SELECT 1 FROM user_account_access x WHERE x.account_id=accounts.account_id AND x.user_id=:u AND x.can_view=TRUE) ORDER BY account_id LIMIT 1",{"a":ACCOUNT,"u":EMPLOYEE_ID})["account_id"]
 for route in ("events","events/stream"):
  r=req(EMPLOYEE_ID,"GET",f"/api/internal-messenger/{route}?account_id={cross}&after_id={start}")
  check("CROSS_TENANT_"+route,r.status_code==403,r.status_code)
 legacy=create_access_token(EMPLOYEE_ID,security_epoch=old_epoch)
 r=req(EMPLOYEE_ID,"GET",f"/api/internal-messenger/events?account_id={ACCOUNT}",token=legacy);check("LEGACY_NO_SID_401",r.status_code==401)
 r=req(EMPLOYEE_ID,"POST","/api/auth/sessions/revoke",{"session_id":sessions[1][1]},token=extra)
 check("SINGLE_SESSION_REVOKE",r.status_code==200)
 check("REVOKED_API_401",req(EMPLOYEE_ID,"GET",f"/api/internal-messenger/events?account_id={ACCOUNT}").status_code==401)
 check("REVOKED_SSE_CLOSE",member.closed.wait(10) and member.error is None,member.error)
except Exception as exc:check("UNEXPECTED",False,type(exc).__name__+":"+str(exc))
finally:
 for uid,sid in sessions:revoke(uid,sid)
 for s in streams:s.closed.wait(4)
 cleanup_result=cleanup(before_dm)
print(json.dumps({"status":"PASS" if checks and all(v["ok"] for v in checks.values()) else "FAIL","checks":checks,"cleanup":cleanup_result},ensure_ascii=False,indent=2))
raise SystemExit(0 if checks and all(v["ok"] for v in checks.values()) else 1)
