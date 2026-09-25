import sys, requests
base=sys.argv[1].rstrip('/')
checks=[('/api/landing/content',{200}),('/docs',{200}),('/openapi.json',{200})]
for path,allowed in checks:
    r=requests.get(base+path,timeout=8)
    if r.status_code not in allowed:
        raise SystemExit(f'CONTRACT_FAIL {path} status={r.status_code}')
# OpenAPI must expose core business surfaces, not merely return HTTP 200.
s=requests.get(base+'/openapi.json',timeout=8).json(); paths=s.get('paths',{})
required_prefixes=['/api/inbox','/api/crm','/api/reliability']
missing=[p for p in required_prefixes if not any(x.startswith(p) for x in paths)]
if missing: raise SystemExit('CONTRACT_FAIL missing='+','.join(missing))
print('CONTRACT_PASS',base,'paths',len(paths))
