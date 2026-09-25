from __future__ import annotations

import subprocess
import sys

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)

from pydantic import BaseModel

from app.api.auth import (
    get_current_user_or_internal,
)

from app.db.session import SessionLocal
from app.factory.client_launch_entry import (
    classify,
)


router=APIRouter(
    prefix="/api/factory/new-client-launch",
    tags=["Factory New Client Launch"],
)


class LaunchRequest(BaseModel):
    account_id: str


def normalize_account_id(
    value: str,
) -> str:
    value=(value or "").strip()

    if not value:
        raise HTTPException(
            status_code=422,
            detail="account_id required",
        )

    if len(value) > 255:
        raise HTTPException(
            status_code=422,
            detail="account_id too long",
        )

    return value


def classify_account(
    account_id: str,
):
    db=SessionLocal()

    try:
        return classify(
            db,
            account_id,
        )

    finally:
        db.close()


@router.get(
    "/{account_id}",
)
def check_launch(
    account_id: str,
    user=Depends(
        get_current_user_or_internal
    ),
):
    """
    READ ONLY.

    Returns:
      READY
      NEEDS_INPUT
      BLOCKED
    """

    account_id=normalize_account_id(
        account_id
    )

    result=classify_account(
        account_id
    )

    return {
        "status":"ok",
        "mode":"check",
        "account_id":account_id,
        "launch":result,
    }


@router.post(
    "/run",
)
def run_launch(
    body: LaunchRequest,
    user=Depends(
        get_current_user_or_internal
    ),
):
    """
    Runs only the already accepted internal Factory pipeline.

    This endpoint itself does NOT:
      send client messages
      publish
      spend money
      modify advertising bids
    """

    account_id=normalize_account_id(
        body.account_id
    )

    before=classify_account(
        account_id
    )

    if before["reason"] == "ACCOUNT_NOT_FOUND":
        raise HTTPException(
            status_code=404,
            detail=before,
        )

    proc=subprocess.run(
        [
            sys.executable,
            "factory_pipeline_runner.py",
        ],
        cwd="/root/BORIS/backend",
        text=True,
        capture_output=True,
        timeout=240,
    )

    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail={
                "message":
                    "Factory pipeline failed",

                "account_id":
                    account_id,

                "returncode":
                    proc.returncode,

                "stdout_tail":
                    "\n".join(
                        proc.stdout
                        .splitlines()[-60:]
                    ),

                "stderr_tail":
                    "\n".join(
                        proc.stderr
                        .splitlines()[-60:]
                    ),
            },
        )

    after=classify_account(
        account_id
    )

    return {
        "status":"ok",
        "mode":"apply",
        "account_id":account_id,
        "before":before,
        "after":after,
        "factory":{
            "returncode":0,
        },
    }

# === BORIS_NEW_CLIENT_LIFECYCLE_PROJECTION_V1 ===
# Account-scoped persisted lifecycle projection over the existing Factory.
# No second orchestrator/scheduler and no external effects.
from datetime import datetime, timedelta
import json
from sqlalchemy import text

_LIFECYCLE_DDL = 'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'

_SAFE_STEPS = [
    ("account", "Аккаунт подключён"),
    ("business_context", "Данные бизнеса собраны"),
    ("integrations", "Интеграции проверены"),
    ("capabilities", "Доступные возможности определены"),
    ("factory", "Внутренняя подготовка Factory завершена"),
]


def _ensure_lifecycle(db):
    db.execute(text(_LIFECYCLE_DDL))


def _lifecycle_row(db, account_id):
    _ensure_lifecycle(db)
    row=db.execute(text("SELECT * FROM factory_launch_lifecycle_state WHERE account_id=:a"),{"a":account_id}).mappings().first()
    return dict(row) if row else None


