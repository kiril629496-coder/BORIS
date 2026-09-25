# -*- coding: utf-8 -*-
"""BORIS Video Factory: cheap-first production planning and local rendering core."""
from __future__ import annotations
import json, os, re, shutil, subprocess, time, uuid
from pathlib import Path
from app.services.exception_observability import observe_suppressed

ROOT = Path(os.getenv("BORIS_VIDEO_ROOT", "/root/BORIS/data/video_factory"))
PROJECTS = ROOT / "projects"
UPLOADS = ROOT / "uploads"
OUTPUTS = ROOT / "outputs"
for p in (PROJECTS, UPLOADS, OUTPUTS): p.mkdir(parents=True, exist_ok=True)

FORMATS = {
    "shorts": {"width":1080,"height":1920,"fps":30,"label":"Shorts / Reels / Клипы"},
    "youtube": {"width":1920,"height":1080,"fps":30,"label":"YouTube / обучение"},
    "square": {"width":1080,"height":1080,"fps":30,"label":"Квадрат 1:1"},
    "feed": {"width":1080,"height":1350,"fps":30,"label":"Пост 4:5"},
}
MODES = {
    "materials": "Собрать из моих фото и видео",
    "from_scratch": "Создать рекламный ролик с нуля",
    "boris_tutorial": "Записать обучение по BORIS",
}
STYLES = {
    "premium":"Дорого и минималистично", "sales":"Продающий", "expert":"Экспертный", "dynamic":"Динамичный"
}

def _safe(v, n=120):
    v=re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ _.-]+","",str(v or "")).strip()
    return v[:n] or "video"

def _duration_seconds(duration):
    try: d=int(duration)
    except Exception: d=30
    return max(10,min(d,600))

def estimate_cost(mode="materials", duration=30, ai_seconds=0, voice="boris", generated_images=0):
    """Transparent USD estimate. Local ffmpeg/capture = marginal API cost 0."""
    duration=_duration_seconds(duration); ai_seconds=max(0,min(int(ai_seconds or 0),duration))
    # Conservative planning assumptions; actual usage gets written separately by providers.
    script=0.003 if duration <= 60 else 0.01
    tts = 0.0 if voice in ("none", "upload") else round(duration/60.0 * 0.015, 4)
    images = max(0,int(generated_images or 0)) * 0.04
    video = ai_seconds * 0.10
    total=round(script+tts+images+video,4)
    return {"currency":"USD","script":script,"tts_estimate":tts,"generated_images_estimate":round(images,4),
            "ai_video_estimate":round(video,4),"local_render":0.0,"local_capture":0.0,"total_estimate":total,
            "principle":"Сначала локальный монтаж. Платная AI-генерация видео только по явному запросу."}

