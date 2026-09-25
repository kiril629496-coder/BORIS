from __future__ import annotations
import os,sys
from pathlib import Path
sys.path.insert(0,"/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.asterisk_gateway import render_mcn_pjsip_config

A="__qa_mcn_registration_runtime_v1"
db=SessionLocal()
try:
    db.execute(text("DELETE FROM telephony_trunks WHERE account_id=:a"),{"a":A})
    db.execute(text("DELETE FROM telephony_provider_configs WHERE account_id=:a"),{"a":A})
    db.commit()
finally: db.close()
os.environ["BORIS_MCN_SIP_BIND"]="127.0.0.1:15061"
out=render_mcn_pjsip_config(include_synthetic=False)
Path("/tmp/boris_mcn_registration_tid").unlink(missing_ok=True)
print("MCN_REGISTRATION_RUNTIME_DB_CLEANUP_OK",out)
