#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$ROOT/.." && pwd)"
SOURCE="$ROOT/index.source.html"
OUT="${1:-$REPO/software-site}"
ASSETS="$OUT/assets"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

"$ROOT/bootstrap.sh" >/dev/null
mkdir -p "$ASSETS"

python3 - "$SOURCE" "$TMP/app.jsx" <<'PY'
import re, sys
from pathlib import Path
src=Path(sys.argv[1]).read_text(encoding="utf-8")
m=re.search(r'<script type="text/babel">\s*(.*?)\s*</script>\s*</body>', src, re.S)
if not m:
    raise SystemExit("JSX script not found")
Path(sys.argv[2]).write_text(m.group(1), encoding="utf-8")
print("JSX bytes", len(m.group(1).encode()))
PY

BABEL_STANDALONE="$ROOT/vendor/babel.min.js" node - "$TMP/app.jsx" "$ASSETS/app.js" <<'JS'
const fs=require('fs');
const Babel=require(process.env.BABEL_STANDALONE);
const input=fs.readFileSync(process.argv[2],'utf8');
const result=Babel.transform(input,{
  presets:[['react',{runtime:'classic'}]],
  sourceType:'script',
  comments:false,
  compact:true,
  minified:true,
});
fs.writeFileSync(process.argv[3],result.code);
console.log('app.js bytes',Buffer.byteLength(result.code));
JS

cd "$REPO/frontend"
SITE_SOURCE="$SOURCE" node - "$ASSETS/site.css" <<'JS'
const fs=require('fs');
const postcss=require('postcss');
const tw=require('@tailwindcss/postcss');
const source=process.env.SITE_SOURCE.replaceAll('\\','/');
const out=process.argv[2];
const css='@import "tailwindcss";\n@source "'+source+'";\n';
postcss([tw({optimize:true})]).process(css,{
  from:'software-site-input.css',
  to:out
}).then(r=>{
  fs.writeFileSync(out,r.css);
  console.log('site.css bytes',Buffer.byteLength(r.css));
  if(r.warnings().length) console.error(r.warnings().map(x=>x.toString()).join('\n'));
}).catch(e=>{console.error(e);process.exit(1)});
JS

cp "$ROOT/vendor/react.production.min.js" "$ASSETS/react.production.min.js"
cp "$ROOT/vendor/react-dom.production.min.js" "$ASSETS/react-dom.production.min.js"

python3 - "$SOURCE" "$OUT/index.html" <<'PY'
import re, sys
from pathlib import Path
src=Path(sys.argv[1]).read_text(encoding="utf-8")

src=re.sub(
    r'\s*<!-- Tailwind CSS -->\s*'
    r'<script src="https://cdn\.tailwindcss\.com"></script>\s*'
    r'<script>\s*tailwind\.config\s*=\s*\{.*?</script>',
    '',
    src,
    count=1,
    flags=re.S,
)
src=re.sub(
    r'\s*<!-- React and Babel -->\s*'
    r'<script src="https://unpkg\.com/react@18/umd/react\.production\.min\.js" crossorigin></script>\s*'
    r'<script src="https://unpkg\.com/react-dom@18/umd/react-dom\.production\.min\.js" crossorigin></script>\s*'
    r'<script src="https://unpkg\.com/@babel/standalone/babel\.min\.js"></script>',
    '',
    src,
    count=1,
    flags=re.S,
)
src=re.sub(r'\s*<script type="text/babel">.*?</script>\s*</body>', '\n</body>', src, count=1, flags=re.S)

css='    <link rel="stylesheet" href="/software-dev/assets/site.css">\n'
if css.strip() not in src:
    src=src.replace('    <style>\n', css+'    <style>\n', 1)

scripts='''    <script src="/software-dev/assets/react.production.min.js"></script>
    <script src="/software-dev/assets/react-dom.production.min.js"></script>
    <script src="/software-dev/assets/app.js"></script>
'''
src=src.replace('</body>', scripts+'</body>', 1)

Path(sys.argv[2]).write_text(src,encoding="utf-8")
print("index.html bytes",len(src.encode()))
PY

if grep -Eq 'cdn\.tailwindcss\.com|@babel/standalone|type="text/babel"' "$OUT/index.html"; then
  echo "ERROR: development runtime dependency remained in index.html" >&2
  exit 1
fi

printf 'BUILD_OK index=%s css=%s js=%s\n'   "$(wc -c < "$OUT/index.html")" "$(wc -c < "$ASSETS/site.css")" "$(wc -c < "$ASSETS/app.js")"
