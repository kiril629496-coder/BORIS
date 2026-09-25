#!/usr/bin/env python3
import json,time,hashlib,hmac,secrets,subprocess,urllib.request,os,signal
from pathlib import Path
ROOT=Path('/root/BORIS/boris_ops'); OUT=ROOT/'INDEPENDENCE_1_4_3.json'; LOG=ROOT/'independence-1.4.3.jsonl'; TARGET='1.4.3'; UNIT='sentinelx-cloud-core.service'
env={}
for line in Path('/etc/boris-ops/agent.env').read_text().splitlines():
    if line.strip() and '=' in line and not line.lstrip().startswith('#'):
        k,v=line.split('=',1); env[k]=v.strip().strip('"').strip("'")
T=env['BORIS_OPS_TOKEN']; S=env['BORIS_OPS_HMAC_SECRET']
def atom(obj):
    tmp=OUT.with_suffix('.tmp'); tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n'); os.replace(tmp,OUT)
def http(url):
    try:
        with urllib.request.urlopen(url,timeout=8) as r:return {'ok':r.status==200,'status':r.status,'body':json.loads(r.read())}
    except Exception as e:return {'ok':False,'error':repr(e)}
def rpc(method,params=None):
    body=json.dumps({'jsonrpc':'2.0','id':secrets.randbelow(999999),'method':method,'params':params or {}},separators=(',',':')).encode(); ts=str(int(time.time())); nonce=secrets.token_hex(16); sig=hmac.new(S.encode(),(ts+'\n'+nonce+'\n').encode()+body,hashlib.sha256).hexdigest()
    req=urllib.request.Request('https://boris-ai.pro/_ops/mcp',data=body,method='POST',headers={'Authorization':'Bearer '+T,'X-Boris-Timestamp':ts,'X-Boris-Nonce':nonce,'X-Boris-Signature':sig,'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
    with urllib.request.urlopen(req,timeout=10) as r:return r.status,json.loads(r.read())
def tool(name,args=None):
    c,d=rpc('tools/call',{'name':name,'arguments':args or {}}); txt=((d.get('result') or {}).get('content') or [{}])[0].get('text','{}'); return c,json.loads(txt)
def mainpid():
    cp=subprocess.run(['systemctl','show',UNIT,'-p','MainPID','--value'],capture_output=True,text=True,timeout=5); return int((cp.stdout or '0').strip() or 0)
def cgroup_pids():
    cp=subprocess.run(['systemctl','show',UNIT,'-p','ControlGroup','--value'],capture_output=True,text=True,timeout=5)
    cg=(cp.stdout or '').strip()
    if not cg: return []
    root=Path('/sys/fs/cgroup')/cg.lstrip('/')
    out=set()
    if root.exists():
        for f in [root/'cgroup.procs', *root.rglob('cgroup.procs')]:
            try:
                out.update(int(x) for x in f.read_text().split() if x.isdigit())
            except Exception: pass
    return sorted(out)
def run():
    start=time.time(); LOG.write_text('',encoding='utf-8'); rows=[]; failures=[]; pid=mainpid(); suspended=False; pre_cgroup=cgroup_pids()
    try:
        pre=http('http://127.0.0.1:8741/health')
        if not (pre.get('ok') and (pre.get('body') or {}).get('version')==TARGET): raise RuntimeError('target_not_ready')
        final=json.loads((ROOT/'FINAL_PRODUCTION_1_4_3.json').read_text())
        current_sha=hashlib.sha256((ROOT/'agent.py').read_bytes()).hexdigest()
        if final.get('ok') is not True or final.get('version')!=TARGET or final.get('agent_sha256')!=current_sha: raise RuntimeError('final_production_identity_not_ready')
        if pid<=1: raise RuntimeError('sentinelx_mainpid_missing')
        os.kill(pid,signal.SIGSTOP); suspended=True; time.sleep(0.2)
        # SIGSTOP is sent to the exact MainPID only, never to the systemd unit/cgroup.
        # Short-lived unrelated cgroup children may naturally exit between snapshots. Treat a child
        # as preservation-required only while its /proc entry still exists; every surviving pre-stop
        # PID must remain in the cgroup. Record natural exits separately as evidence.
        immediate_cgroup=cgroup_pids()
        pre_still_alive=[q for q in pre_cgroup if Path(f'/proc/{q}').exists()]
        immediate_natural_exits=sorted(set(pre_cgroup)-set(pre_still_alive))
        preserved_at_suspend=set(pre_still_alive).issubset(set(immediate_cgroup)) and pid in immediate_cgroup
        if not preserved_at_suspend:
            raise RuntimeError('sentinelx_surviving_cgroup_members_not_preserved_at_suspend')
        for i in range(20):
            row={'sample':i+1,'ts':time.time(),'sentinelx_main_pid':pid}
            try:
                status=Path(f'/proc/{pid}/status').read_text(errors='replace') if Path(f'/proc/{pid}/status').exists() else ''
                row['sentinelx_transport_suspended']='State:\tT' in status or 'State:\tt' in status
                row['local']=http('http://127.0.0.1:8741/health'); row['external']=http('https://boris-ai.pro/_ops/health'); row['public_boris']=http('https://boris-ai.pro/health')
                c,d=rpc('initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'independence-test','version':'1'}}); row['mcp_initialize']=c==200 and (d.get('result') or {}).get('protocolVersion')=='2025-06-18'
                c,d=rpc('tools/list',{}); row['mcp_tools']=len((d.get('result') or {}).get('tools') or []); row['mcp_tools_ok']=c==200 and row['mcp_tools']>=34
                c,x=tool('exec',{'command':'uptime'}); row['safe_exec']=c==200 and x.get('ok') is True
                c,x=tool('postgres_check',{}); row['postgres_check']=c==200 and x.get('ok') is True
                row['boris_ops_service']=subprocess.run(['systemctl','is-active','boris-ops.service'],capture_output=True,text=True,timeout=5).stdout.strip()
                row['ok']=bool(row['sentinelx_transport_suspended'] and row['local'].get('ok') and (row['local'].get('body') or {}).get('version')==TARGET and row['external'].get('ok') and row['public_boris'].get('ok') and row['mcp_initialize'] and row['mcp_tools_ok'] and row['safe_exec'] and row['postgres_check'] and row['boris_ops_service']=='active')
            except Exception as e: row['ok']=False; row['error']=repr(e)
            if not row['ok']: failures.append(row)
            rows.append(row)
            with LOG.open('a') as f:f.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
            if i<19: time.sleep(30)
        during_cgroup=cgroup_pids()
        main_still_in_cgroup=pid in during_cgroup
        preserved=bool(preserved_at_suspend and main_still_in_cgroup)
        natural_child_exits=sorted(set(pre_cgroup)-set(during_cgroup)-{pid})
        if not main_still_in_cgroup:
            failures.append({'error':'sentinelx_main_left_cgroup_while_suspended','pid':pid,'during_cgroup_pids':during_cgroup})
        # PASS is forbidden until the exact main transport process is resumed.
        os.kill(pid,signal.SIGCONT); suspended=False; time.sleep(0.5)
        status=Path(f'/proc/{pid}/status').read_text(errors='replace') if Path(f'/proc/{pid}/status').exists() else ''
        sigcont_ok=bool(status) and 'State:\tT' not in status and 'State:\tt' not in status
        if not sigcont_ok:
            failures.append({'error':'sentinelx_sigcont_not_confirmed','pid':pid})
        ok=not failures and len(rows)==20 and preserved and sigcont_ok
        agent_sha=hashlib.sha256((ROOT/'agent.py').read_bytes()).hexdigest()
        result={'contract':'BORIS_OPS_INDEPENDENCE_1_4_3','ok':ok,'version':TARGET,'agent_sha256':agent_sha,'method':'sentinelx_main_transport_SIGSTOP_only','samples':len(rows),'failures':len(failures),'duration_s':round(time.time()-start,1),'runtime_dependency_on_sentinelx':False if ok else None,'runtime_dependency_on_github':False,'sentinelx_cgroup_children_preserved':preserved,'sentinelx_pre_cgroup_pids':pre_cgroup,'sentinelx_immediate_cgroup_pids':immediate_cgroup,'natural_child_exits_at_suspend':immediate_natural_exits,'sentinelx_during_cgroup_pids':during_cgroup,'natural_child_exits_during_window':natural_child_exits,'sigcont_confirmed':sigcont_ok,'failure_rows':failures[:20],'started_at':start,'finished_at':time.time()}; atom(result); print(json.dumps({'ok':ok,'samples':len(rows),'failures':len(failures),'duration_s':result['duration_s'],'sigcont_confirmed':sigcont_ok,'cgroup_preserved':preserved})); return 0 if ok else 1
    except Exception as e:
        resume_confirmed=None
        if suspended:
            try:
                os.kill(pid,signal.SIGCONT); suspended=False; time.sleep(0.5)
                st=Path(f'/proc/{pid}/status').read_text(errors='replace') if Path(f'/proc/{pid}/status').exists() else ''
                resume_confirmed=bool(st) and 'State:\tT' not in st and 'State:\tt' not in st
            except Exception:
                resume_confirmed=False
        result={'contract':'BORIS_OPS_INDEPENDENCE_1_4_3','ok':False,'version':TARGET,'error':repr(e),'samples':len(rows),'failures':len(failures)+1,'runtime_dependency_on_github':False,'sigcont_confirmed':resume_confirmed,'started_at':start,'finished_at':time.time()}; atom(result); print(json.dumps(result)); return 1
    finally:
        if suspended:
            try: os.kill(pid,signal.SIGCONT)
            except Exception: pass
if __name__=='__main__': raise SystemExit(run())
