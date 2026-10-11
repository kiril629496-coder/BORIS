"""Transactional QA of exact candidate feed; no persisted messages/events."""
import ast,json,sys
from pathlib import Path
sys.path.insert(0,"/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)
from sqlalchemy import text
from app.db.session import SessionLocal
source=Path("/root/BORIS/backend/app/api/internal_messenger.py")
tree=ast.parse(source.read_text());ns={"text":text}
for name in ("_iso","_event_for_memberships","_messenger_event_feed"):
 node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
 exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),"exec"),ns)
db=SessionLocal();checks={}
try:
 assert db.execute(text("SELECT 1 FROM accounts WHERE account_id='qa_dry_moskva'")).first()
 c=db.execute(text("INSERT INTO internal_conversations(account_id,kind,title,created_by_user_id) VALUES ('qa_dry_moskva','GROUP','QA V71 transaction',46) RETURNING id")).scalar()
 db.execute(text("INSERT INTO internal_conversation_members(conversation_id,user_id,role) VALUES (:c,46,'OWNER'),(:c,1932,'MEMBER')"),{"c":c})
 m=db.execute(text("INSERT INTO internal_messages(conversation_id,sender_user_id,body) VALUES (:c,46,'QA private secret') RETURNING id"),{"c":c}).scalar()
 before=db.execute(text("SELECT COALESCE(max(id),0) FROM internal_messenger_events")).scalar()
 ids=[]
 for kind,entity,eid,payload in [
 ("internal.conversation.created","conversation",c,{"title":"secret group"}),
 ("internal.message.created","message",m,{"conversation_id":c,"body":"secret"}),
 ("internal.message.reaction_changed","message",m,{"conversation_id":c,"reaction":"👍"}),
 ("internal.message.pin_changed","message",m,{"conversation_id":c,"pinned":True}),
 ("internal.message.updated","message",m,{"conversation_id":c,"body":"edit"}),
 ("internal.message.deleted","message",m,{"conversation_id":c}),
 ("internal.conversation.member_added","conversation",c,{"user_id":1932}),
 ("unknown.private","conversation",c,{"body":"unknown secret"})]:
  ids.append(db.execute(text("INSERT INTO internal_messenger_events(account_id,event_type,entity_type,entity_id,payload_json) VALUES ('qa_dry_moskva',:t,:et,:id,CAST(:p AS JSONB)) RETURNING id"),{"t":kind,"et":entity,"id":str(eid),"p":json.dumps(payload)}).scalar())
 feed=ns["_messenger_event_feed"]
 member=feed(db,"qa_dry_moskva",before,user_id=1932)
 outsider=feed(db,"qa_dry_moskva",before,user_id=1933)
 checks["all_private_event_kinds_member"]=len(member)==7
 checks["nonmember_no_event_or_placeholder"]=outsider==[]
 checks["cross_tenant_empty"]=feed(db,"qa_other_nonexistent",before,user_id=1932)==[]
 db.execute(text("DELETE FROM internal_conversation_members WHERE conversation_id=:c AND user_id=1932"),{"c":c})
 checks["removed_member_replay_empty"]=feed(db,"qa_dry_moskva",before,user_id=1932)==[]
 for _ in range(210):
  db.execute(text("INSERT INTO internal_messenger_events(account_id,event_type,entity_type,entity_id,payload_json) VALUES ('qa_dry_moskva','internal.message.created','message',:id,CAST(:p AS JSONB))"),{"id":str(m),"p":json.dumps({"conversation_id":c,"body":"private"})})
 public_id=db.execute(text("INSERT INTO internal_messenger_events(account_id,event_type,entity_type,payload_json) VALUES ('qa_dry_moskva','team.changed','membership','{\"private\":\"never emit this\"}') RETURNING id")).scalar()
 visible=feed(db,"qa_dry_moskva",before,1,user_id=1933)
 checks["filter_before_limit"]=len(visible)==1 and visible[0]["id"]==public_id
 checks["account_invalidation_payload_minimized"]=visible[0]["payload"]=={} and visible[0]["entity_id"] is None
 print(json.dumps(checks))
 assert all(checks.values())
finally:db.rollback();db.close()
