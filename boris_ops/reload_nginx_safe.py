#!/usr/bin/env python3
import json,subprocess,time,urllib.request
steps=[]
def run(argv,timeout=30):
    cp=subprocess.run(argv,capture_output=True,text=True,timeout=timeout)
    steps.append({'cmd':' '.join(argv),'rc':cp.returncode,'stdout':cp.stdout[-1000:],'stderr':cp.stderr[-1000:]})
    if cp.returncode: raise RuntimeError('command_failed:'+argv[-1])
try:
    run(['sudo','-n','/usr/sbin/nginx','-t'],30)
    run(['sudo','-n','systemctl','reload','nginx.service'],30)
    ok={}
    for name,url in [('ops','https://boris-ai.pro/_ops/health'),('public','https://boris-ai.pro/health')]:
        with urllib.request.urlopen(url,timeout=5) as r: ok[name]=r.status
    if ok.get('ops')!=200 or ok.get('public')!=200: raise RuntimeError('post_reload_health_failed')
    print(json.dumps({'ok':True,'contract':'BORIS_NGINX_SAFE_RELOAD','health':ok,'steps':steps},ensure_ascii=False))
except Exception as e:
    print(json.dumps({'ok':False,'contract':'BORIS_NGINX_SAFE_RELOAD','error':str(e),'steps':steps},ensure_ascii=False)); raise SystemExit(1)
