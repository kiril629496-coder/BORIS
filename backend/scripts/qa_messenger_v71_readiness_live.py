"""Live readiness and duplicate invite regression, exact synthetic cleanup."""
from pathlib import Path
exec(Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text().split("access0,team0=set_viewer()")[0])
import uuid
tokens={};sessions=[];checks={};cid=None;oid=None;hashes={};invite_id=None
def req(uid,method,path,body=None):
 return requests.request(method,WEB+path,headers={"Authorization":"Bearer "+tokens[uid]},json=body,timeout=20)
def ready(uid):
 r=req(uid,"GET","/api/internal-messenger/readiness?account_id="+ACCOUNT);assert r.status_code==200,r.status_code;return r.json()
try:
 for u in (OWNER_ID,EMPLOYEE_ID,VIEWER_ID):
  t,s,_=issue(u,"V71 readiness live");tokens[u]=t;sessions.append((u,s))
 before={u:ready(u)["push"] for u in (EMPLOYEE_ID,VIEWER_ID)}
 db=SessionLocal()
 try:
  cid=db.execute(text("INSERT INTO internal_conversations(account_id,kind,title,created_by_user_id) VALUES (:a,'GROUP','QA V71 readiness live',46) RETURNING id"),{"a":ACCOUNT}).scalar()
  db.execute(text("INSERT INTO internal_conversation_members(conversation_id,user_id,role) VALUES (:c,46,'OWNER'),(:c,1932,'MEMBER')"),{"c":cid})
  hashes={u:uuid.uuid4().hex+uuid.uuid4().hex for u in (1932,1933)}
  for u,h in hashes.items():db.execute(text("INSERT INTO lead_push_subscriptions(account_id,user_id,endpoint_hash,subscription_enc,active) VALUES (:a,:u,:h,'QA-NO-PUSH-INACTIVE',false)"),{"a":ACCOUNT,"u":u,"h":h})
  oid=db.execute(text("INSERT INTO lead_notification_outbox(account_id,event_key,event_type,title,body,payload_json,status,next_attempt_at) VALUES (:a,:k,'internal_message','QA','QA',CAST(:p AS jsonb),'pending','2099-01-01') RETURNING id"),{"a":ACCOUNT,"k":"qa-v71-"+uuid.uuid4().hex,"p":json.dumps({"target_user_ids":[1932,1933],"internal_conversation_id":cid})}).scalar()
  for u,h in hashes.items():db.execute(text("INSERT INTO lead_notification_deliveries(outbox_id,channel,target_key,status) VALUES (:o,'webpush',:h,'failed')"),{"o":oid,"h":h[:16]})
  db.commit()
 finally:db.close()
 after={u:ready(u)["push"] for u in (1932,1933)}
 checks["NONMEMBER_PENDING_ABSENT"]=after[1933]["pending_outbox"]==before[1933]["pending_outbox"]
 checks["NONMEMBER_FAILURE_ABSENT"]=after[1933]["failed_deliveries_24h"]==before[1933]["failed_deliveries_24h"]
 checks["MEMBER_PENDING_VISIBLE"]=after[1932]["pending_outbox"]==before[1932]["pending_outbox"]+1
 checks["MEMBER_FAILURE_VISIBLE"]=after[1932]["failed_deliveries_24h"]==before[1932]["failed_deliveries_24h"]+1
 db=SessionLocal()
 try:db.execute(text("DELETE FROM internal_conversation_members WHERE conversation_id=:c AND user_id=1932"),{"c":cid});db.commit()
 finally:db.close()
 removed=ready(1932)["push"]
 checks["REMOVED_MEMBER_PRIVATE_ABSENT"]=removed==before[1932]
 phone="+799"+STAMP[-8:]
 body={"account_id":ACCOUNT,"name":"QA V71 duplicate "+STAMP,"phone":phone,"email":"qa.duplicate."+STAMP+"@example.com","role":"MANAGER"}
 r=req(46,"POST","/api/team/invite",body);assert r.status_code==200,r.status_code
 invite_id=dbrow("SELECT id FROM team_memberships WHERE account_id=:a AND phone=:p AND status='INVITED'",{"a":ACCOUNT,"p":phone})["id"]
 r=req(46,"POST","/api/team/invite",body)
 checks["DUPLICATE_PHONE_409"]=r.status_code==409
 checks["CONTROLLED_RUSSIAN_ERROR"]=r.status_code==409 and "телефоном" in r.json().get("detail","") and "SQL" not in r.text
finally:
 if invite_id:req(46,"POST",f"/api/team/memberships/{invite_id}/revoke-invite")
 db=SessionLocal()
 try:
  if oid:
   db.execute(text("DELETE FROM lead_notification_deliveries WHERE outbox_id=:o"),{"o":oid})
   db.execute(text("DELETE FROM lead_notification_outbox WHERE id=:o AND account_id=:a"),{"o":oid,"a":ACCOUNT})
  for u,h in hashes.items():db.execute(text("DELETE FROM lead_push_subscriptions WHERE account_id=:a AND user_id=:u AND endpoint_hash=:h AND active=false"),{"a":ACCOUNT,"u":u,"h":h})
  if cid:db.execute(text("DELETE FROM internal_conversations WHERE id=:c AND account_id=:a"),{"c":cid,"a":ACCOUNT})
  db.commit()
 finally:db.close()
 for u,s in sessions:revoke(u,s)
print(json.dumps({"status":"PASS" if checks and all(checks.values()) else "FAIL","checks":checks,"cleanup":"exact QA rows removed; invite revoked; audit retained"},ensure_ascii=False,indent=2))
assert checks and all(checks.values())
