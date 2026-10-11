import os, getpass
for _l in open('/root/BORIS/backend/.env'):
    _l = _l.strip()
    if _l and not _l.startswith('#') and '=' in _l:
        _k, _v = _l.split('=', 1); os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))
import requests
B = "http://127.0.0.1:8000"
ACC = "3411770_94346"
p = getpass.getpass("Пароль клиента: ")
r = requests.post(B + "/api/auth/login", json={"email": "3411770@mail.ru", "password": p}, timeout=20)
p = None
if r.status_code != 200:
    raise SystemExit("логин не прошёл: " + str(r.status_code))
H = {"Authorization": "Bearer " + (r.json().get("token") or r.json().get("access_token"))}
print("логин: 200")
def probe(name, path, params=None):
    x = requests.get(B + path, params=params, headers=H, timeout=25)
    print("%-22s %s" % (name, x.status_code))
    print("    ", x.text[:200].replace("\n", " "))
probe("inbox/slots", "/api/inbox/slots")
probe("inbox/accounts", "/api/inbox/accounts")
probe("accounts/list", "/api/accounts/list")
probe("home/overview", "/api/home/overview", {"account_id": ACC})
probe("чужой (ждём 403)", "/api/home/overview", {"account_id": "otdushi"})
probe("storage чужой (403)", "/api/storage/load", {"account_id": "otdushi", "key": "billing"})
