#!/usr/bin/env bash
set -euo pipefail
DOMAIN="${1:-}"
if [[ -z "$DOMAIN" || ! "$DOMAIN" =~ ^([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$ ]]; then
  echo "Использование: sudo $0 example.ru"
  exit 2
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_URL="${SOFTWARE_SITE_BASE_URL:-https://$DOMAIN}"
install -m 0755 "$SCRIPT_DIR/watch-site.sh" /usr/local/sbin/software-site-watch.sh
cat >/etc/default/software-site-watch <<EOF
SOFTWARE_SITE_BASE_URL=$BASE_URL
EOF
cat >/etc/systemd/system/software-site-watch.service <<'EOF'
[Unit]
Description=SYSTEMS.AI website health check
After=network-online.target nginx.service
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/default/software-site-watch
ExecStart=/usr/local/sbin/software-site-watch.sh
EOF
cat >/etc/systemd/system/software-site-watch.timer <<'EOF'
[Unit]
Description=Check SYSTEMS.AI website every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=30s
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now software-site-watch.timer
systemctl start software-site-watch.service
systemctl --no-pager status software-site-watch.timer | head -20
