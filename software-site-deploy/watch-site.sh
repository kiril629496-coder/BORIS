#!/usr/bin/env bash
set -euo pipefail
BASE="${SOFTWARE_SITE_BASE_URL:-https://boris-ai.pro/software-dev}"
LOG_PREFIX="SOFTWARE_SITE_WATCH"
paths=("/" "/assets/app.js" "/manifest.webmanifest" "/api/public/software-site/health")

check() {
  local bad=0 p code url
  for p in "${paths[@]}"; do
    if [[ "$p" == /api/* ]]; then
      if [[ "$BASE" == */software-dev ]]; then
        url="https://boris-ai.pro$p"
      else
        url="${BASE%/}$p"
      fi
    else
      url="${BASE%/}$p"
    fi
    code="$(curl -fsSk -o /dev/null -w '%{http_code}' --max-time 10 "$url" || true)"
    if [[ "$code" != "200" ]]; then
      echo "$LOG_PREFIX FAIL url=$url code=$code"
      bad=1
    fi
  done
  return "$bad"
}

if check; then
  echo "$LOG_PREFIX PASS base=$BASE"
  exit 0
fi

echo "$LOG_PREFIX retry_after_nginx_reload"
if nginx -t >/dev/null 2>&1; then
  systemctl reload nginx || true
fi
sleep 2
check
