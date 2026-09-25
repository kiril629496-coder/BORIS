#!/usr/bin/env python3
import json,subprocess,time,urllib.request
STEPS=[]
def run(argv,timeout=30):
    cp=subprocess.run(argv,capture_output=True,text=True,timeout=timeout)
    STEPS.append({'cmd':' '.join(argv),'rc':cp.returncode,'stdout':cp.stdout[-500:],'stderr':cp.stderr[-500:]})
    if cp.returncode: raise RuntimeError('command_failed:'+argv[-1])
def health(url,attempts=20):
    last=''
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url,timeout=3) as r:
                if r.status==200:
                    STEPS.append({'health':url,'status':200,'attempt':i+1}); return
                last='status='+str(r.status)
        except Exception as e: last=str(e)
        time.sleep(.5)
    raise RuntimeError('health_failed:'+url+':'+last[:200])
try:
    # Replica first: production primary remains available.
    run(['sudo','-n','systemctl','restart','boris-backend-replica.service'],60)
    health('http://127.0.0.1:8001/health')
    health('https://boris-ai.pro/health')
    # Then primary while replica is already healthy.
    run(['sudo','-n','systemctl','restart','boris-backend.service'],60)
    health('http://127.0.0.1:8000/health')
    health('https://boris-ai.pro/health')
    print(json.dumps({'ok':True,'contract':'BORIS_BACKEND_ROLLING_RESTART','steps':STEPS},ensure_ascii=False))
except Exception as e:
    print(json.dumps({'ok':False,'contract':'BORIS_BACKEND_ROLLING_RESTART','error':str(e),'steps':STEPS},ensure_ascii=False))
    raise SystemExit(1)
