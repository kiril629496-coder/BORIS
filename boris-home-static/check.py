#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else "/var/www/boris-home/index.html")
text = path.read_text(encoding="utf-8")
errors = []

required = {
    "canonical": '<link rel="canonical" href="https://boris-ai.pro/">',
    "title": "BORIS AI — AI-маркетолог, продажи и автоматизация бизнеса",
    "description": "AI-система",
    "schema": '"@type":"SoftwareApplication"',
    "metrika": "111297252",
    "primary_cta_goal": "boris_primary_cta",
    "lead_goal": "boris_lead_form_submit",
    "avito_ai_sales": 'id="avito-ai-sales"',
}
for key, marker in required.items():
    if marker not in text:
        errors.append(f"missing:{key}")

canonicals = re.findall(
    r'<link\s+rel=["\']canonical["\']\s+href=["\']([^"\']+)["\']',
    text,
    flags=re.I,
)
if canonicals != ["https://boris-ai.pro/"]:
    errors.append(f"canonical_set:{canonicals}")

if "https://boris-ai.pro/software-dev/" in canonicals:
    errors.append("retired_software_dev_canonical")

# A static homepage may legitimately mention development in prose, but never
# use the retired development tree as the canonical target.
if text.count('id="avito-ai-sales"') != 1:
    errors.append(f"avito_ai_sales_count:{text.count('id=\"avito-ai-sales\"')}")

print({
    "file": str(path),
    "bytes": len(text.encode("utf-8")),
    "canonical": canonicals,
    "errors": errors,
})
raise SystemExit(1 if errors else 0)
