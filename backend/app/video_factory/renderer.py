# -*- coding: utf-8 -*-
from __future__ import annotations
import shutil,subprocess,tempfile
from pathlib import Path
from .media import IMAGE_EXT,VIDEO_EXT,AUDIO_EXT
from .qa import inspect_video
from .captions import write_ass
from .motion import overlay_filter,template_for_scene,scene_filter
from .audio import audio_filter,mix_filter
from .focal import crop_filter

FONT='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'

def esc(s): return str(s or '').replace('\\','\\\\').replace("'","’").replace(':','\\:').replace('%','\\%')
def run(cmd,timeout=900):
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout)
    if p.returncode: raise RuntimeError((p.stderr or 'ffmpeg failed')[-2200:])
    return p

def _render_segment(source,out,duration,w,h,fps,index=0,style="premium",role="proof"):
    ext=Path(source).suffix.lower() if source else ''
    if ext in IMAGE_EXT:
        zspeed=.00045+(index%4)*.00010
        args=['-loop','1','-t',f'{duration:.3f}','-i',str(source)]
        vf=crop_filter(source,w,h)+f",zoompan=z='min(zoom+{zspeed:.5f},1.09)':d={max(1,int(duration*fps))}:s={w}x{h}:fps={fps},setsar=1"
    elif ext in VIDEO_EXT:
        args=['-stream_loop','-1','-t',f'{duration:.3f}','-i',str(source)]
        vf=crop_filter(source,w,h)+f",fps={fps},setsar=1"
    else:
        palettes={
            'premium':[('0x0B1020','0x315BEA'),('0x101828','0x344054'),('0x172033','0x6941C6'),('0x0C111D','0x175CD3')],
            'sales':[('0x7A271A','0xF04438'),('0x101828','0xF79009'),('0x102A43','0x2E90FA'),('0x3E1C96','0x7F56D9')],
            'expert':[('0x102A43','0x175CD3'),('0x0C111D','0x344054'),('0x134E48','0x0E9384'),('0x1D2939','0x475467')],
            'dynamic':[('0x1D2939','0x7F56D9'),('0x101828','0x2E90FA'),('0x3E1C96','0xEE46BC'),('0x7A271A','0xF79009')],
        }
        c0,c1=palettes.get(style,palettes['premium'])[index%4]; gtype=index%4
        args=['-f','lavfi','-i',f'gradients=s={w}x{h}:r={fps}:c0={c0}:c1={c1}:n=2:d={duration:.3f}:speed=0.02:t={gtype}']
        vf='null'
    fade=min(.22,max(.08,duration*.04)); template=template_for_scene(style,role,index); vf += ','+scene_filter(template,w,h,duration)+f",fade=t=in:st=0:d={fade:.3f},fade=t=out:st={max(0,duration-fade):.3f}:d={fade:.3f}"
    cmd=['ffmpeg','-y',*args,'-an','-vf',vf,'-t',f'{duration:.3f}','-c:v','libx264','-preset','veryfast','-crf','21','-pix_fmt','yuv420p','-movflags','+faststart',str(out)]
    run(cmd)

def _concat_segments(segments,out):
    listing=out.with_suffix('.concat.txt')
    listing.write_text('\n'.join("file '%s'"%str(p).replace("'","'\\''") for p in segments)+'\n',encoding='utf-8')
    run(['ffmpeg','-y','-f','concat','-safe','0','-i',str(listing),'-c','copy',str(out)])
    listing.unlink(missing_ok=True)


def _xfade_segments(segments,durations,out,fps,overlap=.22):
    if len(segments)<=1:
        shutil.copy2(segments[0],out); return ['cut']
    cmd=['ffmpeg','-y']
    for p in segments: cmd += ['-i',str(p)]
    transitions=['fade','smoothleft','fadeblack','wipeleft']
    parts=[]; prev='0:v'; timeline=0.0; used=[]
    for i in range(1,len(segments)):
        timeline += float(durations[i-1])
        trans=transitions[(i-1)%len(transitions)]; used.append(trans); outlabel=f'x{i}'
        parts.append(f'[{prev}][{i}:v]xfade=transition={trans}:duration={overlap:.3f}:offset={timeline:.3f}[{outlabel}]')
        prev=outlabel
    cmd += ['-filter_complex',';'.join(parts),'-map',f'[{prev}]','-an','-r',str(fps),'-c:v','libx264','-preset','veryfast','-crf','21','-pix_fmt','yuv420p','-movflags','+faststart',str(out)]
    run(cmd)
    return used

