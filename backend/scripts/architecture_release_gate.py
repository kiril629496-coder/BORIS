#!/usr/bin/env python3
from __future__ import annotations
import argparse,fcntl,hashlib,json,os,subprocess,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
RECEIPT=Path('/var/lib/boris-integrity/architecture-release-test-receipt.json')
LOCK=Path('/tmp/boris-production-source-write.lock')
ARCH_TESTS=['tests/test_architecture_constitution.py','tests/test_architecture_registry.py','tests/test_guardian_source_readonly.py','tests/test_social_authority_guard.py','tests/test_operation_contract.py','tests/test_rolling_contract_retry.py','tests/test_architecture_release_gate.py']

def _files():
 out=set()
 for base,pat in [(ROOT/'app','*.py'),(ROOT/'tests','test_*.py'),(ROOT/'migrations','*.sql')]:
  it=base.rglob(pat) if base.name=='app' else base.glob(pat)
  for p in it:
   s=str(p)
   if p.is_file() and not any(x in s for x in ('__pycache__','.pytest_cache','.bak','.before_','.candidate')): out.add(p)
 for p in ROOT.glob('*.py'):
  if not any(x in p.name for x in ('.bak','.before_','.candidate')): out.add(p)
 for rel in ['scripts/architecture_release_gate.py','scripts/action_owner_static_scan.py','scripts/tenant_mutation_scope_scan.py','scripts/runtime_schema_mutation_scan.py','scripts/guardian_source_write_scan.py','scripts/build_backend_release.py','architecture/constitution_v1.json','config/workstream_scope_registry.json']:
  p=ROOT/rel
  if p.is_file(): out.add(p)
 return sorted(out,key=lambda p:str(p.relative_to(ROOT)))

def fingerprint():
 h=hashlib.sha256()
 for p in _files():
  h.update(str(p.relative_to(ROOT)).encode()+b'\0'+hashlib.sha256(p.read_bytes()).hexdigest().encode()+b'\n')
 return h.hexdigest()

def run(cmd,timeout=1800):
 p=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=timeout)
 return {'ok':p.returncode==0,'rc':p.returncode,'tail':((p.stdout or '')+(p.stderr or ''))[-6000:]}

