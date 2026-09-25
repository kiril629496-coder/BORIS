# -*- coding: utf-8 -*-
from __future__ import annotations
import json,os,queue,threading,time,uuid
from pathlib import Path
from .renderer import render_project
from .costs import append as cost_append,summary as cost_summary
from .semantic_qa import score as semantic_score
from .voiceover import generate_boris_voice

_q=queue.Queue(maxsize=100); _started=False; _lock=threading.Lock(); _projects=None; _outputs=None; _load=None; _save=None
ACTIVE={'queued','rendering','qa'}
WORKER_COUNT=max(1,min(int(os.environ.get('BORIS_VIDEO_WORKERS','2') or 2),4))
_workers=[]

def configure(projects,outputs,load,save):
    global _projects,_outputs,_load,_save
    _projects,_outputs,_load,_save=projects,outputs,load,save;start();recover_pending()
def start():
    global _started
    with _lock:
        if _started:return
        for i in range(WORKER_COUNT):
            t=threading.Thread(target=_worker,name=f'boris-video-worker-{i+1}',daemon=True);t.start();_workers.append(t)
        _started=True

def _new_job(pid,kind='preview',recovered=False):
    return {'id':'vfj_'+uuid.uuid4().hex[:10],'project_id':pid,'kind':kind,'status':'queued','progress':0,'recovered':bool(recovered),'created_at':time.strftime('%Y-%m-%dT%H:%M:%S')}

def enqueue(pid,kind='preview'):
    d=_load(pid)
    if not d:raise ValueError('project_not_found')
    existing=d.get('job') or {}
    if existing.get('status') in ACTIVE and existing.get('kind')==kind:return existing
    if _q.full():raise RuntimeError('video_queue_full')
    job=_new_job(pid,kind);d['job']=job;d['status']='queued';_save(d);_q.put_nowait(job);return job

def recover_pending():
    if not _projects:return 0
    recovered=0
    for f in Path(_projects).glob('vf_*.json'):
        try:d=json.loads(f.read_text(encoding='utf-8'))
        except Exception:continue
        if d.get('status') not in ACTIVE:continue
        old=d.get('job') or {};job=_new_job(d.get('id'),old.get('kind') or 'preview',True)
        d['job']=job;d['status']='queued';_save(d)
        try:_q.put_nowait(job);recovered+=1
        except queue.Full:break
    return recovered

def queue_state():return {'queued':_q.qsize(),'capacity':_q.maxsize,'worker_started':_started,'workers':WORKER_COUNT}