def build_plan(payload):
    mode=payload.get("mode") if payload.get("mode") in MODES else "materials"
    fmt=payload.get("format") if payload.get("format") in FORMATS else ("youtube" if mode=="boris_tutorial" else "shorts")
    duration=_duration_seconds(payload.get("duration",30))
    prompt=(payload.get("prompt") or "").strip()
    style=payload.get("style") if payload.get("style") in STYLES else "premium"
    voice=payload.get("voice") or "boris"
    ai_seconds=max(0,min(int(payload.get("ai_seconds") or 0),duration))
    if mode != "from_scratch": ai_seconds=0
    hook=(prompt.split(".")[0].strip() if prompt else ("Показываем результат быстро и понятно" if mode!="boris_tutorial" else "Как решить задачу в BORIS за несколько шагов"))[:110]
    cta=(payload.get("cta") or ("Напишите — расскажем подробнее и поможем выбрать решение" if mode!="boris_tutorial" else "Откройте BORIS и повторите шаги на своём аккаунте"))[:140]
    scene_count=max(4,min(12,round(duration/5)))
    phrases=[x.strip(" -–—") for x in re.split(r"[.!?\n]+",prompt) if x.strip()]
    benefit_defaults=["Показываем результат", "Главное преимущество", "Почему это удобно", "Доказательство качества", "Что получает клиент", "Результат без лишних действий"]
    scenes=[]
    tutorial_steps=[
        "Покажем, где находится нужная функция",
        "Выберем действие в интерфейсе BORIS",
        "Заполним задачу обычными словами",
        "Проверим настройки перед запуском",
        "BORIS соберёт результат автоматически",
        "Готовый результат можно открыть и скачать",
    ]
    for i in range(scene_count):
        start=round(i*duration/scene_count,2); end=round((i+1)*duration/scene_count,2)
        role="hook" if i==0 else ("cta" if i==scene_count-1 else ("proof" if i%2==0 else "benefit"))
        if mode=="boris_tutorial":
            if i==0: caption=("Как выполнить задачу в BORIS: "+prompt[:72]) if prompt else "Показываем работу BORIS по шагам"
            elif role=="cta": caption="Повторите эти шаги в своём аккаунте BORIS"
            else: caption=tutorial_steps[min(i-1,len(tutorial_steps)-1)]
        elif i==0: caption=hook
        elif role=="cta": caption=cta
        else:
            source=phrases[i] if i < len(phrases) else benefit_defaults[(i-1)%len(benefit_defaults)]
            caption=source[:110]
        scenes.append({"index":i+1,"start":start,"end":end,"role":role,"caption":caption,
            "motion":"slow_push" if style in ("premium","expert") else "dynamic_cut"})
    plan={"mode":mode,"mode_label":MODES[mode],"format":fmt,"format_spec":FORMATS[fmt],"duration":duration,
            "style":style,"style_label":STYLES[style],"voice":voice,"prompt":prompt,"hook":hook,"cta":cta,
            "scenes":scenes,"ai_seconds":ai_seconds,"cost":estimate_cost(mode,duration,ai_seconds,voice,payload.get("generated_images",0)),
            "render_policy":{"local_first":True,"max_ai_video_share":0.2,"max_paid_cost_usd":0.10,"subtitles":True,"safe_zone":True,"brand_motion_system":True,"auto_visuals":mode=="from_scratch"}}
    from .producer import enrich_plan
    return enrich_plan(plan)

def create_project(payload):
    plan=build_plan(payload); pid="vf_"+time.strftime("%Y%m%d_%H%M%S")+"_"+uuid.uuid4().hex[:6]
    now=time.strftime("%Y-%m-%dT%H:%M:%S")
    visibility=payload.get("visibility") if payload.get("visibility") in ("client","internal","sample") else "client"
    data={"id":pid,"account_id":payload.get("account_id"),"owner_user_id":payload.get("owner_user_id"),"visibility":visibility,"title":_safe(payload.get("title") or plan["hook"]),"status":"draft","created_at":now,"updated_at":now,
          "plan":plan,"assets":[],"outputs":[],"usage":{"paid_calls":0,"actual_cost_usd":0.0}}
    (PROJECTS/f"{pid}.json").write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    return data

def load_project(pid):
    f=PROJECTS/f"{_safe(pid)}.json"
    if not f.exists(): return None
    return json.loads(f.read_text(encoding="utf-8"))

def save_project(data):
    data["updated_at"]=time.strftime("%Y-%m-%dT%H:%M:%S")
    target=PROJECTS/f"{data['id']}.json"
    tmp=target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    os.replace(tmp,target)

def list_projects(limit=50):
    out=[]
    for f in sorted(PROJECTS.glob("vf_*.json"), reverse=True)[:max(1,min(limit,200))]:
        try:
            d=json.loads(f.read_text(encoding="utf-8")); out.append({k:d.get(k) for k in ("id","account_id","owner_user_id","visibility","title","status","created_at","updated_at","outputs","usage")}|{"plan":d.get("plan")})
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=120)
    return out

def ffmpeg_available(): return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))

