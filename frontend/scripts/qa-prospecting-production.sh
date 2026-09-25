#!/usr/bin/env bash
set -euo pipefail

BASE="${BORIS_PUBLIC_BASE:-https://boris-ai.pro}"
PAGE="${BASE}/dashboard/prospecting"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

fail() {
  echo "PROSPECTING_RUNTIME_QA=FAIL $*"
  exit 1
}

code="$(curl -sS -o "$TMP" -w '%{http_code}' "$PAGE" || true)"
[ "$code" = "200" ] || fail "showcase_http=$code"

ACTIVE_STATIC="/root/BORIS/frontend/.boris-builds/current/static"
for marker in \
  "5 полноценных вариантов на одно направление" \
  "Так письмо выглядит для холодной базы" \
  "CRM И ОБРАБОТКА ОТВЕТОВ" \
  "возможна интеграция с вашей CRM" \
  "Клиентская демонстрация без риска" \
  "Получить расчёт"
do
  if ! grep -Fq "$marker" "$TMP"; then
    grep -R -Fq "$marker" "$ACTIVE_STATIC" || fail "missing_marker=$marker"
  fi
done

for asset in \
  /reviews/materials.jpg \
  /reviews/construction.jpg \
  /reviews/machinery.jpg \
  /reviews/paving.jpg \
  /reviews/towtruck.jpg
do
  asset_code="$(curl -sS -o /dev/null -w '%{http_code}' "${BASE}${asset}" || true)"
  [ "$asset_code" = "200" ] || fail "asset=${asset} http=${asset_code}"
done

for endpoint in \
  "/api/prospecting/scale/campaigns?account_id=__qa__" \
  "/api/prospecting/scale/mailboxes?account_id=__qa__" \
  "/api/prospecting/scale/replies?account_id=__qa__"
do
  api_code="$(curl -sS -o /dev/null -w '%{http_code}' "${BASE}${endpoint}" || true)"
  case "$api_code" in
    401|403) ;;
    *) fail "unauthorized_api_open endpoint=${endpoint} http=${api_code}" ;;
  esac
done

echo "PROSPECTING_RUNTIME_QA=PASS"
echo "SHOWCASE_HTTP=200"
echo "SHOWCASE_ASSETS=5/5"
echo "SHOWCASE_MARKERS=PASS"
echo "PROSPECTING_API_UNAUTH_DENY=PASS"
