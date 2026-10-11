#!/usr/bin/env python3
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    ROOT / "app/messenger/page.tsx",
    ROOT / "app/messenger/invite/page.tsx",
    *sorted((ROOT / "app/messenger/components").rglob("*.tsx")),
]
errors = []
counts = {"button": 0, "select": 0, "input": 0, "textarea": 0, "link": 0}

for path in TARGETS:
    if not path.exists():
        errors.append(f"missing:{path.relative_to(ROOT)}")
        continue
    source = path.read_text(encoding="utf-8")
    rel = path.relative_to(ROOT)
    for kind, pattern in {
        "button": r"<button\b[^>]*>",
        "select": r"<select\b[^>]*>",
        "input": r"<input\b[^>]*>",
        "textarea": r"<textarea\b[^>]*>",
        "link": r"<a\b[^>]*>",
    }.items():
        tags = list(re.finditer(pattern, source, re.S))
        counts[kind] += len(tags)
        if kind == "button":
            for match in tags:
                tag = match.group(0)
                dynamic_nav = "key={key}" in tag
                if "onClick=" not in tag and "type=\"submit\"" not in tag and "formAction=" not in tag and not dynamic_nav:
                    line = source.count("\n", 0, match.start()) + 1
                    errors.append(f"{rel}:{line}:button_without_handler:{tag[:160]}")
    for marker in ("TODO", "console.log(", "alert(", "disabled={true}"):
        if marker in source:
            errors.append(f"{rel}:forbidden_marker:{marker}")

print("MESSENGER_CONTROL_AUDIT")
for key, value in counts.items():
    print(f"{key.upper()}S={value}")
print(f"TOTAL_CONTROLS={sum(counts.values())}")
print(f"FAILURES={len(errors)}")
for item in errors:
    print("FAIL", item)
if errors:
    sys.exit(1)
print("BUTTON_AUDIT_STATIC=PASS")
print("NO_FAKE_CONTROLS_STATIC=PASS")
