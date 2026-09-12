#!/usr/bin/env bash
set -euo pipefail
# OWNER_EMAIL_CANONICAL_RUNTIME_V4
export PROSPECT_OWNER_ALLOW_TRANSPORT_FAILOVER=false
export PROSPECT_AUTOSTART_EMAIL=eliseev-ko@mail.ru
export PROSPECT_OWNER_IMAP_HOST=imap.mail.ru
export PROSPECT_OWNER_IMAP_PORT=993
export PROSPECT_OUTREACH_REPLY_TO=eliseev-ko@mail.ru
exec /root/BORIS/backend/venv/bin/python /root/BORIS/backend/email_queue_runner.py
