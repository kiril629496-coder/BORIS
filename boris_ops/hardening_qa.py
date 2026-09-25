#!/usr/bin/env python3
import base64,hashlib,hmac,json,secrets,time,urllib.parse,urllib.request,urllib.error
from pathlib import Path
BASE='https://boris-ai.pro/_ops'
ENV={}
for line in Path('/etc/boris-ops/agent.env').read_text().splitlines():
    if line.strip() and '=' in line and not line.lstrip().startswith('#'):
        k,v=line.split('=',1); ENV[k]=v.strip().strip('"').strip("'")
TOKEN=ENV['BORIS_OPS_TOKEN']; SECRET=ENV['BORIS_OPS_HMAC_SECRET']
def req(path,method='GET',obj=None,raw_body=None,headers_extra=None):
    body=(raw_body if raw_body is not None else (b'' if obj is None else json.dumps(obj,separators=(',',':')).encode()))
    # HARDENING_RATE_AWARE_V1: QA itself must respect the agent's production
    # token bucket. Parallel witnesses may consume burst capacity, so retry
    # only explicit 429 responses with a fresh nonce/signature and bounded backoff.
    last=(0,{'error':'no_attempt'})
    for attempt in range(1,7):
        ts=str(int(time.time())); nonce=secrets.token_hex(16); sig=hmac.new(SECRET.encode(),(ts+'\n'+nonce+'\n').encode()+body,hashlib.sha256).hexdigest()
        h={'Authorization':'Bearer '+TOKEN,'X-Boris-Timestamp':ts,'X-Boris-Nonce':nonce,'X-Boris-Signature':sig,'Content-Type':'application/json'}
        if headers_extra:h.update(headers_extra)
        r=urllib.request.Request(BASE+path,data=(body if method=='POST' else None),method=method,headers=h)
        try:
            with urllib.request.urlopen(r,timeout=15) as x:return x.status,json.loads(x.read())
        except urllib.error.HTTPError as e:
            try: last=(e.code,json.loads(e.read()))
            except: last=(e.code,{'error':'http'})
            if e.code==429 and attempt<6:
                time.sleep(min(2.5,0.55*attempt)); continue
            return last
    return last
checks={}
# Public and auth boundaries.
with urllib.request.urlopen(BASE+'/health',timeout=10) as r: checks['public_health']=r.status==200
try:
    urllib.request.urlopen(BASE+'/version',timeout=10); checks['unauthorized_denied']=False
except urllib.error.HTTPError as e: checks['unauthorized_denied']=e.code==401
c,v=req('/version'); checks['version']=c==200 and v.get('ok') and v.get('dangerous_scripts_enabled') is False
c,d=req('/host/diagnostics'); checks['diagnostics']=c==200 and d.get('ok') and 'disk' in d and 'services' in d
c,a=req('/audit/recent?limit=3'); checks['audit_recent']=c==200 and a.get('ok') and a.get('count',0)<=3
# Dangerous and arbitrary commands blocked.
c,e=req('/exec','POST',{'command':'rm -rf /'}); checks['dangerous_exec_denied']=c>=400 and e.get('ok') is False
c,e=req('/exec','POST',{'command':'python3 -c "print(1)"'}); checks['arbitrary_exec_denied']=c>=400 and e.get('ok') is False
# Service allowlist and action policy.
c,e=req('/service','POST',{'service':'ssh.service','action':'restart'}); checks['service_deny']=c in (400,403) and e.get('ok') is False
# Read traversal/write roots.
c,e=req('/file/read?path='+urllib.parse.quote('/etc/shadow',safe='')); checks['read_root_deny']=c==403
c,e=req('/file/write','POST',{'path':'/etc/nginx/bad.conf','content':'x'}); checks['write_root_deny']=c==403
# Optimistic patch + stale hash denial on controlled smoke file.
fp='/root/BORIS/boris_ops/.hardening-smoke.txt'
Path(fp).write_text('alpha\n')
sha=hashlib.sha256(Path(fp).read_bytes()).hexdigest()
c,e=req('/file/patch','POST',{'path':fp,'old':'alpha','new':'beta','expected_sha256':sha,'count':1}); checks['patch_ok']=c==200 and e.get('ok')
c,e=req('/file/patch','POST',{'path':fp,'old':'beta','new':'gamma','expected_sha256':sha,'count':1}); checks['stale_patch_deny']=c==409 and e.get('error')=='sha256_mismatch'
# Backup names must be collision-safe even for multiple writes in the same second.
bfp='/root/BORIS/boris_ops/.backup-uniqueness-smoke.txt'
Path(bfp).write_text('v0\n')
c,b1=req('/file/write','POST',{'path':bfp,'content':'v1\n'}); p1=b1.get('backup')
c2,b2=req('/file/write','POST',{'path':bfp,'content':'v2\n'}); p2=b2.get('backup')
checks['backup_unique_same_second']=c==200 and c2==200 and p1 and p2 and p1!=p2 and Path(p1).exists() and Path(p2).exists() and Path(p1).read_text()=='v0\n' and Path(p2).read_text()=='v1\n'
for _bp in [p1,p2]:
    try:
        if _bp: Path(_bp).unlink()
    except: pass
