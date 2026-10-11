#!/usr/bin/env bash
set -euo pipefail

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIVE="/var/www/boris-home"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cp -a "$SOURCE/index.html" "$TMP/index.html"
python3 - "$TMP/index.html" <<'PY'
from pathlib import Path
import re, sys, json
p=Path(sys.argv[1])
html=p.read_text(encoding="utf-8")
errors=[]
canon=re.findall(r'<link\s+rel=["\']canonical["\']\s+href=["\']([^"\']+)["\']',html,re.I)
if canon != ["https://boris-ai.pro/"]:
    errors.append(f"canonical={canon}")
required=[
    "BORIS AI — AI-маркетолог, продажи и автоматизация бизнеса",
    'name="description"',
    "111297252",
    "boris_lead_form_submit",
    "boris_primary_cta",
    'id="avito-ai-sales"',
    "Автоматизация Авито",
    "AI-МОП",
    "AI-РОП",
    'application/ld+json',
]
for token in required:
    if token not in html:
        errors.append(f"missing:{token}")
if "https://boris-ai.pro/software-dev/" in html:
    errors.append("old_software_dev_url_present")
print(json.dumps({"file":str(p),"bytes":p.stat().st_size,"errors":errors},ensure_ascii=False))
if errors:
    raise SystemExit(1)
PY

KEY="A91F6C7E2D4B8A503C6E9F1B7D2A4C8E"
if [ ! -f "$SOURCE/$KEY.txt" ] || [ "$(tr -d '\r\n' < "$SOURCE/$KEY.txt")" != "$KEY" ]; then
  echo "INDEXNOW_KEY_INVALID" >&2
  exit 1
fi

if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "BORIS_HOME_DRY_RUN_PASS"
  exit 0
fi

install -m 0644 "$SOURCE/index.html" "$LIVE/.index.html.next"
mv -f "$LIVE/.index.html.next" "$LIVE/index.html"
for f in "$SOURCE"/*.webp "$SOURCE/$KEY.txt"; do
  [ -e "$f" ] || continue
  base="$(basename "$f")"
  install -m 0644 "$f" "$LIVE/.$base.next"
  mv -f "$LIVE/.$base.next" "$LIVE/$base"
done

python3 - <<'PY'
import requests,re,json
u="https://boris-ai.pro/"
r=requests.get(u,timeout=20,headers={"User-Agent":"BORIS-Home-Deploy-Verify/1.0"})
errors=[]
if r.status_code != 200:
    errors.append(f"http:{r.status_code}")
canon=re.findall(r'<link\s+rel=["\']canonical["\']\s+href=["\']([^"\']+)["\']',r.text,re.I)
if canon != ["https://boris-ai.pro/"]:
    errors.append(f"canonical:{canon}")
for token in ("111297252","boris_primary_cta",'id="avito-ai-sales"'):
    if token not in r.text:
        errors.append(f"missing:{token}")
key=requests.get("https://boris-ai.pro/A91F6C7E2D4B8A503C6E9F1B7D2A4C8E.txt",timeout=20)
if key.status_code!=200 or key.text.strip()!="A91F6C7E2D4B8A503C6E9F1B7D2A4C8E":
    errors.append("indexnow_key_live_fail")
print(json.dumps({"http":r.status_code,"errors":errors},ensure_ascii=False))
if errors:
    raise SystemExit(1)
PY
echo "BORIS_HOME_DEPLOY_PASS"
