#!/usr/bin/env python3
from __future__ import annotations
import os
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import text

import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from app.db.session import SessionLocal


def env_key(path: Path) -> str|None:
    try:
        for line in path.read_text(encoding="utf-8",errors="ignore").splitlines():
            s=line.strip()
            if s.startswith("FERNET_KEY="):
                v=s.split("=",1)[1].strip().strip('"').strip("'")
                return v or None
    except Exception:
        return None
    return None


def can_decrypt(key: str|None, ciphertext: str) -> bool:
    if not key or not ciphertext:
        return False
    try:
        plain=Fernet(key.encode()).decrypt(ciphertext.encode())
        return bool(plain)
    except Exception:
        return False


def main() -> int:
    with SessionLocal() as db:
        row=db.execute(text("select secret_encrypted from client_mailboxes where id=2")).first()
    if not row:
        print("MAILBOX_SECRET=missing")
        return 2
    ciphertext=str(row[0] or "")
    print("CIPHERTEXT_FORMAT="+("fernet" if ciphertext.startswith("gAAAA") else "other"))
    current=env_key(ROOT/".env")
    print("CURRENT_KEY_DECRYPTS="+str(can_decrypt(current,ciphertext)).lower())

    patterns=[
        ROOT/".boris_backups",
        ROOT/"backups",
        ROOT.parent/"backups",
        ROOT,
    ]
    matches=[]
    checked=0
    seen=set()
    for base in patterns:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            name=path.name.lower()
            if ".env" not in name and not name.startswith("backend.env"):
                continue
            try:
                rp=str(path.resolve())
            except Exception:
                rp=str(path)
            if rp in seen or path.resolve()==(ROOT/".env").resolve():
                continue
            seen.add(rp)
            key=env_key(path)
            if not key:
                continue
            checked+=1
            if can_decrypt(key,ciphertext):
                matches.append(str(path))
    print("BACKUP_KEYS_CHECKED="+str(checked))
    print("MATCHING_BACKUP_KEYS="+str(len(matches)))
    for path in matches[:50]:
        print("MATCH="+path)
    return 0 if (can_decrypt(current,ciphertext) or matches) else 3


if __name__=="__main__":
    raise SystemExit(main())
