#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${1:-}"
if [[ -z "$DOMAIN" || ! "$DOMAIN" =~ ^([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$ ]]; then
  echo "Использование: sudo $0 example.ru"
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EMAIL="${CERTBOT_EMAIL:-eliseev-ko@mail.ru}"
WITH_WWW="${WITH_WWW:-auto}"

echo "Проверяю DNS для $DOMAIN..."
mapfile -t APEX_IPS < <(getent ahostsv4 "$DOMAIN" | awk '{print $1}' | sort -u)
if [[ "${#APEX_IPS[@]}" -eq 0 ]]; then
  echo "DNS ещё не видит $DOMAIN. Сначала направьте A-запись на этот сервер."
  exit 3
fi
printf 'A/IPv4: %s\n' "${APEX_IPS[*]}"

WWW_ARGS=()
if [[ "$WITH_WWW" == "1" || "$WITH_WWW" == "auto" ]]; then
  if getent ahostsv4 "www.$DOMAIN" >/dev/null 2>&1; then
    WWW_ARGS=(-d "www.$DOMAIN")
    echo "www.$DOMAIN найден — включаю в сертификат."
  elif [[ "$WITH_WWW" == "1" ]]; then
    echo "WITH_WWW=1, но DNS для www.$DOMAIN не найден."
    exit 4
  else
    echo "www.$DOMAIN пока не настроен — запускаю без www."
  fi
fi

echo "1/3 Разворачиваю production-конфигурацию..."
"$SCRIPT_DIR/deploy-domain.sh" "$DOMAIN"

if ! command -v certbot >/dev/null 2>&1; then
  echo "certbot не установлен. Установите python3-certbot-nginx и повторите."
  exit 5
fi

echo "2/3 Выпускаю/обновляю HTTPS..."
certbot --nginx --non-interactive --agree-tos --email "$EMAIL" --redirect   -d "$DOMAIN" "${WWW_ARGS[@]}"

echo "3/3 Выполняю полный QA домена..."
"$SCRIPT_DIR/qa-domain.sh" "$DOMAIN"

echo "DOMAIN_RELEASE_PASS https://$DOMAIN/"