def _module_matrix(db, account_id):
    # Canonical entitlement read-model only. Never enables a capability.
    launch=db.execute(text("SELECT product_count,launch_status,readiness_pct,packet FROM factory_launch_state WHERE account_id=:a"),{'a':account_id}).mappings().first()
    provisioning=db.execute(text("SELECT provisioning_status,ready_internal,ready_schedule,blockers FROM factory_provisioning_state WHERE account_id=:a"),{'a':account_id}).mappings().first()
    packet=(launch or {}).get('packet') or {}
    products=set(packet.get('products') or [])
    module_product={
      'CRM':'platform','MOP':'mop','ROP':'rop','Campaign / Ads':'feed',
      'AI Marketing':'autopilot','Reactivation':'reactivation','Social':'social',
      'Sitebuild':'sitebuild','Analytics':'reports','Integrations':'platform','Billing / tariff':'platform',
    }
    modules=[]
    for name,product in module_product.items():
        available=product in products
        modules.append({'module':name,'product':product,'status':'AVAILABLE' if available else 'MISSING','readiness':'READY' if available else 'OWNER_ACTION','auto_enabled':False})
    return {
        'products':sorted(products),'modules':modules,
        'factory': dict(launch) if launch else None,
        'provisioning': dict(provisioning) if provisioning else None,
        'auto_enable_performed': False,
    }


def lifecycle_projection(account_id: str):
    db=SessionLocal()
    try:
        launch=classify(db,account_id)
        state=_lifecycle_row(db,account_id)
        matrix=_module_matrix(db,account_id)
        db.commit()
        if not state:
            status=launch.get('status')
            return {
                'account_id':account_id,'status':status,'started':False,
                'steps_total':len(_SAFE_STEPS),'steps_done':0,'working_now':None,
                'completed_steps':[],
                'waiting_reason':launch.get('human_message') if status != 'READY' else None,
                'owner_action_required': (launch.get('owner_items') or [None])[0],
                'result':None,
                'next_action':'Запустить клиента' if status=='READY' else launch.get('human_message'),
                'last_error':None,'retry_count':0,'next_retry_at':None,
                'module_readiness':matrix,
            }
        state['started']=True
        state['module_readiness']=matrix
        return state
    finally:
        db.close()


def _persist_lifecycle(account_id, **values):
    db=SessionLocal()
    try:
        _ensure_lifecycle(db)
        existing=_lifecycle_row(db,account_id)
        base={
            'status':'INITIALIZING','current_step':'factory','steps_total':len(_SAFE_STEPS),'steps_done':0,
            'working_now':'BORIS начинает внутреннюю подготовку','completed_steps':[],
            'waiting_reason':None,'owner_action_required':None,'result':None,'next_action':'BORIS продолжит автоматически',
            'last_error':None,'retry_count':0,'next_retry_at':None,
        }
        if existing:
            base.update({k:existing.get(k) for k in base})
        base.update(values)
        for k in ('completed_steps','result'):
            if not isinstance(base.get(k),str): base[k]=json.dumps(base.get(k),ensure_ascii=False,default=str)
        db.execute(text("""
          INSERT INTO factory_launch_lifecycle_state
          (account_id,status,current_step,steps_total,steps_done,working_now,completed_steps,waiting_reason,owner_action_required,result,next_action,last_error,retry_count,next_retry_at,started_at,updated_at)
          VALUES (:a,:status,:current_step,:steps_total,:steps_done,:working_now,CAST(:completed_steps AS JSONB),:waiting_reason,:owner_action_required,CAST(:result AS JSONB),:next_action,:last_error,:retry_count,:next_retry_at,NOW(),NOW())
          ON CONFLICT(account_id) DO UPDATE SET status=EXCLUDED.status,current_step=EXCLUDED.current_step,steps_total=EXCLUDED.steps_total,steps_done=EXCLUDED.steps_done,working_now=EXCLUDED.working_now,completed_steps=EXCLUDED.completed_steps,waiting_reason=EXCLUDED.waiting_reason,owner_action_required=EXCLUDED.owner_action_required,result=EXCLUDED.result,next_action=EXCLUDED.next_action,last_error=EXCLUDED.last_error,retry_count=EXCLUDED.retry_count,next_retry_at=EXCLUDED.next_retry_at,updated_at=NOW()
        """),{'a':account_id,**base})
        db.commit()
    finally: db.close()


