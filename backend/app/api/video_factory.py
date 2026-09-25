# -*- coding: utf-8 -*-
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.db.session import SessionLocal
from app.services.command_policy import account_visible
from app.video_factory.service import (
    capabilities, build_plan, create_project, list_projects, load_project, save_project,
    UPLOADS, OUTPUTS, analyze_project_assets, project_costs, queue_render,
    update_project, list_versions, restore_version, capture_tutorial_project, finalize_academy_master,
    generate_project_stills, create_batch, load_batch, list_batches, batch_status,
    start_batch, attach_asset_to_batch, clone_project,
)
from app.video_factory.media import analyze_asset
from app.video_factory.jobs import queue_state
from app.video_factory.tutorial import readiness as tutorial_readiness

router=APIRouter(prefix='/api/video-factory',tags=['video-factory'])

def _owned(payload:dict,user):
    d=dict(payload); d['owner_user_id']=getattr(user,'id',None); return d

def _guard_project(project_id:str,user):
    d=load_project(project_id)
    if not d: raise HTTPException(404,'Проект не найден')
    if getattr(user,'role',None)!='owner' and d.get('owner_user_id')!=getattr(user,'id',None): raise HTTPException(403,'Нет доступа к проекту')
    return d

def _guard_batch(batch_id:str,user):
    d=load_batch(batch_id)
    if not d: raise HTTPException(404,'Серия не найдена')
    if getattr(user,'role',None)!='owner' and d.get('owner_user_id')!=getattr(user,'id',None): raise HTTPException(403,'Нет доступа к серии')
    return d
ALLOWED_EXT={'.mp4','.mov','.webm','.m4v','.jpg','.jpeg','.png','.webp','.mp3','.wav','.m4a','.aac'}
MAX_UPLOAD=500*1024*1024

class PlanIn(BaseModel):
    account_id:str|None=None
    mode:str='materials'; format:str='shorts'; duration:int=30; prompt:str=''; title:str=''
    style:str='premium'; voice:str='boris'; cta:str=''; ai_seconds:int=0; generated_images:int=0
class BatchIn(PlanIn): count:int=10
class PatchIn(BaseModel):
    instruction:str=''; mode:str|None=None; format:str|None=None; duration:int|None=None
    prompt:str|None=None; title:str|None=None; style:str|None=None; voice:str|None=None
    cta:str|None=None; ai_seconds:int|None=None

def _access(data,user):
    if not data:return False
    uid=getattr(user,'id',None)
    if data.get('owner_user_id') and data.get('owner_user_id')==uid:return True
    if getattr(user,'role',None)=='owner':return True
    account_id=data.get('account_id')
    if not account_id:return False
    db=SessionLocal()
    try:return bool(account_visible(db,user,account_id))
    finally:db.close()

def _project(project_id,user):
    d=load_project(project_id)
    if not d:raise HTTPException(404,'Проект не найден')
    if not _access(d,user):raise HTTPException(403,'Нет доступа к проекту')
    return d

def _batch(batch_id,user):
    d=load_batch(batch_id)
    if not d:raise HTTPException(404,'Серия не найдена')
    if not _access(d,user):raise HTTPException(403,'Нет доступа к серии')
    return d

async def _save_upload(file:UploadFile,folder:Path,name:str):
    ext=Path(file.filename or 'asset.bin').suffix.lower()
    if ext not in ALLOWED_EXT:raise HTTPException(400,'Неподдерживаемый формат')
    folder.mkdir(parents=True,exist_ok=True);dest=folder/name;size=0
    with dest.open('wb') as fh:
        while chunk:=await file.read(1024*1024):
            size+=len(chunk)
            if size>MAX_UPLOAD:
                dest.unlink(missing_ok=True);raise HTTPException(413,'Файл больше 500 МБ')
            fh.write(chunk)
    return dest,size

@router.get('/capabilities')
def caps(user=Depends(get_current_user)): return capabilities()
@router.post('/estimate')
def estimate(body:PlanIn,user=Depends(get_current_user)): return build_plan(body.model_dump())
@router.get('/queue')
def queue_info(user=Depends(get_current_user)): return queue_state()
@router.get('/tutorial/readiness')
def tutorial_ready(user=Depends(get_current_user)): return tutorial_readiness()

