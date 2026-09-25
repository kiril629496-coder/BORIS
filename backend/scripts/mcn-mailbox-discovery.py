#!/usr/bin/env python3
"""Read-only discovery of MCN/SIP configuration letters in a BORIS mailbox.

Safety contract:
- IMAP SELECT is readonly.
- BODY.PEEK is used, so messages are not marked read.
- No IMAP cursor or BORIS mailbox state is changed.
- Password/username/DID/registrar values are NEVER printed by this script.
- It only reports candidate message metadata and which setting classes were found.

This is intentionally discovery-only. It never configures a trunk or makes a call.
"""
from __future__ import annotations

import argparse
import email
import html
import imaplib
import json
import re
import sys
from email.header import decode_header, make_header
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.client_mailboxes import _row, _mailbox_password, mailbox_error_kind


_MCNSIG = re.compile(
    r"\bMCN\b|МЦН|МСН|виртуальн\w*\s+АТС|телефони|SIP",
    re.I,
)
_REGISTRAR = re.compile(
    r"(?:сервер(?:\s+регистрации)?\s*SIP|registrar|proxy\s+server(?:\s+ip\s*/\s*domain)?|"
    r"sip\s+server|sip\s+domain|proxy\s+server\s+realm)\s*[:=]\s*([^\r\n<]{2,160})",
    re.I,
)
_USERNAME = re.compile(
    r"(?:логин\s*SIP|SIP\s*(?:login|user(?:name)?|account)|user\s*id\s*/\s*account|"
    r"auth(?:entication)?\s*(?:id|user(?:name)?))\s*[:=]\s*([^\r\n<]{1,160})",
    re.I,
)
_PASSWORD = re.compile(
    r"(?:пароль\s*SIP|SIP\s*password|password|пароль)\s*[:=]\s*([^\r\n<]{1,220})",
    re.I,
)
_DID = re.compile(
    r"(?:номер\s*MCN|DID|телефонн(?:ый|ого)\s+номер|caller\s*id|входящ(?:ий|его)\s+номер)"
    r"\s*[:=]\s*([^\r\n<]{4,100})",
    re.I,
)
_SOURCE_IP = re.compile(
    r"(?:source\s*ip|ip[-\s]?адрес|trusted\s*ip|разрешенн\w*\s+ip)"
    r"\s*[:=]\s*([^\r\n<]{3,120})",
    re.I,
)


def _production_fernet_key() -> str | None:
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if s.startswith("FERNET_KEY="):
                value = s.split("=", 1)[1].strip().strip('"').strip("'")
                return value or None
    except Exception:
        return None
    return None


def _decrypt_mailbox_secret(ciphertext: str) -> str | None:
    value = str(ciphertext or "")
    if not value:
        return None
    if not value.startswith("gAAAA"):
        return value
    key = _production_fernet_key()
    if not key:
        return None
    try:
        return Fernet(key.encode()).decrypt(value.encode()).decode()
    except Exception:
        return None


def _decode_header(value: str | None) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return str(value or "")


def _plain_message(msg: email.message.Message) -> str:
    parts: list[str] = []
    if msg.is_multipart():
        payloads = list(msg.walk())
    else:
        payloads = [msg]
    for part in payloads:
        ctype = str(part.get_content_type() or "").lower()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            data = part.get_payload(decode=True)
            charset = part.get_content_charset() or "utf-8"
            text_value = data.decode(charset, errors="replace") if isinstance(data, bytes) else str(data or "")
        except Exception:
            continue
        if ctype == "text/html":
            text_value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text_value)
            text_value = re.sub(r"(?s)<[^>]+>", " ", text_value)
            text_value = html.unescape(text_value)
        parts.append(text_value)
    return "\n".join(parts)


def analyze_text(text_value: str) -> dict[str, Any]:
    text_value = str(text_value or "")
    fields = {
        "registrar": bool(_REGISTRAR.search(text_value)),
        "username": bool(_USERNAME.search(text_value)),
        "password": bool(_PASSWORD.search(text_value)),
        "did": bool(_DID.search(text_value)),
        "source_ip": bool(_SOURCE_IP.search(text_value)),
    }
    registration_complete = all(fields[k] for k in ("registrar", "username", "password", "did"))
    ip_complete = bool(fields["source_ip"] and fields["did"])
    signal_count = sum(1 for value in fields.values() if value)
    score = signal_count * 20
    if _MCNSIG.search(text_value):
        score += 20
    if registration_complete or ip_complete:
        score += 40
    return {
        "fields_found": fields,
        "registration_complete": registration_complete,
        "ip_complete": ip_complete,
        "candidate_complete": bool(registration_complete or ip_complete),
        "score": score,
    }