@router.get('/{account_id}/lifecycle')
def get_lifecycle(account_id: str, user=Depends(get_current_user_or_internal)):
    account_id=normalize_account_id(account_id)
    return {'status':'ok','lifecycle':lifecycle_projection(account_id)}


# Preserve original endpoint function while adding persisted execution lifecycle.
_run_launch_original = run_launch


def _run_safe_lifecycle(body, user):
    account_id=normalize_account_id(body.account_id)
    before=classify_account(account_id)
    if before.get('status') != 'READY':
        raise HTTPException(status_code=409,detail={'message':'Клиент ещё не READY','launch':before})
    previous=lifecycle_projection(account_id)
    if previous.get('status') in {'PREPARED','ACTIVE'}:
        return {'status':'ok','mode':'resume','account_id':account_id,'before':before,'after':before,'lifecycle':previous,'factory':{'returncode':0,'idempotent':True}}
    if previous.get('status') == 'INITIALIZING':
        return {'status':'ok','mode':'resume','account_id':account_id,'before':before,'after':before,'lifecycle':previous,'factory':{'returncode':0,'idempotent':True,'in_progress':True}}
    if previous.get('status') == 'FAILED_RECOVERABLE' and previous.get('next_retry_at'):
        retry_at=previous['next_retry_at']
        if getattr(retry_at,'tzinfo',None) is not None:
            retry_at=retry_at.replace(tzinfo=None)
        if datetime.utcnow() < retry_at:
            return {'status':'ok','mode':'backoff','account_id':account_id,'before':before,'after':before,'lifecycle':previous,'factory':{'returncode':0,'idempotent':True,'backoff':True}}
    completed=[]
    # Persist each safe phase before the canonical account-scoped action. These are
    # projections of already-proven readiness facts; they create no external resource.
    for idx, (step_key, step_title) in enumerate(_SAFE_STEPS[:-1], start=1):
        completed.append({'key':step_key,'title':step_title})
        _persist_lifecycle(account_id,status='INITIALIZING',current_step=step_key,steps_done=idx,working_now=step_title,completed_steps=completed,next_action='BORIS продолжит автоматически')
    _persist_lifecycle(account_id,status='INITIALIZING',current_step='factory',steps_done=len(completed),working_now='Внутренняя подготовка Factory',completed_steps=completed,next_action='BORIS завершает безопасную внутреннюю подготовку')
    try:
        # Account-scoped canonical Factory action engine. READY accounts resolve to
        # an explicit NOOP; no global pipeline sweep and no external side effects.
        from app.factory.launch_action_engine import apply as _factory_apply
        factory_result=_factory_apply(account_id)
        after=classify_account(account_id)
        result={'status':'ok','mode':'apply','account_id':account_id,'before':before,'after':after,'factory':factory_result}
        completed.append({'key':_SAFE_STEPS[-1][0],'title':_SAFE_STEPS[-1][1]})
        if after.get('status')=='READY':
            status='PREPARED'; waiting=None; owner=None; next_action='Внутренняя подготовка завершена. Проверьте следующий бизнес-шаг перед внешними действиями.'
        elif after.get('reason')=='OWNER_REQUIRED':
            status='WAITING_OWNER'; waiting=after.get('human_message'); owner=(after.get('owner_items') or [{}])[0].get('title'); next_action=owner or waiting
        elif after.get('status')=='NEEDS_INPUT':
            status='WAITING_OWNER'; waiting=after.get('human_message'); owner=(after.get('questions') or [{}])[0].get('question_text'); next_action=owner or waiting
        else:
            status='WAITING_MODULE'; waiting=after.get('human_message'); owner=None; next_action=waiting
        _persist_lifecycle(account_id,status=status,current_step='complete' if status=='PREPARED' else 'waiting',steps_done=len(_SAFE_STEPS),working_now=None,completed_steps=completed,waiting_reason=waiting,owner_action_required=owner,result={'technical_launch_complete':True,'client_business_ready': status=='PREPARED','launch':after},next_action=next_action,last_error=None,next_retry_at=None)
        result['lifecycle']=lifecycle_projection(account_id)
        # A freshly completed PREPARED lifecycle immediately enters the canonical
        # safe post-prepared handoff. Historical PREPARED rows are not swept by the timer.
        if status=='PREPARED':
            result['handoff']=_execute_post_prepared(account_id)
            result['lifecycle']=lifecycle_projection(account_id)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        prior=lifecycle_projection(account_id); retry=int(prior.get('retry_count') or 0)+1
        delay=min(3600,60*(2**min(retry-1,5)))
        _persist_lifecycle(account_id,status='FAILED_RECOVERABLE',current_step='factory',working_now=None,waiting_reason='Внутренний шаг временно не завершён',owner_action_required=None,next_action='BORIS повторит внутреннюю подготовку автоматически после backoff.',last_error=type(exc).__name__+': '+str(exc)[:500],retry_count=retry,next_retry_at=datetime.utcnow()+timedelta(seconds=delay))
        raise


