#!/usr/bin/env bash
set -euo pipefail
# GATEWAY_ZIP_SYNC_V1: downloadable Browser Gateway must be byte-identical to
# the current unpacked production extension, never a stale previous version.
ROOT="/root/BORIS/frontend/public"
DIR="$ROOT/boris-browser-gateway"
OUT="$ROOT/boris-browser-gateway.zip"
TMP="$ROOT/.boris-browser-gateway.zip.tmp.$$"
FILES=(manifest.json background.js popup.html popup.js offscreen.html offscreen.js)
for f in "${FILES[@]}"; do [ -f "$DIR/$f" ] || { echo "GATEWAY_ZIP=FAIL missing $f"; exit 41; }; done
rm -f "$TMP"
python3 - "$DIR" "$TMP" <<'PYBUILD'
import sys,zipfile,pathlib
base=pathlib.Path(sys.argv[1]); out=pathlib.Path(sys.argv[2])
files=['manifest.json','background.js','popup.html','popup.js','offscreen.html','offscreen.js']
with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    for f in files:
        z.write(base/f, arcname='boris-browser-gateway/'+f)
PYBUILD
python3 - "$DIR" "$TMP" <<'PY'
import sys,zipfile,pathlib,json
base=pathlib.Path(sys.argv[1]); zpath=pathlib.Path(sys.argv[2])
files=['manifest.json','background.js','popup.html','popup.js','offscreen.html','offscreen.js']
with zipfile.ZipFile(zpath) as z:
    names=sorted(n for n in z.namelist() if not n.endswith('/'))
    expected=sorted('boris-browser-gateway/'+f for f in files)
    if names != expected:
        raise SystemExit(f'GATEWAY_ZIP=FAIL entries={names!r}')
    for f in files:
        if z.read('boris-browser-gateway/'+f) != (base/f).read_bytes():
            raise SystemExit(f'GATEWAY_ZIP=FAIL mismatch={f}')
    manifest=json.loads(z.read('boris-browser-gateway/manifest.json'))
    print('GATEWAY_ZIP_VERSION='+str(manifest.get('version') or ''))
print('GATEWAY_ZIP=PASS')
PY
mv -f "$TMP" "$OUT"
