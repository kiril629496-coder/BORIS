"""Exact readiness SQL transaction: no committed queue, subscription or delivery."""
import ast,json,sys,uuid
from pathlib import Path
sys.path.insert(0,"/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)
from sqlalchemy import text
from app.db.session import SessionLocal
tree=ast.parse(Path("/root/BORIS/backend/app/api/internal_messenger.py").read_text())
fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="internal_messenger_readiness")
queries={}
for n in ast.walk(fn):
 if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id in ("push_failed_24h","push_pending"):
  queries[n.targets[0].id]=next(x.args[0].value for x in ast.walk(n) if isinstance(x,ast.Call) and isinstance(x.func,ast.Name) and x.func.id=="text")
db=SessionLocal();checks={}
def counts(uid):return [db.execute(text(queries[k]),{"a":"qa_dry_moskva","u":uid}).scalar() for k in ("push_failed_24h","push_pending")]
try:
 base={u:counts(u) for u in (46,1932,1933)}
 c=db.execute(text("INSERT INTO internal_conversations(account_id,kind,title,created_by_user_id) VALUES ('qa_dry_moskva','GROUP','QA readiness rollback',46) RETURNING id")).scalar()
 db.execute(text("INSERT INTO internal_conversation_members(conversation_id,user_id,role) VALUES (:c,46,'OWNER'),(:c,1932,'MEMBER')"),{"c":c})
 h=uuid.uuid4().hex
 db.execute(text("INSERT INTO lead_push_subscriptions(account_id,user_id,endpoint_hash,subscription_enc,active) VALUES ('qa_dry_moskva',1932,:h,'qa-never-committed',false)"),{"h":h})
 o=db.execute(text("INSERT INTO lead_notification_outbox(account_id,event_key,event_type,title,body,payload_json) VALUES ('qa_dry_moskva',:k,'internal_message','QA','secret',CAST(:p AS jsonb)) RETURNING id"),{"k":h,"p":json.dumps({"target_user_ids":[1932],"internal_conversation_id":c})}).scalar()
 db.execute(text("INSERT INTO lead_notification_deliveries(outbox_id,channel,target_key,status) VALUES (:o,'webpush',:h,'failed')"),{"o":o,"h":h[:16]})
 checks["own_counts_visible"]=counts(1932)==[n+1 for n in base[1932]]
 checks["nonmember_counts_absent"]=counts(1933)==base[1933]
 checks["owner_not_recipient_counts_absent"]=counts(46)==base[46]
 db.execute(text("DELETE FROM internal_conversation_members WHERE conversation_id=:c AND user_id=1932"),{"c":c})
 checks["removed_member_counts_absent"]=counts(1932)==base[1932]
 print(json.dumps({"status":"PASS" if all(checks.values()) else "FAIL","checks":checks}))
 assert all(checks.values())
finally:db.rollback();db.close()