# Replace route endpoint in-place; do not register a duplicate route.
for _route in router.routes:
    if getattr(_route,'path',None) == '/api/factory/new-client-launch/run' and 'POST' in getattr(_route,'methods',set()):
        _route.endpoint = _run_safe_lifecycle
        _route.dependant.call = _run_safe_lifecycle
        break
# === BORIS_NEW_CLIENT_LIFECYCLE_PROJECTION_V1_END ===


# === BORIS_FACTORY_POST_PREPARED_HANDOFF_V1 ===
# Canonical extension of the existing account-scoped Factory lifecycle.
# Persists handoff inside factory_launch_lifecycle_state.result; no second engine/table/scheduler.
_POST_PHASES = {'PREPARED','HANDOFF_RUNNING','OWNER_REQUIRED','FAILED_HANDOFF_RECOVERABLE','OWNER_READY'}
_OWNER_ACTION_HINTS = {
    'Campaign / Ads': ('Подключите Avito', 'Подключите или подтвердите рабочую интеграцию Avito для публикации.'),
    'Social': ('Подключите соцсети', 'Подключите нужные социальные аккаунты и подтвердите доступ.'),
    'MOP': ('Настройте график ИИ-менеджера', 'Проверьте график и правила работы ИИ-менеджера.'),
    'ROP': ('Проверьте настройки РОП', 'Подтвердите рабочие настройки AI-руководителя.'),
    'Sitebuild': ('Подтвердите публикацию сайта', 'Публикация наружу выполняется только после решения владельца.'),
}

def _handoff_payload(state):
    result=(state or {}).get('result') or {}
    if isinstance(result,str):
        try: result=json.loads(result)
        except Exception: result={}
    return dict(result.get('post_prepared') or {})

def _owner_action(module, account_id, lifecycle_id):
    title, desc=_OWNER_ACTION_HINTS.get(module, (f'Настройте {module}', f'Завершите необходимое действие владельца для {module}.'))
    return {'id':f'{account_id}:{module.lower().replace(" ","_").replace("/","_")}', 'account_id':account_id,
            'module':module,'title':title,'human_description':desc,'reason':'OWNER_ACTION',
            'created_at':datetime.utcnow().isoformat(),'status':'PENDING','completed_at':None,
            'resume_target':'post_prepared_handoff','lifecycle_id':lifecycle_id}

def _canonical_owner_actions(db, account_id, lifecycle_id, prior):
    rows=db.execute(text("""
        SELECT attention_key, issue_type, title, detail, status, created_at, resolved_at
        FROM factory_attention_state
        WHERE account_id=:a AND route='OWNER_REQUIRED'
        ORDER BY priority DESC, attention_key
    """),{'a':account_id}).mappings().all()
    previous={x.get('id'):x for x in (prior.get('owner_required_actions') or []) if x.get('id')}
    result=[]
    for row in rows:
        action_id=f'attention:{row["attention_key"]}'
        status='COMPLETED' if row['status']!='OPEN' else 'PENDING'
        item={
            'id':action_id,'account_id':account_id,'module':'Factory','title':row['title'],
            'human_description':row['detail'],'reason':row['issue_type'] or 'OWNER_ACTION',
            'created_at':str(row['created_at'] or datetime.utcnow()),'status':status,
            'completed_at':str(row['resolved_at']) if row['resolved_at'] else None,
            'resume_target':'post_prepared_handoff','lifecycle_id':lifecycle_id,
            'attention_key':row['attention_key'],
        }
        old=previous.get(action_id)
        if old:
            item['created_at']=old.get('created_at') or item['created_at']
        result.append(item)
    return result