def render_project(data,outputs,kind='preview',audio_override=None):
    plan=data['plan']; spec=plan['format_spec']; w,h,fps=spec['width'],spec['height'],spec['fps']
    duration=float(plan['duration']); duration=min(duration,30.0) if kind=='preview' else duration
    outdir=Path(outputs)/data['id']; outdir.mkdir(parents=True,exist_ok=True); out=outdir/(kind+'.mp4')
    visual=[a for a in data.get('assets',[]) if Path(a.get('path','')).suffix.lower() in IMAGE_EXT|VIDEO_EXT and Path(a.get('path','')).exists()]
    visual=sorted(visual,key=lambda a:a.get('media_score',0),reverse=True)
    mode=plan.get('mode')
    if mode=='materials' and not visual:
        raise RuntimeError('client_visuals_required')
    if mode=='from_scratch' and not visual:
        raise RuntimeError('generated_or_client_visuals_required')
    if mode=='boris_tutorial' and not any(a.get('kind')=='tutorial_capture' for a in visual):
        raise RuntimeError('tutorial_capture_required')
    audio_assets=[a for a in data.get('assets',[]) if Path(a.get('path','')).suffix.lower() in AUDIO_EXT and Path(a.get('path','')).exists()]
    voice_audio=None; music_audio=None
    if audio_override:
        voice_audio={'path':audio_override,'kind':'generated_voice'}
        if audio_assets: music_audio=audio_assets[0]
    elif plan.get('voice')=='upload' and audio_assets:
        voice_audio=audio_assets[0]
        if len(audio_assets)>1: music_audio=audio_assets[1]
    elif audio_assets:
        music_audio=audio_assets[0]
    # Tutorial capture must play CONTINUOUSLY. Restarting the same screen recording
    # at the beginning of every scene made training videos look empty/repetitive.
    tutorial_continuous = plan.get('mode') == 'boris_tutorial' and bool(visual)
    if tutorial_continuous:
        capdur=float(((visual[0].get('analysis') or {}).get('duration')) or duration)
        duration=min(duration,capdur) if capdur>0 else duration
    scenes=[]
    for sc in plan.get('scenes',[]):
        s=max(0,float(sc.get('start',0))); e=min(duration,float(sc.get('end',duration)))
        if e>s+0.05: scenes.append({**sc,'start':s,'end':e})
    if not scenes: scenes=[{'index':1,'start':0,'end':duration,'caption':plan.get('hook','')}]
    tmp=Path(tempfile.mkdtemp(prefix='boris_vf_',dir=str(outdir)))
    try:
        segs=[]
        if tutorial_continuous:
            base=tmp/'base.mp4'; src=visual[0]['path']
            vf=crop_filter(src,w,h)+f",fps={fps},setsar=1"
            run(['ffmpeg','-y','-i',str(src),'-an','-vf',vf,'-t',f'{duration:.3f}','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p','-movflags','+faststart',str(base)])
            transitions=['continuous_capture']
            # Remap planned captions over actual capture duration.
            raw=list(plan.get('scenes') or [])
            if raw and not all(sc.get('route') for sc in raw):
                mapped=[]
                for i,sc in enumerate(raw):
                    a=round(i*duration/len(raw),3); b=round((i+1)*duration/len(raw),3)
                    mapped.append({**sc,'start':a,'end':b})
                scenes=mapped
        else:
            for i,sc in enumerate(scenes):
                seg=tmp/f'seg_{i:03d}.mp4'
                exact=[a for a in visual if int(a.get('scene_index') or 0)==int(sc.get('index') or i+1)]
                chosen=exact[0] if exact else visual[i%len(visual)]
                src=chosen['path']
                nominal=sc['end']-sc['start']; segdur=nominal+(.22 if i<len(scenes)-1 else 0); _render_segment(src,seg,segdur,w,h,fps,i,plan.get('style','premium'),sc.get('role','proof')); segs.append(seg)
            base=tmp/'base.mp4'; transitions=_xfade_segments(segs,[sc['end']-sc['start'] for sc in scenes],base,fps,.22)
        ass=write_ass(tmp/'captions.ass',scenes,w,h,plan.get('style','premium'))
        brand_size=max(24,round(w*.026)); margin=max(24,int(w*.045))
        vf=overlay_filter(plan.get('style','premium'),w,h,duration)
        if plan.get('mode')=='boris_tutorial':
            vf+=f",drawtext=fontfile={FONT}:text='BORIS':fontsize={brand_size}:fontcolor=white@0.94:borderw=2:bordercolor=black@0.25:x={margin}:y={int(h*.075)}"
        vf+=f",subtitles='{ass}':fontsdir='/usr/share/fonts/truetype/dejavu'"
        cmd=['ffmpeg','-y','-i',str(base)]; voice_idx=None; music_idx=None
        if voice_audio:
            voice_idx=1; cmd += ['-stream_loop','-1','-i',voice_audio['path']]
        if music_audio:
            music_idx=2 if voice_audio else 1; cmd += ['-stream_loop','-1','-i',music_audio['path']]
        if voice_idx is None and music_idx is None:
            cmd += ['-f','lavfi','-i','anullsrc=channel_layout=stereo:sample_rate=48000']; silent_idx=1
        cmd += ['-vf',vf,'-map','0:v:0']
        if voice_idx is not None and music_idx is not None:
            cmd += ['-filter_complex',mix_filter(duration,voice_idx,music_idx),'-map','[mix]']
        elif voice_idx is not None:
            cmd += ['-map',f'{voice_idx}:a:0','-af',audio_filter(duration,'voice')]
        elif music_idx is not None:
            cmd += ['-map',f'{music_idx}:a:0','-af',audio_filter(duration,'music')]
        else:
            cmd += ['-map',f'{silent_idx}:a:0']
        cmd += ['-shortest','-t',f'{duration:.3f}','-c:v','libx264','-preset','veryfast','-crf','21','-pix_fmt','yuv420p','-c:a','aac','-b:a','160k','-movflags','+faststart',str(out)]
        run(cmd)
    finally:
        shutil.rmtree(tmp,ignore_errors=True)
    qa=inspect_video(out,spec)
    qa['scene_count']=len(scenes); qa['smart_crop']=True; qa['transitions']=transitions; qa['audio_mix']={'voice':bool(voice_audio),'music':bool(music_audio),'ducking':bool(voice_audio and music_audio)}; qa['motion_templates']=[template_for_scene(plan.get('style','premium'),sc.get('role','proof'),i) for i,sc in enumerate(scenes)]; qa['visual_assets_used']=min(len(visual),len(scenes)) if visual else 0; qa['captions']=True; qa['motion_style']=plan.get('style','premium')
    return out,qa
