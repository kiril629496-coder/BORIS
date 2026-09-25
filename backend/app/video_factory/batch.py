# -*- coding: utf-8 -*-
from __future__ import annotations
import json,time,uuid
from pathlib import Path
from app.services.exception_observability import observe_suppressed

CONTENT_MATRIX=[
 ('problem','Проблема','Что чаще всего мешает получить хороший результат?'),
 ('solution','Решение','Как решить задачу проще и понятнее?'),
 ('expert','Эксперт','На что обратить внимание перед заказом?'),
 ('case','Кейс','Как выглядит хороший результат на практике?'),
 ('before_after','До / после','Что меняется после правильного решения?'),
 ('faq','FAQ','Один из самых частых вопросов клиентов.'),
 ('price','Цена','Что действительно влияет на стоимость?'),
 ('comparison','Сравнение','Как выбрать подходящий вариант и не переплатить?'),
 ('review','Доверие','Почему клиенты выбирают такой подход?'),
 ('offer','Оффер','Самое важное предложение для клиента.'),
]
HOOK_ALT=[
 'Показываем главное без долгого вступления.',
 'Вот что важно знать до принятия решения.',
 'Разберём коротко на конкретных фактах.',
 'Смотрите на результат, а не на обещания.',
 'Три вещи, которые стоит проверить заранее.',
]

def create_batch(root,payload,count,create_project):
    count=max(1,min(int(count or 1),100)); bid='vfb_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
    base=(payload.get('prompt') or '').strip(); projects=[]
    for i in range(count):
        key,label,hook=CONTENT_MATRIX[i%len(CONTENT_MATRIX)]
        cycle=i//len(CONTENT_MATRIX); intro=hook if cycle==0 else HOOK_ALT[(cycle+i)%len(HOOK_ALT)]
        variant=dict(payload); variant['prompt']=f"{intro} {base}".strip(); variant['title']=f"{label} · {i+1:02d}"
        p=create_project(variant); p['content_type']=key; p['batch_id']=bid
        # caller's create_project already persisted; save marker through returned path later is optional
        projects.append({'id':p['id'],'title':p['title'],'content_type':key,'index':i+1})
    data={'id':bid,'account_id':payload.get('account_id'),'owner_user_id':payload.get('owner_user_id'),'title':payload.get('title') or (base[:80] if base else 'Серия видео'),'status':'draft','count':count,'created_at':time.strftime('%Y-%m-%dT%H:%M:%S'),'projects':projects,'content_matrix':True}
    folder=Path(root)/'batches'; folder.mkdir(parents=True,exist_ok=True); (folder/f'{bid}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    return data

def load_batch(root,bid):
    p=Path(root)/'batches'/f'{bid}.json'
    if not p.exists(): return None
    return json.loads(p.read_text(encoding='utf-8'))

def list_batches(root,limit=30):
    folder=Path(root)/'batches'
    if not folder.exists():return []
    out=[]
    for p in sorted(folder.glob('vfb_*.json'),reverse=True)[:max(1,min(limit,100))]:
        try:out.append(json.loads(p.read_text(encoding='utf-8')))
        except Exception as _suppressed_exc:observe_suppressed(__name__, _suppressed_exc, line=51)
    return out

def status(root,bid,load_project):
    b=load_batch(root,bid)
    if not b:return None
    states=[]
    for item in b.get('projects',[]):
        p=load_project(item['id']) or {}; states.append({**item,'status':p.get('status','missing'),'job':p.get('job'),'qa':p.get('qa'),'semantic_qa':p.get('semantic_qa'),'outputs':p.get('outputs',[])})
    counts={}
    for x in states:counts[x['status']]=counts.get(x['status'],0)+1
    ready=counts.get('ready',0); failed=counts.get('failed',0)+counts.get('qa_failed',0)
    b['status']='ready' if ready==len(states) and states else ('partial' if ready else 'rendering' if any(x['status'] in ('queued','rendering','qa') for x in states) else 'draft')
    b['counts']=counts; b['ready']=ready; b['failed']=failed; b['items']=states
    return b
