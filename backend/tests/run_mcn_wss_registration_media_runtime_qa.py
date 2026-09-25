from __future__ import annotations

import asyncio
import os
import hashlib
import json
import re
import secrets
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, "/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env", override=True)

import websockets
from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.phone_media_gateway import webrtc_endpoint_health
from app.services.telephony_core import media_session_guardian
from tests.mcn_media_qa_cleanup import cleanup_mcn_media_qa

STATE_FILE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/boris_mcn_media_guardian_state.json")
INVITE_FILE = Path("/tmp/boris_media_invite_qa.json")


def _headers(message: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in message.replace("\r\n", "\n").split("\n")[1:]:
        if not line:
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        out.setdefault(key.strip().lower(), []).append(value.strip())
    return out


def _digest_fields(value: str) -> dict[str, str]:
    value = re.sub(r"^Digest\s+", "", value.strip(), flags=re.I)
    out: dict[str, str] = {}
    for match in re.finditer(r'(\w+)=(?:"([^"]*)"|([^,\s]+))', value):
        out[match.group(1).lower()] = match.group(2) if match.group(2) is not None else match.group(3)
    return out


def _md5(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def _sip_response(request: str, code: int, reason: str, *, body: str = "", to_tag: str) -> str:
    headers = _headers(request)
    lines = [f"SIP/2.0 {code} {reason}"]
    for via in headers.get("via") or []:
        lines.append("Via: " + via)
    from_value = (headers.get("from") or [""])[0]
    to_value = (headers.get("to") or [""])[0]
    if "tag=" not in to_value:
        to_value += f";tag={to_tag}"
    lines.extend([
        "From: " + from_value,
        "To: " + to_value,
        "Call-ID: " + (headers.get("call-id") or [""])[0],
        "CSeq: " + (headers.get("cseq") or [""])[0],
        "Contact: <sip:qa-webphone@qa.invalid;transport=ws>",
        "Server: BORIS-MCN-WSS-QA/1.0",
    ])
    if body:
        lines.extend(["Content-Type: application/sdp", f"Content-Length: {len(body.encode('utf-8'))}", "", body])
    else:
        lines.extend(["Content-Length: 0", "", ""])
    return "\r\n".join(lines)


def _synthetic_sdp_answer(offer: str) -> str:
    # Signaling-only QA. A deliberately synthetic ICE/DTLS answer is enough to
    # prove SIP answer -> StasisStart -> bridge attachment, but it is never used
    # as evidence of real RTP/audio.
    codecs: list[str] = []
    for line in offer.replace("\r\n", "\n").split("\n"):
        if line.startswith("m=audio "):
            parts = line.split()
            codecs = parts[3:]
            break
    codecs = codecs or ["0", "8", "101"]
    ufrag = secrets.token_hex(8)
    pwd = secrets.token_hex(16)
    fingerprint = ":".join(["AA"] * 32)
    session_id = str(int(time.time() * 1000))
    lines = [
        "v=0",
        f"o=- {session_id} {session_id} IN IP4 127.0.0.1",
        "s=BORIS-QA",
        "c=IN IP4 127.0.0.1",
        "t=0 0",
        "a=group:BUNDLE audio-0",
        f"m=audio 9 UDP/TLS/RTP/SAVPF {' '.join(codecs)}",
        "a=connection:new",
        "a=setup:active",
        f"a=fingerprint:SHA-256 {fingerprint}",
        f"a=ice-ufrag:{ufrag}",
        f"a=ice-pwd:{pwd}",
        "a=candidate:1 1 UDP 2130706431 127.0.0.1 9 typ host",
        "a=rtpmap:0 PCMU/8000",
        "a=rtpmap:8 PCMA/8000",
        "a=rtpmap:101 telephone-event/8000",
        "a=fmtp:101 0-16",
        "a=ptime:20",
        "a=sendrecv",
        "a=rtcp-mux",
        "a=mid:audio-0",
    ]
    return "\r\n".join(lines) + "\r\n"


class RegisterSession:
    def __init__(self, invite: dict):
        self.user = str(invite["authorization_username"])
        self.password = str(invite["session_token"])
        self.wss = str(invite["signaling_url"])
        self.domain = str(invite["sip_uri"]).split("@", 1)[1]
        self.uri = f"sip:{self.domain}"
        self.aor = f"sip:{self.user}@{self.domain}"
        self.call_id = f"qa-{uuid.uuid4().hex}@boris"
        self.tag = uuid.uuid4().hex[:12]
        self.contact = f"<sip:{self.user}@qa.invalid;transport=ws;ob>"
        self.challenge: dict[str, str] | None = None

    def _branch(self) -> str:
        return "z9hG4bK" + uuid.uuid4().hex[:18]

    def _authorization(self, method: str = "REGISTER") -> str:
        assert self.challenge
        realm = self.challenge.get("realm", "")
        nonce = self.challenge.get("nonce", "")
        algorithm = (self.challenge.get("algorithm") or "MD5").upper()
        if algorithm not in {"MD5", "MD5-SESS"}:
            raise AssertionError(f"unsupported_digest_algorithm:{algorithm}")
        qops = [x.strip() for x in (self.challenge.get("qop") or "").split(",") if x.strip()]
        qop = "auth" if "auth" in qops else ""
        cnonce = secrets.token_hex(8)
        nc = "00000001"
        ha1 = _md5(f"{self.user}:{realm}:{self.password}")
        if algorithm == "MD5-SESS":
            ha1 = _md5(f"{ha1}:{nonce}:{cnonce}")
        ha2 = _md5(f"{method}:{self.uri}")
        response = _md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}") if qop else _md5(f"{ha1}:{nonce}:{ha2}")
        parts = [
            f'username="{self.user}"',
            f'realm="{realm}"',
            f'nonce="{nonce}"',
            f'uri="{self.uri}"',
            f'response="{response}"',
            f"algorithm={algorithm}",
        ]
        if self.challenge.get("opaque"):
            parts.append(f'opaque="{self.challenge["opaque"]}"')
        if qop:
            parts.extend([f"qop={qop}", f"nc={nc}", f'cnonce="{cnonce}"'])
        return "Digest " + ", ".join(parts)

    def register_message(self, cseq: int, *, authorized: bool, expires: int = 90) -> str:
        lines = [
            f"REGISTER {self.uri} SIP/2.0",
            f"Via: SIP/2.0/WSS qa.invalid;branch={self._branch()};rport",
            "Max-Forwards: 70",
            f"To: <{self.aor}>",
            f"From: <{self.aor}>;tag={self.tag}",
            f"Call-ID: {self.call_id}",
            f"CSeq: {cseq} REGISTER",
            f"Contact: {self.contact};expires={expires}",
            f"Expires: {expires}",
            "Supported: outbound, path, gruu",
            "User-Agent: BORIS-MCN-WSS-QA/1.0",
        ]
        if authorized:
            lines.append("Authorization: " + self._authorization())
        lines.extend(["Content-Length: 0", "", ""])
        return "\r\n".join(lines)


async def _recv_response(ws, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        payload = await asyncio.wait_for(ws.recv(), max(0.2, deadline - time.monotonic()))
        message = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else str(payload)
        first = message.split("\r\n", 1)[0]
        last = first
        if first.startswith("SIP/2.0 "):
            return message
    raise AssertionError(f"sip_response_timeout:{last}")


async def run_probe(invite: dict, state: dict) -> None:
    session = RegisterSession(invite)
    async with websockets.connect(
        session.wss,
        subprotocols=["sip"],
        origin="https://boris-ai.pro",
        open_timeout=10,
        close_timeout=3,
        ping_interval=None,
        max_size=2**20,
    ) as ws:
        await ws.send(session.register_message(1, authorized=False))
        first = await _recv_response(ws)
        first_line = first.split("\r\n", 1)[0]
        print("MCN_WSS_REGISTER_CHALLENGE", first_line)
        assert first_line.startswith(("SIP/2.0 401", "SIP/2.0 407")), first_line
        hdr = _headers(first)
        challenge = (hdr.get("www-authenticate") or hdr.get("proxy-authenticate") or [None])[0]
        assert challenge, "digest_challenge_missing"
        session.challenge = _digest_fields(challenge)

        await ws.send(session.register_message(2, authorized=True))
        second = await _recv_response(ws)
        second_line = second.split("\r\n", 1)[0]
        print("MCN_WSS_REGISTER_RESULT", second_line)
        assert second_line.startswith("SIP/2.0 200"), second_line

        endpoint = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            endpoint = webrtc_endpoint_health(session.user)
            if endpoint.get("ready"):
                break
            await asyncio.sleep(0.15)
        print("MCN_WSS_ENDPOINT_HEALTH", {
            "status": (endpoint or {}).get("status"),
            "state": (endpoint or {}).get("state"),
            "ready": bool((endpoint or {}).get("ready")),
        })
        assert endpoint and endpoint.get("ready") is True, endpoint

        guardian = media_session_guardian(100, include_synthetic=True)
        print("MCN_WSS_MEDIA_GUARDIAN", {
            "activation_started": guardian.get("activation_started"),
            "activation_waiting_registration": guardian.get("activation_waiting_registration"),
            "activation_failed": guardian.get("activation_failed"),
        })
        assert int(guardian.get("activation_started") or 0) >= 1, guardian

        # The media-leg originate now targets this live WebSocket registration.
        # Capture the real SIP INVITE before deciding whether Stasis activation
        # is missing or simply waiting for the browser to answer.
        invite_request = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            payload = await asyncio.wait_for(ws.recv(), max(0.2, deadline - time.monotonic()))
            message = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else str(payload)
            first_line = message.split("\r\n", 1)[0]
            if first_line.startswith("INVITE "):
                invite_request = message
                break
        assert invite_request, "media_leg_invite_not_received"
        req_headers = _headers(invite_request)
        body = invite_request.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in invite_request else ""
        # Debug evidence must never make the runtime QA fail because another
        # operator/process left a root-owned file in /tmp.
        try:
            debug = Path(f"/tmp/boris_mcn_wss_invite_debug_{os.getuid()}.txt")
            debug.write_text(invite_request, encoding="utf-8")
            debug.chmod(0o600)
        except OSError:
            pass
        print("MCN_WSS_MEDIA_INVITE_RECEIVED", {
            "content_type": (req_headers.get("content-type") or [None])[0],
            "body_bytes": len(body.encode("utf-8")),
            "has_fingerprint": "a=fingerprint:" in body,
            "has_ice": "a=ice-ufrag:" in body and "a=ice-pwd:" in body,
            "has_rtcp_mux": "a=rtcp-mux" in body,
        })

        answer_tag = "qa" + uuid.uuid4().hex[:10]
        await ws.send(_sip_response(invite_request, 180, "Ringing", to_tag=answer_tag))
        sdp_answer = _synthetic_sdp_answer(body)
        await ws.send(_sip_response(invite_request, 200, "OK", body=sdp_answer, to_tag=answer_tag))
        ack_seen = False
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                payload = await asyncio.wait_for(ws.recv(), max(0.2, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                break
            message = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else str(payload)
            first_line = message.split("\r\n", 1)[0]
            if first_line.startswith("ACK "):
                ack_seen = True
                break
            if first_line.startswith("BYE "):
                raise AssertionError("media_leg_bye_before_ack")
        print("MCN_WSS_MEDIA_ANSWERED", {"ack_seen": ack_seen})
        assert ack_seen, "media_leg_ack_not_received"

        activated = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            db = SessionLocal()
            try:
                activated = db.execute(text(
                    """SELECT status,bridge_id,media_channel_id,activated_at,last_bridge_error
                       FROM telephony_media_sessions WHERE id=:i"""
                ), {"i": int(state["lease_id"])}).mappings().first()
            finally:
                db.close()
            if activated and activated.get("activated_at") is not None:
                break
            await asyncio.sleep(0.1)
        proof = dict(activated or {})
        print("MCN_WSS_MEDIA_ACTIVATED", {
            "status": proof.get("status"),
            "bridge": bool(proof.get("bridge_id")),
            "media_channel": bool(proof.get("media_channel_id")),
            "activated": proof.get("activated_at") is not None,
            "error": proof.get("last_bridge_error"),
        })
        assert proof.get("status") == "active", proof
        assert proof.get("activated_at") is not None, proof
        assert proof.get("bridge_id") and proof.get("media_channel_id"), proof


def main() -> None:
    state = json.loads(STATE_FILE.read_text())
    invite = json.loads(INVITE_FILE.read_text())
    try:
        asyncio.run(run_probe(invite, state))
        print("MCN_WSS_REGISTRATION_MEDIA_RUNTIME_OK")
    finally:
        cleanup = cleanup_mcn_media_qa(state)
        try:
            STATE_FILE.unlink(missing_ok=True)
            Path("/tmp/boris_mcn_wss_invite_debug.txt").unlink(missing_ok=True)
        except Exception:
            pass
        print("MCN_WSS_QA_FINALLY", {
            "status": cleanup.get("status"),
            "rows_deleted": cleanup.get("rows_deleted"),
            "released_status": (cleanup.get("released") or {}).get("status") if isinstance(cleanup.get("released"), dict) else None,
            "endpoint_status": (cleanup.get("endpoint") or {}).get("status") if isinstance(cleanup.get("endpoint"), dict) else None,
        })


if __name__ == "__main__":
    main()