def _build_handoff(account_id, state, matrix, owner_actions=None):
    lifecycle_id=f'factory:{account_id}'
    prior=_handoff_payload(state)
    completed=list(prior.get('completed_actions') or [])
    available=[]; missing=[]; safe=[]; modules=[]
    for row in matrix.get('modules') or []:
        name=row.get('module'); purchased=row.get('status')=='AVAILABLE'
        if purchased:
            available.append(name)
            action={'id':f'{account_id}:ready:{name.lower().replace(" ","_").replace("/","_")}', 'module':name,
                    'title':f'Проверить готовность {name}','kind':'READINESS_CHECK','safe_internal':True}
            safe.append(action)
            modules.append({**row,'handoff_status':'READY'})
        else:
            missing.append(name)
            modules.append({**row,'handoff_status':'MISSING'})
    owners=list(owner_actions or [])
    now=datetime.utcnow().isoformat()
    return {'account_id':account_id,'lifecycle_id':lifecycle_id,'prepared_at':prior.get('prepared_at') or str((state or {}).get('updated_at') or now),
            'current_phase':'HANDOFF_RUNNING','available_modules':available,'missing_modules':missing,
            'module_handoff':modules,'safe_internal_actions':safe,'owner_required_actions':owners,
            'completed_actions':completed,'failed_actions':list(prior.get('failed_actions') or []),'current_action':None,
            'next_action':None,'started_at':prior.get('started_at') or now,'updated_at':now,'finished_at':None,'result':None}

def _persist_handoff(account_id, state, handoff, status, *, last_error=None, retry_count=None, next_retry_at=None):
    result=(state or {}).get('result') or {}
    if isinstance(result,str):
        try: result=json.loads(result)
        except Exception: result={}
    result=dict(result); result['post_prepared']=handoff
    _persist_lifecycle(account_id,status=status,current_step='post_prepared',working_now=handoff.get('current_action'),
        waiting_reason=(handoff.get('next_action') if status=='OWNER_REQUIRED' else None),
        owner_action_required=(handoff.get('next_action') if status=='OWNER_REQUIRED' else None),result=result,
        next_action=handoff.get('next_action') or ('Клиент готов к работе' if status=='OWNER_READY' else 'BORIS продолжит автоматически'),
        last_error=last_error,retry_count=(retry_count if retry_count is not None else int((state or {}).get('retry_count') or 0)),next_retry_at=next_retry_at)

