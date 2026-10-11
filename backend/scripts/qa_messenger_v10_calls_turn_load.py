"""BORIS Messenger V10 bounded Calls + TURN load QA on QA tenant only."""
from pathlib import Path
exec(Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text().split("access0,team0=set_viewer()")[0])

import concurrent.futures
import os
import secrets
import statistics
import time
from uuid import uuid4

from app.api.auth import hash_password
from app.services.auth_sessions import issue_session

for _key in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy"):
    os.environ.pop(_key, None)
os.environ["NO_PROXY"]="127.0.0.1,localhost,boris-ai.pro"
os.environ["no_proxy"]=os.environ["NO_PROXY"]

PREFIX="QA-V10-CALL-LOAD-"+STAMP
users=[]
conversations=[]
calls=[]
history_messages=[]
tokens={}
checks={}
metrics={}
turn_result={}
password_hash=hash_password(secrets.token_urlsafe(36))


def wait_api_generation(timeout=360):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        codes=[]
        for port in (8000,8001):
            try:
                r=requests.post(f"http://127.0.0.1:{port}/api/internal-messenger/telemetry",
                                headers={"Content-Type":"application/json"},json={},timeout=3)
                codes.append(r.status_code)
            except Exception:
                codes.append(0)
        if codes==[401,401]: return True
        time.sleep(3)
    return False


def create_users_and_dms():
    db=SessionLocal()
    try:
        for i in range(20):
            u=User(email=f"{PREFIX.lower()}.{i}@example.com",password_hash=password_hash,role="client",is_active=True,status="active")
            db.add(u); db.flush(); users.append(int(u.id))
            db.execute(text("""INSERT INTO user_account_access(user_id,account_id,role,can_view,can_reply)
                VALUES (:u,:a,'MANAGER',true,true)"""),{"u":u.id,"a":ACCOUNT})
            db.execute(text("""
                INSERT INTO messenger_legal_acceptances(
                    user_id,document_type,document_version,document_hash,document_title,document_snapshot,
                    accepted_at,ip_hash,user_agent,session_id)
                SELECT :u,x.document_type,x.document_version,x.document_hash,x.document_title,x.document_snapshot,
                       now(),NULL,'BORIS V10 QA',NULL
                FROM (
                    SELECT DISTINCT ON (document_type)
                           document_type,document_version,document_hash,document_title,document_snapshot
                    FROM messenger_legal_acceptances
                    WHERE user_id=:owner
                    ORDER BY document_type,accepted_at DESC
                ) x
                ON CONFLICT DO NOTHING
            """),{"u":u.id,"owner":OWNER_ID})
        for i in range(10):
            a,b=users[2*i],users[2*i+1]
            cid=int(db.execute(text("""INSERT INTO internal_conversations(account_id,kind,title,created_by_user_id)
                 VALUES (:acct,'DM',NULL,:u) RETURNING id"""),{"acct":ACCOUNT,"u":a}).scalar_one())
            conversations.append(cid)
            db.execute(text("""INSERT INTO internal_conversation_members(conversation_id,user_id,role)
                 VALUES (:c,:a,'MEMBER'),(:c,:b,'MEMBER')"""),{"c":cid,"a":a,"b":b})
        db.commit()
    finally: db.close()

    for uid in users:
        db=SessionLocal()
        try:
            u=db.query(User).filter(User.id==uid).one()
            epoch=int(getattr(u,"security_epoch",0) or 0)
            sid,expires=issue_session(db,uid,epoch,user_agent="BORIS V10 Call Load",ip="127.0.0.1",device_label="V10 call load")
            db.commit()
            tokens[uid]=create_access_token(uid,session_id=sid,security_epoch=epoch,expires_at=expires)
        finally: db.close()


def post(uid,path,body):
    started=time.monotonic()
    r=requests.post(WEB+"/api/internal-messenger"+path,
                    headers={"Authorization":"Bearer "+tokens[uid],"Content-Type":"application/json"},
                    json=body,timeout=30)
    elapsed=(time.monotonic()-started)*1000
    if r.status_code!=200:
        raise AssertionError((uid,path,r.status_code,r.text[:500]))
    return r.json(),elapsed


def start_one(index,client_id=None):
    uid=users[index*2]
    cid=conversations[index]
    client=client_id or str(uuid4())
    payload,ms=post(uid,f"/conversations/{cid}/calls",{"client_call_id":client,"call_type":"video" if index%2 else "audio"})
    return index,payload["call"],ms,client


def transition(index,call_id,uid,action):
    payload,ms=post(uid,f"/calls/{call_id}/transition",{"action":action,"session_id":str(uuid4()) if action=="accept" else None})
    return payload["call"],ms


def signal(index,call_id,uid,event_type,payload):
    _,ms=post(uid,f"/calls/{call_id}/signal",{"event_type":event_type,"payload":payload})
    return ms


