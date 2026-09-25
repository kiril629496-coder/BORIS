#!/usr/bin/env python3
import os,json,time,hashlib,hmac,secrets,subprocess,urllib.request,urllib.error,pwd,signal,shutil
from pathlib import Path
ROOT=Path('/root/BORIS/boris_ops'); CAND=ROOT/'releases/agent-1.4.3-candidate.py'; OUT=ROOT/'CANDIDATE_1_4_3_SAFETY_MODES_QA.json'; sha=hashlib.sha256(CAND.read_bytes()).hexdigest(); u=pwd.getpwnam('borisops')
def spawn(port,extra):
    base=Path(f'/var/lib/boris-ops/candidate143-safety-{port}'); shutil.rmtree(base,ignore_errors=True); base.mkdir(parents=True); os.chown(base,u.pw_uid,u.pw_gid); os.chmod(base,0o700)
    token=secrets.token_urlsafe(32); secret=secrets.token_hex(48); env=os.environ.copy(); env.update({'BORIS_OPS_BIND':'127.0.0.1','BORIS_OPS_PORT':str(port),'BORIS_OPS_TOKEN':token,'BORIS_OPS_HMAC_SECRET':secret,'BORIS_OPS_AUDIT':str(base/'audit.jsonl'),'BORIS_OPS_NONCE_FILE':str(base/'nonces.json'),'BORIS_OPS_JOB_DIR':str(base/'jobs'),'BORIS_OPS_SCRIPT_DIR':str(base/'scripts'),'BORIS_OPS_TRASH_DIR':str(base/'trash'),'BORIS_OPS_READ_ROOTS':'/root/BORIS,/var/www,/etc/nginx,/etc/systemd/system,/var/log','BORIS_OPS_WRITE_ROOTS':'/root/BORIS','BORIS_OPS_ALLOW_DANGEROUS_SCRIPTS':'0','BORIS_OPS_DISK_WRITE_BLOCK_PERCENT':'92'}); env.update(extra)
    def demote(): os.setgroups([]); os.setgid(u.pw_gid); os.setuid(u.pw_uid)
    proc=subprocess.Popen(['python3',str(CAND)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True,env=env,preexec_fn=demote)
    for _ in range(100):
        if proc.poll() is not None: break
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=.5) as r:
                if r.status==200:return proc,base,token,secret
        except Exception: time.sleep(.1)
    out,err=proc.communicate(timeout=2); raise RuntimeError('start_failed:'+err[-1000:])
def stop(proc,base):
    if proc.poll() is None:
        try: os.killpg(proc.pid,signal.SIGTERM)
        except Exception: proc.terminate()
        try: proc.wait(timeout=5)
        except Exception: proc.kill()
    shutil.rmtree(base,ignore_errors=True)
def headers(secret,token,body=b''):
    ts=str(int(time.time())); nonce=secrets.token_hex(16); sig=hmac.new(secret.encode(),(ts+'\n'+nonce+'\n').encode()+body,hashlib.sha256).hexdigest(); return {'Authorization':'Bearer '+token,'X-Boris-Timestamp':ts,'X-Boris-Nonce':nonce,'X-Boris-Signature':sig,'Content-Type':'application/json','Accept':'application/json'}
def get(port,path,token,secret):
    req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,headers=headers(secret,token),method='GET')
    try:
        with urllib.request.urlopen(req,timeout=5) as r:return r.status,json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:return e.code,json.loads(e.read())
        except Exception:return e.code,{}
def rpc(port,token,secret,name,args):
    obj={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args}}; body=json.dumps(obj,separators=(',',':')).encode(); req=urllib.request.Request(f'http://127.0.0.1:{port}/mcp',data=body,headers=headers(secret,token,body),method='POST')
    try:
        with urllib.request.urlopen(req,timeout=5) as r:d=json.loads(r.read()); code=r.status
    except urllib.error.HTTPError as e:
        code=e.code
        try:d=json.loads(e.read())
        except Exception:d={}
    txt=((d.get('result') or {}).get('content') or [{}])[0].get('text','{}') if isinstance(d,dict) else '{}'
    try:x=json.loads(txt)
    except Exception:x=d
    return code,x
checks={}; details={}
# Rate limiter: 5-token burst and almost no refill; authenticated /version must be throttled.
proc=base=token=secret=None
try:
    proc,base,token,secret=spawn(8887,{'BORIS_OPS_RATE_LIMIT_PER_MIN':'1','BORIS_OPS_RATE_LIMIT_BURST':'5'})
    codes=[]
    for _ in range(8): codes.append(get(8887,'/version',token,secret)[0])
    checks['agent_rate_limit']=codes[:5].count(200)>=4 and 429 in codes[5:]
    details['rate_codes']=codes
finally:
    if proc is not None: stop(proc,base)
# Fail-safe read-only mode: reads still work, writes are denied by central write_guard.
proc=base=token=secret=None
try:
    proc,base,token,secret=spawn(8888,{'BORIS_OPS_READ_ONLY':'1','BORIS_OPS_RATE_LIMIT_BURST':'50'})
    c,w=rpc(8888,token,secret,'file_write',{'path':'/root/BORIS/boris_ops/.readonly-qa-should-not-exist','content':'x'}); checks['read_only_denies_write']=c==200 and w.get('ok') is False and w.get('error')=='read_only_mode'
    c,r=rpc(8888,token,secret,'file_read',{'path':'/root/BORIS/boris_ops/README.md'}); checks['read_only_allows_read']=c==200 and r.get('ok') is True
    c,e=rpc(8888,token,secret,'exec',{'command':'uptime'}); checks['read_only_allows_safe_exec']=c==200 and e.get('ok') is True
    c,d=rpc(8888,token,secret,'host_diagnostics',{}); runtime=(d.get('runtime') or {}) if isinstance(d,dict) else {}; checks['read_only_diagnostic']=c==200 and d.get('ok') is True and runtime.get('read_only') is True and runtime.get('write_guard')=='read_only_mode'; details['read_only_runtime']=runtime
    checks['read_only_no_file_created']=not Path('/root/BORIS/boris_ops/.readonly-qa-should-not-exist').exists()
finally:
    if proc is not None: stop(proc,base)
checks['sha_stable']=hashlib.sha256(CAND.read_bytes()).hexdigest()==sha
ok=all(checks.values()); report={'contract':'BORIS_OPS_1_4_3_SAFETY_MODES_QA','ok':ok,'candidate_sha256':sha,'checks':checks,'details':details,'ts':time.time()}; tmp=OUT.with_suffix('.tmp'); tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n'); os.replace(tmp,OUT); print(json.dumps(report,ensure_ascii=False)); raise SystemExit(0 if ok else 1)
