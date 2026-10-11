import sys
sys.path.insert(0, "/root/BORIS/backend")
import requests
from app.api.auth import create_access_token
BASE = "https://boris-ai.pro"
ACC = "3411770_94346"
PATHS = ["/api/team/list", "/api/support/all", "/api/economics/overview", "/api/inbox/slots", "/api/inbox/pricing"]
for uid, name in [(42, "Sergey owner"), (57, "Yuliya emp")]:
    h = {"Authorization": "Bearer " + create_access_token(uid)}
    print("")
    print("=== %s ===" % name)
    for p in PATHS:
        try:
            r = requests.get(BASE + p, params={"account_id": ACC}, headers=h, timeout=30)
            print("%s %s" % (r.status_code, p))
            print("   %s" % r.text[:300].replace("\n", " "))
        except Exception as e:
            print("ERR %s %s" % (p, e))
