from __future__ import annotations
import sys
sys.path.insert(0,"/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.mcn_core import ensure_schema, trunk_upsert
from app.services.asterisk_gateway import render_mcn_pjsip_config, GENERATED_PJSIP

A="__qa_mcn_render_isolation_v1"
ensure_schema()
db=SessionLocal()
try:
    db.execute(text("DELETE FROM telephony_trunks WHERE account_id=:a"),{"a":A});db.commit()
finally: db.close()
try:
    r=trunk_upsert(A,{
        "provider":"mcn","name":"MCN synthetic isolation","auth_mode":"registration",
        "registrar":"sip.qa.invalid","username":"qa-user","password":"qa-pass",
        "source_ips":[],"enabled":True,
    })
    assert r.get("status")=="ok",r
    out=render_mcn_pjsip_config()
    assert out.get("trunks_rendered")==0,out
    text_body=GENERATED_PJSIP.read_text("utf-8") if GENERATED_PJSIP.exists() else ""
    assert "sip.qa.invalid" not in text_body
    assert "__qa_mcn_render_isolation_v1" not in text_body
    print("MCN_SYNTHETIC_RENDER_ISOLATION_OK",{"rendered":out.get("trunks_rendered"),"skipped":out.get("trunks_skipped")})
finally:
    db=SessionLocal()
    try:
        db.execute(text("DELETE FROM telephony_trunks WHERE account_id=:a"),{"a":A});db.commit()
    finally: db.close()
