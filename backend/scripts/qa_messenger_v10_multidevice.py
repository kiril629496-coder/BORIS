"""V10 multi-device incoming-call acceptance in the isolated Messenger QA tenant.

Uses two real Chromium contexts for the same employee session. No provider calls.
Creates one temporary DM/call and cleans them by exact ID; audit rows remain append-only.
"""
from pathlib import Path
exec(Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text().split("access0,team0=set_viewer()")[0])

import os, time, uuid
from types import SimpleNamespace
from app.services import messenger_calls as calls

for _key in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy"):
    os.environ.pop(_key, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost,boris-ai.pro"
os.environ["no_proxy"] = os.environ["NO_PROXY"]
os.environ["MESSENGER_CALLS_ENABLED"] = "true"
os.environ["MESSENGER_VIDEO_CALLS_ENABLED"] = "true"

sessions=[]
conversation_id=None
call_id=None
checks={}
try:
    employee_token, employee_sid, _ = issue(EMPLOYEE_ID, "V10 multi-device")
    sessions.append((EMPLOYEE_ID, employee_sid))
    db=SessionLocal()
    try:
        conversation_id=int(db.execute(text(
            "INSERT INTO internal_conversations(account_id,kind,title,created_by_user_id) "
            "VALUES (:a,'DM',NULL,:u) RETURNING id"
        ),{"a":ACCOUNT,"u":OWNER_ID}).scalar_one())
        db.execute(text(
            "INSERT INTO internal_conversation_members(conversation_id,user_id,role) "
            "VALUES (:c,:o,'MEMBER'),(:c,:e,'MEMBER')"
        ),{"c":conversation_id,"o":OWNER_ID,"e":EMPLOYEE_ID})
        db.commit()
    finally:
        db.close()

    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=[
            "--no-sandbox","--use-fake-device-for-media-stream","--use-fake-ui-for-media-stream"
        ])
        contexts=[
            browser.new_context(permissions=["microphone","camera"]),
            browser.new_context(permissions=["microphone","camera"]),
        ]
        pages=[ctx.new_page() for ctx in contexts]
        for page in pages:
            workspace(page, employee_token)

        db=SessionLocal()
        try:
            call,_=calls.start(db,conversation_id,SimpleNamespace(id=OWNER_ID),"audio",str(uuid.uuid4()))
            db.commit()
            call_id=call["call_id"]
        finally:
            db.close()

        dialogs=[page.get_by_role("dialog",name="Входящий звонок") for page in pages]
        for dialog in dialogs:
            dialog.wait_for(timeout=15000)
        checks["BOTH_SURFACES_RING"]=all(dialog.is_visible() for dialog in dialogs)

        pages[0].get_by_role("button",name="Принять",exact=True).click()
        deadline=time.monotonic()+15
        state=""
        while time.monotonic()<deadline:
            db=SessionLocal()
            try:
                state=str(db.execute(text("SELECT state FROM internal_calls WHERE id=:id"),{"id":call_id}).scalar_one())
            finally:
                db.close()
            if state in {"accepted","connecting","connected","reconnecting"}:
                break
            time.sleep(.2)
        checks["ACCEPTED_ON_FIRST_SURFACE"]=state in {"accepted","connecting","connected","reconnecting"}

        dialogs[1].wait_for(state="hidden",timeout=12000)
        checks["SECOND_SURFACE_STOPS_RINGING"]=not dialogs[1].is_visible()
        try:
            pages[1].get_by_text("Звонок принят на другом устройстве",exact=True).wait_for(timeout=6000)
            checks["SECOND_SURFACE_HUMAN_STATUS_VISIBLE"]=True
        except Exception:
            checks["SECOND_SURFACE_HUMAN_STATUS_VISIBLE"]=False

        db=SessionLocal()
        try:
            checks["SINGLE_CALL_ROW"]=int(db.execute(text(
                "SELECT count(*) FROM internal_calls WHERE id=:id"
            ),{"id":call_id}).scalar_one())==1
        finally:
            db.close()
        browser.close()
finally:
    if call_id:
        db=SessionLocal()
        try:
            row=db.execute(text("SELECT state FROM internal_calls WHERE id=:id"),{"id":call_id}).first()
            if row and row[0] not in {"ended","missed","declined","busy","failed"}:
                calls.transition(db,call_id,SimpleNamespace(id=OWNER_ID),"end")
            db.commit()
        finally:
            db.close()
    if conversation_id:
        db=SessionLocal()
        try:
            mids=db.execute(text("SELECT id FROM internal_messages WHERE conversation_id=:c"),
                            {"c":conversation_id}).scalars().all()
            db.execute(text(
                "DELETE FROM internal_messenger_events WHERE account_id=:a AND ("
                "(entity_type='conversation' AND entity_id=:c) OR "
                "(entity_type='message' AND entity_id=ANY(CAST(:m AS text[]))))"
            ),{"a":ACCOUNT,"c":str(conversation_id),"m":[str(x) for x in mids] or ["-1"]})
            db.execute(text("DELETE FROM internal_conversations WHERE id=:c"),{"c":conversation_id})
            db.commit()
        finally:
            db.close()
    for uid,sid in sessions:
        revoke(uid,sid)

print(json.dumps({"status":"PASS" if checks and all(checks.values()) else "FAIL","checks":checks},
                 ensure_ascii=False,indent=2))
raise SystemExit(0 if checks and all(checks.values()) else 1)
