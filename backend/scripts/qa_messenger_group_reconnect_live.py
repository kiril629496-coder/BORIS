#!/usr/bin/env python3
"""BORIS Messenger: live 4-browser group call reconnect/leave acceptance."""
from pathlib import Path
import os, time, json

for key in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy","ALL_PROXY","all_proxy"):
    os.environ.pop(key, None)

helper = Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text(encoding="utf-8")
exec(helper.split("access0,team0=set_viewer()")[0])

from sqlalchemy import text
from app.db.session import SessionLocal

USERS = [2341,2342,2343,2344]
CALLER, A, B, C = USERS


def current_builds():
    import subprocess
    out = {}
    for unit in ("boris-backend.service","boris-backend-replica.service","boris-background-worker.service"):
        pid = subprocess.check_output(["systemctl","show",unit,"-p","MainPID","--value"], text=True).strip()
        build = ""
        if pid and pid != "0":
            try:
                raw = Path("/proc")/pid/"cmdline"
                cmd = raw.read_bytes().replace(b"\0",b" ").decode("utf-8","replace")
                marker="/releases/backend/"
                if marker in cmd:
                    build=cmd.split(marker,1)[1].split("/backend",1)[0].strip("/")
            except Exception:
                pass
        out[unit]=build
    return out


def cleanup(cid, sessions):
    db=SessionLocal()
    try:
        if cid:
            mids=list(db.execute(text("SELECT id FROM internal_messages WHERE conversation_id=:c"),{"c":cid}).scalars().all())
            db.execute(text(
                "DELETE FROM internal_messenger_events WHERE account_id=:a AND "
                "((entity_type='conversation' AND entity_id=:c) OR "
                "(entity_type='message' AND entity_id=ANY(CAST(:m AS text[]))))"
            ),{"a":ACCOUNT,"c":str(cid),"m":[str(x) for x in mids] or ["-1"]})
            db.execute(text("DELETE FROM internal_conversations WHERE id=:c"),{"c":cid})
            db.commit()
    except Exception as exc:
        db.rollback()
        print("CLEANUP_ERR",type(exc).__name__,str(exc)[:500],flush=True)
    finally:
        db.close()
    for uid,sid in sessions:
        try: revoke(uid,sid)
        except Exception: pass


def tracks(page):
    return page.evaluate("""() => Array.from(document.querySelectorAll('video')).reduce((n,v)=>{
      const s=v.srcObject;
      return n+(s&&s.getTracks?s.getTracks().filter(t=>t.readyState==='live').length:0);
    },0)""")


