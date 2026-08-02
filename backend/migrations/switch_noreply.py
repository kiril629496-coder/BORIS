import os, re, stat, sys

ENV = os.environ.get("ENV_PATH", "/root/BORIS/backend/.env")
pw = os.environ.get("APPPW", "")

if not pw:
    print("ПАРОЛЬ ПУСТ — ничего не меняю"); sys.exit(1)
if "'" in pw:
    print("В пароле есть одинарная кавычка — остановлено, скажите об этом"); sys.exit(1)

NEW = [
    ("SMTP_USER", "noreply@boris-ai.pro"),
    ("SMTP_PASS", "'%s'" % pw),
    ("EMAIL_FROM_ADDRESS", "noreply@boris-ai.pro"),
    ("EMAIL_FROM_NAME", '"BORIS AI"'),
    ("EMAIL_REPLY_TO", "support@boris-ai.pro"),
    ("SUPPORT_EMAIL", "ostapenko-kirill-86@yandex.ru"),
]

lines = open(ENV, encoding="utf-8").read().split("\n")
for key, val in NEW:
    row = "%s=%s" % (key, val)
    found = False
    for i, ln in enumerate(lines):
        if re.match(r"^%s=" % re.escape(key), ln):
            lines[i] = row; found = True; break
    if not found:
        while lines and lines[-1].strip() == "":
            lines.pop()
        lines.append(row)
    print("  %-20s %s" % (key, "заменено" if found else "добавлено"))

open(ENV, "w", encoding="utf-8").write("\n".join(lines).rstrip("\n") + "\n")
os.chmod(ENV, stat.S_IRUSR | stat.S_IWUSR)
print("права .env: 600")
