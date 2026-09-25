#!/usr/bin/env bash
set -euo pipefail
# GATEWAY_PUBLIC_ATOMIC_SYNC_V1
CANON=/root/BORIS/frontend/public/boris-browser-gateway.zip
BUILD=/root/BORIS/frontend/scripts/build-browser-gateway.sh
DEST=/var/www/boris-browser-gateway.zip
TMP=/var/www/.boris-browser-gateway.zip.tmp.$$
"$BUILD"
python3 - "$CANON" <<'PY'
import sys,zipfile,json
p=sys.argv[1]
with zipfile.ZipFile(p) as z:
    expected=sorted('boris-browser-gateway/'+f for f in ['manifest.json','background.js','popup.html','popup.js','offscreen.html','offscreen.js'])
    actual=sorted(n for n in z.namelist() if not n.endswith('/'))
    if actual != expected: raise SystemExit('invalid gateway entries')
    print('SOURCE_VERSION='+str(json.loads(z.read('boris-browser-gateway/manifest.json')).get('version') or ''))
PY
cp "$CANON" "$TMP"
chmod 0644 "$TMP"
python3 - "$CANON" "$TMP" <<'PY'
import sys,hashlib
x=open(sys.argv[1],'rb').read(); y=open(sys.argv[2],'rb').read()
if x!=y: raise SystemExit('staged mismatch')
print('STAGED_SHA256='+hashlib.sha256(y).hexdigest())
PY
mv -f "$TMP" "$DEST"
chmod 0644 "$DEST"
python3 - "$CANON" "$DEST" <<'PY'
import sys,hashlib,zipfile,json
x=open(sys.argv[1],'rb').read(); y=open(sys.argv[2],'rb').read()
if x!=y: raise SystemExit('final mismatch')
with zipfile.ZipFile(sys.argv[2]) as z: v=json.loads(z.read('boris-browser-gateway/manifest.json'))['version']
print('GATEWAY_PUBLIC_VERSION='+str(v)); print('GATEWAY_PUBLIC_SHA256='+hashlib.sha256(y).hexdigest()); print('GATEWAY_PUBLIC=PASS')
PY
