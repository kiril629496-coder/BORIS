#!/usr/bin/env python3
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    ROOT / "app/messenger/page.tsx",
    ROOT / "app/messenger/invite/page.tsx",
    *sorted((ROOT / "app/messenger/components").rglob("*.tsx")),
]

failures = []
button_total = 0

for path in TARGETS:
    if not path.is_file():
        failures.append(f"missing_file={path.relative_to(ROOT)}")
        continue
    source = path.read_text(encoding="utf-8")
    rel = path.relative_to(ROOT)
    buttons = list(re.finditer(r"<button\b([^>]*)>(.*?)</button>", source, re.S))
    button_total += len(buttons)

    for match in buttons:
        attrs = match.group(1)
        body = " ".join(re.sub(r"<[^>]+>", " ", match.group(2)).split())[:120]
        line = source.count("\n", 0, match.start()) + 1
        if "onClick=" not in attrs and 'type="submit"' not in attrs and "type='submit'" not in attrs and "formAction=" not in attrs:
            failures.append(f"{rel}:{line}:button_without_action text={body!r}")

    for pattern, label in (
        (r"\bTODO\b", "todo"),
        (r"console\.log\(", "console_log"),
        (r"\balert\(", "alert_only"),
    ):
        for match in re.finditer(pattern, source):
            failures.append(f"{rel}:{source.count(chr(10), 0, match.start()) + 1}:{label}")

    for label in ("Пригласить участника", "Отозвать доступ", "Создать задачу", "Установить BORIS Messenger"):
        for match in re.finditer(r"<(?:span|div)\b[^>]*>\s*" + re.escape(label) + r"\s*</(?:span|div)>", source):
            failures.append(f"{rel}:{source.count(chr(10), 0, match.start()) + 1}:fake_action_element text={label!r}")

print(f"MESSENGER_CONTROL_FILES={len(TARGETS)}")
print(f"MESSENGER_CONTROLS_TOTAL={button_total}")
print(f"MESSENGER_CONTROLS_FAIL={len(failures)}")
for failure in failures:
    print("FAIL", failure)

if failures:
    raise SystemExit(1)
print("MESSENGER_CONTROLS_QA=PASS")
