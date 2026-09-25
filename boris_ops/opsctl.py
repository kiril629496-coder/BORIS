#!/usr/bin/env python3
import argparse,base64,hashlib,hmac,json,secrets,time,urllib.parse,urllib.request,urllib.error
from pathlib import Path

def load_env(path='/etc/boris-ops/agent.env'):
    out={}
    for line in Path(path).read_text().splitlines():
        if line.strip() and '=' in line and not line.lstrip().startswith('#'):
            k,v=line.split('=',1); out[k]=v.strip().strip('"').strip("'")
    return out

class Client:
    def __init__(self, base='https://boris-ai.pro/_ops'):
        e=load_env(); self.base=base.rstrip('/'); self.token=e['BORIS_OPS_TOKEN']; self.secret=e['BORIS_OPS_HMAC_SECRET']
    def call(self,path,method='GET',obj=None):
        body=b'' if obj is None else json.dumps(obj,separators=(',',':')).encode(); ts=str(int(time.time())); nonce=secrets.token_hex(12)
        sig=hmac.new(self.secret.encode(),(ts+'\n'+nonce+'\n').encode()+body,hashlib.sha256).hexdigest()
        req=urllib.request.Request(self.base+path,data=body if method=='POST' else None,method=method,headers={'Authorization':'Bearer '+self.token,'X-Boris-Timestamp':ts,'X-Boris-Nonce':nonce,'X-Boris-Signature':sig,'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
        try:
            with urllib.request.urlopen(req,timeout=1900) as r:return r.status,json.loads(r.read())
        except urllib.error.HTTPError as e:
            try: data=json.loads(e.read())
            except Exception: data={'ok':False,'error':'http_'+str(e.code)}
            return e.code,data

def main():
    ap=argparse.ArgumentParser(prog='boris-opsctl'); ap.add_argument('--base',default='https://boris-ai.pro/_ops')
    sp=ap.add_subparsers(dest='cmd',required=True)
    sp.add_parser('health'); sp.add_parser('version'); sp.add_parser('boris-health'); sp.add_parser('diagnostics'); sp.add_parser('runtime-capabilities'); sp.add_parser('nginx-validate'); sp.add_parser('postgres-check'); sp.add_parser('jobs')
    p=sp.add_parser('audit'); p.add_argument('--limit',type=int,default=100)
    p=sp.add_parser('processes'); p.add_argument('--limit',type=int,default=50)
    p=sp.add_parser('http-check'); p.add_argument('target',choices=['public','ops','backend_primary','backend_replica']); p.add_argument('--timeout',type=float,default=5.0)
    p=sp.add_parser('exec'); p.add_argument('command'); p.add_argument('--cwd'); p.add_argument('--timeout',type=int,default=30); p.add_argument('--background',action='store_true')
    p=sp.add_parser('job'); p.add_argument('job_id')
    p=sp.add_parser('cancel'); p.add_argument('job_id')
    p=sp.add_parser('read'); p.add_argument('path')
    p=sp.add_parser('list'); p.add_argument('path'); p.add_argument('--limit',type=int,default=200)
    p=sp.add_parser('stat'); p.add_argument('path')
    p=sp.add_parser('download'); p.add_argument('path'); p.add_argument('--output')
    p=sp.add_parser('search'); p.add_argument('query'); p.add_argument('--root',default='/root/BORIS')
    p=sp.add_parser('write'); p.add_argument('path'); p.add_argument('content'); p.add_argument('--validator',choices=['json','python','sh']); p.add_argument('--diff',action='store_true')
    p=sp.add_parser('patch'); p.add_argument('path'); p.add_argument('old'); p.add_argument('new'); p.add_argument('--sha256'); p.add_argument('--count',type=int,default=1); p.add_argument('--validator',choices=['json','python','sh']); p.add_argument('--diff',action='store_true')
    p=sp.add_parser('upload'); p.add_argument('source'); p.add_argument('destination'); p.add_argument('--validator',choices=['json','python','sh'])
    p=sp.add_parser('copy'); p.add_argument('source'); p.add_argument('destination'); p.add_argument('--overwrite',action='store_true')
    p=sp.add_parser('move'); p.add_argument('source'); p.add_argument('destination'); p.add_argument('--overwrite',action='store_true')
    p=sp.add_parser('delete'); p.add_argument('path')
    p=sp.add_parser('restore'); p.add_argument('trash_id'); p.add_argument('--destination')
    p=sp.add_parser('service'); p.add_argument('service'); p.add_argument('action',choices=['status','restart','start','stop','reload'])
    p=sp.add_parser('logs'); p.add_argument('service'); p.add_argument('--lines',type=int,default=100)
    p=sp.add_parser('action'); p.add_argument('action',choices=['restart_backend','restart_frontend','reload_nginx','frontend_build','frontend_deploy']); p.add_argument('--artifact'); p.add_argument('--background',action='store_true')
    a=ap.parse_args(); c=Client(a.base)
    if a.cmd=='health':
        with urllib.request.urlopen(c.base+'/health',timeout=10) as r: code,data=r.status,json.loads(r.read())
    elif a.cmd=='version': code,data=c.call('/version')
    elif a.cmd=='boris-health': code,data=c.call('/boris/health')
    elif a.cmd=='diagnostics': code,data=c.call('/host/diagnostics')
    elif a.cmd=='runtime-capabilities': code,data=c.call('/runtime/capabilities')
    elif a.cmd=='nginx-validate': code,data=c.call('/nginx/validate')
    elif a.cmd=='postgres-check': code,data=c.call('/postgres/check')
    elif a.cmd=='processes': code,data=c.call('/process/list?limit='+str(max(1,min(a.limit,200))))
    elif a.cmd=='http-check': code,data=c.call('/http/check?target='+urllib.parse.quote(a.target,safe='')+'&timeout='+str(max(.5,min(a.timeout,10.0))))
    elif a.cmd=='audit': code,data=c.call('/audit/recent?limit='+str(max(1,min(a.limit,500))))
    elif a.cmd=='jobs': code,data=c.call('/jobs')
    elif a.cmd=='job': code,data=c.call('/jobs/'+a.job_id)
    elif a.cmd=='cancel': code,data=c.call('/jobs/'+a.job_id+'/cancel','POST',{})
    elif a.cmd=='exec': code,data=c.call('/exec','POST',{'command':a.command,'cwd':a.cwd,'timeout':a.timeout,'background':a.background})
    elif a.cmd=='read': code,data=c.call('/file/read?path='+urllib.parse.quote(a.path,safe=''))
    elif a.cmd=='list': code,data=c.call('/file/list?path='+urllib.parse.quote(a.path,safe='')+'&limit='+str(max(1,min(a.limit,1000))))
    elif a.cmd=='stat': code,data=c.call('/file/stat?path='+urllib.parse.quote(a.path,safe=''))
    elif a.cmd=='download':
        code,data=c.call('/file/download?path='+urllib.parse.quote(a.path,safe=''))
        if 200<=code<300 and data.get('ok') and a.output:
            Path(a.output).write_bytes(base64.b64decode(data['content_base64'])); data={k:v for k,v in data.items() if k!='content_base64'}; data['saved_to']=a.output
    elif a.cmd=='search': code,data=c.call('/file/search?root='+urllib.parse.quote(a.root,safe='')+'&q='+urllib.parse.quote(a.query,safe=''))
    elif a.cmd=='write': code,data=c.call('/file/write','POST',{'path':a.path,'content':a.content,'validator':a.validator,'return_diff':a.diff})
    elif a.cmd=='patch': code,data=c.call('/file/patch','POST',{'path':a.path,'old':a.old,'new':a.new,'expected_sha256':a.sha256 or '', 'count':a.count,'validator':a.validator,'return_diff':a.diff})
    elif a.cmd=='upload':
        raw=Path(a.source).read_bytes(); code,data=c.call('/file/upload','POST',{'path':a.destination,'content_base64':base64.b64encode(raw).decode(),'expected_sha256':hashlib.sha256(raw).hexdigest(),'validator':a.validator})
    elif a.cmd=='copy': code,data=c.call('/file/copy','POST',{'source':a.source,'destination':a.destination,'overwrite':a.overwrite})
    elif a.cmd=='move': code,data=c.call('/file/move','POST',{'source':a.source,'destination':a.destination,'overwrite':a.overwrite})
    elif a.cmd=='delete': code,data=c.call('/file/delete','POST',{'path':a.path})
    elif a.cmd=='restore': code,data=c.call('/file/restore','POST',{'trash_id':a.trash_id,'destination':a.destination})
    elif a.cmd=='service': code,data=c.call('/service','POST',{'service':a.service,'action':a.action})
    elif a.cmd=='logs': code,data=c.call('/logs','POST',{'service':a.service,'lines':a.lines})
    elif a.cmd=='action':
        code,rpc=c.call('/mcp','POST',{'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'boris_action','arguments':{'action':a.action,'artifact':a.artifact,'background':a.background}}})
        try:
            content=((rpc.get('result') or {}).get('content') or [{}])[0].get('text','{}'); data=json.loads(content)
            if (rpc.get('result') or {}).get('isError') and data.get('ok',True): data['ok']=False
        except Exception: data={'ok':False,'error':'invalid_mcp_action_response','raw':rpc}

    print(json.dumps({'http_status':code,'response':data},ensure_ascii=False,indent=2)); raise SystemExit(0 if 200<=code<300 and data.get('ok',True) else 1)
if __name__=='__main__': main()
