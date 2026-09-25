# -*- coding: utf-8 -*-
from __future__ import annotations
import json,math,subprocess,tempfile,hashlib
from pathlib import Path
from app.services.exception_observability import observe_suppressed

VIDEO_EXT={'.mp4','.mov','.webm','.m4v'}
IMAGE_EXT={'.jpg','.jpeg','.png','.webp'}
AUDIO_EXT={'.mp3','.wav','.m4a','.aac'}

def probe(path):
    p=Path(path); ext=p.suffix.lower(); kind='video' if ext in VIDEO_EXT else 'image' if ext in IMAGE_EXT else 'audio' if ext in AUDIO_EXT else 'unknown'
    out={'kind':kind,'extension':ext,'size':p.stat().st_size if p.exists() else 0,'width':None,'height':None,'duration':0.0,'fps':None,'has_audio':False,'orientation':None,'quality_score':0}
    if not p.exists(): return out
    r=subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(p)],capture_output=True,text=True,timeout=30)
    if r.returncode: out['probe_error']=(r.stderr or '')[-500:]; return out
    try:d=json.loads(r.stdout or '{}')
    except Exception:return out
    for s in d.get('streams',[]):
        if s.get('codec_type')=='video' and out['width'] is None:
            out['width']=int(s.get('width') or 0) or None; out['height']=int(s.get('height') or 0) or None
            rate=s.get('avg_frame_rate') or s.get('r_frame_rate') or '0/1'
            try:a,b=rate.split('/');out['fps']=round(float(a)/float(b),3) if float(b) else None
            except Exception as _suppressed_exc:observe_suppressed(__name__, _suppressed_exc, line=23)
            try:out['duration']=float(s.get('duration') or 0)
            except Exception as _suppressed_exc:observe_suppressed(__name__, _suppressed_exc, line=25)
        if s.get('codec_type')=='audio':out['has_audio']=True
    if not out['duration']:
        try:out['duration']=float((d.get('format') or {}).get('duration') or 0)
        except Exception as _suppressed_exc:observe_suppressed(__name__, _suppressed_exc, line=29)
    w,h=out['width'],out['height']
    if w and h:out['orientation']='portrait' if h>w else 'landscape' if w>h else 'square'
    px=(w or 0)*(h or 0);res_score=min(55,round(px/(1920*1080)*55)) if px else 20
    duration_score=25 if kind=='image' else min(25,round(max(0,out['duration'])/6*25)) if kind=='video' else 20
    size_score=min(20,round(math.log10(max(10,out['size']))*3));out['quality_score']=max(10,min(100,res_score+duration_score+size_score))
    return out

def _visual_frame(path,meta):
    p=Path(path);tmp=None
    try:
        from PIL import Image
        if meta.get('kind')=='image':return Image.open(p).convert('L')
        if meta.get('kind')=='video':
            tf=tempfile.NamedTemporaryFile(suffix='.jpg',delete=False);tf.close();tmp=Path(tf.name);t=max(.1,min(float(meta.get('duration') or 1)*.3,2.5))
            r=subprocess.run(['ffmpeg','-y','-ss',f'{t:.3f}','-i',str(p),'-frames:v','1','-vf','scale=360:-2',str(tmp)],capture_output=True,timeout=20)
            if r.returncode or not tmp.exists():return None
            return Image.open(tmp).convert('L').copy()
    except Exception:return None
    finally:
        if tmp:
            try:tmp.unlink(missing_ok=True)
            except Exception as _suppressed_exc:observe_suppressed(__name__, _suppressed_exc, line=51)

def _visual_metrics(path,meta):
    img=_visual_frame(path,meta)
    if img is None:return {}
    try:
        import numpy as np
        img.thumbnail((360,360));a=np.asarray(img,dtype='float32')
        brightness=float(a.mean());contrast=float(a.std());gx=np.abs(np.diff(a,axis=1,prepend=a[:,:1]));gy=np.abs(np.diff(a,axis=0,prepend=a[:1,:]));sharp=float((gx+gy).mean())
        small=np.asarray(img.resize((16,16)),dtype='float32');bits=(small>small.mean()).astype('uint8').flatten();packed=np.packbits(bits).tobytes().hex()
        visual_score=max(0,min(100,round(35+min(30,contrast*.7)+min(35,sharp*2.1)-max(0,abs(brightness-128)-80)*.25)))
        return {'brightness':round(brightness,1),'contrast':round(contrast,1),'sharpness':round(sharp,1),'visual_hash':packed,'visual_quality_score':visual_score}
    except Exception:return {}

def analyze_asset(asset):
    meta=probe(asset['path']);metrics=_visual_metrics(asset['path'],meta) if meta.get('kind') in ('video','image') else {}
    meta.update(metrics)
    base=meta.get('quality_score',0);v=meta.get('visual_quality_score')
    score=round(base*.55+v*.45) if v is not None else base
    return {**asset,'analysis':meta,'media_score':max(0,min(100,score))}

def mark_duplicates(assets):
    seen={};out=[]
    for a in assets:
        h=(a.get('analysis') or {}).get('visual_hash');dup=None
        if h:
            if h in seen:dup=seen[h]
            else:seen[h]=a.get('name')
        b=dict(a);b['duplicate_of']=dup
        if dup:b['media_score']=max(1,int(b.get('media_score',0)*.25))
        out.append(b)
    return out
