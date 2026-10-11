#!/usr/bin/env python3
"""Runtime private event / monotonic read / session revoke checks in QA tenant."""
from pathlib import Path
ns={}
exec(Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text().split("access0,team0=set_viewer()")[0],ns)
import json,threading,time,requests
from sqlalchemy import text
web=ns["WEB"];account=ns["ACCOUNT"];owner=ns["OWNER_ID"];employee=ns["EMPLOYEE_ID"];other=ns["VIEWER_ID"]
tokens={};sessions=[];group=None;checks={};responses=[]
def req(uid,method,path,body=None,token=None):
    return requests.request(method,web+path,headers={"Authorization":"Bearer "+(token or tokens[uid])},json=body,timeout=20)
def check(name,ok,detail=None):
    checks[name]={"ok":bool(ok),"detail":detail}
try:
    for uid in (owner,employee,other):
        token,sid,epoch=ns["issue"](uid,"V7 security acceptance")
        tokens[uid]=token;sessions.append((uid,sid))
    second,second_sid,epoch=ns["issue"](employee,"V7 revoker");sessions.append((employee,second_sid))
    r=req(owner,"POST","/api/internal-messenger/conversations",{"account_id":account,"kind":"GROUP","title":ns["GROUP_TITLE"],"member_user_ids":[employee]})
    assert r.status_code==200,r.status_code
    group=r.json()["conversation_id"]
    secret=ns["PREFIX"]+" PRIVATE"
    r=req(owner,"POST",f"/api/internal-messenger/conversations/{group}/messages",{"body":secret});assert r.status_code==200
    mid=r.json()["message"]["id"]
    cursor=ns["dbrow"]("SELECT id FROM internal_messenger_events WHERE event_type='internal.message.created' AND entity_id=:id ORDER BY id DESC LIMIT 1",{"id":str(mid)})["id"]-1
    for uid,label in ((employee,"MEMBER_EVENT_BODY"),(other,"NONMEMBER_EVENT_REDACTED")):
        r=req(uid,"GET",f"/api/internal-messenger/events?account_id={account}&after_id={cursor}")
        assert r.status_code==200
        event=next(e for e in r.json()["items"] if e["id"]==cursor+1)
        check(label,event["payload"].get("body")==secret if uid==employee else event["type"]=="cursor.advance" and event["payload"]=={} and event["entity_id"] is None)
    sql="SELECT count(*) AS n FROM internal_messenger_events WHERE account_id=:a AND event_type='internal.message.read' AND entity_id=:c AND payload_json->>'user_id'=:u"
    params={"a":account,"c":str(group),"u":str(employee)}
    before=ns["dbrow"](sql,params)["n"]
    for _ in range(3):
        assert req(employee,"POST",f"/api/internal-messenger/conversations/{group}/read?message_id={mid}").status_code==200
    check("READ_RECEIPT_IDEMPOTENT",ns["dbrow"](sql,params)["n"]-before==1)
    ready=threading.Event();closed=threading.Event();stream_status=[]
    def stream():
        try:
            with requests.get(web+f"/api/internal-messenger/events/stream?account_id={account}&after_id={cursor}",headers={"Authorization":"Bearer "+tokens[employee]},stream=True,timeout=(10,15)) as response:
                responses.append(response);stream_status.append(response.status_code);ready.set()
                for line in response.iter_lines(chunk_size=1):pass
        except Exception as exc:
            stream_status.append(type(exc).__name__)
        finally:closed.set()
    thread=threading.Thread(target=stream,daemon=True);thread.start();assert ready.wait(12)
    r=req(employee,"POST","/api/auth/sessions/revoke",{"session_id":sessions[1][1]},token=second)
    check("SESSION_REVOKE_REQUEST",r.status_code==200,r.status_code)
    r=req(employee,"GET",f"/api/internal-messenger/events?account_id={account}")
    check("SESSION_REVOKE_IMMEDIATE",r.status_code==401,r.status_code)
    check("SSE_REVOKE_CLOSE",closed.wait(10) and stream_status==[200],stream_status)
    r=req(owner,"DELETE",f"/api/internal-messenger/conversations/{group}/members/{employee}")
    check("MEMBER_REMOVE",r.status_code==200,r.status_code)
    r=req(employee,"GET",f"/api/internal-messenger/events?account_id={account}&after_id={cursor}",token=second)
    check("REMOVED_MEMBER_EVENT_REDACTED",r.status_code==200 and all(not str(e["type"]).startswith("internal.") or e["payload"].get("conversation_id")!=group for e in r.json().get("items",[])) and secret not in r.text)
except Exception as exc:
    check("UNEXPECTED",False,type(exc).__name__+":"+str(exc))
finally:
    for response in responses:response.close()
    for uid,sid in sessions:ns["revoke"](uid,sid)
    if group is not None:
        db=ns["SessionLocal"]()
        try:
            db.execute(text("DELETE FROM internal_messenger_events WHERE account_id=:a AND (payload_json->>'conversation_id'=:c OR (entity_type='conversation' AND entity_id=:c))"),{"a":account,"c":str(group)})
            db.execute(text("DELETE FROM internal_conversations WHERE id=:c AND account_id=:a AND title=:t"),{"c":group,"a":account,"t":ns["GROUP_TITLE"]})
            db.commit()
        finally:db.close()
print(json.dumps({"status":"PASS" if checks and all(x["ok"] for x in checks.values()) else "FAIL","checks":checks},ensure_ascii=False,indent=2))
raise SystemExit(0 if checks and all(x["ok"] for x in checks.values()) else 1)