@router.post('/projects')
def create(body:PlanIn,user=Depends(get_current_user)):
    payload=body.model_dump();payload['owner_user_id']=getattr(user,'id',None)
    return create_project(payload)
@router.get('/projects')
def projects(limit:int=50,account_id:str|None=None,user=Depends(get_current_user)):
    items=[]
    for x in list_projects(limit):
        full=load_project(x.get('id'))
        if not (_access(full,user) and full and full.get('status')=='ready' and full.get('outputs') and (full.get('semantic_qa') or {}).get('pass')): continue
        visibility=full.get('visibility') or ('internal' if full.get('internal_test') else 'internal')
        if visibility=='internal': continue
        if visibility=='sample':
            items.append(x); continue
        if account_id and full.get('account_id')!=account_id: continue
        if not account_id and full.get('account_id') and full.get('account_id')!='boris_video_demo': continue
        items.append(x)
    return {'items':items}
@router.get('/projects/{project_id}')
def project(project_id:str,user=Depends(get_current_user)):
    d=_project(project_id,user);d['usage']=project_costs(project_id);return d
@router.patch('/projects/{project_id}')
def patch_project(project_id:str,body:PatchIn,user=Depends(get_current_user)):
    _project(project_id,user)
    try:return update_project(project_id,{k:v for k,v in body.model_dump().items() if v is not None})
    except ValueError:raise HTTPException(404,'Проект не найден')

@router.post('/projects/{project_id}/clone')
def clone(project_id:str,user=Depends(get_current_user)):
    _project(project_id,user)
    try:return clone_project(project_id)
    except ValueError:raise HTTPException(404,'Проект не найден')

@router.get('/projects/{project_id}/costs')
def costs(project_id:str,user=Depends(get_current_user)):
    _project(project_id,user);return project_costs(project_id)
@router.get('/projects/{project_id}/versions')
def versions(project_id:str,user=Depends(get_current_user)):
    _project(project_id,user);return {'items':list_versions(project_id)}
@router.post('/projects/{project_id}/versions/{version}/restore')
def restore(project_id:str,version:int,user=Depends(get_current_user)):
    _project(project_id,user)
    try:return restore_version(project_id,version)
    except ValueError as e:raise HTTPException(404,'Версия не найдена' if str(e)=='version_not_found' else 'Проект не найден')

@router.post('/projects/{project_id}/assets')
async def upload_asset(project_id:str,file:UploadFile=File(...),kind:str=Form('source'),user=Depends(get_current_user)):
    d=_project(project_id,user);name=f"{len(d.get('assets',[]))+1:03d}_{Path(file.filename or 'asset').name}"
    dest,size=await _save_upload(file,UPLOADS/project_id,name)
    asset=analyze_asset({'name':name,'original_name':file.filename,'kind':kind,'size':size,'path':str(dest)})
    d.setdefault('assets',[]).append(asset);save_project(d);return asset
@router.post('/projects/{project_id}/analyze')
def analyze(project_id:str,user=Depends(get_current_user)):
    _project(project_id,user)
    try:return analyze_project_assets(project_id)
    except ValueError:raise HTTPException(404,'Проект не найден')
@router.post('/projects/{project_id}/render-preview')
def render_preview(project_id:str,user=Depends(get_current_user)):
    _project(project_id,user)
    try:return {'job':queue_render(project_id,'preview')}
    except ValueError:raise HTTPException(404,'Проект не найден')
    except Exception as e:raise HTTPException(503,str(e))
@router.post('/projects/{project_id}/render')
def render_final(project_id:str,user=Depends(get_current_user)):
    _project(project_id,user)
    try:return {'job':queue_render(project_id,'final')}
    except ValueError:raise HTTPException(404,'Проект не найден')
    except Exception as e:raise HTTPException(503,str(e))
@router.post('/projects/{project_id}/finalize-academy')
def finalize_academy(project_id:str,user=Depends(get_current_user)):
    _project(project_id,user)
    try:return {'job':finalize_academy_master(project_id)}
    except ValueError as e:
        if str(e)=='not_tutorial_project':raise HTTPException(400,'Это не проект обучения BORIS')
        raise HTTPException(404,'Проект не найден')
    except RuntimeError as e:raise HTTPException(409,str(e))

