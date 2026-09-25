# -*- coding: utf-8 -*-
from __future__ import annotations
import json,subprocess

def inspect_video(path,expected=None):
    r=subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],capture_output=True,text=True,timeout=30)
    if r.returncode: return {'pass':False,'checks':{},'error':(r.stderr or '')[-800:]}
    d=json.loads(r.stdout or '{}'); v=next((x for x in d.get('streams',[]) if x.get('codec_type')=='video'),{}); a=next((x for x in d.get('streams',[]) if x.get('codec_type')=='audio'),None)
    duration=float((d.get('format') or {}).get('duration') or 0); w=int(v.get('width') or 0); h=int(v.get('height') or 0); codec=v.get('codec_name'); pix=v.get('pix_fmt')
    checks={'has_video':bool(v),'duration_positive':duration>0.1,'h264':codec=='h264','yuv420p':pix=='yuv420p','dimensions_positive':w>0 and h>0,'has_audio':bool(a)}
    if expected:
        checks['width']=w==expected.get('width'); checks['height']=h==expected.get('height')
    required=[k for k in checks if k!='has_audio']
    return {'pass':all(checks[k] for k in required),'checks':checks,'width':w,'height':h,'duration':round(duration,3),'codec':codec,'pixel_format':pix,'has_audio':bool(a)}