def main():
    builds=current_builds()
    values={v for v in builds.values() if v}
    if len(values) != 1:
        raise AssertionError("backend_generation_not_converged:"+json.dumps(builds))
    print("BUILDS",json.dumps(builds),flush=True)

    sessions=[]
    cid=None
    call_id=None
    checks={}
    tokens={}
    try:
        for uid in USERS:
            token,sid,_=issue(uid,f"Group Live QA {uid}")
            tokens[uid]=token
            sessions.append((uid,sid))

        db=SessionLocal()
        try:
            cid=int(db.execute(text(
                "INSERT INTO internal_conversations(account_id,kind,title,created_by_user_id) "
                "VALUES (:a,'GROUP','QA Group Live Reconnect',:u) RETURNING id"
            ),{"a":ACCOUNT,"u":CALLER}).scalar_one())
            for uid in USERS:
                db.execute(text(
                    "INSERT INTO internal_conversation_members(conversation_id,user_id,role) "
                    "VALUES (:c,:u,'MEMBER')"
                ),{"c":cid,"u":uid})
            db.commit()
        finally:
            db.close()

        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,args=[
                "--no-sandbox",
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
                "--proxy-server=direct://",
                "--proxy-bypass-list=*",
                "--host-resolver-rules=MAP boris-ai.pro 127.0.0.1,EXCLUDE localhost",
            ])
            contexts={uid:browser.new_context(permissions=["microphone","camera"]) for uid in USERS}
            pages={uid:contexts[uid].new_page() for uid in USERS}
            for uid in USERS:
                workspace(pages[uid],tokens[uid])
                pages[uid].goto(
                    WEB+f"/messenger?account_id={ACCOUNT}&section=chats&internal_conversation_id={cid}",
                    wait_until="domcontentloaded",timeout=30000
                )
                pages[uid].wait_for_timeout(1400)

            caller,pa,pb,pc=pages[CALLER],pages[A],pages[B],pages[C]
            caller.get_by_role("button",name="Аудиозвонок").wait_for(state="visible",timeout=15000)
            caller.get_by_role("button",name="Аудиозвонок").click()
            for page in (pa,pb,pc):
                page.get_by_role("dialog",name="Входящий звонок").wait_for(state="visible",timeout=15000)
            checks["ring_3"]=True

            pa.get_by_role("button",name="Принять",exact=True).click()
            pc.get_by_role("button",name="Отклонить",exact=True).click()

            deadline=time.monotonic()+25
            state=""
            while time.monotonic()<deadline:
                db=SessionLocal()
                try:
                    row=db.execute(text(
                        "SELECT id,state FROM internal_calls WHERE conversation_id=:c ORDER BY created_at DESC LIMIT 1"
                    ),{"c":cid}).mappings().first()
                    call_id=str(row["id"]) if row else None
                    state=str(row["state"]) if row else ""
                finally:
                    db.close()
                if state=="connected": break
                time.sleep(.4)
            checks["first_connected"]=state=="connected"
            if state!="connected":
                raise AssertionError("group_not_connected")

            pb.get_by_role("button",name="Принять",exact=True).click()
            deadline=time.monotonic()+30
            late=False
            while time.monotonic()<deadline:
                db=SessionLocal()
                try:
                    pstate=db.execute(text(
                        "SELECT state FROM internal_call_participants WHERE call_id=:id AND user_id=:u"
                    ),{"id":call_id,"u":B}).scalar_one()
                    cstate=db.execute(text("SELECT state FROM internal_calls WHERE id=:id"),{"id":call_id}).scalar_one()
                finally:
                    db.close()
                if pstate=="connected" and cstate=="connected":
                    late=True
                    break
                time.sleep(.4)
            checks["late_join"]=late

            deadline=time.monotonic()+20
            fanout=False
            snapshot={}
            while time.monotonic()<deadline:
                snapshot={uid:tracks(pages[uid]) for uid in (CALLER,A,B)}
                if snapshot[CALLER]>=2 and snapshot[A]>=1 and snapshot[B]>=1:
                    fanout=True
                    break
                time.sleep(.4)
            checks["media_fanout"]=fanout
            print("MEDIA",json.dumps(snapshot),flush=True)

            contexts[B].set_offline(True)
            time.sleep(8)
            db=SessionLocal()
            try:
                during=db.execute(text("SELECT state FROM internal_calls WHERE id=:id"),{"id":call_id}).scalar_one()
            finally:
                db.close()
            checks["group_connected_during_one_offline"]=during=="connected"
            contexts[B].set_offline(False)

            deadline=time.monotonic()+40
            recovered=False
            while time.monotonic()<deadline:
                db=SessionLocal()
                try:
                    cstate=db.execute(text("SELECT state FROM internal_calls WHERE id=:id"),{"id":call_id}).scalar_one()
                    pstate=db.execute(text(
                        "SELECT state FROM internal_call_participants WHERE call_id=:id AND user_id=:u"
                    ),{"id":call_id,"u":B}).scalar_one()
                finally:
                    db.close()
                if cstate=="connected" and pstate=="connected" and tracks(pb)>=1:
                    recovered=True
                    break
                time.sleep(.5)
            checks["reconnect_recovered"]=recovered

            pa.get_by_role("button",name="Завершить звонок").click()
            deadline=time.monotonic()+20
            continued=False
            while time.monotonic()<deadline:
                db=SessionLocal()
                try:
                    cstate=db.execute(text("SELECT state FROM internal_calls WHERE id=:id"),{"id":call_id}).scalar_one()
                    states={int(x[0]):str(x[1]) for x in db.execute(
                        text("SELECT user_id,state FROM internal_call_participants WHERE call_id=:id"),
                        {"id":call_id}
                    ).all()}
                finally:
                    db.close()
                if cstate=="connected" and states.get(A)=="left" and states.get(CALLER)=="connected" and states.get(B)=="connected":
                    continued=True
                    break
                time.sleep(.4)
            checks["one_leaves_others_continue"]=continued
            checks["caller_ui_active"]=caller.get_by_role("dialog",name="Активный звонок").is_visible()
            checks["late_joiner_ui_active"]=pb.get_by_role("dialog",name="Активный звонок").is_visible()

            caller.get_by_role("button",name="Завершить звонок").click()
            browser.close()
    finally:
        cleanup(cid,sessions)

    ok=bool(checks) and all(checks.values())
    print(json.dumps({"status":"PASS" if ok else "FAIL","checks":checks},ensure_ascii=False,indent=2))
    raise SystemExit(0 if ok else 1)


if __name__=="__main__":
    main()
