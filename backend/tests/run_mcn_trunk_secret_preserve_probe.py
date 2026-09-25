from __future__ import annotations
import sys
sys.path.insert(0,"/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.mcn_core import ensure_schema, trunk_upsert, trunk_list

A="__qa_mcn_secret_preserve_v1"
N="MCN QA secret preserve"

ensure_schema()
db=SessionLocal()
try:
    db.execute(text("DELETE FROM telephony_trunks WHERE account_id=:a"),{"a":A})
    db.commit()
finally:
    db.close()

try:
    first=trunk_upsert(A,{
        "provider":"mcn","name":N,"auth_mode":"registration",
        "registrar":"sip.example.test","username":"qa-login","password":"qa-password",
        "source_ips":[],"codecs":["alaw","ulaw"],"enabled":True,
    })
    assert first.get("status")=="ok",first
    t1=first["trunk"]
    assert t1["status"]=="configured_unverified",t1
    assert t1["has_username"] and t1["has_password"],t1

    second=trunk_upsert(A,{
        "provider":"mcn","name":N,"auth_mode":"registration",
        "registrar":"sip.example.test","source_ips":[],
        "codecs":["alaw","ulaw"],"allowed_cli":["+74950000000"],"enabled":True,
    })
    assert second.get("status")=="ok",second
    t2=second["trunk"]
    assert t2["status"]=="configured_unverified",t2
    assert t2["has_username"] and t2["has_password"],t2

    listed=trunk_list(A)
    assert len(listed)==1,listed
    assert listed[0]["status"]=="configured_unverified",listed[0]
    assert listed[0]["has_username"] and listed[0]["has_password"],listed[0]
    print("MCN_TRUNK_SECRET_PRESERVE_OK",{"status":listed[0]["status"],"has_username":listed[0]["has_username"],"has_password":listed[0]["has_password"]})
finally:
    db=SessionLocal()
    try:
        db.execute(text("DELETE FROM telephony_trunks WHERE account_id=:a"),{"a":A})
        db.commit()
    finally:
        db.close()