def _worker():
    while True:
        job=_q.get();pid=job['project_id']
        try:
            d=_load(pid);d.pop('render_error',None);d.pop('qa',None);d.pop('semantic_qa',None);job.update(status='rendering',progress=15,started_at=time.strftime('%Y-%m-%dT%H:%M:%S'));d['job']=job;d['status']='rendering';_save(d)
            plan=d.get('plan') or {}; mode=plan.get('mode')
            visual=[a for a in d.get('assets',[]) if (a.get('analysis') or {}).get('kind') in ('video','image')]
            if mode=='from_scratch' and not visual:
                job.update(progress=25,status='creating_visuals');d['job']=job;d['status']='creating_visuals';_save(d)
                from .ai_stills import generate_missing_stills
                target=max(3,min(4,len(plan.get('scenes') or []) or 4))
                made=generate_missing_stills(d,_projects,_outputs,target,quality='medium' if job['kind']=='final' else 'low')
                d.setdefault('assets',[]).extend(made);d['auto_visuals']={'generated':len(made),'target':target,'at':time.strftime('%Y-%m-%dT%H:%M:%S')};_save(d)
            elif mode=='materials' and not visual:
                raise RuntimeError('client_visuals_required')
            elif mode=='boris_tutorial' and not any(a.get('kind')=='tutorial_capture' for a in d.get('assets',[])):
                raise RuntimeError('tutorial_capture_required')
            if mode=='from_scratch':
                generated=[a for a in d.get('assets',[]) if a.get('kind')=='generated_ai' and (a.get('analysis') or {}).get('kind')=='image']
                if job['kind']=='final' and generated and any(a.get('generation_quality')!='medium' for a in generated):
                    job.update(progress=28,status='upgrading_visuals');d['job']=job;d['status']='upgrading_visuals';_save(d)
                    from .ai_stills import generate_missing_stills
                    scene_ids=sorted({int(a.get('scene_index') or 0) for a in generated if int(a.get('scene_index') or 0)>0})[:4]
                    upgraded=generate_missing_stills(d,_projects,_outputs,len(scene_ids) or min(4,len(plan.get('scenes') or [])),scene_indexes=scene_ids or None,quality='medium')
                    replaced={int(a.get('scene_index') or 0) for a in upgraded};d['assets']=[a for a in d.get('assets',[]) if not (a.get('kind')=='generated_ai' and int(a.get('scene_index') or 0) in replaced)]+upgraded
                    d.pop('visual_qa',None);d['visual_upgrade']={'quality':'medium','scenes':sorted(replaced),'at':time.strftime('%Y-%m-%dT%H:%M:%S')};_save(d)
                    generated=[a for a in d.get('assets',[]) if a.get('kind')=='generated_ai' and (a.get('analysis') or {}).get('kind')=='image']
                if generated and not (d.get('visual_qa') or {}).get('pass'):
                    job.update(progress=32,status='visual_qa');d['job']=job;d['status']='visual_qa';_save(d)
                    from .visual_qa import audit as audit_visuals
                    verdict=audit_visuals(d,generated[:4],_projects);d['visual_qa']=verdict;_save(d)
                    if not verdict.get('pass'):
                        bad=[int(x.get('scene_index') or 0) for x in verdict.get('items',[]) if not x.get('pass') and int(x.get('scene_index') or 0)>0]
                        if bad:
                            job.update(progress=35,status='reworking_visuals');d['job']=job;d['status']='reworking_visuals';_save(d)
                            from .ai_stills import generate_missing_stills
                            replacements=generate_missing_stills(d,_projects,_outputs,len(bad),scene_indexes=bad,quality='medium' if job['kind']=='final' else 'low')
                            badset=set(bad);d['assets']=[a for a in d.get('assets',[]) if not (a.get('kind')=='generated_ai' and int(a.get('scene_index') or 0) in badset)]+replacements
                            d['visual_rework']={'scenes':bad,'generated':len(replacements),'at':time.strftime('%Y-%m-%dT%H:%M:%S')};_save(d)
                            generated=[a for a in d.get('assets',[]) if a.get('kind')=='generated_ai' and (a.get('analysis') or {}).get('kind')=='image']
                            verdict=audit_visuals(d,generated[:4],_projects);d['visual_qa']=verdict;_save(d)
                        if not verdict.get('pass'): raise RuntimeError('visual_quality_failed')
            job.update(progress=40,status='rendering');d['job']=job;d['status']='rendering';_save(d)
            audio_override=None
            if job['kind']=='final' and (d.get('plan') or {}).get('voice')=='boris':
                max_cost=float(((d.get('plan') or {}).get('render_policy') or {}).get('max_paid_cost_usd',0.10));audio_override,_=generate_boris_voice(d,_projects,_outputs,max_cost)
            out,qa=render_project(d,_outputs,job['kind'],audio_override=audio_override);job.update(progress=90,status='qa');d['job']=job;d['status']='qa';_save(d)
            cost_append(_projects,pid,'local','ffmpeg','render',1,'render',0.0,{'kind':job['kind']})
            rel=f"/api/video-factory/media/{pid}/{out.name}";d.setdefault('outputs',[]);d['outputs']=[x for x in d['outputs'] if x.get('kind')!=job['kind']]+[{'kind':job['kind'],'url':rel,'path':str(out),'qa':qa}]
            d['qa']=qa;d['semantic_qa']=semantic_score(d,qa);d['usage']=cost_summary(_projects,pid)
            client_pass=bool(qa.get('pass')) and bool((d.get('semantic_qa') or {}).get('pass'))
            job.update(progress=100,status='ready' if client_pass else 'qa_failed',finished_at=time.strftime('%Y-%m-%dT%H:%M:%S'));d['job']=job;d['status']='ready' if client_pass else 'qa_failed';_save(d)
        except Exception as e:
            d=_load(pid) or {'id':pid};job.update(status='failed',error=str(e),progress=100);d['job']=job;d['status']='failed';d['render_error']=str(e);_save(d)
        finally:_q.task_done()