def capabilities():
    from .tutorial import readiness as tutorial_readiness
    tr=tutorial_readiness()
    return {"status":"ok","engine":"BORIS Video Factory","modes":MODES,"formats":FORMATS,"styles":STYLES,
            "ffmpeg":ffmpeg_available(),"local_render_ready":ffmpeg_available(),"browser_capture_core":tr.get("playwright",False),"tutorial_capture":tr,
            "paid_ai_video_default":False,"max_ai_video_share_default":0.2,
            "features":{"multi_scene_montage":True,"captions_ass":True,"motion_styles":4,"motion_templates":12,"audio_normalization":True,"queue_recovery":True,"versioning":True,"semantic_qa":True},
            "voice":{"boris":"OpenAI TTS","boris_tts_configured":bool(os.environ.get("OPENAI_API_KEY")),"none":"Без озвучки","upload":"Загруженная дорожка","owner_clone":"Профиль голоса владельца — подключается отдельно с явным согласием"},
            "privacy":"Обучающая запись BORIS должна проходить только через существующий demo/preflight isolation contour."}

def render_demo(pid):
    """Free local proof render: motion background + captions. No paid provider call."""
    data=load_project(pid)
    if not data: raise ValueError("project_not_found")
    if not ffmpeg_available(): raise RuntimeError("ffmpeg_not_installed")
    plan=data["plan"]; spec=plan["format_spec"]; w,h=spec["width"],spec["height"]; dur=min(plan["duration"],30)
    outdir=OUTPUTS/pid; outdir.mkdir(parents=True,exist_ok=True); out=outdir/"preview.mp4"
    font="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    hook=plan["hook"].replace("'","’").replace(":","\\:")[:80]; cta=plan["cta"].replace("'","’").replace(":","\\:")[:90]
    fs=max(36,round(w*0.045)); small=max(24,round(w*0.026))
    vf=(f"drawbox=x=0:y=0:w=iw:h=ih:color=0xF7F9FC:t=fill,"
        f"drawbox=x={int(w*.07)}:y={int(h*.12)}:w={int(w*.86)}:h={int(h*.76)}:color=white@0.96:t=fill,"
        f"drawtext=fontfile={font}:text='BORIS VIDEO':fontsize={small}:fontcolor=0x667085:x=(w-text_w)/2:y=h*0.19,"
        f"drawtext=fontfile={font}:text='{hook}':fontsize={fs}:fontcolor=0x101828:x=(w-text_w)/2:y=h*0.39,"
        f"drawtext=fontfile={font}:text='{cta}':fontsize={small}:fontcolor=0x344054:x=(w-text_w)/2:y=h*0.62")
    cmd=["ffmpeg","-y","-f","lavfi","-i",f"color=c=0xF7F9FC:s={w}x{h}:r=30:d={dur}","-vf",vf,"-c:v","libx264","-preset","veryfast","-pix_fmt","yuv420p","-movflags","+faststart",str(out)]
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=180)
    if p.returncode: raise RuntimeError((p.stderr or "ffmpeg failed")[-1200:])
    rel=f"/api/video-factory/media/{pid}/preview.mp4"; data["status"]="preview_ready"; data["outputs"]=[{"kind":"preview","url":rel,"path":str(out),"duration":dur}]; save_project(data)
    return data

# Production extensions: media analysis, real local montage, queue and cost ledger.
from .media import analyze_asset, mark_duplicates
from .costs import summary as cost_summary
from .jobs import configure as configure_jobs, enqueue as enqueue_render

def analyze_project_assets(pid):
    data=load_project(pid)
    if not data: raise ValueError('project_not_found')
    data['assets']=mark_duplicates([analyze_asset(a) for a in data.get('assets',[])])
    data['assets'].sort(key=lambda a:a.get('media_score',0),reverse=True)
    data['media_analysis']={'count':len(data['assets']),'best_score':max([a.get('media_score',0) for a in data['assets']] or [0]),'duplicates':sum(1 for a in data['assets'] if a.get('duplicate_of')),'low_quality':sum(1 for a in data['assets'] if a.get('media_score',0)<45)}
    save_project(data); return data

