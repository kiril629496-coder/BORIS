#!/usr/bin/env python3
import subprocess,time,os,json,hashlib,hmac,secrets,urllib.request,urllib.error,shutil,pwd,signal
from pathlib import Path
ROOT=Path('/root/BORIS/boris_ops'); CAND=ROOT/'releases/agent-1.4.3-candidate.py'; OUT=ROOT/'CANDIDATE_1_4_3_SOAK.json'; LOG=ROOT/'candidate-1.4.3-soak.jsonl'; BASE=Path('/var/lib/boris-ops/candidate143-soak'); PORT=8880
TARGET_SAMPLES=60; INTERVAL=45
start=time.time(); LOG.write_text('',encoding='utf-8'); shutil.rmtree(BASE,ignore_errors=True); BASE.mkdir(parents=True)
u=pwd.getpwnam('borisops'); os.chown(BASE,u.pw_uid,u.pw_gid); os.chmod(BASE,0o700)
sha=hashlib.sha256(CAND.read_bytes()).hexdigest(); token=secrets.token_urlsafe(48); secret=secrets.token_hex(64)
env={'BORIS_OPS_BIND':'127.0.0.1','BORIS_OPS_PORT':str(PORT),'BORIS_OPS_TOKEN':token,'BORIS_OPS_HMAC_SECRET':secret,'BORIS_OPS_AUDIT':str(BASE/'audit.jsonl'),'BORIS_OPS_NONCE_FILE':str(BASE/'nonces.json'),'BORIS_OPS_JOB_DIR':str(BASE/'jobs'),'BORIS_OPS_SCRIPT_DIR':str(BASE/'scripts'),'BORIS_OPS_TRASH_DIR':str(BASE/'trash'),'BORIS_OPS_READ_ROOTS':'/root/BORIS,/var/www,/etc/nginx,/etc/systemd/system,/var/log','BORIS_OPS_WRITE_ROOTS':'/root/BORIS','BORIS_OPS_ALLOW_DANGEROUS_SCRIPTS':'0','BORIS_OPS_DISK_WRITE_BLOCK_PERCENT':'92'}
child_env=os.environ.copy(); child_env.update(env)
def demote():
    os.setgroups([]); os.setgid(u.pw_gid); os.setuid(u.pw_uid)
proc=subprocess.Popen(['python3',str(CAND)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True,env=child_env,preexec_fn=demote)
def rpc(obj):
 body=json.dumps(obj,separators=(',',':')).encode(); ts=str(int(time.time())); nonce=secrets.token_hex(16); sig=hmac.new(secret.encode(),(ts+'\n'+nonce+'\n').encode()+body,hashlib.sha256).hexdigest(); req=urllib.request.Request(f'http://127.0.0.1:{PORT}/mcp',data=body,method='POST',headers={'Authorization':'Bearer '+token,'X-Boris-Timestamp':ts,'X-Boris-Nonce':nonce,'X-Boris-Signature':sig,'Content-Type':'application/json','Accept':'application/json, text/event-stream'});
 with urllib.request.urlopen(req,timeout=10) as r:return r.status,json.loads(r.read())
def tool(name,args=None):
 c,d=rpc({'jsonrpc':'2.0','id':secrets.randbelow(999999),'method':'tools/call','params':{'name':name,'arguments':args or {}}}); txt=((d.get('result') or {}).get('content') or [{}])[0].get('text','{}'); return c,json.loads(txt)
def write_result(obj):
 tmp=OUT.with_suffix('.tmp'); tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); os.replace(tmp,OUT)
fail=[]; samples=0
try:
 for _ in range(100):
  if proc.poll() is not None: break
  try:
   with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/health',timeout=1) as r:
    if r.status==200: break
  except Exception: time.sleep(.1)
 else: raise RuntimeError('candidate_start_timeout')
 if proc.poll() is not None:
  out,err=proc.communicate(timeout=2); raise RuntimeError('candidate_start_failed:'+err[-1000:])
 for i in range(TARGET_SAMPLES):
  row={'sample':i+1,'ts':time.time(),'candidate_sha256':hashlib.sha256(CAND.read_bytes()).hexdigest()}
  try:
   with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/health',timeout=5) as r: h=json.loads(r.read()); row['health']=r.status==200 and h.get('version')=='1.4.3'
   c,d=rpc({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'candidate-soak','version':'1'}}}); row['initialize']=c==200 and (d.get('result') or {}).get('protocolVersion')=='2025-06-18'
   c,d=rpc({'jsonrpc':'2.0','id':2,'method':'ping','params':{}}); row['ping']=c==200 and d.get('result')=={}
   c,d=rpc({'jsonrpc':'2.0','id':3,'method':'tools/list','params':{}}); row['tools']=len((d.get('result') or {}).get('tools') or []); row['tools_ok']=c==200 and row['tools']>=34
   for name,args in [('process_list',{'limit':5}),('http_check',{'target':'public'}),('postgres_check',{}),('nginx_validate',{}),('runtime_capabilities',{}),('file_trash',{'limit':3})]:
    c,x=tool(name,args); row[name]=c==200 and x.get('ok') is True
   c,x=tool('file_download',{'path':'/root/BORIS/boris_ops/README.md'}); row['file_download']=c==200 and x.get('ok') is True and bool(x.get('sha256')) and bool(x.get('content_base64'))
   row['sha_stable']=row['candidate_sha256']==sha
   row['child_alive']=proc.poll() is None
   row['ok']=all(row.get(k) is True for k in ['health','initialize','ping','tools_ok','process_list','http_check','postgres_check','nginx_validate','runtime_capabilities','file_trash','file_download','sha_stable','child_alive'])
  except Exception as e: row['ok']=False; row['error']=repr(e)
  if not row['ok']: fail.append(row)
  with LOG.open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
  samples+=1
  # Write rolling state so guardian/operator can observe progress.
  write_result({'contract':'BORIS_OPS_CANDIDATE_1_4_3_SOAK','ok':None if i<TARGET_SAMPLES-1 else not fail,'complete':i==TARGET_SAMPLES-1,'candidate_sha256':sha,'samples':samples,'target_samples':TARGET_SAMPLES,'failures':len(fail),'started_at':start,'last_sample_at':time.time(),'failure_rows':fail[-10:]})
  if i<TARGET_SAMPLES-1: time.sleep(INTERVAL)
 final_sha=hashlib.sha256(CAND.read_bytes()).hexdigest(); ok=not fail and final_sha==sha and proc.poll() is None
 result={'contract':'BORIS_OPS_CANDIDATE_1_4_3_SOAK','ok':ok,'complete':True,'candidate_sha256':sha,'final_candidate_sha256':final_sha,'samples':samples,'target_samples':TARGET_SAMPLES,'failures':len(fail),'duration_s':round(time.time()-start,1),'started_at':start,'finished_at':time.time(),'failure_rows':fail[-20:]}
 write_result(result); print(json.dumps({'ok':ok,'contract':result['contract'],'samples':samples,'failures':len(fail),'duration_s':result['duration_s'],'sha256':sha},ensure_ascii=False)); raise SystemExit(0 if ok else 1)
finally:
 if proc.poll() is None:
  try: os.killpg(proc.pid,signal.SIGTERM)
  except Exception: proc.terminate()
  try: proc.wait(timeout=5)
  except Exception: proc.kill()
 shutil.rmtree(BASE,ignore_errors=True)
