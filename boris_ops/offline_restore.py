#!/usr/bin/env python3
"""Offline BORIS Ops recovery installer/checker. No GitHub/SentinelX required."""
import argparse,hashlib,json,os,pwd,grp,secrets,shutil,subprocess,sys
from pathlib import Path

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def run(argv,check=True):
    cp=subprocess.run(argv,capture_output=True,text=True)
    if check and cp.returncode:
        raise RuntimeError('command_failed: '+' '.join(argv)+' :: '+cp.stderr[-1000:])
    return cp

def locate_root(start):
    p=Path(start).resolve()
    if (p/'SHA256SUMS').is_file(): return p
    kids=[x for x in p.iterdir() if x.is_dir() and (x/'SHA256SUMS').is_file()] if p.is_dir() else []
    if len(kids)==1:return kids[0]
    raise RuntimeError('recovery_root_not_found')

def verify_pack(root):
    sums=root/'SHA256SUMS'; errors=[]; checked=0
    for line in sums.read_text(encoding='utf-8').splitlines():
        if not line.strip(): continue
        try: expected,rel=line.split('  ',1)
        except ValueError: errors.append('bad_sum_line'); continue
        fp=(root/rel).resolve()
        if root not in fp.parents and fp!=root: errors.append('path_escape:'+rel); continue
        if not fp.is_file(): errors.append('missing:'+rel); continue
        if sha(fp)!=expected: errors.append('sha_mismatch:'+rel); continue
        checked+=1
    forbidden=[]
    for fp in root.rglob('*'):
        if fp.is_file() and (fp.name=='agent.env' or (str(fp.relative_to(root)).startswith('etc-boris-ops/') and fp.name!='agent.env.template')): forbidden.append(str(fp.relative_to(root)))
    return {'ok':not errors and not forbidden,'checked':checked,'errors':errors,'forbidden':forbidden}

def ensure_user():
    try:return pwd.getpwnam('borisops')
    except KeyError:
        run(['useradd','--system','--home','/nonexistent','--shell','/usr/sbin/nologin','borisops'])
        return pwd.getpwnam('borisops')

def copy_tree(src,dst):
    if not src.exists(): return []
    copied=[]
    dst.mkdir(parents=True,exist_ok=True)
    for fp in src.iterdir():
        if not fp.is_file(): continue
        target=dst/fp.name; shutil.copy2(fp,target); copied.append(str(target))
    return copied

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('recovery_path'); ap.add_argument('--install',action='store_true'); ap.add_argument('--replace-nginx',action='store_true'); ap.add_argument('--rotate-secrets',action='store_true'); args=ap.parse_args()
    root=locate_root(args.recovery_path); proof=verify_pack(root)
    result={'contract':'BORIS_OPS_OFFLINE_RESTORE','root':str(root),'verification':proof,'install_requested':args.install,'runtime_dependency_on_github':False,'runtime_dependency_on_sentinelx':False}
    if not proof['ok']:
        result['ok']=False; print(json.dumps(result,ensure_ascii=False)); return 2
    if not args.install:
        result['ok']=True; result['mode']='check_only'; print(json.dumps(result,ensure_ascii=False)); return 0
    if os.geteuid()!=0: raise RuntimeError('install_requires_root')
    u=ensure_user(); copied=[]
    copied += copy_tree(root/'root-BORIS-boris_ops',Path('/root/BORIS/boris_ops'))
    copied += copy_tree(root/'etc-systemd',Path('/etc/systemd/system'))
    copied += copy_tree(root/'etc-sudoers.d',Path('/etc/sudoers.d'))
    copied += copy_tree(root/'usr-local-sbin',Path('/usr/local/sbin'))
    copied += copy_tree(root/'etc-logrotate.d',Path('/etc/logrotate.d'))
    # Nginx config is reference-only by default: never overwrite a live site accidentally.
    ngref=root/'etc-nginx'/'boris-ai.reference'
    if args.replace_nginx and ngref.is_file():
        target=Path('/etc/nginx/sites-enabled/boris-ai'); target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(ngref,target); copied.append(str(target))
    envdir=Path('/etc/boris-ops'); envdir.mkdir(parents=True,exist_ok=True); os.chmod(envdir,0o750)
    envfile=envdir/'agent.env'
    generated_or_rotated = bool(args.rotate_secrets or not envfile.exists())
    if generated_or_rotated:
        token=secrets.token_urlsafe(48); secret=secrets.token_hex(64)
        env='\n'.join([
            'BORIS_OPS_TOKEN='+token,
            'BORIS_OPS_HMAC_SECRET='+secret,
            'BORIS_OPS_BIND=127.0.0.1','BORIS_OPS_PORT=8741',
            'BORIS_OPS_AUDIT=/var/log/boris-ops/audit.jsonl',
            'BORIS_OPS_READ_ROOTS=/root/BORIS,/var/www,/etc/nginx,/etc/systemd/system,/var/log',
            'BORIS_OPS_WRITE_ROOTS=/root/BORIS','BORIS_OPS_ALLOW_DANGEROUS_SCRIPTS=0','BORIS_OPS_DISK_WRITE_BLOCK_PERCENT=92'])+'\n'
        tmp=envfile.with_suffix('.tmp'); tmp.write_text(env,encoding='utf-8'); os.chmod(tmp,0o640); os.chown(tmp,0,u.pw_gid); os.replace(tmp,envfile)
    Path('/var/log/boris-ops').mkdir(parents=True,exist_ok=True); os.chown('/var/log/boris-ops',u.pw_uid,u.pw_gid); os.chmod('/var/log/boris-ops',0o750)
    Path('/var/lib/boris-ops').mkdir(parents=True,exist_ok=True); os.chown('/var/lib/boris-ops',u.pw_uid,u.pw_gid); os.chmod('/var/lib/boris-ops',0o750)
    # Validate privileged policy before activating anything.
    sudoers=Path('/etc/sudoers.d/boris-ops')
    if sudoers.is_file(): run(['visudo','-cf',str(sudoers)])
    run(['systemctl','daemon-reload'])
    run(['/usr/sbin/nginx','-t'])
    # Core service + autonomous safety/QA timers.
    units=['boris-ops.service','boris-ops-watchdog.timer','boris-ops-guardian.timer','boris-ops-longqa.timer','boris-ops-qa.timer','boris-ops-cleanup.timer','boris-ops-pipeline-guardian.timer']
    for unit in units: run(['systemctl','enable',unit],check=False)
    run(['systemctl','restart','boris-ops.service'])
    for unit in units[1:]: run(['systemctl','start',unit],check=False)
    result.update({'ok':True,'mode':'install','copied':copied,'generated_or_rotated_secrets':generated_or_rotated,'runtime_user_uid':u.pw_uid})
    print(json.dumps(result,ensure_ascii=False)); return 0
if __name__=='__main__':
    try: raise SystemExit(main())
    except Exception as e:
        print(json.dumps({'contract':'BORIS_OPS_OFFLINE_RESTORE','ok':False,'error':str(e)[:1000]},ensure_ascii=False)); raise SystemExit(1)