def project_costs(pid): return cost_summary(PROJECTS,pid)
def queue_render(pid,kind='preview'): return enqueue_render(pid,kind)
configure_jobs(PROJECTS,OUTPUTS,load_project,save_project)

VERSIONS = ROOT / "versions"
VERSIONS.mkdir(parents=True, exist_ok=True)

def _snapshot_project(data, reason='edit'):
    folder=VERSIONS/data['id']; folder.mkdir(parents=True,exist_ok=True)
    existing=sorted(folder.glob('v*.json')); no=len(existing)+1
    snap=dict(data); snap['version_meta']={'version':no,'reason':reason,'saved_at':time.strftime('%Y-%m-%dT%H:%M:%S')}
    path=folder/f'v{no:03d}.json'; path.write_text(json.dumps(snap,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'version':no,'path':str(path),'reason':reason,'saved_at':snap['version_meta']['saved_at']}

def list_versions(pid):
    folder=VERSIONS/_safe(pid); out=[]
    if not folder.exists(): return out
    for p in sorted(folder.glob('v*.json'),reverse=True):
        try:
            d=json.loads(p.read_text(encoding='utf-8')); out.append(d.get('version_meta') or {'version':int(p.stem[1:])})
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=189)
    return out

def update_project(pid, patch):
    data=load_project(pid)
    if not data: raise ValueError('project_not_found')
    _snapshot_project(data,'edit')
    base=data.get('plan') or {}; payload={
        'mode':patch.get('mode',base.get('mode','materials')),
        'format':patch.get('format',base.get('format','shorts')),
        'duration':patch.get('duration',base.get('duration',30)),
        'prompt':patch.get('prompt',base.get('prompt','')),
        'style':patch.get('style',base.get('style','premium')),
        'voice':patch.get('voice',base.get('voice','boris')),
        'cta':patch.get('cta',base.get('cta','')),
        'ai_seconds':patch.get('ai_seconds',base.get('ai_seconds',0)),
    }
    instruction=(patch.get('instruction') or '').strip()
    if instruction:
        low=instruction.lower(); data.setdefault('revision_notes',[]).append({'at':time.strftime('%Y-%m-%dT%H:%M:%S'),'instruction':instruction})
        if 'без голоса' in low or 'убери голос' in low: payload['voice']='none'
        if 'динамич' in low: payload['style']='dynamic'
        if 'дорог' in low or 'преми' in low: payload['style']='premium'
        if 'эксперт' in low or 'спокойн' in low: payload['style']='expert'
        if 'прода' in low: payload['style']='sales'
        if '9:16' in low or 'рилс' in low or 'reels' in low or 'shorts' in low: payload['format']='shorts'
        if '16:9' in low or 'youtube' in low or 'ютуб' in low: payload['format']='youtube'
    old_prompt=(base.get('prompt') or '').strip(); new_prompt=(payload.get('prompt') or '').strip(); old_mode=base.get('mode'); new_mode=payload.get('mode')
    data['account_id']=patch.get('account_id',data.get('account_id')); data['plan']=build_plan(payload); data['title']=_safe(patch.get('title') or data.get('title') or data['plan']['hook']); data['status']='draft'; data['job']=None
    if old_prompt!=new_prompt or old_mode!=new_mode or instruction:
        data['outputs']=[]; data.pop('qa',None); data.pop('semantic_qa',None); data.pop('visual_qa',None); data.pop('visual_rework',None); data.pop('auto_visuals',None)
        data['assets']=[a for a in data.get('assets',[]) if a.get('kind')!='generated_ai']
    save_project(data); return data

def restore_version(pid,version):
    current=load_project(pid)
    if not current: raise ValueError('project_not_found')
    path=VERSIONS/_safe(pid)/f'v{int(version):03d}.json'
    if not path.exists(): raise ValueError('version_not_found')
    _snapshot_project(current,'before_restore')
    old=json.loads(path.read_text(encoding='utf-8')); old.pop('version_meta',None); old['id']=pid; old['status']='draft'; old['job']=None; save_project(old); return old