try: Path(bfp).unlink()
except: pass
# Upload hash mismatch and success.
raw=b'upload-smoke\n'; good=hashlib.sha256(raw).hexdigest()
c,e=req('/file/upload','POST',{'path':'/root/BORIS/boris_ops/.upload-smoke.txt','content_base64':base64.b64encode(raw).decode(),'expected_sha256':'0'*64}); checks['upload_hash_deny']=c==409
c,e=req('/file/upload','POST',{'path':'/root/BORIS/boris_ops/.upload-smoke.txt','content_base64':base64.b64encode(raw).decode(),'expected_sha256':good}); checks['upload_ok']=c==200 and e.get('sha256')==good
# Soft delete + restore.
c,e=req('/file/delete','POST',{'path':'/root/BORIS/boris_ops/.upload-smoke.txt'}); tid=e.get('trash_id'); checks['soft_delete']=c==200 and bool(tid)
c,e=req('/file/restore','POST',{'trash_id':tid}); checks['restore']=c==200 and e.get('ok')
# MCP contract and unknown tool error.
c,m=req('/mcp','POST',{'jsonrpc':'2.0','id':1,'method':'tools/list','params':{}}); names=[x.get('name') for x in ((m.get('result') or {}).get('tools') or [])]
checks['mcp_tools']=c==200 and len(names)>=27 and 'host_diagnostics' in names and 'audit_recent' in names
c,m=req('/mcp','POST',{'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'definitely_unknown','arguments':{}}}); checks['mcp_unknown']=c==200 and (m.get('result') or {}).get('isError') is True

# Least-privilege persistent-state permissions: directories private to owner/group; state files not world-readable.
import stat as _stat
_state_dirs=[Path('/var/lib/boris-ops/jobs'),Path('/var/lib/boris-ops/trash'),Path('/var/log/boris-ops')]
checks['state_dirs_private']=all((not d.exists()) or (_stat.S_IMODE(d.stat().st_mode) & 0o007)==0 for d in _state_dirs)
_state_files=list(Path('/var/lib/boris-ops/jobs').glob('job_*.json')) + list(Path('/var/lib/boris-ops/trash').glob('trash_*.json')) + [Path('/var/log/boris-ops/audit.jsonl'), Path('/var/lib/boris-ops/nonces.json')]
checks['state_files_private']=all((not f.exists()) or (_stat.S_IMODE(f.stat().st_mode) & 0o007)==0 for f in _state_files)

# Long-certification policy must never classify a real public BORIS 5xx as an
# effective PASS. Acceptance also needs enough budget for 60 strict samples.
_lq=Path('/root/BORIS/boris_ops/longqa_tick.py').read_text(errors='ignore')
_acc=Path('/root/BORIS/boris_ops/acceptance_orchestrator.py').read_text(errors='ignore')
_unit=Path('/etc/systemd/system/boris-ops-final-acceptance.service').read_text(errors='ignore')
checks['strict_public_5xx_cert_policy']="POLICY='strict-public-v3-autoretry'" in _lq and "row['dependency_ok']=bs==200 and b.get('ok') is True" in _lq and "effective_ok=bool(row.get('control_plane_ok')) and bool(row.get('dependency_ok'))" in _lq
checks['strict_cert_acceptance_budget']='range(0,76)' in _acc and 'TimeoutStartSec=4800' in _unit

# Cleanup local smoke files (these are direct QA files only).
for x in [Path(fp),Path('/root/BORIS/boris_ops/.upload-smoke.txt')]:
    try:x.unlink()
    except:pass
out={'contract':'BORIS_OPS_HARDENING_QA','ok':all(checks.values()),'passed':sum(checks.values()),'total':len(checks),'checks':checks,'failed':[k for k,v in checks.items() if not v]}
Path('/root/BORIS/boris_ops/HARDENING_QA.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
print(json.dumps(out,ensure_ascii=False))
raise SystemExit(0 if out['ok'] else 1)
