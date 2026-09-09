#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR="$ROOT/vendor"
mkdir -p "$VENDOR"

fetch() {
  local url="$1" out="$2"
  if [[ -s "$out" ]]; then return 0; fi
  curl -fsSL --retry 3 --retry-delay 1 "$url" -o "$out.tmp"
  mv "$out.tmp" "$out"
}

fetch "https://unpkg.com/react@18.3.1/umd/react.production.min.js" "$VENDOR/react.production.min.js"
fetch "https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js" "$VENDOR/react-dom.production.min.js"
fetch "https://unpkg.com/@babel/standalone/babel.min.js" "$VENDOR/babel.min.js"

echo "software-site build vendor ready"