def capture_tutorial_project(pid):
    from .tutorial import capture
    data=load_project(pid)
    if not data: raise ValueError('project_not_found')
    if (data.get('plan') or {}).get('mode')!='boris_tutorial': raise ValueError('not_tutorial_project')
    result=capture(data, OUTPUTS/pid)
    path=Path(result['path'])
    asset=analyze_asset({'name':path.name,'original_name':path.name,'kind':'tutorial_capture','size':path.stat().st_size,'path':str(path)})
    data['assets']=[a for a in data.get('assets',[]) if a.get('kind')!='tutorial_capture']+[asset]; data['tutorial_capture']=result; data['status']='draft'; save_project(data)
    return data

from .batch import create_batch as _create_batch, load_batch as _load_batch, list_batches as _list_batches, status as _batch_status

def create_batch(payload,count=10): return _create_batch(ROOT,payload,count,create_project)
def load_batch(batch_id): return _load_batch(ROOT,batch_id)
def list_batches(limit=30): return _list_batches(ROOT,limit)
def batch_status(batch_id): return _batch_status(ROOT,batch_id,load_project)
def start_batch(batch_id,kind='preview'):
    b=load_batch(batch_id)
    if not b: raise ValueError('batch_not_found')
    jobs=[]
    for item in b.get('projects',[]): jobs.append(queue_render(item['id'],kind))
    return {'batch_id':batch_id,'queued':len(jobs),'jobs':jobs}

def attach_asset_to_batch(batch_id, asset):
    b=load_batch(batch_id)
    if not b: raise ValueError('batch_not_found')
    attached=0
    for item in b.get('projects',[]):
        d=load_project(item['id'])
        if not d: continue
        copy=dict(asset); copy['batch_shared']=True; d.setdefault('assets',[]).append(copy); save_project(d); attached+=1
    return {'batch_id':batch_id,'attached_projects':attached,'asset':asset}


def generate_project_stills(pid,count=1):
    from .ai_stills import generate_missing_stills
    data=load_project(pid)
    if not data: raise ValueError('project_not_found')
    if (data.get('plan') or {}).get('mode')!='from_scratch': raise ValueError('not_from_scratch')
    assets=generate_missing_stills(data,PROJECTS,OUTPUTS,count)
    data.setdefault('assets',[]).extend(assets); data['usage']=project_costs(pid); data['status']='draft'; save_project(data); return data

def clone_project(pid):
    data=load_project(pid)
    if not data: raise ValueError('project_not_found')
    plan=data.get('plan') or {}; styles=['premium','dynamic','sales','expert']
    cur=plan.get('style','premium'); next_style=styles[(styles.index(cur)+1)%len(styles)] if cur in styles else 'premium'
    payload={
        'owner_user_id':data.get('owner_user_id'),'account_id':data.get('account_id'),
        'mode':plan.get('mode','materials'),'format':plan.get('format','shorts'),'duration':plan.get('duration',30),
        'prompt':plan.get('prompt',''),'style':next_style,'voice':plan.get('voice','none'),'cta':plan.get('cta',''),
        'title':(data.get('title') or 'Видео')+' · вариант',
    }
    new=create_project(payload); new['assets']=[dict(a) for a in data.get('assets',[])]; new['source_project_id']=pid; save_project(new); return new


def finalize_academy_master(pid):
    """Queue the canonical BORIS Academy master through the normal worker path."""
    data=load_project(pid)
    if not data: raise ValueError('project_not_found')
    plan=data.get('plan') or {}
    if plan.get('mode')!='boris_tutorial': raise ValueError('not_tutorial_project')
    captures=[a for a in data.get('assets',[]) if a.get('kind')=='tutorial_capture' and Path(a.get('path','')).exists()]
    if not captures: raise RuntimeError('tutorial_capture_required')
    if not any((s.get('narration') or '').strip() for s in plan.get('scenes',[])):
        raise RuntimeError('tutorial_narration_required')
    plan['voice']='boris'; data['plan']=plan; data['status']='draft'; data['job']=None; save_project(data)
    return queue_render(pid,'final')
