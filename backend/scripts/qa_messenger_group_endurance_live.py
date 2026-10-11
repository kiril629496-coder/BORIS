#!/usr/bin/env python3
"""BORIS Messenger: 10-minute live group call endurance acceptance."""
from pathlib import Path
import os, sys, time, json, hashlib

for key in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy","ALL_PROXY","all_proxy"):
    os.environ.pop(key, None)

sys.path.insert(0,"/root/BORIS/backend/scripts")
from qa_messenger_group_reconnect_live import (
    ACCOUNT, WEB, USERS, CALLER, A, B, C, issue, revoke, workspace,
    SessionLocal, text, sync_playwright, current_builds, cleanup, tracks,
)


CALL_RUNTIME_FILES=(
    "app/api/internal_messenger.py",
    "app/services/messenger_calls.py",
    "app/services/messenger_call_maintenance.py",
)


def call_runtime_signature(build_id):
    if not build_id:
        return ""
    root=Path("/root/BORIS/releases/backend")/build_id/"backend"
    h=hashlib.sha256()
    for rel in CALL_RUNTIME_FILES:
        p=root/rel
        if not p.is_file():
            return ""
        h.update(rel.encode("utf-8")); h.update(bytes([0])); h.update(p.read_bytes())
    return h.hexdigest()


def call_runtime_signatures():
    builds=current_builds()
    return builds,{unit:call_runtime_signature(build) for unit,build in builds.items()}


def main():
    builds,signatures=call_runtime_signatures()
    values={v for v in signatures.values() if v}
    if len(values)!=1 or not values:
        raise AssertionError("call_runtime_not_converged:"+json.dumps({"builds":builds,"signatures":signatures}))
    expected_signature=next(iter(values))
    checks={"call_runtime_stable": True}
    print("CALL_RUNTIME",json.dumps({"builds":builds,"signature":expected_signature}),flush=True)

    sessions=[]
    cid=None
    call_id=None
    tokens={}
    try:
        for uid in USERS:
            token,sid,_=issue(uid,f"Group 10m QA {uid}")
            tokens[uid]=token
            sessions.append((uid,sid))

        db=SessionLocal()
        try:
            cid=int(db.execute(text(
                "INSERT INTO internal_conversations(account_id,kind,title,created_by_user_id) "
                "VALUES (:a,'GROUP','QA Group 10m Endurance',:u) RETURNING id"
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

            time.sleep(2)
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

            start=time.monotonic()
            next_mark=60
            marks=0
            reconnect_done=False
            reconnect_recovered=False
            leave_done=False
            continued_after_leave=False
            while time.monotonic()-start < 605:
                elapsed=time.monotonic()-start

                if not reconnect_done and elapsed>=120:
                    print("RECONNECT_BEGIN",flush=True)
                    contexts[B].set_offline(True)
                    time.sleep(8)
                    db=SessionLocal()
                    try:
                        checks["group_connected_during_one_offline"] = (
                            db.execute(text("SELECT state FROM internal_calls WHERE id=:id"),{"id":call_id}).scalar_one()=="connected"
                        )
                    finally:
                        db.close()
                    contexts[B].set_offline(False)
                    reconnect_done=True

                    deadline=time.monotonic()+40
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
                            reconnect_recovered=True
                            break
                        time.sleep(.5)
                    print("RECONNECT_RECOVERED",reconnect_recovered,flush=True)

                if not leave_done and elapsed>=360:
                    pa.get_by_role("button",name="Завершить звонок").click()
                    leave_done=True
                    deadline=time.monotonic()+20
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
                            continued_after_leave=True
                            break
                        time.sleep(.4)
                    print("CONTINUED_AFTER_LEAVE",continued_after_leave,flush=True)

                if elapsed>=next_mark:
                    live_builds,live_signatures=call_runtime_signatures()
                    if any(value != expected_signature for value in live_signatures.values()):
                        checks["call_runtime_stable"]=False
                        print("INVALID_CALL_RUNTIME_CHANGE",json.dumps({
                            "expected_signature":expected_signature,
                            "live_builds":live_builds,
                            "live_signatures":live_signatures
                        },ensure_ascii=False),flush=True)
                        break
                    db=SessionLocal()
                    try:
                        call=db.execute(text(
                            "SELECT state,ended_reason FROM internal_calls WHERE id=:id"
                        ),{"id":call_id}).mappings().one()
                        parts=[dict(x) for x in db.execute(text(
                            "SELECT user_id,state,updated_at FROM internal_call_participants "
                            "WHERE call_id=:id ORDER BY user_id"
                        ),{"id":call_id}).mappings().all()]
                    finally:
                        db.close()
                    print("MARK",json.dumps({
                        "sec":int(elapsed),"call":dict(call),"participants":parts,
                        "call_signature":expected_signature
                    },default=str,ensure_ascii=False),flush=True)
                    marks+=1
                    next_mark+=60
                    if call["state"]!="connected":
                        break
                time.sleep(.4)

            db=SessionLocal()
            try:
                final=db.execute(text(
                    "SELECT state,ended_reason FROM internal_calls WHERE id=:id"
                ),{"id":call_id}).mappings().one()
                parts=[dict(x) for x in db.execute(text(
                    "SELECT user_id,state,updated_at FROM internal_call_participants "
                    "WHERE call_id=:id ORDER BY user_id"
                ),{"id":call_id}).mappings().all()]
            finally:
                db.close()

            checks["reconnect_recovered"]=reconnect_recovered
            checks["one_leaves_others_continue"]=continued_after_leave
            checks["alive_10min"]=final["state"]=="connected"
            checks["minute_marks"]=marks>=10
            checks["caller_ui_active"]=caller.get_by_role("dialog",name="Активный звонок").is_visible()
            checks["late_joiner_ui_active"]=pb.get_by_role("dialog",name="Активный звонок").is_visible()
            print("FINAL",json.dumps({
                "call":dict(final),"participants":parts,"marks":marks
            },default=str,ensure_ascii=False),flush=True)

            caller.get_by_role("button",name="Завершить звонок").click()
            browser.close()
    finally:
        cleanup(cid,sessions)

    ok=bool(checks) and all(checks.values())
    print(json.dumps({"status":"PASS" if ok else "FAIL","checks":checks},ensure_ascii=False,indent=2))
    raise SystemExit(0 if ok else 1)


if __name__=="__main__":
    main()
