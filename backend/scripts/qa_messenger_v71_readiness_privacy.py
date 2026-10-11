"""Exact source readiness privacy, synthetic transaction rolled back in finally."""
import ast,json,sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
sys.path.insert(0,"/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)
from sqlalchemy import text
from fastapi import HTTPException
from app.db.session import SessionLocal
source=Path("/root/BORIS/backend/app/api/internal_messenger.py")
tree=ast.parse(source.read_text())
db=SessionLocal()
class BorrowedSession:
 def execute(self,*a,**kw):return db.execute(*a,**kw)
 def close(self):pass
ns={"text":text,"HTTPException":HTTPException,"SessionLocal":BorrowedSession,
    "is_private_platform_owner":lambda user:False,"GENERAL_CHAT_TITLE":"Общий чат BORIS"}
for name in ("_access","internal_messenger_readiness"):
 node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
 node.decorator_list=[]
 if name=='internal_messenger_readiness':node.args.defaults=[]
 for arg in node.args.args:arg.annotation=None
 exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),"exec"),ns)
ready=ns["internal_messenger_readiness"]
def snapshot(uid):return ready("qa_dry_moskva",SimpleNamespace(id=uid))
checks={}
try:
 assert db.execute(text("SELECT 1 FROM accounts WHERE account_id='qa_dry_moskva' AND owner_user_id=46")).first()
 before={uid:snapshot(uid) for uid in (1932,1933)}
 c=db.execute(text("INSERT INTO internal_conversations(account_id,kind,title,created_by_user_id) VALUES ('qa_dry_moskva','GROUP','QA readiness transaction',46) RETURNING id")).scalar()
 db.execute(text("INSERT INTO internal_conversation_members(conversation_id,user_id,role) VALUES (:c,46,'OWNER'),(:c,1932,'MEMBER')"),{"c":c})
 hashes={uid:uuid4().hex+uuid4().hex for uid in (1932,1933)}
 for uid,h in hashes.items():
  db.execute(text("INSERT INTO lead_push_subscriptions(account_id,user_id,endpoint_hash,subscription_enc,active) VALUES ('qa_dry_moskva',:u,:h,'QA-NOT-A-SUBSCRIPTION',FALSE)"),{"u":uid,"h":h})
 # Even a payload explicitly naming the nonmember cannot make private activity visible.
 outbox=db.execute(text("INSERT INTO lead_notification_outbox(account_id,event_key,event_type,title,body,payload_json,status) VALUES ('qa_dry_moskva',:key,'internal_message','QA','QA',CAST(:p AS JSONB),'pending') RETURNING id"),
  {"key":"qa-readiness-"+uuid4().hex,"p":json.dumps({"target_user_ids":[1932,1933],"internal_conversation_id":c})}).scalar()
 for uid,h in hashes.items():
  db.execute(text("INSERT INTO lead_notification_deliveries(outbox_id,channel,target_key,status) VALUES (:o,'webpush',:h,'failed')"),{"o":outbox,"h":h[:16]})
 after={uid:snapshot(uid) for uid in (1932,1933)}
 checks["NONMEMBER_PENDING_PRIVATE_ABSENT"]=after[1933]["push"]["pending_outbox"]==before[1933]["push"]["pending_outbox"]
 checks["NONMEMBER_FAILED_PRIVATE_ABSENT"]=after[1933]["push"]["failed_deliveries_24h"]==before[1933]["push"]["failed_deliveries_24h"]
 checks["MEMBER_PENDING_VISIBLE"]=after[1932]["push"]["pending_outbox"]==before[1932]["push"]["pending_outbox"]+1
 checks["MEMBER_FAILED_VISIBLE"]=after[1932]["push"]["failed_deliveries_24h"]==before[1932]["push"]["failed_deliveries_24h"]+1
 db.execute(text("DELETE FROM internal_conversation_members WHERE conversation_id=:c AND user_id=1932"),{"c":c})
 removed=snapshot(1932)
 checks["REMOVED_MEMBER_PENDING_PRIVATE_ABSENT"]=removed["push"]["pending_outbox"]==before[1932]["push"]["pending_outbox"]
 checks["REMOVED_MEMBER_FAILED_PRIVATE_ABSENT"]=removed["push"]["failed_deliveries_24h"]==before[1932]["push"]["failed_deliveries_24h"]
 serialized=json.dumps(after)
 checks["NO_ENDPOINT_OR_PAYLOAD"]=not any(x in serialized for x in ["endpoint","subscription_enc","QA-NOT-A-SUBSCRIPTION",*hashes.values()])
 print(json.dumps({"status":"PASS" if all(checks.values()) else "FAIL","checks":checks,"persistence":"ROLLBACK","source":str(source)}))
 assert all(checks.values())
finally:db.rollback();db.close()
