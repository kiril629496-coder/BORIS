#!/usr/bin/env python3
from pathlib import Path
import re, sys
ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "app/messenger/page.tsx").read_text(encoding="utf-8")
BACKEND = ROOT.parent / "backend/app"
full_routes = []
for path in BACKEND.rglob("*.py"):
    if ".bak" in path.name:
        continue
    text = path.read_text(encoding="utf-8", errors="ignore")
    pm = re.search(r'router\s*=\s*APIRouter\(prefix="([^"]*)"', text)
    prefix = pm.group(1) if pm else ""
    for match in re.finditer(r'@router\.(get|post|patch|delete|put)\("([^"]+)"\)', text):
        full_routes.append((match.group(1).upper(), prefix + match.group(2)))
def normalize_frontend(path):
    path = path.split("?")[0]
    return re.sub(r"\$\{[^}]+\}", "{param}", path)
def route_matches(front, back):
    fp=[p for p in front.strip("/").split("/") if p]
    bp=[p for p in back.strip("/").split("/") if p]
    if len(fp)!=len(bp): return False
    for a,b in zip(fp,bp):
        if a=="{param}" or (b.startswith("{") and b.endswith("}")): continue
        if a!=b: return False
    return True
calls=[]
pattern = r'''api(?:<[^\n]*?>)?\((\x60[^\x60]+\x60|"[^"]+"|'[^']+')'''
for match in re.finditer(pattern, source):
    raw=match.group(1); path=raw[1:-1]
    tail=source[match.end():match.end()+420]
    mm=re.search(r'''method:\s*["']([A-Z]+)''', tail)
    method=mm.group(1) if mm else "GET"
    calls.append((method,path,source.count("\n",0,match.start())+1))
failures=[]
for method,path,line in calls:
    front=normalize_frontend(path)
    ok=any(meth==method and route_matches(front,back) for meth,back in full_routes)
    if not ok:
        stable=front.rstrip("/")
        ok=any(meth==method and back.startswith(stable+"/{") for meth,back in full_routes)
    if not ok and path == "/api/messenger/draft/":
        ok = all(any(meth == method and back == "/api/messenger/draft/" + suffix for meth, back in full_routes) for suffix in ("approve", "discard"))
    if not ok and path == "/api/internal-messenger/messages/":
        ok = all(any(meth == candidate_method and back == "/api/internal-messenger/messages/{message_id}/pin" for meth, back in full_routes) for candidate_method in ("POST", "DELETE"))
    if not ok: failures.append(f"missing_route line={line} method={method} path={path}")
print(f"MESSENGER_ROUTE_CALLS={len(calls)}")
print(f"MESSENGER_ROUTE_AUDIT_FAIL={len(failures)}")
for failure in failures: print("FAIL", failure)
if failures: sys.exit(1)
print("FRONTEND_API_WITHOUT_BACKEND_ROUTE=0")
print("ROUTE_AUDIT=PASS")
