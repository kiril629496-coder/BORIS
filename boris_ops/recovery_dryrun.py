#!/usr/bin/env python3
import hashlib,json,tarfile,tempfile,subprocess,re,shutil
from pathlib import Path
ROOT=Path('/root/BORIS/boris_ops')
manifest=json.loads((ROOT/'INSTALL_MANIFEST.json').read_text(encoding='utf-8'))
pack=Path(str(manifest.get('recovery_pack','')))
expected=str(manifest.get('recovery_pack_sha256',''))
checks={}
checks['pack_exists']=pack.is_file()
actual=hashlib.sha256(pack.read_bytes()).hexdigest() if pack.is_file() else ''
checks['pack_sha256']=bool(expected and actual==expected)
checks['manifest_no_runtime_dependencies']=manifest.get('runtime_dependency_on_sentinelx') is False and manifest.get('runtime_dependency_on_github') is False
checks['manifest_no_secrets']=manifest.get('secrets_included') is False
errors=[]
if pack.is_file():
    with tempfile.TemporaryDirectory(prefix='boris-ops-recovery-dryrun-') as td:
        td=Path(td)
        try:
            with tarfile.open(pack,'r:gz') as tf:
                # Reject path traversal before extraction.
                for m in tf.getmembers():
                    target=(td/m.name).resolve()
                    if td.resolve()!=target and td.resolve() not in target.parents:
                        raise RuntimeError('archive_path_traversal:'+m.name)
                tf.extractall(td)
            roots=[x for x in td.iterdir() if x.is_dir()]
            stage=roots[0] if len(roots)==1 else td
            sums=stage/'SHA256SUMS'
            ok_sums=sums.is_file()
            if ok_sums:
                for line in sums.read_text(encoding='utf-8').splitlines():
                    if not line.strip(): continue
                    h,name=line.split(None,1); name=name.strip(); fp=stage/name
                    if not fp.is_file() or hashlib.sha256(fp.read_bytes()).hexdigest()!=h:
                        ok_sums=False; errors.append('checksum:'+name)
            checks['internal_sha256s']=ok_sums
            # No credential file and no concrete credential assignment in pack.
            secret_paths=[x for x in stage.rglob('*') if x.is_file() and x.name in {'agent.env','.env','.env.local'}]
            secret_hits=[]
            # Detect concrete credential material, not source code that dynamically generates credentials.
            concrete=[]
            try:
                for line in Path('/etc/boris-ops/agent.env').read_text(encoding='utf-8').splitlines():
                    if '=' not in line or line.lstrip().startswith('#'): continue
                    k,v=line.split('=',1); v=v.strip().strip('\"').strip("'")
                    if k in {'BORIS_OPS_TOKEN','BORIS_OPS_HMAC_SECRET'} and len(v)>=24: concrete.append(v.encode())
            except Exception: pass
            # Also catch literal high-entropy assignments embedded in text configs/code.
            literal_pat=re.compile(rb'(?i)(BORIS_OPS_TOKEN|BORIS_OPS_HMAC_SECRET)\s*=\s*[\"\']?([A-Za-z0-9_\-]{24,}|[0-9a-f]{48,})')
            for fp in stage.rglob('*'):
                if fp.is_file() and fp.stat().st_size<2_000_000:
                    try:
                        raw=fp.read_bytes()
                        if any(v and v in raw for v in concrete) or literal_pat.search(raw): secret_hits.append(str(fp.relative_to(stage)))
                    except Exception: pass
            checks['secret_files_absent']=not secret_paths
            checks['credential_values_absent']=not secret_hits
            if secret_paths: errors.extend('secret_path:'+str(x.relative_to(stage)) for x in secret_paths)
            if secret_hits: errors.extend('secret_value:'+x for x in secret_hits)
            # Compile every Python recovery component in isolation.
            py_ok=True
            for fp in stage.rglob('*.py'):
                cp=subprocess.run(['/usr/bin/python3','-m','py_compile',str(fp)],capture_output=True,text=True,timeout=20)
                if cp.returncode:
                    py_ok=False; errors.append('py_compile:'+str(fp.relative_to(stage))+':'+cp.stderr[-200:])
            checks['python_compile']=py_ok
            checks['recovery_instructions']=(stage/'RECOVERY.txt').is_file()
        except Exception as e:
            errors.append(repr(e)); checks['extract']=False
        else: checks['extract']=True
ok=all(checks.values()) and not errors
report={'contract':'BORIS_OPS_RECOVERY_DRYRUN','ok':ok,'pack':str(pack),'pack_sha256':actual,'checks':checks,'errors':errors[:30]}
out=ROOT/'RECOVERY_DRYRUN.json'; out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
raise SystemExit(0 if ok else 1)
