# -*- coding: utf-8 -*-
from __future__ import annotations
import functools,subprocess,tempfile
from pathlib import Path
from .media import probe,IMAGE_EXT,VIDEO_EXT
from app.services.exception_observability import observe_suppressed

@functools.lru_cache(maxsize=256)
def focal_point(path):
    """Cheap local saliency approximation. Returns normalized x/y; falls back to center."""
    p=Path(path); frame=None
    try:
        from PIL import Image
        import numpy as np
        if p.suffix.lower() in IMAGE_EXT:
            img=Image.open(p).convert('L')
        elif p.suffix.lower() in VIDEO_EXT:
            meta=probe(p); t=max(0.1,min(float(meta.get('duration') or 1)*0.35,3.0))
            tf=tempfile.NamedTemporaryFile(suffix='.jpg',delete=False); tf.close(); frame=Path(tf.name)
            r=subprocess.run(['ffmpeg','-y','-ss',f'{t:.3f}','-i',str(p),'-frames:v','1','-vf','scale=360:-2',str(frame)],capture_output=True,timeout=20)
            if r.returncode or not frame.exists(): return (0.5,0.5)
            img=Image.open(frame).convert('L')
        else:return (0.5,0.5)
        img.thumbnail((360,360)); a=np.asarray(img,dtype='float32')
        if a.size<100:return (0.5,0.5)
        gx=np.abs(np.diff(a,axis=1,prepend=a[:,:1])); gy=np.abs(np.diff(a,axis=0,prepend=a[:1,:])); e=gx+gy
        # mild center prior prevents locking onto borders/watermarks.
        yy,xx=np.mgrid[0:e.shape[0],0:e.shape[1]]; cx=(e.shape[1]-1)/2; cy=(e.shape[0]-1)/2
        prior=0.65+0.35*(1-(((xx-cx)/max(1,cx))**2+((yy-cy)/max(1,cy))**2)/2)
        e=e*prior
        total=float(e.sum())
        if total<=0:return (0.5,0.5)
        fx=float((e*xx).sum()/total/max(1,e.shape[1]-1)); fy=float((e*yy).sum()/total/max(1,e.shape[0]-1))
        return (max(.12,min(.88,fx)),max(.12,min(.88,fy)))
    except Exception:return (0.5,0.5)
    finally:
        if frame:
            try:frame.unlink(missing_ok=True)
            except Exception as _suppressed_exc:observe_suppressed(__name__, _suppressed_exc, line=38)

def crop_filter(path,target_w,target_h):
    meta=probe(path); sw=int(meta.get('width') or 0); sh=int(meta.get('height') or 0)
    if not sw or not sh:return f'scale={target_w}:{target_h}:force_original_aspect_ratio=increase,crop={target_w}:{target_h}'
    fx,fy=focal_point(str(path)); target=target_w/target_h; src=sw/sh
    if src>target:
        cw=max(2,int(sh*target)//2*2); ch=sh; x=max(0,min(sw-cw,int(fx*sw-cw/2))); y=0
    else:
        cw=sw; ch=max(2,int(sw/target)//2*2); x=0; y=max(0,min(sh-ch,int(fy*sh-ch/2)))
    return f'crop={cw}:{ch}:{x}:{y},scale={target_w}:{target_h}'
