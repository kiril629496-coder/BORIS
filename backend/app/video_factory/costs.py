# -*- coding: utf-8 -*-
from __future__ import annotations
import json,time
from pathlib import Path
from app.services.exception_observability import observe_suppressed

def ledger_path(projects,pid): return Path(projects)/f'{pid}.costs.jsonl'
def append(projects,pid,provider,model,operation,quantity=0,unit='call',cost_usd=0.0,meta=None):
    row={'timestamp':time.strftime('%Y-%m-%dT%H:%M:%S'),'project_id':pid,'provider':provider,'model':model,'operation':operation,'quantity':quantity,'unit':unit,'cost_usd':round(float(cost_usd or 0),6),'meta':meta or {}}
    with ledger_path(projects,pid).open('a',encoding='utf-8') as f: f.write(json.dumps(row,ensure_ascii=False)+'\n')
    return row
def read(projects,pid):
    p=ledger_path(projects,pid); out=[]
    if p.exists():
        for line in p.read_text(encoding='utf-8').splitlines():
            try: out.append(json.loads(line))
            except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=16)
    return out
def summary(projects,pid):
    rows=read(projects,pid); return {'paid_calls':sum(1 for r in rows if r.get('cost_usd',0)>0),'actual_cost_usd':round(sum(float(r.get('cost_usd') or 0) for r in rows),6),'operations':len(rows),'items':rows[-100:]}