def turn_browser_probe(ice_servers, count, label):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        page=browser.new_page()
        try:
            result=page.evaluate("""async ({servers,count,label})=>{
              async function one(i){
                const a=new RTCPeerConnection({iceServers:servers,iceTransportPolicy:'relay'});
                const b=new RTCPeerConnection({iceServers:servers,iceTransportPolicy:'relay'});
                const dc=a.createDataChannel('qa'+i);
                let got=false;
                b.ondatachannel=e=>{e.channel.onmessage=()=>{got=true}};
                a.onicecandidate=e=>{if(e.candidate)b.addIceCandidate(e.candidate).catch(()=>{})};
                b.onicecandidate=e=>{if(e.candidate)a.addIceCandidate(e.candidate).catch(()=>{})};
                const started=performance.now();
                const offer=await a.createOffer(); await a.setLocalDescription(offer); await b.setRemoteDescription(offer);
                const answer=await b.createAnswer(); await b.setLocalDescription(answer); await a.setRemoteDescription(answer);
                const deadline=Date.now()+15000;
                while(a.connectionState!=='connected' && Date.now()<deadline) await new Promise(r=>setTimeout(r,50));
                if(a.connectionState!=='connected') throw new Error(label+' pair '+i+' connect timeout '+a.connectionState);
                dc.send('ping');
                const msgDeadline=Date.now()+5000;
                while(!got && Date.now()<msgDeadline) await new Promise(r=>setTimeout(r,20));
                const stats=await a.getStats();
                let relay=false,transport='';
                stats.forEach(r=>{
                  if(r.type==='local-candidate' && r.candidateType==='relay'){relay=true; transport=String(r.protocol||transport)}
                });
                const ms=performance.now()-started;
                a.close();b.close();
                if(!got||!relay) throw new Error(label+' pair '+i+' relay/data failed');
                return {ms,transport};
              }
              return await Promise.all(Array.from({length:count},(_,i)=>one(i)));
            }""",{"servers":ice_servers,"count":count,"label":label})
            return result
        finally:
            browser.close()


