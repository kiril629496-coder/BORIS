#!/bin/bash
# Утренний цикл реактивации: сначала разбор новых диалогов, затем уведомления.
# Порядок важен: уведомлять можно только о том, что уже разобрано.
cd /root/BORIS/backend
set -a; . ./.env; set +a
{
  /root/BORIS/backend/venv/bin/python3 react_daily.py --apply
  /root/BORIS/backend/venv/bin/python3 react_notify.py --send
} >> /var/log/react_daily.log 2>&1
