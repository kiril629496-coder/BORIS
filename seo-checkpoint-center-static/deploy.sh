#!/usr/bin/env bash
set -euo pipefail
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIVE="/var/www/boris-seo-checkpoints"

node --check "$SOURCE/assets/app.js"
python3 - "$SOURCE/index.html" "$SOURCE/downloads/boris-seo-checkpoint-autofill.zip" <<'PY'
from pathlib import Path
import sys,zipfile,json
html=Path(sys.argv[1]).read_text(encoding='utf-8')
required=[
 'Очередь проверок',
 'startCaptchaModeButton',
 'topFiveCards',
 'openFirstFiveButton',
 'openNextFiveButton',
 'Открыть выбранные',
 '/seo-checkpoints-assets/app.js?v=20261005-r2',
]
missing=[x for x in required if x not in html]
if missing:
    raise SystemExit('HTML_MISSING:'+','.join(missing))
with zipfile.ZipFile(sys.argv[2]) as z:
    mf=json.loads(z.read('manifest.json').decode())
    bg=z.read('background.js').decode('utf-8','ignore')
    ct=z.read('content.js').decode('utf-8','ignore')
assert mf.get('version')=='1.2.0'
assert 'state: "opened"' in bg
assert 'ТВОЁ ДЕЙСТВИЕ — CAPTCHA' in ct
print('STATIC_SOURCE_VALIDATION=PASS')
PY

if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "SEO_CHECKPOINT_STATIC_DRY_RUN=PASS"
  exit 0
fi

install -d -m 0755 "$LIVE/assets" "$LIVE/downloads"
install -m 0644 "$SOURCE/index.html" "$LIVE/.index.html.next"
install -m 0644 "$SOURCE/assets/app.css" "$LIVE/assets/.app.css.next"
install -m 0644 "$SOURCE/assets/app.js" "$LIVE/assets/.app.js.next"
install -m 0644 "$SOURCE/downloads/boris-seo-checkpoint-autofill.zip" "$LIVE/downloads/.boris-seo-checkpoint-autofill.zip.next"
mv -f "$LIVE/.index.html.next" "$LIVE/index.html"
mv -f "$LIVE/assets/.app.css.next" "$LIVE/assets/app.css"
mv -f "$LIVE/assets/.app.js.next" "$LIVE/assets/app.js"
mv -f "$LIVE/downloads/.boris-seo-checkpoint-autofill.zip.next" "$LIVE/downloads/boris-seo-checkpoint-autofill.zip"

nginx -t
systemctl reload nginx
echo "SEO_CHECKPOINT_STATIC_DEPLOY=PASS"