def _execute_post_prepared(account_id):
    state=lifecycle_projection(account_id)
    if state.get('status') not in _POST_PHASES:
        raise HTTPException(status_code=409,detail={'message':'Сначала завершите безопасную подготовку до PREPARED','lifecycle':state})
    if state.get('status')=='OWNER_READY':
        return {'status':'ok','mode':'resume','account_id':account_id,'lifecycle':state,'idempotent':True}
    if state.get('status')=='FAILED_HANDOFF_RECOVERABLE' and state.get('next_retry_at'):
        retry_at=state['next_retry_at']; retry_at=retry_at.replace(tzinfo=None) if getattr(retry_at,'tzinfo',None) else retry_at
        if datetime.utcnow() < retry_at:
            return {'status':'ok','mode':'backoff','account_id':account_id,'lifecycle':state,'idempotent':True}
    db=SessionLocal()
    try:
        matrix=_module_matrix(db,account_id)
        owner_actions=_canonical_owner_actions(db,account_id,f'factory:{account_id}',_handoff_payload(state))
    finally:
        db.close()
    handoff=_build_handoff(account_id,state,matrix,owner_actions)
    try:
        done={x.get('id') for x in handoff['completed_actions']}
        for action in handoff['safe_internal_actions']:
            if action['id'] in done: continue
            handoff['current_action']=action['title']; handoff['updated_at']=datetime.utcnow().isoformat()
            _persist_handoff(account_id,state,handoff,'HANDOFF_RUNNING')
            # Safe activation is deliberately a readiness verification over canonical state.
            handoff['completed_actions'].append({**action,'completed_at':datetime.utcnow().isoformat(),'status':'DONE'})
            done.add(action['id'])
        pending=[x for x in handoff['owner_required_actions'] if x.get('status')!='COMPLETED']
        handoff['current_action']=None; handoff['updated_at']=datetime.utcnow().isoformat()
        if pending:
            handoff['current_phase']='OWNER_REQUIRED'; handoff['next_action']=pending[0]['title']
            _persist_handoff(account_id,state,handoff,'OWNER_REQUIRED')
        else:
            handoff['current_phase']='OWNER_READY'; handoff['next_action']='Клиент готов к работе'; handoff['finished_at']=datetime.utcnow().isoformat()
            handoff['result']={'owner_ready':True,'purchased_scope_ready':True,'external_effects':False}
            _persist_handoff(account_id,state,handoff,'OWNER_READY',last_error=None,next_retry_at=None)
        return {'status':'ok','mode':'handoff','account_id':account_id,'lifecycle':lifecycle_projection(account_id)}
    except HTTPException: raise
    except Exception as exc:
        retry=int(state.get('retry_count') or 0)+1; delay=min(3600,60*(2**min(retry-1,5)))
        handoff['current_phase']='FAILED_HANDOFF_RECOVERABLE'; handoff['failed_actions'].append({'action':handoff.get('current_action'),'error':type(exc).__name__+': '+str(exc)[:500],'at':datetime.utcnow().isoformat()})
        handoff['next_action']='BORIS повторит этот внутренний шаг автоматически после backoff.'
        _persist_handoff(account_id,state,handoff,'FAILED_HANDOFF_RECOVERABLE',last_error=type(exc).__name__+': '+str(exc)[:500],retry_count=retry,next_retry_at=datetime.utcnow()+timedelta(seconds=delay))
        raise


def auto_resume_post_prepared(limit: int = 100):
    db=SessionLocal()
    try:
        rows=db.execute(text("""
            SELECT account_id, status, next_retry_at
            FROM factory_launch_lifecycle_state s
            WHERE status IN ('OWNER_REQUIRED','FAILED_HANDOFF_RECOVERABLE','HANDOFF_RUNNING')
              AND NOT EXISTS (
                  SELECT 1 FROM reliability_kill_switches r
                   WHERE r.account_id=s.account_id AND r.blocked=true
                     AND r.module IN ('actions','background')
              )
            ORDER BY s.updated_at ASC
            LIMIT :limit
        """),{'limit':limit}).mappings().all()
    finally:
        db.close()
    stats={'checked':0,'advanced':0,'waiting_owner':0,'backoff':0,'errors':0}
    for row in rows:
        stats['checked'] += 1
        try:
            result=_execute_post_prepared(row['account_id'])
            lifecycle=result.get('lifecycle') or {}
            status=lifecycle.get('status')
            if result.get('mode')=='backoff': stats['backoff'] += 1
            elif status=='OWNER_REQUIRED': stats['waiting_owner'] += 1
            else: stats['advanced'] += 1
        except Exception:
            stats['errors'] += 1
    return stats

@router.post('/{account_id}/handoff')
def run_post_prepared_handoff(account_id: str, user=Depends(get_current_user_or_internal)):
    return _execute_post_prepared(normalize_account_id(account_id))

@router.post('/{account_id}/handoff/resume')
def resume_post_prepared_handoff(account_id: str, user=Depends(get_current_user_or_internal)):
    return _execute_post_prepared(normalize_account_id(account_id))
# === BORIS_FACTORY_POST_PREPARED_HANDOFF_V1_END ===
