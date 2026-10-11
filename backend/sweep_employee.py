import re, sys
sys.path.insert(0, "/root/BORIS/backend")
import requests
from app.api.auth import create_access_token
from app.main import app

BASE = "https://boris-ai.pro"
ACC = "3411770_94346"
SKIP = re.compile(r"sync|send|delete|remove|/run|start|generate|publish|import|refresh|analy|classify|download|upload|export|full_ai|infographic|diagonal|carousel|parse|train|learn|reset|apply|grant|create|save|add|test|webhook|callback|login|logout|register|verify|password|pay|invoice|wallet|billing", re.I)

paths = []
for r in getattr(app, "routes", []):
    p = getattr(r, "path", "") or ""
    m = getattr(r, "methods", None) or set()
    if "GET" in m and "{" not in p and p.startswith("/api/") and not SKIP.search(p):
        paths.append(p)
paths = sorted(set(paths))

def probe(path, token):
    h = {"Authorization": "Bearer " + token}
    try:
        r = requests.get(BASE + path, params={"account_id": ACC}, headers=h, timeout=25)
    except Exception as e:
        return "ERR", repr(e)[:60]
    tag = ""
    try:
        j = r.json()
        if isinstance(j, list):
            tag = "len=%d" % len(j)
        elif isinstance(j, dict):
            for k in ("items", "dialogs", "accounts", "rows", "tasks", "list", "data", "results"):
                if isinstance(j.get(k), list):
                    tag = "%s=%d" % (k, len(j[k]))
                    break
            if not tag and j.get("status") == "error":
                tag = "error: " + str(j.get("message"))[:60]
    except Exception:
        pass
    return str(r.status_code), tag

if "--run" not in sys.argv:
    print("кандидатов: %d" % len(paths))
    for p in paths:
        print("  " + p)
    sys.exit(0)

to = create_access_token(42)
te = create_access_token(57)
same = 0
for p in paths:
    so, ao = probe(p, to)
    se, ae = probe(p, te)
    if so == se and ao == ae:
        same += 1
        continue
    print("")
    print(p)
    print("   владелец  : %s %s" % (so, ao))
    print("   сотрудница: %s %s" % (se, ae))
print("")
print("совпало: %d из %d" % (same, len(paths)))
