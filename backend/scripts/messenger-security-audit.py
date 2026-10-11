#!/usr/bin/env python3
from pathlib import Path
import argparse
import re

parser = argparse.ArgumentParser()
parser.add_argument("--backend-root", default="/root/BORIS/backend")
parser.add_argument("--frontend-root", default="/root/BORIS/frontend")
args = parser.parse_args()

BACKEND = Path(args.backend_root).resolve()
FRONTEND = Path(args.frontend_root).resolve()

targets = [
    FRONTEND / "app/messenger",
    FRONTEND / "next.config.ts",
    BACKEND / "app/api/internal_messenger.py",
    BACKEND / "app/api/team.py",
    BACKEND / "app/api/legal.py",
    BACKEND / "app/api/auth.py",
    BACKEND / "app/api/lead_notifications.py",
    BACKEND / "app/services/auth_sessions.py",
    BACKEND / "app/services/messenger_legal.py",
    BACKEND / "app/services/messenger_security.py",
    BACKEND / "app/services/messenger_rate_limit.py",
]

files = []
for target in targets:
    if target.is_dir():
        files.extend(p for p in target.rglob("*") if p.is_file() and ".bak" not in p.name and p.suffix in {".py", ".ts", ".tsx", ".css", ".md"})
    elif target.is_file():
        files.append(target)

secret_patterns = {
    "PRIVATE_KEY": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "OPENAI_STYLE_KEY": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "AWS_ACCESS_KEY": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "DATABASE_URL_WITH_PASSWORD": re.compile(r"\b(?:postgres|postgresql|mysql)://[^\s:/]+:[^\s@]+@[^\s]+", re.I),
}
failures = []
for path in sorted(set(files)):
    text = path.read_text(encoding="utf-8", errors="ignore")
    for name, pattern in secret_patterns.items():
        if pattern.search(text):
            failures.append(f"{name}:{path}")

def read(path: Path) -> str:
    if not path.is_file():
        failures.append(f"MISSING:{path}")
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")

messenger = read(FRONTEND / "app/messenger/page.tsx")
internal = read(BACKEND / "app/api/internal_messenger.py")
team = read(BACKEND / "app/api/team.py")
push = read(BACKEND / "app/api/lead_notifications.py")
main = read(BACKEND / "app/main.py")
next_config = read(FRONTEND / "next.config.ts")
security_audit = read(BACKEND / "app/services/messenger_security.py")

checks = {
    "NO_RAW_HTML": "dangerouslySetInnerHTML" not in messenger,
    "INTERNAL_LEGAL_GATE": "dependencies=[Depends(require_messenger_legal_user)]" in internal,
    "TEAM_LEGAL_GATE": "Depends(require_messenger_legal_user)" in team,
    "PUSH_LEGAL_GATE": "dependencies=[Depends(require_messenger_legal_user)]" in push,
    "CORS_NOT_WILDCARD": 'allow_origins=["*"]' not in main and '_cors_origins = ["https://boris-ai.pro"]' in main,
    "BACKEND_SECURITY_HEADERS": all(x in main for x in ("Strict-Transport-Security", "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Permissions-Policy")),
    "FRONTEND_CSP": "Content-Security-Policy" in next_config and "frame-ancestors 'none'" in next_config and "object-src 'none'" in next_config,
    "NO_INLINE_SVG_UPLOAD": '"image/svg+xml"' not in internal,
    "ATTACHMENT_MAGIC": "_validate_attachment_magic" in internal,
    "SECURITY_AUDIT": 'source="messenger_security"' in security_audit,
}
for name, ok in checks.items():
    if not ok:
        failures.append(name)

print("MESSENGER_SECURITY_SOURCE_AUDIT")
print(f"BACKEND_ROOT={BACKEND}")
print(f"FRONTEND_ROOT={FRONTEND}")
print(f"FILES_SCANNED={len(set(files))}")
for name, ok in checks.items():
    print(f"{name}={'PASS' if ok else 'FAIL'}")
print(f"FAILURES={len(failures)}")
for failure in failures:
    print("FAIL", failure)
if failures:
    raise SystemExit(1)
print("SECRET_SCAN=PASS")
print("MESSENGER_SECURITY_SOURCE_AUDIT=PASS")
