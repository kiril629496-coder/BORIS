#!/usr/bin/env python3
"""Re-probe one BORIS mailbox using the production Fernet key.

No message is sent. SMTP only authenticates + NOOP; IMAP selects INBOX readonly.
The canonical client_mailboxes.test_mailbox() updates channel health truthfully.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

def _load_required_env_key(name: str) -> bool:
    try:
        for line in (ROOT/'.env').read_text(encoding='utf-8',errors='ignore').splitlines():
            s=line.strip()
            if not s or s.startswith('#') or '=' not in s:
                continue
            k,v=s.split('=',1)
            if k.strip()==name:
                value=v.strip().strip('"').strip("'")
                if value:
                    os.environ[name]=value
                    return True
    except Exception:
        return False
    return False

if not _load_required_env_key('FERNET_KEY'):
    print({'status':'blocked','reason':'production_fernet_key_missing'})
    raise SystemExit(3)

from app.services.client_mailboxes import test_mailbox

mailbox_id=int(sys.argv[1]) if len(sys.argv)>1 else 2
out=test_mailbox(mailbox_id)
safe={
  'smtp':bool(out.get('smtp')),
  'imap':bool(out.get('imap')),
  'smtp_error':out.get('smtp_error'),
  'imap_error':out.get('imap_error'),
}
print(safe)
raise SystemExit(0 if safe['smtp'] and safe['imap'] else 2)
