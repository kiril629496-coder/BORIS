# -*- coding: utf-8 -*-
from __future__ import annotations
import re
from pathlib import Path

def _ass_time(sec):
    sec=max(0,float(sec)); h=int(sec//3600); sec-=h*3600; m=int(sec//60); sec-=m*60
    return f"{h}:{m:02d}:{sec:05.2f}"

def wrap_text(text,max_chars=28,max_lines=2):
    words=re.sub(r'\s+',' ',str(text or '')).strip().split(' ')
    lines=[]; cur=''
    for w in words:
        nxt=(cur+' '+w).strip()
        if len(nxt)<=max_chars or not cur: cur=nxt
        else: lines.append(cur); cur=w
    if cur: lines.append(cur)
    if len(lines)>max_lines:
        lines=lines[:max_lines]
        if len(lines[-1])>3: lines[-1]=lines[-1][:max(3,max_chars-1)].rstrip()+'…'
    return r'\N'.join(lines)

def write_ass(path,scenes,width,height,style='premium'):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    portrait=height>width
    font_size=max(42,round(width*(0.050 if portrait else 0.034)))
    margin_v=round(height*(0.13 if portrait else 0.10))
    outline=3 if width>=1080 else 2
    style_map={
        'premium':('DejaVu Sans',font_size,'&H00FFFFFF','&H00000000',outline),
        'sales':('DejaVu Sans',round(font_size*1.08),'&H00FFFFFF','&H00200000',outline+1),
        'expert':('DejaVu Sans',round(font_size*.92),'&H00FFFFFF','&H00101010',outline),
        'dynamic':('DejaVu Sans',round(font_size*1.10),'&H00FFFFFF','&H00000000',outline+1),
    }
    font,fs,primary,back,ol=style_map.get(style,style_map['premium'])
    head=f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,{font},{fs},{primary},{primary},&H80000000,{back},-1,0,0,0,100,100,0,0,1,{ol},1,2,70,70,{margin_v},1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    rows=[]
    for sc in scenes:
        raw=(sc.get('caption') or '').strip()
        text=wrap_text(raw)
        if not text: continue
        if style in ('dynamic','sales'):
            words=re.sub(r'\s+',' ',raw).split(' '); total=max(.1,float(sc.get('end',0))-float(sc.get('start',0))); each=max(4,int(total*100/max(1,len(words))))
            kara=' '.join('{\\k%d}%s'%(each,w) for w in words)
            text=kara
        rows.append(f"Dialogue: 0,{_ass_time(sc.get('start',0))},{_ass_time(sc.get('end',0))},Default,,0,0,0,,{text}")
    path.write_text(head+'\n'.join(rows)+'\n',encoding='utf-8')
    return path