def analyze_message(raw: bytes, uid: str) -> dict[str, Any]:
    msg = email.message_from_bytes(raw)
    subject = _decode_header(msg.get("Subject"))
    sender = _decode_header(msg.get("From"))
    date_value = _decode_header(msg.get("Date"))
    body = _plain_message(msg)
    analysis = analyze_text("\n".join((sender, subject, body)))
    addr = parseaddr(sender)[1]
    sender_domain = addr.rsplit("@", 1)[-1].lower() if "@" in addr else None
    return {
        "uid": str(uid),
        "date": date_value,
        "from": sender,
        "sender_domain": sender_domain,
        "subject": subject,
        **analysis,
    }


def discover(mailbox_id: int, *, limit: int = 1200, max_candidates: int = 20) -> dict[str, Any]:
    row = _row(int(mailbox_id))
    if not row:
        return {"status": "blocked", "reason": "mailbox_not_found", "mailbox_id": int(mailbox_id)}
    secret = _mailbox_password(row)
    if not secret:
        return {
            "status": "blocked",
            "reason": "mailbox_secret_decryption_unavailable",
            "mailbox_id": int(mailbox_id),
            "mailbox": row.get("email_address"),
        }

    cls = imaplib.IMAP4_SSL if row["imap_ssl"] else imaplib.IMAP4
    conn = None
    try:
        conn = cls(row["imap_host"], int(row["imap_port"]))
        conn.login(row["username"], secret)
        status, count_data = conn.select("INBOX", readonly=True)
        if status != "OK":
            return {
                "status": "blocked",
                "reason": "inbox_select_failed",
                "mailbox_id": int(mailbox_id),
                "mailbox": row.get("email_address"),
            }
        status, uid_data = conn.uid("search", None, "ALL")
        all_uids = uid_data[0].split() if status == "OK" and uid_data and uid_data[0] else []
        chosen = all_uids[-max(1, min(int(limit), 5000)):]
        candidates: list[dict[str, Any]] = []
        scanned = 0
        for uid_raw in reversed(chosen):
            uid = uid_raw.decode(errors="ignore")
            status, fetched = conn.uid("fetch", uid_raw, "(BODY.PEEK[])")
            if status != "OK" or not fetched:
                continue
            raw = b"".join(
                item[1]
                for item in fetched
                if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], (bytes, bytearray))
            )
            if not raw:
                continue
            scanned += 1
            item = analyze_message(raw, uid)
            if item["score"] < 20:
                continue
            candidates.append(item)
            if len(candidates) >= max(1, min(int(max_candidates), 100)):
                break
        complete = [x for x in candidates if x.get("candidate_complete")]
        return {
            "status": "ok",
            "mailbox_id": int(mailbox_id),
            "mailbox": row.get("email_address"),
            "folder": "INBOX",
            "messages_available": int(count_data[0]) if count_data and count_data[0] else None,
            "messages_scanned": scanned,
            "candidates": candidates,
            "complete_candidates": len(complete),
            "truth": "read-only BODY.PEEK discovery; no credentials or message body are returned",
        }
    except Exception as exc:
        reason = f"{type(exc).__name__}:{' '.join(str(exc).split())[:180]}"
        kind = mailbox_error_kind(reason)
        return {
            "status": "blocked",
            "reason": kind or "imap_access_failed",
            "error_type": type(exc).__name__,
            "mailbox_id": int(mailbox_id),
            "mailbox": row.get("email_address"),
        }
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mailbox-id", type=int, default=2)
    ap.add_argument("--limit", type=int, default=1200)
    ap.add_argument("--max-candidates", type=int, default=20)
    args = ap.parse_args()
    out = discover(args.mailbox_id, limit=args.limit, max_candidates=args.max_candidates)
    print(json.dumps(out, ensure_ascii=False, default=str, indent=2))
    if out.get("status") == "ok":
        return 0 if out.get("complete_candidates") else 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
