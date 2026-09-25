#!/usr/bin/env python3
import hashlib,hmac,json,secrets,time,urllib.request,urllib.error,subprocess,os
from pathlib import Path
ROOT=Path('/root/BORIS/boris_ops'); CAND=ROOT/'releases/agent-1.4.3-candidate.py'; EXPECTED='5afb45b81bf7cc7339d1ec05f8ed0e0d97fd4d611f9c295a77967a6cd5e85b65'; OUT=ROOT/'PROMOTION_1_4_3.json'; GATE=Path('/etc/boris-ops/allow-self-update.json')
state={'contract':'BORIS_OPS_PROMOTE_1_4_3','started_at':time.time(),'candidate':str(CAND),'expected_sha256':EXPECTED,'steps':[]}
def save():
    tmp=OUT.with_suffix('.tmp'); tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n'); os.replace(tmp,OUT)
def fail(msg):
    state['ok']=False; state['error']=msg; state['finished_at']=time.time(); save(); print(json.dumps({'ok':False,'error':msg})); raise SystemExit(1)
def req(path,method='GET',obj=None):
    body=b'' if obj is None else json.dumps(obj,separators=(',',':')).encode(); ts=str(int(time.time())); nonce=secrets.token_hex(12); sig=hmac.new(S.encode(),(ts+'\n'+nonce+'\n').encode()+body,hashlib.sha256).hexdigest(); r=urllib.request.Request('https://boris-ai.pro/_ops'+path,data=body if method=='POST' else None,method=method,headers={'Authorization':'Bearer '+T,'X-Boris-Timestamp':ts,'X-Boris-Nonce':nonce,'X-Boris-Signature':sig,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(r,timeout=20) as x:return x.status,json.loads(x.read())
    except urllib.error.HTTPError as e:
        try:return e.code,json.loads(e.read())
        except:return e.code,{}
try:
    post=json.loads((ROOT/'POST_ACCEPTANCE.json').read_text())
    if post.get('ok') is not True: fail('post_acceptance_not_pass')
    remotep=Path('/var/lib/boris-ops/MCP_REMOTE_60M.json')
    if not remotep.is_file(): fail('remote_mcp_60m_missing')
    remote=json.loads(remotep.read_text())
    if remote.get('ok') is not True or int(remote.get('samples',0)) < 60 or int(remote.get('failures',1)) != 0: fail('remote_mcp_60m_not_pass')
    state['remote_mcp_60m']={k:remote.get(k) for k in ['ok','samples','failures','versions_seen','duration_s','dependency_degraded_events']}
    actual=hashlib.sha256(CAND.read_bytes()).hexdigest()
    if actual!=EXPECTED: fail('candidate_sha_mismatch')
    soakp=ROOT/'CANDIDATE_1_4_3_SOAK.json'
    if not soakp.is_file(): fail('candidate_soak_missing')
    soak=json.loads(soakp.read_text())
    if soak.get('ok') is not True or soak.get('complete') is not True or soak.get('candidate_sha256')!=EXPECTED or soak.get('final_candidate_sha256')!=EXPECTED or int(soak.get('samples',0))<60: fail('candidate_soak_not_passed')
    state['candidate_soak']={k:soak.get(k) for k in ['ok','complete','samples','failures','duration_s','candidate_sha256','final_candidate_sha256']}
    boundedp=ROOT/'CANDIDATE_1_4_3_BOUNDED_QA.json'
    if not boundedp.is_file(): fail('candidate_bounded_resources_qa_missing')
    bounded=json.loads(boundedp.read_text())
    if bounded.get('ok') is not True or bounded.get('candidate_sha256')!=EXPECTED: fail('candidate_bounded_resources_qa_not_passed')
    state['bounded_resources_qa']={k:bounded.get(k) for k in ['ok','candidate_sha256','tools','checks']}
    preflightp=ROOT/'CANDIDATE_1_4_3_PREFLIGHT.json'
    if not preflightp.is_file(): fail('candidate_full_preflight_missing')
    preflight=json.loads(preflightp.read_text())
    if preflight.get('ok') is not True or preflight.get('candidate_sha256')!=EXPECTED or int(preflight.get('tools',0))<34: fail('candidate_full_preflight_not_passed')
    state['full_preflight']={k:preflight.get(k) for k in ['ok','candidate_sha256','tools','checks']}
    safetyp=ROOT/'CANDIDATE_1_4_3_SAFETY_MODES_QA.json'
    if not safetyp.is_file(): fail('candidate_safety_modes_qa_missing')
    safety=json.loads(safetyp.read_text())
    if safety.get('ok') is not True or safety.get('candidate_sha256')!=EXPECTED: fail('candidate_safety_modes_qa_not_passed')
    state['safety_modes_qa']={k:safety.get(k) for k in ['ok','candidate_sha256','checks']}
    cp=subprocess.run(['/usr/bin/python3','-m','py_compile',str(CAND)],capture_output=True,text=True,timeout=30)
    if cp.returncode: fail('candidate_compile_failed')
    env={}
    for line in Path('/etc/boris-ops/agent.env').read_text().splitlines():
        if line.strip() and '=' in line and not line.lstrip().startswith('#'):
            k,v=line.split('=',1); env[k]=v.strip().strip('"').strip("'")
    T=env['BORIS_OPS_TOKEN']; S=env['BORIS_OPS_HMAC_SECRET']
    # Resume-safe promotion: a previous attempt may already have installed the exact
    # candidate and then failed only because MCP was still warming after restart.
    # In that case never perform a second self-update; continue from the proven identity.
    current_sha=hashlib.sha256((ROOT/'agent.py').read_bytes()).hexdigest()
    current_version=None
    try:
        with urllib.request.urlopen('http://127.0.0.1:8741/health',timeout=5) as r:
            current_version=json.loads(r.read()).get('version')
    except Exception:
        pass
    already_installed=(current_version=='1.4.3' and current_sha==EXPECTED)
    state['steps'].append({'resume_identity':{'version':current_version,'sha256':current_sha,'already_installed':already_installed}})
    if not already_installed:
        # One-shot checksum-bound update authorization. It is removed on every exit.
        GATE.write_text(json.dumps({'sha256':EXPECTED,'expires_at':time.time()+300,'purpose':'post_acceptance_1.4.3'})); os.chown(GATE,0,int(subprocess.check_output(['id','-g','borisops'],text=True).strip())); os.chmod(GATE,0o640)
        code,resp=req('/self/update','POST',{'candidate':str(CAND),'sha256':EXPECTED}); state['steps'].append({'update_request':code,'response':resp})
        if code!=202 or not resp.get('ok'): fail('self_update_request_failed')
    recovered=False
    for i in range(120):
        if i: time.sleep(.5)
        try:
            with urllib.request.urlopen('http://127.0.0.1:8741/health',timeout=2) as r:
                d=json.loads(r.read())
                live_sha=hashlib.sha256((ROOT/'agent.py').read_bytes()).hexdigest()
                if r.status==200 and d.get('version')=='1.4.3' and live_sha==EXPECTED:
                    recovered=True; state['steps'].append({'recovered_after_s':round(i*.5,1),'sha256':live_sha}); break
        except Exception: pass
    if not recovered: fail('v1.4.3_not_recovered')
    # The HTTP/MCP listener can become healthy slightly before all MCP tools are ready.
    # Retry only the non-mutating QA probes during a bounded warm-up window.
    qa_plan=[('qa_e2e',['/usr/bin/python3',str(ROOT/'qa_e2e.py')],2),('hardening',['/usr/bin/python3',str(ROOT/'hardening_qa.py')],2),('recovery_pack',['/usr/bin/python3',str(ROOT/'build_recovery_pack.py')],1),('verify',['/usr/bin/python3',str(ROOT/'verify_install.py')],1),('recovery_dryrun',['/usr/bin/python3',str(ROOT/'recovery_dryrun.py')],1)]
    for name,argv,attempts in qa_plan:
        # 1.4.3 token bucket is intentional. Give QA bursts a full refill window.
        if name in {'qa_e2e','hardening'}: time.sleep(18)
        cp=None
        for attempt in range(1,attempts+1):
            cp=subprocess.run(argv,capture_output=True,text=True,timeout=180)
            state['steps'].append({'name':name,'attempt':attempt,'rc':cp.returncode,'stdout':cp.stdout[-5000:],'stderr':cp.stderr[-2000:]})
            if cp.returncode==0: break
            if attempt<attempts: time.sleep(18)
        if cp is None or cp.returncode: fail(name+'_failed')
    state['ok']=True; state['finished_at']=time.time(); state['version']='1.4.3'; save(); print(json.dumps({'ok':True,'version':'1.4.3','sha256':EXPECTED}))
finally:
    try: GATE.unlink()
    except Exception: pass
