#!/usr/bin/env python3
import os,json,time,hashlib,hmac,secrets,subprocess,urllib.request,urllib.error,pwd,signal,shutil,base64
from pathlib import Path
ROOT=Path('/root/BORIS/boris_ops'); CAND=ROOT/'releases/agent-1.4.3-candidate.py'; OUT=ROOT/'CANDIDATE_1_4_3_PREFLIGHT.json'; BOUNDED=ROOT/'CANDIDATE_1_4_3_BOUNDED_QA.json'; BASE=Path('/var/lib/boris-ops/candidate143-preflight'); PORT=8886
sha=hashlib.sha256(CAND.read_bytes()).hexdigest(); token=secrets.token_urlsafe(32); secret=secrets.token_hex(48)
shutil.rmtree(BASE,ignore_errors=True); BASE.mkdir(parents=True)
u=pwd.getpwnam('borisops'); os.chown(BASE,u.pw_uid,u.pw_gid); os.chmod(BASE,0o700)
env=os.environ.copy(); env.update({'BORIS_OPS_BIND':'127.0.0.1','BORIS_OPS_PORT':str(PORT),'BORIS_OPS_TOKEN':token,'BORIS_OPS_HMAC_SECRET':secret,'BORIS_OPS_AUDIT':str(BASE/'audit.jsonl'),'BORIS_OPS_NONCE_FILE':str(BASE/'nonces.json'),'BORIS_OPS_JOB_DIR':str(BASE/'jobs'),'BORIS_OPS_SCRIPT_DIR':str(BASE/'scripts'),'BORIS_OPS_TRASH_DIR':str(BASE/'trash'),'BORIS_OPS_READ_ROOTS':'/root/BORIS,/var/www,/etc/nginx,/etc/systemd/system,/var/log','BORIS_OPS_WRITE_ROOTS':'/root/BORIS','BORIS_OPS_ALLOW_DANGEROUS_SCRIPTS':'0','BORIS_OPS_DISK_WRITE_BLOCK_PERCENT':'92','BORIS_OPS_MAX_CONCURRENT_JOBS':'2','BORIS_OPS_SEARCH_MAX_FILES':'500','BORIS_OPS_SEARCH_MAX_SECONDS':'2','BORIS_OPS_RATE_LIMIT_PER_MIN':'6000','BORIS_OPS_RATE_LIMIT_BURST':'200'})
def demote(): os.setgroups([]); os.setgid(u.pw_gid); os.setuid(u.pw_uid)
proc=subprocess.Popen(['python3',str(CAND)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True,env=env,preexec_fn=demote)
def sign(body):
 ts=str(int(time.time())); nonce=secrets.token_hex(16); sig=hmac.new(secret.encode(),(ts+'\n'+nonce+'\n').encode()+body,hashlib.sha256).hexdigest(); return {'Authorization':'Bearer '+token,'X-Boris-Timestamp':ts,'X-Boris-Nonce':nonce,'X-Boris-Signature':sig,'Content-Type':'application/json','Accept':'application/json, text/event-stream'}
def rpc(method,params=None):
 body=json.dumps({'jsonrpc':'2.0','id':secrets.randbelow(999999),'method':method,'params':params or {}},separators=(',',':')).encode(); r=urllib.request.Request(f'http://127.0.0.1:{PORT}/mcp',data=body,method='POST',headers=sign(body));
 with urllib.request.urlopen(r,timeout=10) as x:return x.status,json.loads(x.read())
def tool(name,args=None):
 c,d=rpc('tools/call',{'name':name,'arguments':args or {}}); txt=((d.get('result') or {}).get('content') or [{}])[0].get('text','{}'); return c,json.loads(txt),bool((d.get('result') or {}).get('isError'))
def get_job(jid): return tool('job_get',{'job_id':jid})[1]
def wait_ready():
 for _ in range(100):
  if proc.poll() is not None: return False
  try:
   with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/health',timeout=.5) as r:
    if r.status==200:return True
  except Exception: time.sleep(.1)
 return False
checks={}; details={}
try:
 checks['start']=wait_ready()
 if not checks['start']:
  out,err=proc.communicate(timeout=2); raise RuntimeError('candidate_start_failed:'+err[-1000:])
 c,d=rpc('initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'full-preflight','version':'1'}}); checks['initialize']=c==200 and (d.get('result') or {}).get('protocolVersion')=='2025-06-18'
 c,d=rpc('ping',{}); checks['ping']=c==200 and d.get('result')=={}
 c,d=rpc('tools/list',{}); names=[x.get('name') for x in (d.get('result') or {}).get('tools',[])]; required={'file_download','file_trash','process_list','http_check','postgres_check','nginx_validate','runtime_capabilities','job_cancel','file_upload','file_restore','boris_action'}; checks['toolset']=c==200 and required.issubset(names) and len(names)>=34; details['tools']=names
 # MCP download + sha
 target=ROOT/'README.md'; c,x,e=tool('file_download',{'path':str(target)}); raw=base64.b64decode(x.get('content_base64','')) if x.get('content_base64') else b''; checks['file_download']=c==200 and not e and x.get('ok') is True and raw==target.read_bytes() and x.get('sha256')==hashlib.sha256(raw).hexdigest()
 c,x,e=tool('file_trash',{'limit':5}); checks['file_trash']=c==200 and not e and x.get('ok') is True and isinstance(x.get('items'),list) and len(x.get('items'))<=5
 # read-only ops
 for name,args in [('process_list',{'limit':5}),('http_check',{'target':'public'}),('postgres_check',{}),('nginx_validate',{}),('runtime_capabilities',{})]:
  c,x,e=tool(name,args); checks[name]=c==200 and not e and x.get('ok') is True
 c,x,e=tool('file_search',{'root':'/root/BORIS/boris_ops','q':'BORIS','limit':10,'max_files':100,'max_seconds':1}); checks['bounded_search']=c==200 and not e and x.get('ok') is True and x.get('count',999)<=10 and x.get('scanned_files',999999)<=100 and float(x.get('duration_s',99))<=2.5; details['search']=x
 # concurrency: 5 jobs, max 2 running; then cancel one queued
 ids=[]
 for _ in range(5):
  c,x,e=tool('exec',{'command':'sleep 3','background':True,'timeout':10});
  if x.get('job_id'): ids.append(x['job_id'])
 time.sleep(.5); states={j:get_job(j).get('status') for j in ids}; run=sum(v=='running' for v in states.values()); queued=[j for j,v in states.items() if v=='queued']; checks['job_concurrency']=len(ids)==5 and run<=2 and len(queued)>=1; details['job_states_initial']=states
 qid=queued[0] if queued else None
 if qid:
  c,x,e=tool('job_cancel',{'job_id':qid}); time.sleep(.3); checks['queued_cancel_sticky']=x.get('ok') is True and get_job(qid).get('status')=='cancelled'
 else: checks['queued_cancel_sticky']=False
 # dedicated timeout job after concurrency drains enough
 deadline=time.time()+8
 while time.time()<deadline:
  if sum(get_job(j).get('status') in {'queued','running'} for j in ids)<=0: break
  time.sleep(.25)
 c,x,e=tool('exec',{'command':'sleep 3','background':True,'timeout':1}); tid=x.get('job_id'); time.sleep(1.8); tj=get_job(tid) if tid else {}; checks['timeout_state']=tj.get('status')=='timeout' and tj.get('returncode')==124 and tj.get('error')=='timeout'; details['timeout']=tj
 # dangerous script remains off
 c,x,e=tool('script_run',{'content':'echo no','interpreter':'bash'}); checks['dangerous_script_default_off']=x.get('ok') is False and x.get('error')=='script_run_disabled'
 # generic systemctl denied
 c,x,e=tool('exec',{'command':'systemctl status nginx.service'}); checks['generic_systemctl_denied']=x.get('ok') is False
 checks['sha_stable']=hashlib.sha256(CAND.read_bytes()).hexdigest()==sha
 ok=all(checks.values())
 report={'contract':'BORIS_OPS_1_4_3_FULL_PREFLIGHT','ok':ok,'candidate_sha256':sha,'tools':len(names),'checks':checks,'details':details,'ts':time.time()}
 tmp=OUT.with_suffix('.tmp'); tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n'); os.replace(tmp,OUT)
 bounded={'contract':'BORIS_OPS_1_4_3_BOUNDED_RESOURCES_QA','ok':ok and all(checks[k] for k in ['bounded_search','job_concurrency','queued_cancel_sticky','timeout_state','sha_stable']),'candidate_sha256':sha,'tools':len(names),'checks':{k:checks[k] for k in ['bounded_search','job_concurrency','queued_cancel_sticky','timeout_state','sha_stable']},'bounded_search':details.get('search'),'job_states_initial':details.get('job_states_initial'),'timeout_status':(details.get('timeout') or {}).get('status'),'ts':time.time()}
 tmp2=BOUNDED.with_suffix('.tmp'); tmp2.write_text(json.dumps(bounded,ensure_ascii=False,indent=2)+'\n'); os.replace(tmp2,BOUNDED)
 print(json.dumps({'ok':ok,'sha256':sha,'tools':len(names),'checks':checks},ensure_ascii=False)); raise SystemExit(0 if ok else 1)
finally:
 if proc.poll() is None:
  try: os.killpg(proc.pid,signal.SIGTERM)
  except Exception: proc.terminate()
  try: proc.wait(timeout=5)
  except Exception: proc.kill()
 shutil.rmtree(BASE,ignore_errors=True)