def static(require_receipt=True):
 sys.path.insert(0,str(ROOT))
 from app.db.session import SessionLocal
 from app.models.control_plane import ControlActionOwner,ControlModuleContract
 from app.services.architecture_registry import CANONICAL_STATES,REQUIRED_MODULES,mandatory_tests_for_modules,validate_control_architecture
 from app.services.operation_contract import OperationSemantic,OperationState,SEMANTIC_TO_STATE,STATE_TO_SEMANTIC
 db=SessionLocal()
 try:
  arch=validate_control_architecture(db); owners=db.query(ControlActionOwner).filter(ControlActionOwner.enabled.is_(True)).all(); contracts={r.module:r.contract_json for r in db.query(ControlModuleContract).all()}
 finally: db.close()
 map_ok={x.value for x in OperationSemantic}=={'ALLOW','BLOCK','WAIT','RETRY','SUCCESS','DEGRADED','FAIL'} and set(SEMANTIC_TO_STATE)==set(OperationSemantic) and set(STATE_TO_SEMANTIC)==set(OperationState)
 bad=[]; valid=0
 for o in owners:
  e=[]
  if tuple(o.terminal_states_json or ())!=tuple(CANONICAL_STATES): e.append('states')
  if o.external_side_effect and not o.runtime_ledger_required:e.append('ledger')
  if o.external_side_effect and not o.proof_required:e.append('proof')
  if o.owner_module!='development' and o.external_side_effect and not o.tenant_scope_required:e.append('tenant')
  if e: bad.append({'action':o.action_key,'errors':e})
  else: valid+=1
 denom=int(arch.get('required_business_rules') or 0)+int(arch.get('required_action_owners') or 0)
 numer=(int(arch.get('business_rules') or 0) if arch.get('ok') else 0)+valid
 coverage=(100.0*numer/denom) if denom else 0.0
 mandatory=mandatory_tests_for_modules(contracts,set(REQUIRED_MODULES)); dep=bool(mandatory) and all(x.get('ok') for x in (arch.get('dependency_graph_coverage') or {}).values())
 cmds={'action_owner':[sys.executable,'scripts/action_owner_static_scan.py'],'tenant':[sys.executable,'scripts/tenant_mutation_scope_scan.py'],'schema':[sys.executable,'scripts/runtime_schema_mutation_scan.py'],'guardian':[sys.executable,'scripts/guardian_source_write_scan.py'],'migrations':[sys.executable,'migrations/run_all.py','verify']}
 scans={k:run(v,180) for k,v in cmds.items()}
 fp=fingerprint(); rec={}; rec_ok=not require_receipt
 if require_receipt:
  try: rec=json.loads(RECEIPT.read_text())
  except Exception: rec={}
  rec_ok=rec.get('source_fingerprint')==fp and rec.get('full_backend_tests')=='PASS' and rec.get('architecture_tests')=='PASS' and rec.get('mandatory_dependency_tests')=='PASS' and int(rec.get('failed') or 0)==0
 ok=bool(arch.get('ok') and map_ok and coverage==100.0 and not bad and dep and all(v['ok'] for v in scans.values()) and rec_ok)
 return {'gate':'BORIS_ARCHITECTURE_RELEASE_GATE','ok':ok,'source_fingerprint':fp,'standard_operation_result_coverage':coverage,'untracked_business_failure_paths':len(bad),'operation_semantics_mapping':map_ok,'mandatory_dependency_test_ids':len(mandatory),'mandatory_dependency_graph_ok':dep,'architecture':arch,'scans':{k:{'ok':v['ok'],'rc':v['rc'],'tail':v['tail'][-1000:]} for k,v in scans.items()},'test_receipt_ok':rec_ok,'test_receipt':rec if require_receipt else None,'owner_contract_errors':bad}

def certify():
 fd=os.open(str(LOCK),os.O_RDWR|os.O_CREAT,0o666); fcntl.flock(fd,fcntl.LOCK_EX)
 try:
  os.ftruncate(fd,0);os.write(fd,f'pid={os.getpid()} architecture_test_certification'.encode())
  before=fingerprint(); st=static(False)
  if not st['ok']: return {'ok':False,'stage':'static','detail':st}
  a=run([sys.executable,'-m','pytest','-q',*ARCH_TESTS],900)
  if not a['ok']: return {'ok':False,'stage':'architecture_tests','detail':a}
  f=run([sys.executable,'-m','pytest','-q','tests'],1800)
  if not f['ok']: return {'ok':False,'stage':'full_backend_tests','detail':f}
  after=fingerprint()
  if after!=before:return {'ok':False,'stage':'source_changed_during_tests','before':before,'after':after}
  rec={'source_fingerprint':before,'created_at':datetime.now(timezone.utc).isoformat(),'full_backend_tests':'PASS','architecture_tests':'PASS','mandatory_dependency_tests':'PASS','failed':0,'architecture_tail':a['tail'][-3000:],'full_tail':f['tail'][-3000:]}
  RECEIPT.parent.mkdir(parents=True,exist_ok=True); x,tmp=tempfile.mkstemp(prefix='.arch-test-receipt.',dir=RECEIPT.parent)
  with os.fdopen(x,'w') as h: json.dump(rec,h,ensure_ascii=False,indent=2);h.flush();os.fsync(h.fileno())
  os.chmod(tmp,0o644);os.replace(tmp,RECEIPT);return {'ok':True,'receipt':rec}
 finally: fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)

def main():
 ap=argparse.ArgumentParser();g=ap.add_mutually_exclusive_group(required=True);g.add_argument('--verify',action='store_true');g.add_argument('--static-no-receipt',action='store_true');g.add_argument('--certify-tests',action='store_true');a=ap.parse_args()
 r=certify() if a.certify_tests else static(a.verify);print(json.dumps(r,ensure_ascii=False,default=str));return 0 if r.get('ok') else 1
if __name__=='__main__':raise SystemExit(main())
