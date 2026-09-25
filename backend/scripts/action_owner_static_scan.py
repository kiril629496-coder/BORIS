#!/usr/bin/env python3
from __future__ import annotations
import ast, json
from pathlib import Path
ROOT=Path('/root/BORIS/backend')
FILES=list((ROOT/'app').rglob('*.py'))+[ROOT/'posting_runner.py']
RULES={
 'AVITO_BID_WRITE': {'needles':['api.avito.ru/cpxpromo/1/setManual','/cpxpromo/1/setManual'], 'allowed':{'app/api/cpx_advisor.py'}},
 'AVITO_PROMOTION_WRITE': {'needles':['api.avito.ru/cpxpromo/1/remove','/cpxpromo/1/remove'], 'allowed':{'app/api/cpx_advisor.py'}},
 'MESSAGE_IMAGE_UPLOAD': {'needles':['/uploadImages'], 'allowed':{'app/api/messenger.py'}},
 'MESSAGE_IMAGE_SEND': {'needles':['/messages/image'], 'allowed':{'app/api/messenger.py'}},
 'FEED_PUBLISH': {'needles':['/autoload/v1/upload'], 'allowed':{'app/api/avito.py'}},
 'SOCIAL_VK_WRITE': {'needles':['api.vk.com/method/wall.post','api.vk.com/method/wall.delete','api.vk.com/method/wall.edit'], 'allowed':{'posting_runner.py'}},
}

def strings(node):
 out=[]
 for n in ast.walk(node):
  if isinstance(n, ast.Constant) and isinstance(n.value,str): out.append(n.value)
  elif isinstance(n, ast.JoinedStr):
   s=''.join(str(x.value) for x in n.values if isinstance(x,ast.Constant) and isinstance(x.value,str))
   if s: out.append(s)
 return out

def rel(p): return p.relative_to(ROOT).as_posix()
viol=[]; hits=[]
for p in FILES:
 try: src=p.read_text(encoding='utf-8',errors='replace'); tree=ast.parse(src,filename=str(p))
 except Exception as e:
  viol.append({'code':'parse_error','file':rel(p),'detail':type(e).__name__}); continue
 rp=rel(p)
 imports_smtplib=any(isinstance(n,(ast.Import,ast.ImportFrom)) and (any(a.name=='smtplib' for a in n.names) if isinstance(n,ast.Import) else n.module=='smtplib') for n in tree.body)
 for n in ast.walk(tree):
  if not isinstance(n,ast.Call): continue
  values='\n'.join(strings(n))
  for key,spec in RULES.items():
   if any(x in values for x in spec['needles']):
    rec={'action':key,'file':rp,'line':getattr(n,'lineno',0)}; hits.append(rec)
    if rp not in spec['allowed']: viol.append({**rec,'code':'direct_side_effect_owner_violation'})
  # Physical SMTP DATA writer; connection/noop tests are not side effects.
  if isinstance(n.func,ast.Attribute) and n.func.attr=='send_message' and imports_smtplib:
   rec={'action':'EMAIL_SEND','file':rp,'line':getattr(n,'lineno',0)}; hits.append(rec)
   if rp!='app/services/email_service.py': viol.append({**rec,'code':'direct_side_effect_owner_violation'})
  # Telephony provider adapter physical commands.
  if isinstance(n.func,ast.Attribute) and n.func.attr in {'make_call','command'}:
   base=ast.unparse(n.func.value) if hasattr(ast,'unparse') else ''
   if base=='adapter':
    rec={'action':'PHONE_PROVIDER_COMMAND','file':rp,'line':getattr(n,'lineno',0)}; hits.append(rec)
    if rp!='app/services/telephony_core.py': viol.append({**rec,'code':'direct_side_effect_owner_violation'})
print(json.dumps({'check':'ACTION_OWNER_STATIC_SCAN','ok':not viol,'hits':hits,'violations':viol,'direct_side_effect_owner_violations':len(viol)},ensure_ascii=False))
raise SystemExit(0 if not viol else 1)
