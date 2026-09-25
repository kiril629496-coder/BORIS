#!/usr/bin/env python3
import hashlib,json,subprocess,urllib.request,os,time
from pathlib import Path
ROOT=Path('/root/BORIS/boris_ops'); MANIFEST=ROOT/'INSTALL_MANIFEST.json'; OUT=ROOT/'VERIFY_INSTALL_LAST.json'
def sha(p):
    p=Path(p)
    try:return hashlib.sha256(p.read_bytes()).hexdigest()
    except Exception:
        cp=subprocess.run(['sha256sum',str(p)],capture_output=True,text=True,timeout=10)
        return cp.stdout.split()[0] if cp.returncode==0 and cp.stdout.strip() else None
def active(unit):
    cp=subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True,timeout=10); return cp.stdout.strip()
result={'contract':'BORIS_OPS_VERIFY_INSTALL','ok':True,'files':{},'services':{},'http':{},'checked_at':time.time()}
try: manifest=json.loads(MANIFEST.read_text())
except Exception as e:
    result['ok']=False; result['manifest_error']=str(e)[:300]; manifest={}
# Verify every immutable component listed by the generated manifest, not a stale hard-coded subset.
for raw,meta in (manifest.get('files') or {}).items():
    p=Path(raw); exists=p.is_file(); actual=sha(p) if exists else None; expected=(meta or {}).get('sha256'); match=bool(exists and expected and actual==expected)
    result['files'][raw]={'exists':exists,'sha256':actual,'expected_sha256':expected,'match':match}; result['ok'] &= match
# Core runtime must be active. Pipeline stage units are verified above for existence/hash but need not all be active simultaneously.
for svc in ['boris-ops.service','boris-ops-watchdog.timer','boris-ops-guardian.timer','boris-ops-longqa.timer','boris-ops-qa.timer','boris-ops-cleanup.timer','boris-ops-pipeline-guardian.timer','nginx.service']:
    st=active(svc); result['services'][svc]=st; result['ok'] &= st=='active'
# Recovery archive integrity and dependency invariants.
try:
    pack=Path(manifest.get('recovery_pack','')); expected=manifest.get('recovery_pack_sha256',''); actual=sha(pack) if pack.is_file() else ''
    rec={'pack':str(pack),'exists':pack.is_file(),'sha256_match':bool(expected and actual==expected),'runtime_dependency_on_sentinelx':manifest.get('runtime_dependency_on_sentinelx'),'runtime_dependency_on_github':manifest.get('runtime_dependency_on_github'),'secrets_included':manifest.get('secrets_included')}; result['recovery']=rec
    result['ok'] &= rec['exists'] and rec['sha256_match'] and rec['runtime_dependency_on_sentinelx'] is False and rec['runtime_dependency_on_github'] is False and rec['secrets_included'] is False
except Exception as e: result['recovery']={'error':str(e)[:300]}; result['ok']=False
for name,url in [('local','http://127.0.0.1:8741/health'),('external','https://boris-ai.pro/_ops/health')]:
    try:
        with urllib.request.urlopen(url,timeout=8) as r: result['http'][name]=r.status; result['ok'] &= r.status==200
    except Exception as e: result['http'][name]=str(e)[:300]; result['ok']=False
result['manifest']={'schema':manifest.get('schema'),'version':manifest.get('version'),'files':len(manifest.get('files') or {}),'systemd_units':len(manifest.get('systemd_units') or []),'runtime_dependency_on_sentinelx':manifest.get('runtime_dependency_on_sentinelx'),'runtime_dependency_on_github':manifest.get('runtime_dependency_on_github'),'secrets_included':manifest.get('secrets_included')}
tmp=OUT.with_suffix('.tmp'); tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n'); os.chmod(tmp,0o664); os.replace(tmp,OUT)
print(json.dumps({'ok':result['ok'],'contract':result['contract'],'version':result['manifest'].get('version'),'files':len(result['files']),'services':result['services'],'http':result['http'],'recovery':result.get('recovery')},ensure_ascii=False)); raise SystemExit(0 if result['ok'] else 1)
