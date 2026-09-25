from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0,"/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)

import websockets
from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.phone_media_gateway import webrtc_endpoint_health
from app.services.telephony_core import media_session_guardian
from mcn_media_qa_cleanup import cleanup_mcn_media_qa

STATE_FILE=Path("/tmp/boris_mcn_media_guardian_state.json")
INVITE_FILE=Path("/tmp/boris_media_invite_qa.json")


def _headers(message: str) -> dict[str,list[str]]:
    out: dict[str,list[str]]={}
    for line in message.split("\r\n")[1:]:
        if not line:
            break
        if ":" not in line:
            continue
        key,value=line.split(":",1)
        out.setdefault(key.strip().lower(),[]).append(value.strip())
    return out


def _digest_fields(value: str) -> dict[str,str]:
    value=re.sub(r"^Digest\s+","",str(value or "").strip(),flags=re.I)
    out={}
    for m in re.finditer(r'(\w+)=(?:"([^"]*)"|([^,\s]+))',value):
        out[m.group(1).lower()]=m.group(2) if m.group(2) is not None else m.group(3)
    return out


async def _register(invite: dict) -> dict:
    user=str(invite["authorization_username"])
    secret=str(invite["session_token"])
    wss=str(invite["signaling_url"])
    domain=str(invite["sip_uri"]).split("@",1)[1]
    request_uri=f"sip:{domain}"
    aor=f"sip:{user}@{domain}"
    call_id=f"qa-{uuid.uuid4().hex}@boris"
    from_tag=uuid.uuid4().hex[:12]

    def make_register(cseq: int, authorization: str|None=None, expires: int=60) -> str:
        branch="z9hG4bK"+uuid.uuid4().hex[:18]
        lines=[
            f"REGISTER {request_uri} SIP/2.0",
            f"Via: SIP/2.0/WSS qa.invalid;branch={branch};rport",
            "Max-Forwards: 70",
            f"To: <{aor}>",
            f"From: <{aor}>;tag={from_tag}",
            f"Call-ID: {call_id}",
            f"CSeq: {cseq} REGISTER",
            f"Contact: <sip:{user}@qa.invalid;transport=ws>;expires={expires}",
            f"Expires: {expires}",
            "User-Agent: BORIS-WSS-QA/1.0",
        ]
        if authorization:
            lines.append("Authorization: "+authorization)
        lines.extend(["Content-Length: 0","",""])
        return "\r\n".join(lines)

    async with websockets.connect(
        wss,
        subprotocols=["sip"],
        open_timeout=10,
        close_timeout=3,
        ping_interval=None,
        max_size=2**20,
    ) as ws:
        async def recv_final_response(timeout: float=10.0) -> tuple[str,int,str]:
            deadline=time.time()+timeout
            last_line=""
            while time.time()<deadline:
                raw=await asyncio.wait_for(ws.recv(),max(.1,deadline-time.time()))
                message=raw.decode("utf-8","replace") if isinstance(raw,(bytes,bytearray)) else str(raw)
                match=re.search(r"(?im)^\s*SIP/2\.0\s+(\d{3})\b[^\r\n]*",message)
                if not match:
                    continue
                line=match.group(0).strip()
                last_line=line
                code=int(match.group(1))
                if code<200:
                    continue
                return message,code,line
            raise AssertionError("sip_final_response_timeout:"+last_line)

        await ws.send(make_register(1))
        first,first_code,first_line=await recv_final_response(10)
        if first_code not in {401,407}:
            raise AssertionError("sip_digest_challenge_missing:"+first_line)
        hdr=_headers(first)
        challenge=(hdr.get("www-authenticate") or hdr.get("proxy-authenticate") or [None])[0]
        if not challenge:
            raise AssertionError("sip_authenticate_header_missing")
        f=_digest_fields(challenge)
        realm=f.get("realm") or ""
        nonce=f.get("nonce") or ""
        algorithm=(f.get("algorithm") or "MD5").upper()
        if algorithm not in {"MD5","MD5-SESS"}:
            raise AssertionError("unsupported_digest_algorithm:"+algorithm)
        qops=[x.strip().lower() for x in (f.get("qop") or "").split(",") if x.strip()]
        qop="auth" if "auth" in qops else ""
        nc="00000001"
        cnonce=secrets.token_hex(8)
        md5=lambda value: hashlib.md5(value.encode()).hexdigest()
        ha1=md5(f"{user}:{realm}:{secret}")
        if algorithm=="MD5-SESS":
            ha1=md5(f"{ha1}:{nonce}:{cnonce}")
        ha2=md5(f"REGISTER:{request_uri}")
        response=md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}") if qop else md5(f"{ha1}:{nonce}:{ha2}")
        parts=[
            f'username="{user}"',
            f'realm="{realm}"',
            f'nonce="{nonce}"',
            f'uri="{request_uri}"',
            f'response="{response}"',
            f'algorithm={algorithm}',
        ]
        if f.get("opaque"):
            parts.append(f'opaque="{f["opaque"]}"')
        if qop:
            parts.extend([f"qop={qop}",f"nc={nc}",f'cnonce="{cnonce}"'])
        await ws.send(make_register(2,"Digest "+", ".join(parts)))
        second,second_code,result_line=await recv_final_response(10)
        if second_code!=200:
            raise AssertionError("sip_register_not_200:"+result_line)

        health={}
        deadline=time.time()+5
        while time.time()<deadline:
            health=webrtc_endpoint_health(user)
            if health.get("ready"):
                break
            await asyncio.sleep(.1)
        if not health.get("ready"):
            raise AssertionError("endpoint_not_online_after_register")

        guardian=media_session_guardian(100,include_synthetic=True)
        if int(guardian.get("activation_started") or 0)<1:
            raise AssertionError("media_bridge_originate_not_started:"+repr({
                "status":guardian.get("status"),
                "attempted":guardian.get("activation_attempted"),
                "waiting_registration":guardian.get("activation_waiting_registration"),
                "errors":guardian.get("activation_errors"),
            }))

        state=json.loads(STATE_FILE.read_text())
        db=SessionLocal()
        try:
            lease=db.execute(text("""SELECT status,activated_at,bridge_id,media_channel_id,last_bridge_error
                                     FROM telephony_media_sessions WHERE id=:i"""),
                             {"i":int(state["lease_id"])}).mappings().first()
        finally:
            db.close()
        if not lease or not lease.get("bridge_id") or not lease.get("media_channel_id") or lease.get("last_bridge_error"):
            raise AssertionError("media_bridge_ids_missing_after_originate:"+repr(dict(lease or {})))

        invite_received=False
        deadline=time.time()+5
        while time.time()<deadline:
            try:
                raw=await asyncio.wait_for(ws.recv(),max(.1,deadline-time.time()))
            except asyncio.TimeoutError:
                break
            message=raw.decode("utf-8","replace") if isinstance(raw,(bytes,bytearray)) else str(raw)
            if re.search(r"(?im)^\s*INVITE\s+sip:",message):
                invite_received=True
                break
        if not invite_received:
            raise AssertionError("browser_invite_not_received_after_bridge_originate")

        return {
            "challenge":"digest",
            "register":"200",
            "endpoint_online":True,
            "guardian_activation_started":int(guardian.get("activation_started") or 0),
            "lease_status":lease.get("status"),
            "bridge_created":True,
            "media_channel_created":True,
            "invite_received":True,
            "rtp_proven":False,
        }


def main() -> int:
    state=json.loads(STATE_FILE.read_text())
    invite=json.loads(INVITE_FILE.read_text())
    try:
        out=asyncio.run(_register(invite))
        print("BORIS_WSS_REGISTER_AND_MEDIA_ACTIVATION_OK",out)
        return 0
    finally:
        print("MCN_MEDIA_QA_FINALLY",cleanup_mcn_media_qa(state))


if __name__=="__main__":
    raise SystemExit(main())
