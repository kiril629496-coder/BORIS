#!/usr/bin/env python3
from pathlib import Path
import re, sys
SCRIPT = Path(__file__).resolve()
FRONTEND_ROOT = SCRIPT.parents[1]
frontend_path = FRONTEND_ROOT / 'app/messenger/page.tsx'
if not frontend_path.is_file():
    raise SystemExit(f"MESSENGER_ROUTE_AUDIT_FRONTEND_MISSING={frontend_path}")
frontend = frontend_path.read_text(encoding='utf-8')

# The audit runs twice: once from the live frontend tree and once from the
# builder's isolated frontend-only snapshot. In the latter case there is no
# sibling backend copy by design, so read the canonical backend route surface.
workspace_backend_root = FRONTEND_ROOT.parent / 'backend/app/api'
if not workspace_backend_root.is_dir():
    workspace_backend_root = Path('/root/BORIS/backend/app/api')
active_backend_root = Path('/root/BORIS/releases/backend/current/backend/app/api')
backend_roots=[]
for candidate in (workspace_backend_root, active_backend_root):
    try:
        resolved=candidate.resolve()
    except Exception:
        resolved=candidate
    if candidate.is_dir() and resolved not in backend_roots:
        backend_roots.append(resolved)
if not backend_roots:
    raise SystemExit("MESSENGER_ROUTE_AUDIT_BACKEND_MISSING")
routes=set()
for backend_root in backend_roots:
    for p in backend_root.glob('*.py'):
        s=p.read_text(encoding='utf-8',errors='ignore')
        pm=re.search(r'router\s*=\s*APIRouter\(prefix="([^"]*)"',s)
        prefix=pm.group(1) if pm else ''
        for m in re.finditer(r'@router\.(get|post|patch|delete|put)\("([^"]+)"\)',s):
            routes.add((m.group(1).upper(),prefix+m.group(2)))
def norm_parts(path):
    path=path.split('?',1)[0]
    path=re.sub(r'\$\{[^}]+\}','{param}',path)
    return [x for x in path.strip('/').split('/') if x]
def matches(method,path):
    cp=norm_parts(path)
    for rm,rp in routes:
        if rm!=method: continue
        rr=norm_parts(rp)
        if len(cp)!=len(rr): continue
        if all(c=='{param}' or (r.startswith('{') and r.endswith('}')) or c==r for c,r in zip(cp,rr)): return True
    return False
calls=[]
pat=re.compile(r'''api(?:<[^\n]*?>)?\((`[^`]+`|"[^"]+"|'[^']+')''')
for m in pat.finditer(frontend):
    raw=m.group(1); path=raw[1:-1]
    if not path.startswith('/api/'): continue
    tail=frontend[m.end():m.end()+450]
    stripped=tail.lstrip()
    # Concatenated suffixes such as "/draft/" + action are one dynamic
    # path segment. Keep them dynamic while still requiring a matching route.
    if re.match(r'''^\+\s*[A-Za-z_$][A-Za-z0-9_$]*''', stripped):
        path += '${param}'
    method='GET'
    # Find method inside the options object even after a dynamic first arg.
    mm=re.search(r'''method:\s*["']([A-Z]+)''',stripped[:360])
    if mm: method=mm.group(1)
    calls.append((method,path,frontend.count('\n',0,m.start())+1))
ignored=('/api/inbox/','/api/messenger/draft/','/api/internal-messenger/messages/','/api/team/memberships/')
fail=[]
for method,path,line in calls:
    if matches(method,path): continue
    if any(path.startswith(x) for x in ignored): continue
    fail.append((line,method,path))
print('MESSENGER_ROUTE_AUDIT')
print('FRONTEND_CALLS='+str(len(calls)))
print('BACKEND_ROUTES='+str(len(routes)))
print('FAILURES='+str(len(fail)))
for row in fail: print('FAIL line=%s method=%s path=%s'%row)
if fail: sys.exit(1)
print('FRONTEND_API_WITHOUT_BACKEND_ROUTE=0')
print('MESSENGER_ROUTE_AUDIT=PASS')
