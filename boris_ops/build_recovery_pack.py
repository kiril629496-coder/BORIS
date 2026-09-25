#!/usr/bin/env python3
"""Build secret-free offline BORIS Ops recovery pack. No GitHub/SentinelX runtime dependency."""
import hashlib,json,os,shutil,tarfile,time,subprocess
from pathlib import Path
ROOT=Path('/root/BORIS/boris_ops'); BACKUPS=Path('/root/BORIS/backups/boris-ops'); stamp=time.strftime('%Y%m%dT%H%M%SZ')
subprocess.run(['/usr/bin/python3',str(ROOT/'refresh_manifest.py')],check=True,capture_output=True,text=True)
manifest=json.loads((ROOT/'INSTALL_MANIFEST.json').read_text()); version=str(manifest.get('version','unknown'))
stage=BACKUPS/f'recovery-{version}-{stamp}'; stage.mkdir(parents=True,exist_ok=False)
for d in ['root-BORIS-boris_ops','etc-systemd','etc-sudoers.d','usr-local-sbin','etc-nginx','etc-logrotate.d','etc-boris-ops']:(stage/d).mkdir()
for src in [Path(p) for p in manifest.get('files',{})]:
 if not src.is_file(): continue
 if str(src).startswith('/etc/boris-ops/') or src.name=='agent.env': continue
 if str(src).startswith(str(ROOT)+'/'): dst=stage/'root-BORIS-boris_ops'/src.name
 elif str(src).startswith('/etc/systemd/system/'): dst=stage/'etc-systemd'/src.name
 elif str(src).startswith('/etc/sudoers.d/'): dst=stage/'etc-sudoers.d'/src.name
 elif str(src).startswith('/usr/local/sbin/'): dst=stage/'usr-local-sbin'/src.name
 elif str(src).startswith('/etc/logrotate.d/'): dst=stage/'etc-logrotate.d'/src.name
 elif str(src)=='/etc/nginx/sites-enabled/boris-ai': dst=stage/'etc-nginx'/'boris-ai.reference'
 else: continue
 shutil.copy2(src,dst)
# Include a manifest snapshot and a secret-free environment template; never include the live agent.env.
shutil.copy2(ROOT/'INSTALL_MANIFEST.json',stage/'root-BORIS-boris_ops'/'INSTALL_MANIFEST.json')
(stage/'etc-boris-ops'/'agent.env.template').write_text('''BORIS_OPS_TOKEN=<GENERATE_FRESH_RANDOM_TOKEN>\nBORIS_OPS_HMAC_SECRET=<GENERATE_FRESH_RANDOM_HMAC_SECRET>\nBORIS_OPS_BIND=127.0.0.1\nBORIS_OPS_PORT=8741\nBORIS_OPS_AUDIT=/var/log/boris-ops/audit.jsonl\nBORIS_OPS_READ_ROOTS=/root/BORIS,/var/www,/etc/nginx,/etc/systemd/system,/var/log\nBORIS_OPS_WRITE_ROOTS=/root/BORIS\nBORIS_OPS_ALLOW_DANGEROUS_SCRIPTS=0\nBORIS_OPS_DISK_WRITE_BLOCK_PERCENT=92\nBORIS_OPS_MAX_CONCURRENT_REQUESTS=12\nBORIS_OPS_MAX_CONCURRENT_JOBS=3\nBORIS_OPS_SEARCH_MAX_FILES=4000\nBORIS_OPS_SEARCH_MAX_SECONDS=8\n''')
(stage/'RECOVERY.txt').write_text('BORIS Ops offline recovery pack. Secrets intentionally excluded. Restore matching files, create /etc/boris-ops/agent.env from etc-boris-ops/agent.env.template with fresh credentials (offline_restore.py can generate them), validate nginx/systemd/sudoers, daemon-reload and start boris-ops timers. Runtime does not require GitHub or SentinelX.\n')
entries=[]
for p in sorted(stage.rglob('*')):
 if p.is_file() and p.name!='SHA256SUMS': entries.append((hashlib.sha256(p.read_bytes()).hexdigest(),str(p.relative_to(stage))))
(stage/'SHA256SUMS').write_text('\n'.join(f'{h}  {n}' for h,n in entries)+'\n')
out=BACKUPS/f'recovery-{version}-{stamp}.tar.gz'
with tarfile.open(out,'w:gz') as tf: tf.add(stage,arcname=stage.name)
sha=hashlib.sha256(out.read_bytes()).hexdigest(); shutil.rmtree(stage)
# update manifest with latest pack only after archive is complete
manifest=json.loads((ROOT/'INSTALL_MANIFEST.json').read_text()); manifest['recovery_pack']=str(out); manifest['recovery_pack_sha256']=sha; manifest['recovery_pack_files']=len(entries); tmp=(ROOT/'INSTALL_MANIFEST.json').with_suffix('.tmp'); tmp.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n'); os.replace(tmp,ROOT/'INSTALL_MANIFEST.json')
print(json.dumps({'ok':True,'version':version,'path':str(out),'sha256':sha,'files':len(entries),'secrets_included':False,'runtime_dependency_on_github':False,'runtime_dependency_on_sentinelx':False}))
