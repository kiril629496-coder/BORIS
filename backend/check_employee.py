import sys
sys.path.insert(0, "/root/BORIS/backend")
import requests
from app.api.auth import create_access_token
BASE = "https://boris-ai.pro"
USERS = [(42, "Sergey owner"), (56, "Valeriya emp"), (57, "Yuliya emp")]
PATHS = [
    "/api/accounts/list",
    "/api/inbox/accounts?account_id=all",
    "/api/inbox/dialogs?account_id=all&limit=5",
    "/api/inbox/dialogs?account_id=3411770_94346&limit=5",
    "/api/inbox/dialogs?account_id=prodazha_bytovok_25677&limit=5",
    "/api/inbox/dialogs?account_id=andrey_mebel_launzh_moskva_69737&limit=5",
    "/api/crm/badges?account_id=3411770_94346",
]
def size(j):
    if isinstance(j, list):
        return "len=%d" % len(j)
    if isinstance(j, dict):
        for k in ("dialogs", "accounts", "items", "slots", "results"):
            v = j.get(k)
            if isinstance(v, list):
                return "%s=%d" % (k, len(v))
    return ""
for uid, name in USERS:
    tok = create_access_token(uid)
    h = {"Authorization": "Bearer " + tok}
    print("")
    print("=== user %d - %s ===" % (uid, name))
    for p in PATHS:
        try:
            r = requests.get(BASE + p, headers=h, timeout=40)
            try:
                n = size(r.json())
            except Exception:
                n = ""
            body = r.text[:120].replace("\n", " ")
            print("%s  %-62s %s" % (r.status_code, p, n))
            if r.status_code != 200 or not n:
                print("      %s" % body)
        except Exception as e:
            print("ERR %-62s %s" % (p, e))
