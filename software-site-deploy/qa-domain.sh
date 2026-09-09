#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${1:-}"
if [[ -z "$DOMAIN" || ! "$DOMAIN" =~ ^([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$ ]]; then
  echo "Использование: $0 example.ru"
  exit 2
fi

BASE="https://$DOMAIN"
FAIL=0

check_code() {
  local path="$1" expected="$2" label="$3"
  local code
  code="$(curl -fsSk -o /dev/null -w '%{http_code}' --max-time 15 "$BASE$path" || true)"
  if [[ "$code" == "$expected" ]]; then
    echo "OK   $label -> $code"
  else
    echo "FAIL $label -> $code (ожидалось $expected)"
    FAIL=1
  fi
}

for p in   "/"   "/services"   "/services/mobile-app-development"   "/services/saas-development"   "/cases/boris-ai"   "/prices"   "/estimate"   "/contacts"   "/blog/skolko-stoit-mobilnoe-prilozhenie"; do
  check_code "$p" "200" "$p"
done

check_code "/manifest.webmanifest" "200" "PWA manifest"
check_code "/sw.js" "200" "Service worker"
check_code "/robots.txt" "200" "robots.txt"
check_code "/sitemap.xml" "200" "sitemap.xml"
check_code "/api/public/software-site/health" "200" "Форма/почта health"
check_code "/admin/" "401" "Закрытая админка без пароля"
check_code "/api/private/software-site/leads" "401" "Закрытый API без пароля"

python3 - "$DOMAIN" <<'PY'
import json, re, ssl, sys, urllib.request, xml.etree.ElementTree as ET
from urllib.parse import urljoin

domain=sys.argv[1]
ctx=ssl.create_default_context()
base=f"https://{domain}"

def read(path):
    req=urllib.request.Request(base+path,headers={"User-Agent":"SYSTEMS.AI-QA/1.0"})
    with urllib.request.urlopen(req,context=ctx,timeout=15) as r:
        return r.status, r.headers, r.read().decode("utf-8","replace")

errors=[]
for path in ["/","/services","/services/mobile-app-development","/cases/boris-ai","/prices","/contacts","/blog/skolko-stoit-mobilnoe-prilozhenie"]:
    try:
        status, headers, html=read(path)
    except Exception as e:
        errors.append(f"{path}: fetch {e}")
        continue
    if status != 200: errors.append(f"{path}: HTTP {status}")
    if not re.search(r"<title>.+?</title>",html,re.S): errors.append(f"{path}: title")
    m=re.search(r'<link rel="canonical" href="([^"]+)"',html)
    if not m or m.group(1) != base+path:
        errors.append(f"{path}: canonical={m.group(1) if m else None}")
    if 'application/ld+json' not in html: errors.append(f"{path}: schema")
    if re.search(r'cdn\.tailwindcss\.com|@babel/standalone|type="text/babel"',html):
        errors.append(f"{path}: dev runtime leaked")
    if "<noscript>" not in html: errors.append(f"{path}: noscript fallback")

try:
    _,_,xml=read("/sitemap.xml")
    root=ET.fromstring(xml)
    urls=root.findall("{http://www.sitemaps.org/schemas/sitemap/0.9}url")
    if len(urls) < 30: errors.append(f"sitemap urls={len(urls)}")
except Exception as e:
    errors.append(f"sitemap parse: {e}")

try:
    _,_,health=read("/api/public/software-site/health")
    d=json.loads(health)
    if not d.get("ok"): errors.append(f"health not ok: {d}")
    if d.get("recipient_count") != 2: errors.append(f"recipient_count={d.get('recipient_count')}")
except Exception as e:
    errors.append(f"health parse: {e}")

if errors:
    print("SEO/API QA FAIL")
    for e in errors: print(" -",e)
    raise SystemExit(1)
print("SEO/API QA PASS")
PY

if [[ -x /root/BORIS/backend/venv/bin/python ]]; then
  DOMAIN="$DOMAIN" /root/BORIS/backend/venv/bin/python - <<'PY'
import os
from playwright.sync_api import sync_playwright
domain=os.environ["DOMAIN"]
paths=["/","/services/mobile-app-development","/cases/boris-ai","/prices","/contacts","/blog/skolko-stoit-mobilnoe-prilozhenie"]
with sync_playwright() as p:
    b=p.chromium.launch(headless=True)
    failed=[]
    for width in (320,390,768,1440):
        for path in paths:
            page=b.new_page(viewport={"width":width,"height":900})
            errors=[]
            page.on("pageerror",lambda e,a=errors:a.append(str(e)))
            try:
                resp=page.goto(f"https://{domain}{path}",wait_until="domcontentloaded",timeout=30000)
                page.wait_for_timeout(350)
                overflow=page.evaluate("document.documentElement.scrollWidth-document.documentElement.clientWidth")
                broken=page.evaluate("[...document.images].filter(i=>i.complete&&i.naturalWidth===0).length")
                ok=bool(resp and resp.status==200 and overflow==0 and broken==0 and not errors)
                print(("OK  " if ok else "FAIL"),width,path,"status",resp.status if resp else None,"overflow",overflow,"broken",broken,"js",len(errors))
                if not ok: failed.append((width,path))
            except Exception as e:
                print("FAIL",width,path,e)
                failed.append((width,path))
            finally:
                page.close()
    b.close()
    if failed:
        raise SystemExit(f"Browser QA failed: {failed}")
print("BROWSER_QA_PASS")
PY
fi

if [[ "$FAIL" -ne 0 ]]; then
  exit 1
fi

echo "DOMAIN_QA_PASS $DOMAIN"
