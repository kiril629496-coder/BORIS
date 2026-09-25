#!/usr/bin/env python3
import hashlib,hmac,json,secrets,time,urllib.request,urllib.error
from pathlib import Path
BASE='https://boris-ai.pro/_ops'
ENV={}
for line in Path('/etc/boris-ops/agent.env').read_text().splitlines():
    if line.strip() and not line.lstrip().startswith('#') and '=' in line:
        k,v=line.split('=',1); ENV[k]=v.strip().strip('"').strip("'")
TOKEN=ENV['BORIS_OPS_TOKEN']; SECRET=ENV['BORIS_OPS_HMAC_SECRET']
def req(path, method='GET', obj=None, nonce=None):
    body=b'' if obj is None else json.dumps(obj,separators=(',',':')).encode()
    ts=str(int(time.time())); nonce=nonce or secrets.token_hex(12)
    sig=hmac.new(SECRET.encode(),(ts+'\n'+nonce+'\n').encode()+body,hashlib.sha256).hexdigest()
    r=urllib.request.Request(BASE+path,data=(body if method=='POST' else None),method=method,headers={'Authorization':'Bearer '+TOKEN,'X-Boris-Timestamp':ts,'X-Boris-Nonce':nonce,'X-Boris-Signature':sig,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(r,timeout=15) as x: return x.status,json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code,json.loads(e.read())
checks=[]
checks.append(('version',req('/version')))
checks.append(('search',req('/file/search?root=/root/BORIS/boris_ops&q=VERSION')))
checks.append(('dangerous_exec',req('/exec','POST',{'command':'rm -rf /'})))
checks.append(('uptime_exec',req('/exec','POST',{'command':'uptime'})))
# replay test uses identical signed request created manually
body=b''; ts=str(int(time.time())); nonce=secrets.token_hex(12); sig=hmac.new(SECRET.encode(),(ts+'\n'+nonce+'\n').encode()+body,hashlib.sha256).hexdigest(); hdr={'Authorization':'Bearer '+TOKEN,'X-Boris-Timestamp':ts,'X-Boris-Nonce':nonce,'X-Boris-Signature':sig}
r=urllib.request.Request(BASE+'/version',headers=hdr)
with urllib.request.urlopen(r,timeout=10) as x: first=x.status
try:
    urllib.request.urlopen(r,timeout=10); second=200
except urllib.error.HTTPError as e: second=e.code
checks.append(('replay',(first,second)))
print(json.dumps(checks,ensure_ascii=False,indent=2))
assert checks[0][1][0]==200
assert checks[1][1][0]==200
assert checks[2][1][0] in (400,403)
assert checks[3][1][0]==200
assert checks[4][1]==(200,401)
print('BORIS_OPS_TEST=PASS')