@router.post('/projects/{project_id}/generate-stills')
def generate_stills(project_id:str,count:int=1,user=Depends(get_current_user)):
    _project(project_id,user)
    try:return generate_project_stills(project_id,count)
    except ValueError as e:
        if str(e)=='not_from_scratch':raise HTTPException(400,'AI-кадры доступны для режима создания с нуля')
        raise HTTPException(404,'Проект не найден')
    except RuntimeError as e:raise HTTPException(409,str(e))
@router.post('/projects/{project_id}/capture-tutorial')
def capture_tutorial(project_id:str,user=Depends(get_current_user)):
    _project(project_id,user)
    try:return capture_tutorial_project(project_id)
    except ValueError as e:
        if str(e)=='not_tutorial_project':raise HTTPException(400,'Это не проект обучения BORIS')
        raise HTTPException(404,'Проект не найден')
    except RuntimeError as e:raise HTTPException(409,str(e))
@router.get('/media/{project_id}/{filename}')
def media(project_id:str,filename:str,download:bool=False,user=Depends(get_current_user)):
    d=_project(project_id,user);safe=Path(filename).name;path=OUTPUTS/project_id/safe
    if not path.exists() or path.suffix.lower() not in {'.mp4','.webm','.mp3','.wav','.m4a'}:raise HTTPException(404,'Файл не найден')
    allowed={Path(str(x.get('path') or '')).name for x in d.get('outputs',[]) if x.get('path')}
    allowed.update(Path(str(x.get('path') or '')).name for x in d.get('assets',[]) if x.get('path') and x.get('kind')=='tutorial_capture')
    if safe not in allowed:raise HTTPException(403,'Файл не принадлежит проекту')
    media_type='video/mp4' if path.suffix.lower()=='.mp4' else 'video/webm' if path.suffix.lower()=='.webm' else 'audio/mpeg'
    return FileResponse(path,media_type=media_type,filename=(safe if download else None),content_disposition_type=('attachment' if download else 'inline'))

@router.get('/projects/{project_id}/download')
def download_final(project_id:str,user=Depends(get_current_user)):
    d=_project(project_id,user)
    finals=[x for x in d.get('outputs',[]) if x.get('kind')=='final' and x.get('path')]
    if d.get('status')!='ready' or not (d.get('semantic_qa') or {}).get('pass') or not finals:raise HTTPException(409,'Готовое видео ещё не прошло проверку')
    path=Path(finals[-1]['path'])
    if not path.exists() or path.parent.resolve()!=(OUTPUTS/project_id).resolve():raise HTTPException(404,'Файл не найден')
    title='boris-video-'+project_id+'.mp4'
    return FileResponse(path,media_type='video/mp4',filename=title,content_disposition_type='attachment')

@router.post('/batches')
def new_batch(body:BatchIn,user=Depends(get_current_user)):
    payload=body.model_dump();payload['owner_user_id']=getattr(user,'id',None)
    return create_batch(payload,body.count)
@router.get('/batches')
def batches(limit:int=30,user=Depends(get_current_user)):
    return {'items':[x for x in list_batches(limit) if _access(x,user)]}
@router.get('/batches/{batch_id}')
def get_batch(batch_id:str,user=Depends(get_current_user)):
    _batch(batch_id,user);return batch_status(batch_id)
@router.post('/batches/{batch_id}/assets')
async def upload_batch_asset(batch_id:str,file:UploadFile=File(...),kind:str=Form('source'),user=Depends(get_current_user)):
    _batch(batch_id,user);name=f"shared_{Path(file.filename or 'asset').name}";dest,size=await _save_upload(file,UPLOADS/batch_id,name)
    asset=analyze_asset({'name':name,'original_name':file.filename,'kind':kind,'size':size,'path':str(dest)})
    return attach_asset_to_batch(batch_id,asset)
@router.post('/batches/{batch_id}/render-preview')
def render_batch_preview(batch_id:str,user=Depends(get_current_user)):
    _batch(batch_id,user)
    try:return start_batch(batch_id,'preview')
    except ValueError:raise HTTPException(404,'Серия не найдена')
@router.post('/batches/{batch_id}/render')
def render_batch_final(batch_id:str,user=Depends(get_current_user)):
    _batch(batch_id,user)
    try:return start_batch(batch_id,'final')
    except ValueError:raise HTTPException(404,'Серия не найдена')
