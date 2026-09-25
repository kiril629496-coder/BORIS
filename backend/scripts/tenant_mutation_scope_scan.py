#!/usr/bin/env python3
from __future__ import annotations
import ast,json
from pathlib import Path
ROOT=Path('/root/BORIS/backend')
REQUIRED={
 'app/api/cpx_advisor.py':{'_provider_set_manual_bid':'account_id','_provider_remove_promotion':'account_id'},
 'app/api/messenger.py':{'send_message':'account_id','_provider_upload_image':'account_id','_provider_send_image':'account_id'},
 'app/services/email_service.py':{'send_email':'tenant_scope','_smtp_transport_send':'tenant_scope'},
 'posting_runner.py':{'_send_telegram':'tenant_scope','_send_vk':'tenant_scope'},
 'app/api/avito.py':{'feed_send_to_avito':'account_id'},
 'app/api/billing.py':{'check_and_consume':'account_id'},
 'app/services/crm_service.py':{'create_task':'account_id'},
 'app/crm/bridge.py':{'sync_dialog_for_account':'account_id'},
}
CALL_KW={
 'send_email':'tenant_scope','_smtp_transport_send':'tenant_scope',
 '_send_telegram':'tenant_scope','_send_vk':'tenant_scope',
 '_provider_set_manual_bid':'account_id','_provider_remove_promotion':'account_id',
 '_provider_upload_image':'account_id','_provider_send_image':'account_id',
}
viol=[]; covered=[]
for rel, funcs in REQUIRED.items():
 p=ROOT/rel
 if not p.exists():
  viol.append({'code':'tenant_owner_file_missing','file':rel}); continue
 try:t=ast.parse(p.read_text(encoding='utf-8',errors='replace'),filename=str(p))
 except Exception as e:
  viol.append({'code':'tenant_owner_parse_error','file':rel,'error':type(e).__name__});continue
 defs={n.name:n for n in t.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
 for fn,param in funcs.items():
  node=defs.get(fn)
  if node is None:
   viol.append({'code':'tenant_owner_function_missing','file':rel,'function':fn});continue
  args={a.arg for a in list(node.args.args)+list(node.args.kwonlyargs)}
  if param not in args:
   viol.append({'code':'tenant_scope_param_missing','file':rel,'function':fn,'param':param})
  else: covered.append(f'{rel}:{fn}')
# Every call to tenant-enforced transport helpers must propagate the explicit scope.
files=list((ROOT/'app').rglob('*.py'))+[ROOT/'posting_runner.py']
for p in files:
 if not p.exists(): continue
 try:t=ast.parse(p.read_text(encoding='utf-8',errors='replace'),filename=str(p))
 except Exception:continue
 rel=p.relative_to(ROOT).as_posix()
 for n in ast.walk(t):
  if not isinstance(n,ast.Call):continue
  name=''
  if isinstance(n.func,ast.Name):name=n.func.id
  elif isinstance(n.func,ast.Attribute):name=n.func.attr
  param=CALL_KW.get(name)
  if not param:continue
  # Function definition isn't a Call; all real calls require a keyword to make scope explicit.
  if not any(k.arg==param for k in n.keywords):
   viol.append({'code':'unscoped_mutating_call','file':rel,'line':n.lineno,'function':name,'required':param})
# Global telephony workers are queue-scoped: prove both physical provider boundaries carry account identity.
tele=(ROOT/'app/services/telephony_core.py').read_text(encoding='utf-8',errors='replace')
if "metadata={'account_id':account_id" not in tele:
 viol.append({'code':'telephony_make_call_tenant_missing'})
if "cmd['account_id']" not in tele or 'adapter.command(' not in tele:
 viol.append({'code':'telephony_command_tenant_missing'})
else: covered+=['telephony:make_call','telephony:command']
print(json.dumps({'check':'TENANT_MUTATION_SCOPE_SCAN','ok':not viol,'covered_boundaries':len(covered),'violations':viol,'unscoped_mutating_operations':len(viol)},ensure_ascii=False))
raise SystemExit(0 if not viol else 1)
