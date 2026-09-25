from __future__ import annotations
import sys
sys.path.insert(0,"/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.mcn_core import normalize_sip_target, normalize_source_ip, trunk_upsert, ensure_schema

assert normalize_sip_target("sip:sip.example.ru")=="sip.example.ru"
assert normalize_sip_target("sip.example.ru:5060")=="sip.example.ru:5060"
assert normalize_sip_target("[2001:db8::1]:5060")=="[2001:db8::1]:5060"
for bad in ["sip.example.ru\n[evil]","user@sip.example.ru","sip.example.ru;transport=udp","bad host","host:99999"]:
    assert normalize_sip_target(bad)=="",bad
assert normalize_source_ip("1.2.3.4")=="1.2.3.4"
assert normalize_source_ip("1.2.3.4/24")=="1.2.3.0/24"
assert normalize_source_ip("2001:db8::1")=="2001:db8::1"
assert normalize_source_ip("1.2.3.999")==""

A="__qa_mcn_input_guard_v1"
ensure_schema()
db=SessionLocal()
try:
    db.execute(text("DELETE FROM telephony_trunks WHERE account_id=:a"),{"a":A});db.commit()
finally: db.close()
try:
    assert trunk_upsert(A,{"name":"x","auth_mode":"ip","registrar":"sip.example.ru\n[evil]","source_ips":["1.2.3.4"]})["status"]=="invalid_registrar"
    assert trunk_upsert(A,{"name":"x","auth_mode":"ip","registrar":"sip.example.ru","source_ips":["not-an-ip"]})["status"]=="invalid_source_ip"
    assert trunk_upsert(A,{"name":"x","auth_mode":"registration","registrar":"sip.example.ru","username":"u\n[evil]","password":"p"})["status"]=="invalid_sip_username"
    assert trunk_upsert(A,{"name":"x","auth_mode":"registration","registrar":"sip.example.ru","username":"u;comment","password":"p"})["status"]=="invalid_sip_username"
    assert trunk_upsert(A,{"name":"x","auth_mode":"registration","registrar":"sip.example.ru","username":"u@example","password":"p"})["status"]=="invalid_sip_username"
    assert trunk_upsert(A,{"name":"x","auth_mode":"registration","registrar":"sip.example.ru","username":"u","password":"p\n[evil]"})["status"]=="invalid_sip_password"
    print("MCN_INPUT_GUARD_OK")
finally:
    db=SessionLocal()
    try:
        db.execute(text("DELETE FROM telephony_trunks WHERE account_id=:a"),{"a":A});db.commit()
    finally: db.close()