try:
    if not wait_api_generation():
        raise AssertionError("api_generation_not_converged")
    create_users_and_dms()

    # Pair 0: emulate eight concurrent retries after a lost HTTP response.
    dup_client=str(uuid4())
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        dup=list(pool.map(lambda _:start_one(0,dup_client),range(8)))
    dup_ids={row[1]["call_id"] for row in dup}
    checks["CALL_START_IDEMPOTENCY_8"]={"ok":len(dup_ids)==1,"requests":8,"call_ids":len(dup_ids)}
    if len(dup_ids)!=1: raise AssertionError("call_duplicate_detected")
    first_call=dup[0][1]
    calls.append(first_call["call_id"])

    # Remaining nine pairs start simultaneously; together there are ten live calls.
    with concurrent.futures.ThreadPoolExecutor(max_workers=9) as pool:
        rest=list(pool.map(start_one,range(1,10)))
    start_rows=[dup[0],*rest]
    for _,call,_,_ in rest: calls.append(call["call_id"])
    start_lat=[row[2] for row in start_rows]

    setup=[]
    reconnect=[]
    signal_ms=[]
    for index,call,_,_ in start_rows:
        cid=call["call_id"]; peer=users[index*2+1]; caller=users[index*2]
        t0=time.monotonic()
        _,ms=transition(index,cid,peer,"accept"); setup.append(ms)
        signal_ms.append(signal(index,cid,caller,"offer",{"description":{"type":"offer","sdp":"v=0\r\n"}}))
        signal_ms.append(signal(index,cid,peer,"answer",{"description":{"type":"answer","sdp":"v=0\r\n"}}))
        signal_ms.append(signal(index,cid,caller,"ice",{"candidate":{"candidate":"candidate:qa","sdpMid":"0","sdpMLineIndex":0}}))
        _,ms=transition(index,cid,caller,"connecting"); setup.append(ms)
        _,ms=transition(index,cid,caller,"connected"); setup.append(ms)
        signal_ms.append(signal(index,cid,caller,"heartbeat",{"state":"connected"}))
        signal_ms.append(signal(index,cid,peer,"heartbeat",{"state":"connected"}))
        r0=time.monotonic()
        transition(index,cid,caller,"reconnecting")
        transition(index,cid,caller,"connected")
        reconnect.append((time.monotonic()-r0)*1000)
        setup.append((time.monotonic()-t0)*1000)

    # Confirm exactly 10 concurrent calls reached connected.
    db=SessionLocal()
    try:
        connected=int(db.execute(text("SELECT count(*) FROM internal_calls WHERE id::text=ANY(:ids) AND state='connected'"),{"ids":calls}).scalar() or 0)
    finally:db.close()
    checks["CALLS_10_CONNECTED"]={"ok":connected==10,"connected":connected}

    metrics["calls"]={
        "calls":10,
        "start_median_ms":round(statistics.median(start_lat),1),
        "start_max_ms":round(max(start_lat),1),
        "setup_step_median_ms":round(statistics.median(setup),1),
        "setup_max_ms":round(max(setup),1),
        "signal_median_ms":round(statistics.median(signal_ms),1),
        "reconnect_median_ms":round(statistics.median(reconnect),1),
        "reconnect_max_ms":round(max(reconnect),1),
    }

    # Fetch production ephemeral TURN credentials through the authenticated API.
    r=requests.get(WEB+"/api/internal-messenger/calls/ice-config",headers={"Authorization":"Bearer "+tokens[users[0]]},timeout=20)
    assert r.status_code==200,(r.status_code,r.text[:500])
    ice=r.json().get("iceServers") or []
    assert ice,ice

    def filtered(mode):
        selected=[]
        for server in ice:
            urls=server.get("urls") or []
            if isinstance(urls,str): urls=[urls]
            if mode=="udp": urls=[u for u in urls if "transport=udp" in u]
            elif mode=="tcp": urls=[u for u in urls if "transport=tcp" in u and not u.startswith("turns:")]
            elif mode=="tls": urls=[u for u in urls if u.startswith("turns:")]
            if urls:
                selected.append({**server,"urls":urls})
        return selected

    udp=filtered("udp"); tcp=filtered("tcp"); tls=filtered("tls")
    assert udp and tcp and tls,{"udp":udp,"tcp":tcp,"tls":tls}
    udp_rows=turn_browser_probe(udp,10,"udp")
    tcp_rows=turn_browser_probe(tcp,1,"tcp")
    tls_rows=turn_browser_probe(tls,1,"tls")
    turn_result={
        "udp_10":{"ok":len(udp_rows)==10,"median_ms":round(statistics.median(x["ms"] for x in udp_rows),1),"max_ms":round(max(x["ms"] for x in udp_rows),1),"transports":sorted({x["transport"] for x in udp_rows})},
        "tcp_1":{"ok":len(tcp_rows)==1,"ms":round(tcp_rows[0]["ms"],1),"transport":tcp_rows[0]["transport"]},
        "tls_1":{"ok":len(tls_rows)==1,"ms":round(tls_rows[0]["ms"],1),"transport":tls_rows[0]["transport"]},
    }
    checks["TURN_LOAD_UDP_10"]={"ok":turn_result["udp_10"]["ok"]}
    checks["TURN_TCP"]={"ok":turn_result["tcp_1"]["ok"]}
    checks["TURN_TLS"]={"ok":turn_result["tls_1"]["ok"]}

    # End all calls and collect history ids for exact cleanup.
    for index,call,_,_ in start_rows:
        transition(index,call["call_id"],users[index*2],"end")
    db=SessionLocal()
    try:
        rows=db.execute(text("SELECT id,history_message_id,state FROM internal_calls WHERE id::text=ANY(:ids)"),{"ids":calls}).mappings().all()
        history_messages.extend(int(x["history_message_id"]) for x in rows if x["history_message_id"])
        checks["CALLS_TERMINAL"]={"ok":len(rows)==10 and all(x["state"]=="ended" for x in rows),"rows":len(rows)}
    finally:db.close()

finally:
    try:
        db=SessionLocal()
        try:
            if calls:
                db.execute(text("DELETE FROM internal_call_events WHERE call_id::text=ANY(:ids)"),{"ids":calls})
                db.execute(text("DELETE FROM internal_call_participants WHERE call_id::text=ANY(:ids)"),{"ids":calls})
                db.execute(text("DELETE FROM internal_messenger_events WHERE payload_json->>'call_id'=ANY(:ids)"),{"ids":calls})
                db.execute(text("DELETE FROM internal_calls WHERE id::text=ANY(:ids)"),{"ids":calls})
            if history_messages:
                mids=[str(x) for x in history_messages]
                db.execute(text("DELETE FROM internal_messenger_events WHERE entity_type='message' AND entity_id=ANY(:ids)"),{"ids":mids})
                db.execute(text("DELETE FROM internal_messages WHERE id=ANY(:ids)"),{"ids":history_messages})
            if conversations:
                db.execute(text("DELETE FROM internal_conversations WHERE id=ANY(:ids)"),{"ids":conversations})
            if users:
                db.execute(text("DELETE FROM users WHERE id=ANY(:ids)"),{"ids":users})
            db.commit()
            checks["CLEANUP"]={"ok":True,"users":len(users),"conversations":len(conversations),"calls":len(calls)}
        finally:db.close()
    except Exception as exc:
        checks["CLEANUP"]={"ok":False,"error":type(exc).__name__+":"+str(exc)}

ok=checks and all(v.get("ok") for v in checks.values())
print(json.dumps({"status":"PASS" if ok else "FAIL","metrics":metrics,"turn":turn_result,"checks":checks},ensure_ascii=False,default=str,indent=2))
raise SystemExit(0 if ok else 1)
